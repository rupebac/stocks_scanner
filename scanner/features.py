"""Per-stock feature assembly: SEC facts + weekly prices -> doc 03/05 metric inputs.

Produces one dict per stock: TTM metrics, 5y averages, margin brakes, the weekly
SELF timeline (as-known fundamentals stepped onto the price grid), and SELF stats.
All as-of discipline follows doc 06 §3 / doc 08 §3: facts visible after `filed`.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from . import config, metrics as M
from .sec_edgar import (
    DURATION_FIELDS, INSTANT_FIELDS, cover_shares_all, durations_all,
    fiscal_years, instant_asof, instants_all, quarters_all,
)

_FLOW_FIELDS = list(DURATION_FIELDS.keys())
_STACK_FIELDS = [
    "cash", "sti", "debt_current", "debt_noncurrent", "debt_total_tag",
    "finance_lease", "operating_lease", "preferred", "minority_interest",
]


def _snap_quarter(d: dt.date) -> dt.date:
    """Nearest calendar quarter end (ties forward). A fiscal quarter ending in the
    first half of a calendar quarter (Apr 1, Jun 14, a late-Jan FY end...) covers
    mostly the PRIOR calendar quarter, so it snaps back — bucketing by containing
    month mislabeled such quarters a full quarter late and could collide two
    adjacent fiscal quarters onto one snapped bucket."""
    qm = ((d.month - 1) // 3 + 1) * 3
    cur = dt.date(d.year + 1, 1, 1) - dt.timedelta(days=1) if qm == 12 else dt.date(d.year, qm + 1, 1) - dt.timedelta(days=1)
    prev = dt.date(d.year, qm - 2, 1) - dt.timedelta(days=1)
    return prev if (d - prev).days < (cur - d).days else cur


def _ttm_timeline(facts: dict, asof: dt.date) -> pd.DataFrame:
    """As-known TTM step series per flow field (docs 06 §3, 08 §3): at each FILING
    event, TTM = sum of the 4 most recent CONSECUTIVE snapped quarters, each quarter
    valued as known at that event (its latest filing <= event).

    Why not a latest-filed-per-quarter grid with known_from = max filed: SEC
    companyfacts re-file every quarter ~a year later as prior-year comparatives
    (and every 10-K re-presents 3 years). A latest-filed grid therefore keys TTM
    rows off comparative filings, making them "visible" 1-2 years after the value
    was actually reported — and stale (or wrongly-valued) for historical SELF
    weeks. Replaying filings in order keeps genuine restatements re-basing TTM at
    their filing date (doc 06 §3) while mere comparatives change nothing.

    Columns: `effective` (filing date) + one TTM column per field (NaN until 4
    consecutive quarters are known; a gap in the quarter sequence -> NaN, never a
    sum over non-adjacent quarters) + `qend_<field>` (newest quarter end, for
    staleness checks) + derived fcf / fcf_adj / ebitda."""
    events: dict[dt.date, dict[str, list[tuple[dt.date, float]]]] = {}
    for f in _FLOW_FIELDS:
        q = quarters_all(durations_all(facts, f))
        if q.empty:
            continue
        q = q[q["filed"] <= asof]
        if q.empty:
            continue
        revs = q.sort_values(["filed", "end"]).groupby(["filed", "end"], as_index=False).last()
        for filed, qe, v in zip(revs["filed"], revs["end"], revs["value"]):
            events.setdefault(filed, {}).setdefault(f, []).append((qe, float(v)))
    fields = sorted({f for ev in events.values() for f in ev})
    if not fields:
        return pd.DataFrame()
    active: dict[str, dict[dt.date, float]] = {f: {} for f in fields}
    rows = []
    for t in sorted(events):
        for f, ups in events[t].items():
            active[f].update(dict(ups))
        row: dict = {"effective": t}
        for f in fields:
            # collapse near-duplicate quarter ends (<60d apart: end-date drift or
            # restatements with a shifted end) keeping the LATEST of each cluster —
            # raw ends, no calendar-quarter snapping: filers like COST/AZO end their
            # quarters mid-month and two adjacent fiscal quarters could collide on
            # one snapped bucket, silently losing quarters
            keys = sorted(active[f])
            kept: dict[dt.date, float] = {}
            last_k = None
            for e in keys:
                if last_k is not None and (e - last_k).days < 60:
                    kept.pop(last_k)
                kept[e] = active[f][e]
                last_k = e
            active[f] = kept
            ends = sorted(active[f])
            if len(ends) >= 4:
                last4 = ends[-4:]
                if (last4[-1] - last4[0]).days <= 300:  # 4 adjacent quarters span <= ~276d
                    row[f] = sum(active[f][e] for e in last4)
                    row["qend_" + f] = pd.Timestamp(last4[-1])
        rows.append(row)
    tl = pd.DataFrame(rows)
    if tl.empty:
        return tl
    if {"cfo", "capex"} <= set(tl.columns):
        tl["fcf"] = tl["cfo"] - tl["capex"]
        tl["qend_fcf"] = tl[["qend_cfo", "qend_capex"]].max(axis=1).where(tl["fcf"].notna())
        if "sbc" in tl.columns:
            tl["fcf_adj"] = tl["fcf"] - tl["sbc"]
            tl["qend_fcf_adj"] = tl[["qend_fcf", "qend_sbc"]].max(axis=1).where(tl["fcf_adj"].notna())
        else:
            # SBC never tagged (26 standard-group filers, trivial stock comp):
            # treated as zero, dq-flagged downstream (owner-approved v0.5.2)
            tl["fcf_adj"] = tl["fcf"]
            tl["qend_fcf_adj"] = tl["qend_fcf"]
    if {"ebit", "dna"} <= set(tl.columns):
        tl["ebitda"] = tl["ebit"] + tl["dna"]
        tl["qend_ebitda"] = tl[["qend_ebit", "qend_dna"]].max(axis=1).where(tl["ebitda"].notna())
    return tl


def _instant_steps(instants: pd.DataFrame) -> pd.DataFrame | None:
    """Step series [effective(filed), val] for an instant field: at each filing date,
    the value of the latest balance-sheet date known by then."""
    if instants.empty:
        return None
    rows = instants.sort_values(["filed", "end"])
    steps = []
    best_end, best_val = None, None
    for _, r in rows.iterrows():
        if best_end is None or r["end"] >= best_end:
            best_end, best_val = r["end"], float(r["val"])
        steps.append({"effective": r["filed"], "val": best_val})
    out = pd.DataFrame(steps).groupby("effective", as_index=False).last()
    out["effective"] = pd.to_datetime(out["effective"])
    return out


def _asof_step(weeks: pd.DataFrame, steps: pd.DataFrame | None, col: str) -> pd.Series:
    if steps is None or steps.empty:
        return pd.Series(np.nan, index=weeks.index, name=col)
    s = steps.copy()
    s["effective"] = pd.to_datetime(s["effective"])
    if col in s.columns and col != "val":
        s = s.rename(columns={col: "val"})
    s = s[["effective", "val"]]
    left = weeks[["week"]].assign(week=lambda d: d["week"].astype("datetime64[ns]"))
    s["effective"] = s["effective"].astype("datetime64[ns]")
    merged = pd.merge_asof(
        left, s.sort_values("effective"),
        left_on="week", right_on="effective", direction="backward",
    )
    # merge_asof resets the index — keep `weeks`' index so hist parts align on concat
    # (a misaligned concat silently dropped shares/mktcap from the newest weeks)
    merged.index = weeks.index
    return merged["val"].rename(col)


def _stack_steps(instants_by_field: dict[str, pd.DataFrame]) -> pd.DataFrame | None:
    """Effective-date grid of the EV non-equity stack (debt+leases+pref+MI−cash−STI).
    Components absent from a filing hold last known; a field never reported is 0,
    except cash (critical)."""
    frames = {}
    for f in _STACK_FIELDS:
        steps = _instant_steps(instants_by_field.get(f, pd.DataFrame()))
        if steps is not None:
            frames[f] = steps.rename(columns={"val": f})
    if not frames:
        return None
    all_eff = sorted(set().union(*[set(df["effective"]) for df in frames.values()]))
    grid = pd.DataFrame({"effective": pd.to_datetime(pd.Series(all_eff))})
    for f, df in frames.items():
        df = df.copy()
        df["effective"] = pd.to_datetime(df["effective"])
        grid = pd.merge_asof(grid, df.sort_values("effective"), on="effective", direction="backward")
    for f in _STACK_FIELDS:          # a field never reported by this filer -> 0
        if f not in grid.columns:
            grid[f] = 0.0
    grid = grid.ffill().fillna(0.0)
    # per-row fallback: wherever LongTermDebtNoncurrent is absent but LongTermDebt
    # (total) is reported, noncurrent = total − current for THAT row (all-or-nothing
    # would zero noncurrent debt for filers that adopted the split tag late)
    use_total = (grid["debt_noncurrent"] <= 0) & (grid["debt_total_tag"] > 0)
    grid.loc[use_total, "debt_noncurrent"] = (
        grid.loc[use_total, "debt_total_tag"] - grid.loc[use_total, "debt_current"]
    ).clip(lower=0)
    grid["stack"] = (
        grid["debt_current"] + grid["debt_noncurrent"] + grid["finance_lease"]
        + grid["operating_lease"] + grid["preferred"] + grid["minority_interest"]
        - grid["cash"] - grid["sti"]
    )
    return grid[["effective", "stack"] + _STACK_FIELDS]


def _fy_features(facts: dict, asof: dt.date) -> tuple[pd.DataFrame, dict]:
    """Fiscal-year frame (revenue, ebit, fcf, margins...) + per-FY ROIC via IC at FY end.
    As-of honest: only facts FILED on or before `asof` enter (doc 06 §3) — a later
    restatement must not rewrite history for a historical scan date."""
    def d_asof(field):
        d = durations_all(facts, field)
        return d[d["filed"] <= asof] if not d.empty else d

    def i_asof(field):
        ins = instants_all(facts, field)
        return ins[ins["filed"] <= asof] if not ins.empty else ins

    fy: dict[str, pd.Series] = {}
    for f in ["revenue", "gross_profit", "cost_revenue", "ebit", "dna", "cfo", "capex",
              "sbc", "net_income", "tax_provision", "pretax_income"]:
        s = fiscal_years(d_asof(f))
        if not s.empty:
            fy[f] = s.set_index("end")["value"]
    if "revenue" not in fy:
        return pd.DataFrame(), {}
    F = pd.DataFrame(fy).sort_index()
    F = F[~F.index.duplicated(keep="last")]
    if {"cfo", "capex"} <= set(F.columns):
        F["fcf"] = F["cfo"] - F["capex"]
        if "sbc" in F.columns:
            F["fcf_adj"] = F["fcf"] - F["sbc"]
        else:
            F["fcf_adj"] = F["fcf"]  # SBC untagged -> zero, dq-flagged (v0.5.2)
    if "gross_profit" in F.columns:
        F["gm"] = F["gross_profit"] / F["revenue"]
    elif {"revenue", "cost_revenue"} <= set(F.columns):
        # GM derived from cost-of-revenue (owner-approved v0.5.2; 143 filers)
        F["gm"] = (F["revenue"] - F["cost_revenue"]) / F["revenue"]
    if "fcf_adj" in F.columns:
        F["fcf_margin"] = F["fcf_adj"] / F["revenue"]
    if {"tax_provision", "pretax_income"} <= set(F.columns):
        F["t_eff"] = [
            M.t_eff_winsorized(t, p) for t, p in zip(F["tax_provision"], F["pretax_income"])
        ]
        # fallback: trailing 3y mean of PRIOR years (doc 06 §6) — a median over all
        # years would leak future rates into historical NOPAT/ROIC
        F["t_eff"] = F["t_eff"].fillna(F["t_eff"].rolling(3, min_periods=1).mean().shift(1))

    # IC at each FY end from as-of-visible instants (within 30d of the FY end)
    eq = i_asof("equity")
    stack = _stack_steps({f: i_asof(f) for f in _STACK_FIELDS})
    ic_rows = {}
    for fy_end in F.index:
        ic = _ic_at(eq, stack, fy_end)
        ic_rows[fy_end] = ic
    F["ic"] = pd.Series(ic_rows)
    if "ebit" in F.columns and "t_eff" in F.columns and F["t_eff"].notna().any():
        F["nopat"] = [M.nopat(e, t) for e, t in zip(F["ebit"], F["t_eff"])]
        F["roic"] = F["nopat"] / F["ic"]
    return F, {"equity": eq, "stack": stack}


def _ic_at(eq, stack, fy_end) -> float | None:
    """Invested capital as of a fiscal-year end: equity from the FILING THAT FIRST
    reported the FY-end balance sheet (end within ±30d of fy_end, filed within
    +120d), and the non-equity stack as of that same filing — NOT the last step
    before fy_end+120d, which for fast filers is already the NEXT quarter's
    balance sheet (Adobe's 10-K lands ~48d after FY end, its Q1 10-Q ~117d)."""
    if eq is None or eq.empty or stack is None or stack.empty:
        return None
    hits = eq[(eq["end"] >= fy_end - dt.timedelta(days=30)) & (eq["end"] <= fy_end + dt.timedelta(days=30))]
    hits = hits[hits["filed"] <= fy_end + dt.timedelta(days=120)]
    if hits.empty:
        return None
    dist = (hits["end"] - fy_end).map(lambda d: abs(d.days))
    first = hits.assign(_d=dist).sort_values(["_d", "filed"]).iloc[0]
    srow = stack[stack["effective"] <= pd.Timestamp(first["filed"])]
    if srow.empty:
        return None
    # stack = debt+leases+pref+MI−cash−sti, so IC = stack + equity (doc 06 §5 identity)
    return float(srow["stack"].iloc[-1]) + float(first["val"])


def build_features(facts: dict, weekly: pd.DataFrame, asof: dt.date,
                   fallback_shares: float | None = None) -> dict | None:
    """Weekly frame: [week, close] for this ticker (6y). Returns the metric row or None."""
    px = weekly.sort_values("week").reset_index(drop=True)
    if len(px) < 60:
        return None
    tl = _ttm_timeline(facts, asof)
    if tl.empty or "revenue" not in tl.columns:
        return None
    F, inst = _fy_features(facts, asof)

    # --- live instants & derived valuation (asof) ------------------------------
    def now(field):
        return instant_asof(instants_all(facts, field), asof)[1]

    cash, sti = now("cash"), now("sti") or 0.0
    pref = now("preferred") or 0.0
    mi = now("minority_interest") or 0.0
    gw = now("goodwill")
    intan = now("intangibles")
    if intan is None:
        intan = now("intangibles_alt")
    equity = now("equity")

    stack_now = None
    if inst.get("stack") is not None and not inst["stack"].empty:
        srow = inst["stack"][inst["stack"]["effective"] <= pd.Timestamp(asof)]
        if not srow.empty:
            stack_now = float(srow["stack"].iloc[-1])
    if stack_now is None or cash is None:
        return None  # no EV possible (doc 08 §2: cash is critical)

    covers = cover_shares_all(facts)

    def shares_fallbacks():
        """Proxy chain when the cover tags are unusable: basic weighted-average shares
        timeline (a period average, not point-in-time -> dq-flagged), then a constant
        yfinance count anchored at the window start (keeps a constant-shares SELF
        history). Returns a covers frame or None."""
        if "shares_basic_was" in tl.columns and tl["shares_basic_was"].notna().any():
            s = tl[tl["shares_basic_was"].notna() & (tl["effective"] <= asof)]
            if not s.empty:
                eff = pd.to_datetime(s["effective"])
                return pd.DataFrame({
                    "end": eff.dt.date,
                    "filed": eff.dt.date,
                    "val": s["shares_basic_was"].astype(float),
                })
        if fallback_shares and float(fallback_shares) > 0:
            return pd.DataFrame({"end": [px["week"].iloc[0].date()],
                                 "filed": [px["week"].iloc[0].date()],
                                 "val": [float(fallback_shares)]})
        return None

    shares_was_proxy = False
    cv = covers[covers["filed"] <= asof].sort_values(["end", "filed"])
    shares_row = cv.iloc[-1] if not cv.empty else None
    # STALE cover shares (nothing newer than ~15 months: DDOG's last real cover row is
    # its IPO era) are as unusable as none — fall through to the proxies
    if shares_row is None or (asof - shares_row["end"]).days > 450:
        proxied = shares_fallbacks()
        if proxied is None or proxied.empty:
            return None
        shares_was_proxy = True
        cv = proxied[proxied["filed"] <= asof].sort_values(["end", "filed"])
        shares_row = cv.iloc[-1] if not cv.empty else None
        if shares_row is None:
            return None
    shares_now = float(shares_row["val"])
    if not shares_now:
        return None
    price_now = float(px["close"].iloc[-1])
    mktcap = price_now * shares_now
    ev_now = mktcap + stack_now

    # --- TTM point values (last as-known step; stale beyond 400d) ---------------
    def t(field):
        if tl.empty or field not in tl.columns:
            return None
        s = tl[tl[field].notna()]
        if s.empty or (pd.Timestamp(asof) - s["qend_" + field].iloc[-1]).days > 400:
            return None
        return float(s[field].iloc[-1])

    rev, ebit, dna = t("revenue"), t("ebit"), t("dna")
    cfo, capex, sbc = t("cfo"), t("capex"), t("sbc")
    ni, tax, pretax = t("net_income"), t("tax_provision"), t("pretax_income")
    sbc_unreported = sbc is None
    fcf_t = M.fcf(cfo, capex)
    # SBC untagged -> treated as zero (owner-approved v0.5.2), dq-flagged below
    fcf_adj_t = M.fcf_adj(cfo, capex, sbc) if sbc is not None else fcf_t
    ebitda_t = M.ebitda(ebit, dna)
    t3y = None
    if F is not None and "t_eff" in F.columns and F["t_eff"].notna().any():
        t3y = float(F["t_eff"].dropna().tail(3).mean())
    t_eff = M.t_eff_winsorized(tax, pretax, fallback=t3y)
    nopat_t = M.nopat(ebit, t_eff)

    ic_now = None
    if equity is not None:
        ic_now = stack_now + equity  # see _ic_at identity
    roic_ttm = M.safe_div(nopat_t, ic_now) if nopat_t and ic_now and ic_now > 0 else None

    ev_ebit = M.safe_div(ev_now, ebit) if ebit and ebit > 0 else None
    ev_fcf = M.safe_div(ev_now, fcf_adj_t) if fcf_adj_t and fcf_adj_t > 0 else None
    fcf_yield = M.safe_div(fcf_adj_t, ev_now)
    pe_ttm = M.safe_div(mktcap, ni) if ni and ni > 0 else None
    fcf_ni = M.safe_div(fcf_t, ni) if ni and ni > 0 else None
    # net debt excludes preferred and minority interest (doc 03 §3.5 / doc 06 §4):
    # nd_ebitda = (debt + leases − cash − sti) / EBITDA, not the whole EV stack
    nd_ebitda = (
        M.safe_div(stack_now - pref - mi, ebitda_t) if ebitda_t and ebitda_t != 0 else None
    )

    # --- 5y/10y averages, medians, path, CAGR (doc 03 / 05 §2.1 v0.5.8) ----------
    out: dict = {}
    if F is not None and not F.empty:
        out.update(M.fy_window_quality(F, cur_rev=rev))
    # brakes: TTM vs 5y avg — GM from the derived path when GrossProfit is unreported
    gm_ttm = M.safe_div(t("gross_profit"), rev)
    if gm_ttm is None:
        cr_ratio = M.safe_div(t("cost_revenue"), rev)
        gm_ttm = (1.0 - cr_ratio) if cr_ratio is not None else None
    fcf_margin_ttm = M.safe_div(fcf_adj_t, rev)
    out["gm_ttm"] = gm_ttm
    out["fcf_margin_ttm"] = fcf_margin_ttm
    out["brake_gm"] = M.safe_div(gm_ttm, out.get("gm")) if out.get("gm") not in (None, 0) else None
    out["brake_fcf_margin"] = M.safe_div(fcf_margin_ttm, out.get("fcf_margin")) if out.get("fcf_margin") not in (None, 0) else None
    out["brake_roic"] = M.safe_div(roic_ttm, out.get("roic")) if out.get("roic") not in (None, 0) else None

    # --- SELF weekly timeline (5y window, as-known fundamentals) -----------------
    win = px.tail(int(config.SELF_WINDOW_YEARS * 52) + 8).copy()
    win = win[win["week"] <= pd.Timestamp(asof)]
    # the SELF timeline's share step uses whichever frame won: real covers, or the
    # proxy (whose rows live in cv) when the cover tags were missing/stale
    self_covers = cv if shares_was_proxy else covers
    shares_step = _instant_steps(self_covers) if not self_covers.empty else None
    stack = inst.get("stack")
    hist_parts = [win[["week", "close"]].rename(columns={"close": "price"})]
    if shares_step is not None:
        hist_parts.append(_asof_step(win, shares_step, "shares").to_frame())
    else:
        hist_parts.append(pd.DataFrame({"shares": np.nan}, index=win.index))
    hist_parts.append(_asof_step(win, stack, "stack").to_frame())
    for col, name in (("fcf_adj", "fcf_adj_ttm"), ("net_income", "ni_ttm")):
        if col in tl.columns:
            steps = tl[tl[col].notna()][["effective", col]].rename(columns={col: "val"})
            steps["effective"] = pd.to_datetime(steps["effective"])
            hist_parts.append(_asof_step(win, steps, name).to_frame())
        else:
            hist_parts.append(pd.DataFrame({name: np.nan}, index=win.index))
    hist = pd.concat(hist_parts, axis=1)
    hist["mktcap"] = hist["price"] * hist["shares"]
    hist["ev"] = hist["mktcap"] + hist["stack"]
    hist["ev_fcf"] = hist["ev"] / hist["fcf_adj_ttm"].where(hist["fcf_adj_ttm"] > 0)
    # SELF validity (docs 02/06 §9): >=60% of the WINDOW weeks valid, not just >=3y
    # of valid observations — the old tail-of-valid-drops silently passed 60%-invalid
    # windows that happened to clear the 3y minimum.
    recent = hist.tail(int(config.SELF_WINDOW_YEARS * 52))
    valid = recent["ev_fcf"].dropna()
    # pass the NaN-INCLUSIVE window: self_stats enforces >=60%-valid and
    # current-value-valid itself (doc 06 §9); the caller-side fraction check is
    # kept as belt-and-braces
    self = (
        M.self_stats(recent["ev_fcf"])
        if len(recent) and len(valid) / len(recent) >= config.SELF_MIN_VALID_FRAC
        else None
    )
    if self:
        out["ev_fcf_self_pct"] = self["pct"]
        out["ev_fcf_self_z"] = self["z"]
        out["ev_fcf_self_ratio"] = self["median_ratio"]
        out["ev_fcf_self_n"] = self["n_valid"]

    # --- data-quality flags (doc 08 §6, minimal set: flag, never silently poison) --
    dq = []
    if shares_was_proxy:
        dq.append("shares_was_proxy")
    if sbc_unreported and fcf_adj_t is not None:
        dq.append("sbc_unreported")  # fcf_adj = plain fcf (owner-approved v0.5.2)
    if F is not None and "gm" in getattr(F, "columns", []) and "gross_profit" not in F.columns:
        dq.append("gm_derived")      # gm = (revenue - cost of revenue)/revenue (v0.5.2)
    if (ev_now is not None and ev_now <= 0) or (ic_now is not None and ic_now <= 0):
        dq.append("ev_or_ic_nonpos")
    if bool(shares_row.get("multi", False)):
        dq.append("dual_class_shares_summed")
    if len(cv) >= 2:
        s_prev = float(cv["val"].iloc[-2])
        if s_prev > 0 and abs(shares_now / s_prev - 1.0) > 0.20:
            dq.append("share_jump_gt20pct")
    if cfo is not None and capex is not None and cfo > 0 and capex > 2 * cfo:
        dq.append("capex_gt_2x_cfo")
    if rev is not None and rev < 0:
        dq.append("neg_quarter_revenue")  # doc 08 §3.2: a YTD-monotonicity violation
    out["dq_flags"] = ";".join(dq)

    # --- flat row ---------------------------------------------------------------
    out.update({
        "price": price_now, "shares_cover": shares_now, "mktcap": mktcap,
        "ev": ev_now, "ic": ic_now, "stack": stack_now,
        "revenue_ttm": rev, "ebit_ttm": ebit, "fcf_ttm": fcf_t, "fcf_adj_ttm": fcf_adj_t,
        "ebitda_ttm": ebitda_t, "ni_ttm": ni, "nopat_ttm": nopat_t, "t_eff": t_eff,
        "roic_ttm": roic_ttm,
        "ev_ebit": ev_ebit, "ev_fcf": ev_fcf, "fcf_yield": fcf_yield,
        "pe_ttm": pe_ttm, "fcf_ni": fcf_ni, "nd_ebitda": nd_ebitda,
        "goodwill": gw, "intangibles": intan,
    })
    if equity is not None and ic_now:
        out["goodwill_share_ic"] = M.safe_div((gw or 0) + (intan or 0), ic_now)
    history = hist[["week", "price", "mktcap", "ev", "fcf_adj_ttm", "ni_ttm", "ev_fcf"]].copy()
    history.insert(0, "asof", pd.Timestamp(asof))
    return {**out, "history": history}
