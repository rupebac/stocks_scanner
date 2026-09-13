"""Streamlit UI for stocks_scanner — reads scan artifacts (data/scans/<date>/).

Two pages: **Ideas** (the default) and **Data manager** (ops). Ideas is one
scrolling page for the manual cash-secured-put workflow: the hunt map (quality
vs residual — cheaper-than-quality to the right), tagged cards for the
highlights, and a company detail that appears once you pick a ticker.
The company pane has two tabs: **Financials** (scores, assignment FCF, history
charts, margins) and **Options** (live chain — pick a put or call for
breakeven and greeks). Cheapness on this page means the valuation residual: log(EV/FCF) minus the
multiple this ROIC/industry usually gets; the highlights require the quality
floor, residual < 0, ROIC ≥ 10% and EV/FCF low enough to beat max(4%, 10Y) in
cash. A named cash setting chooses reported FCF or owner earnings for the
FCF-CAGR gate, QualityScore FCF sleeves, residual, and the yield bar; overlay
strike yields stay reported. SELF (vs own 5y
history) stays a chart, never a rank. Every number is display-only — the
scanner ranks, the human decides.

Run: streamlit run dashboard/app.py   (or ./control.sh start)
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import flags as FL, market_data as MD, scoring as SC  # noqa: E402
from scanner.metrics import holdability_roic  # noqa: E402
SCANS = ROOT / "data" / "scans"

st.set_page_config(page_title="stocks_scanner", layout="wide")

# Menu cleanup: hide the built-in Print / Record-screencast entries (useless here;
# each item carries data-testid="stMainMenuItem-<key>" in this Streamlit version).
st.markdown(
    """
    <style>
      [data-testid="stMainMenuItem-print"],
      [data-testid="stMainMenuItem-recordScreencast"] {
        display: none !important;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

# Column hover docs for the compact table. Mirrors scanner/config.py + docs 05/06 —
# if a threshold changes there, fix this text.
COLUMN_DOCS = {
    "quality_score": (
        "How good the business is on absolute bars, not versus industry peers: "
        "profitability 40% (ROIC and FCF margin, using min of TTM and 5y median so a "
        "boom year cannot lift the score) · growth 30% (5y revenue and FCF CAGR, capped "
        "at 12%) · balance 30% (net debt/EBITDA; net cash = 100, 3× = 0). That core is "
        "then multiplied by path stability: revenue-up-year share 50%, gross-margin CoV "
        "25%, FCF CoV 25% (missing slots renormalize; FCF cannot dominate when GM is "
        "unreported). A jumpy cyclical cannot average its way to a high score. Floor is "
        "Quality ≥ 60 and conservative ROIC in [10%, 100%] when ROIC is known "
        "(>100% is a tiny-capital artifact and fails; missing does not). FCF sleeves "
        "follow the Ideas cash setting (reported FCF vs owner earnings)."
    ),
    "residual": (
        "log(EV/FCF) minus the multiple this ROIC and industry group usually get "
        "(regression fit). More negative = cheaper than the quality deserves. "
        "The zone cut is residual < 0. EV/FCF follows the Ideas cash setting."
    ),
    "ev_fcf": (
        "EV / TTM cash (SBC-expensed). Default is reported FCF (CFO − capex − SBC). "
        "Owner-earnings mode uses CFO − D&A − SBC instead."
    ),
    "price": "Last weekly close.",
    "yield at −5%": "FCF yield you'd lock in if put the stock at −5% (from the options chain).",
}


@st.cache_data(show_spinner=False)
def load_scan(scan_dir: Path, _stamp: float):
    """_stamp = config.json mtime, so a re-scan into the same date folder invalidates
    the cache and the UI picks up new artifacts without a restart."""
    metrics = pd.read_csv(scan_dir / "metrics.csv")
    cfg = json.loads((scan_dir / "config.json").read_text())
    hist = pd.read_parquet(scan_dir / "history.parquet") if (scan_dir / "history.parquet").exists() else pd.DataFrame()
    overlay = pd.read_csv(scan_dir / "overlay.csv") if (scan_dir / "overlay.csv").exists() else pd.DataFrame()
    return metrics, cfg, hist, overlay


def _latest_scan() -> Path | None:
    dirs = sorted(p for p in SCANS.iterdir() if p.is_dir()) if SCANS.exists() else []
    return dirs[-1] if dirs else None


# ------------------------------------------------------------ live options ---
# Cached so slider/select reruns don't hammer yfinance; keys carry today so the
# cache rolls over at midnight. Live quotes never touch the highlights.
@st.cache_data(ttl=90, show_spinner=False)
def _live_expiries(ticker: str, today: str) -> list[str]:
    return [d.isoformat() for d in MD.listed_expiries(ticker, dt.date.fromisoformat(today))]


@st.cache_data(ttl=90, show_spinner=False)
def _live_chain(ticker: str, expiry: str) -> dict | None:
    raw = MD.fetch_option_chain(ticker, dt.date.fromisoformat(expiry))
    if raw is None:
        return None
    return {"puts": raw["puts"].to_dict("records"),
            "calls": raw["calls"].to_dict("records"),
            "fetched_at": dt.datetime.now().isoformat(timespec="seconds")}


@st.cache_data(ttl=3600, show_spinner=False)
def _next_earnings(ticker: str, today: str) -> str | None:
    d = MD.next_earnings_date(ticker, dt.date.fromisoformat(today))
    return d.isoformat() if d else None


def _chain_frames(ticker: str, expiry: str) -> tuple[pd.DataFrame, pd.DataFrame, str | None]:
    raw = _live_chain(ticker, expiry)
    if not raw:
        return pd.DataFrame(), pd.DataFrame(), None
    return pd.DataFrame(raw["puts"]), pd.DataFrame(raw["calls"]), raw.get("fetched_at")


def _quote_from_side(df: pd.DataFrame, spot, target_pct: float, expiry: str,
                     today: dt.date) -> dict | None:
    q = MD.pick_nearest_strike(df, float(spot), target_pct)
    if q is None:
        return None
    q["expiry"] = expiry
    q["dte"] = (dt.date.fromisoformat(expiry) - today).days
    return q


def _put_one_liner(q) -> str:
    """Standing/event put as one scannable line — not a table."""
    if q is None:
        return "no quote"
    p = MD.premium_pct_of_strike(q)
    bid = f"{q['bid']:.2f}" if q.get("bid") is not None else "—"
    paid = f"{p:.2%}" if p is not None else "—"
    note = " · weak quote" if q.get("weak_quote") else ""
    return f"${q['strike']:,.0f} · bid {bid} · paid {paid}{note}"


def _chain_picked_i(key: str, n: int) -> int | None:
    """Row index from a previous chain click (session state), if still in range."""
    state = st.session_state.get(key)
    if state is None or n <= 0:
        return None
    rows = None
    try:
        rows = state.selection.rows
    except Exception:
        try:
            rows = (state.get("selection") or {}).get("rows")
        except Exception:
            rows = None
    if not rows:
        return None
    i = int(rows[0])
    return i if 0 <= i < n else None


def _options_section(sel: str, spot, gs10, ov_row) -> None:
    """Live chain + selected-contract CSP numbers. Display-only: never hides or ranks."""
    today = dt.date.today()
    if spot is None or not (float(spot) > 0):
        st.caption("No spot price — cannot quote options.")
        return
    spot = float(spot)
    rate = float(gs10) if gs10 is not None and pd.notna(gs10) else 0.0

    exps = _live_expiries(sel, today.isoformat())
    if not exps:
        st.caption("No option chain available right now.")
        return
    dte_of = {e: (dt.date.fromisoformat(e) - today).days for e in exps}
    ge21 = [e for e in exps if dte_of[e] >= 21]
    default = (min(exps, key=lambda e: abs(dte_of[e] - 35)) if ge21
               else max(exps, key=lambda e: dte_of[e]))
    label_of = {f"{dt.date.fromisoformat(e):%a %b %d} · {dte_of[e]} DTE": e for e in exps}
    labels = list(label_of.keys())
    default_label = next(l for l, e in label_of.items() if e == default)
    exp_key = f"opt_exp_{sel}"
    cur_label = st.session_state.get(exp_key)
    if cur_label not in label_of:
        cur_label = default_label
    active_snap = min((7, 21, 30, 45, 60),
                      key=lambda n: abs(n - dte_of[label_of[cur_label]]))
    if exp_key not in st.session_state or st.session_state[exp_key] not in label_of:
        st.session_state[exp_key] = default_label
    snap_key = f"opt_horizon_{sel}"
    if snap_key not in st.session_state:
        st.session_state[snap_key] = active_snap

    def _jump_from_snap():
        n = st.session_state.get(snap_key)
        if n not in (7, 21, 30, 45, 60):
            return
        choice = min(exps, key=lambda e: abs(dte_of[e] - int(n)))
        st.session_state[exp_key] = (
            f"{dt.date.fromisoformat(choice):%a %b %d} · {dte_of[choice]} DTE")

    def _snap_from_exp():
        lab = st.session_state.get(exp_key)
        if lab not in label_of:
            return
        d = dte_of[label_of[lab]]
        st.session_state[snap_key] = min((7, 21, 30, 45, 60), key=lambda n: abs(n - d))

    st.segmented_control(
        "Horizon",
        options=[7, 21, 30, 45, 60],
        format_func=lambda n: f"{n}d",
        key=snap_key,
        on_change=_jump_from_snap,
        label_visibility="collapsed",
        width="content",
    )

    c_exp, c_tgt, c_side, c_near = st.columns([2.2, 1.15, 1.35, 1.3])
    with c_exp:
        label = st.selectbox("Expiry", labels, key=exp_key, on_change=_snap_from_exp)
    expiry = label_of[label]
    dte = dte_of[expiry]
    puts, calls, chain_at = _chain_frames(sel, expiry)

    rule_pct = MD.strike_target_pct(dte)
    tgt_key, lastexp_key = f"opt_target_{sel}", f"opt_lastexp_{sel}"
    if st.session_state.get(lastexp_key) != label or tgt_key not in st.session_state:
        st.session_state[lastexp_key] = label
        st.session_state[tgt_key] = round(rule_pct * 100, 1)
    with c_tgt:
        st.number_input(
            "Strike % of spot", 50.0, 105.0, key=tgt_key, step=0.5,
            help=f"Default at {dte} DTE is {rule_pct:.0%} of spot.")
    with c_side:
        side = st.radio("Side", ["Puts", "Calls"], horizontal=True, key=f"opt_side_{sel}")
    with c_near:
        around = st.toggle("Near the spot", value=True, key=f"opt_near_{sel}")

    # quote freshness: when this chain was pulled, plus a manual pull-now button
    q_cap, q_btn = st.columns([3.2, 1])
    clock = chain_at.split("T")[-1] if chain_at else None
    q_cap.caption(
        f"**Quotes as of {clock} today** · live, auto-refreshes within 90s."
        if clock else "Live quotes · auto-refresh within 90s.")
    if q_btn.button("↻ Refresh quotes", key=f"opt_refresh_{sel}",
                    help="Drop the cached chains and pull fresh quotes from the "
                         "option chain now (prices move during the day)."):
        _live_expiries.clear()
        _live_chain.clear()
        st.rerun()

    target_pct = float(st.session_state[tgt_key]) / 100.0
    raw = puts if side == "Puts" else calls
    if raw.empty:
        st.caption(f"No {side.lower()} listed for this expiry.")
        return
    view = raw.copy()
    if around:
        # window centered on the SUGGESTED strike (follows the strike-target input)
        # so the preselected row sits mid-table; wide enough upward to keep ITM
        # strikes visible. Toggle off to see the whole chain.
        tgt_strike = spot * (target_pct if side == "Puts" else 1.00)
        near = view[(view["strike"] >= tgt_strike * 0.90) & (view["strike"] <= tgt_strike * 1.18)]
        if not near.empty:
            view = near
    view = view.reset_index(drop=True)

    chain_key = f"opt_chain_{sel}_{expiry}_{side}_{int(around)}"
    default_i = int((view["strike"] - spot * (target_pct if side == "Puts" else 1.00)).abs().idxmin())
    # keep the table's selected row in sync with the suggestion: a change of
    # strike target (or a fresh expiry/side view) preselects the suggested row;
    # once you click another row, your click wins until the target changes again
    sync_key = f"opt_sync_{chain_key}"
    sync_sig = f"{target_pct:.3f}"
    if st.session_state.get(sync_key) != sync_sig:
        st.session_state[sync_key] = sync_sig
        st.session_state[chain_key] = {"selection": {"rows": [default_i], "columns": []}}
    picked_i = _chain_picked_i(chain_key, len(view))
    if picked_i is None:
        picked_i = default_i
    picked_i = max(0, min(int(picked_i), len(view) - 1))
    crow = view.iloc[picked_i]
    bid, ask, last = MD._n(crow.get("bid")), MD._n(crow.get("ask")), MD._n(crow.get("last"))
    prem, mid, weak = MD._premium_from_quotes(bid, ask, last)
    wide = (bid is not None and ask is not None and mid is not None
            and mid > 0 and (ask - bid) / mid >= 0.5)
    stats = MD.contract_analytics(
        "put" if side == "Puts" else "call",
        spot, float(crow["strike"]), prem, dte, MD._n(crow.get("iv")), rate=rate,
    )

    kind = side[:-1].lower()
    st.markdown(
        f"**This {kind}** · ${float(crow['strike']):,.0f} strike · {dte}d · "
        f"premium {_fmt(prem, '{:.2f}')}"
    )
    if weak:
        st.caption("Weak quote — bid is empty, so this is mid/last, not a real bid.")
    elif wide:
        st.caption(f"Wide market · bid {bid} / ask {ask} — treat the bid as the price.")

    cash = stats.get("cash_pct")
    ann = stats.get("annualized")
    ann_vs = stats.get("ann_vs_rate")
    ipr = stats.get("income_per_risk")
    cushion = stats.get("cushion_pct")
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric(
        "Paid", _fmt(cash, "{:.2%}"),
        help="The premium you collect, as a share of the cash you post (the strike). "
             "2.9% means $2.90 per $100 locked up. Compare this across stocks at the "
             "same expiry — a thin number means the put barely pays you to wait.")
    k2.metric(
        "Paid / odds", _fmt(ipr, "{:.2f}"),
        help="Odds = the number next to this one ('Odds you own it') — that's the "
             "put's delta, used as the chance you'll be assigned. Paid / odds is "
             "how much you collect per unit of that chance. Same 3% credit is more "
             "attractive when assignment is unlikely (small delta) than when it's a "
             "coin-flip. Delta is a model guess, not a forecast.")
    k3.metric(
        "Price to breakeven",
        _fmt(cushion, "{:.1%}"),
        help=("How far today's share price is from your breakeven. For a put, "
              "breakeven is strike minus the premium you kept — this is that gap, "
              "as a percent of today's price. The dollar figure is 'Breakeven'. "
              "Bigger percent = more cushion before the put is a worse buy than buying now."
              if side == "Puts" else
              "How far today's share price is from this call's breakeven (strike plus "
              "premium), as a percent. The dollar figure is 'Breakeven'."))
    k4.metric(
        "Odds you own it", _fmt(stats.get("assignment_risk"), "{:.0%}"),
        help="The put's delta, read as a percent: roughly the chance this put finishes "
             "in the money and you have to buy the shares. That's the 'odds' in "
             "Paid / odds. A model guess from listed implied vol — not a forecast.")
    k5.metric(
        "Breakeven", _fmt(stats.get("breakeven"), "{:.2f}"),
        help="The share price where this put breaks even if you're assigned: strike "
             "minus the premium you already kept. That's your cost basis if you end "
             "up owning the stock.")
    if ann is not None:
        year_bit = f"{_fmt(ann, '{:.0%}')}/year · {_fmt(ann_vs, '{:.1f}')}× the 10Y."
    else:
        year_bit = "Too short to quote as a yearly rate."
    st.caption(
        "Paid and Paid/odds compare names at this expiry. "
        "The other three describe this put. " + year_bit
    )

    ned = None
    ned_s = _next_earnings(sel, today.isoformat())
    if ned_s:
        ned = dt.date.fromisoformat(ned_s)
    elif ov_row is not None and pd.notna(ov_row.get("days_to_earnings")):
        ned = today + dt.timedelta(days=int(ov_row["days_to_earnings"]))
    in_event_window = ned is not None and 0 <= (ned - today).days <= 5
    sq = _quote_from_side(puts, spot, target_pct, expiry, today)
    stand = f"Standing put (nearest {target_pct:.0%} of spot): {_put_one_liner(sq)}"
    if ned and dt.date.fromisoformat(expiry) >= ned and not in_event_window:
        stand += " · earnings before this expiry"
    st.caption(stand)
    if in_event_window:
        cand = [e for e in exps if dt.date.fromisoformat(e) <= ned]
        e_exp = cand[-1] if cand else exps[0]
        e_puts, _, _ = _chain_frames(sel, e_exp)
        eq = _quote_from_side(e_puts, spot, 1.00, e_exp, today)
        st.caption(f"Earnings in 0–5 days — event ATM put: {_put_one_liner(eq)} "
                   f"({dte_of[e_exp]}d, not annualized)")

    itm_flag = view["in_the_money"] if "in_the_money" in view.columns else pd.Series(pd.NA, index=view.index)

    def _is_itm(f, k: float) -> bool:
        if f is not None and not pd.isna(f):
            return bool(f)
        return k >= spot if side == "Puts" else k <= spot

    def _s(v, spec: str) -> str:
        return spec.format(float(v)) if v is not None and pd.notna(v) else "—"

    show = pd.DataFrame({
        "strike": [_s(k, "{:.1f}") for k in view["strike"]],
        "bid": [_s(v, "{:.2f}") for v in view["bid"]],
        "ask": [_s(v, "{:.2f}") for v in view["ask"]],
        "last": [_s(v, "{:.2f}") for v in view["last"]],
        "IV": [_s(v, "{:.1%}") for v in view["iv"]],
        "volume": [_s(v, "{:,.0f}") for v in view["volume"]],
        "OI": [_s(v, "{:,.0f}") for v in view["oi"]],
    })
    itm_rows = [i for i, (f, k) in enumerate(zip(itm_flag, view["strike"]))
                if _is_itm(f, float(k))]
    styled = show.style
    if itm_rows:
        styled = styled.map(lambda v: "background-color: rgba(228,87,46,0.16);",
                            subset=pd.IndexSlice[itm_rows, :])
    styled = styled.map(lambda v: "font-weight: bold;", subset=pd.IndexSlice[:, "strike"])
    st.dataframe(
        styled,
        width="stretch", height=560, hide_index=True,
        on_select="rerun", selection_mode="single-row",
        key=chain_key,
    )
    st.caption("Click a row to inspect it. Orange = already in the money. "
               "The suggested row is preselected.")
    with st.expander("Greeks"):
        g1, g2, g3, g4 = st.columns(4)
        g1.metric("Delta", _fmt(stats.get("delta"), "{:.2f}"))
        g2.metric("Gamma", _fmt(stats.get("gamma"), "{:.3f}"))
        g3.metric("Theta / day", _fmt(stats.get("theta"), "{:.3f}"))
        g4.metric("Vega", _fmt(stats.get("vega"), "{:.3f}"))
        st.caption(
            f"Strike vs spot {_fmt(stats.get('strike_vs_spot'), '{:.1%}')} · "
            f"IV {_fmt(MD._n(crow.get('iv')), '{:.1%}')}."
        )


def _fmt(v, spec: str) -> str:
    """NaN-safe number formatting — row.get(...) on a Series yields NaN, not None."""
    try:
        return spec.format(v) if v is not None and pd.notna(v) else "—"
    except (TypeError, ValueError):
        return "—"


def _gated(metrics: pd.DataFrame) -> pd.DataFrame:
    """Scored names that pass gates + coverage — the population the page ranks
    (needs a QualityScore; the zone additionally needs a residual)."""
    return metrics[
        metrics["quality_score"].notna()
        & metrics["gates_pass"].fillna(False)
        & (metrics["input_coverage"] >= 0.8)
    ]


def _highlighted(metrics: pd.DataFrame, cfg: dict, floor: float | None = None,
                 max_resid: float | None = None, min_roic: float | None = None,
                 max_ev_fcf: float | None = None) -> pd.DataFrame:
    """The highlights: gated + quality floor + residual < 0 + absolute holdability —
    ROIC ≥ 10% (5y avg, else conservative min(TTM, 5y median)) and EV/FCF low enough
    that the cash return clears max(4%, 10Y). Missing ROIC or EV/FCF fails.
    Ranked by residual ascending. The four cuts default to the scan's saved rule;
    the sidebar sliders can move them for this session (display-only — the saved
    scan, gates and metrics are unchanged)."""
    gs10 = cfg.get("gs10") or 0.0
    if max_ev_fcf is None:
        max_ev_fcf = 1.0 / max(0.04, gs10)          # 4% / 10Y cash bar, in years
    if floor is None:
        floor = float(cfg.get("quality_floor_threshold") or 60.0)
    if max_resid is None:
        max_resid = 0.0
    if min_roic is None:
        min_roic = 0.10
    g = metrics[
        metrics["quality_score"].notna()
        & metrics["gates_pass"].fillna(False)
        & (metrics["input_coverage"] >= 0.8)
    ].copy()
    if g.empty:
        return g
    roic_h = pd.Series(
        [holdability_roic(r.get("roic"), r.get("roic_ttm"), r.get("roic_median_5y"))
         for _, r in g.iterrows()],
        index=g.index,
    )
    hi = g[
        (g["quality_score"] >= floor)
        & g["residual"].notna() & (g["residual"] < max_resid)
        & roic_h.notna() & (roic_h >= min_roic)
        & g["ev_fcf"].notna() & (g["ev_fcf"] <= max_ev_fcf)
    ]
    return hi.sort_values("residual", ascending=True)


CHIP_HELP = {
    "beaten": "Price has lagged the sector, but analyst actions still look constructive.",
    "⚠ margins": "Gross margin, FCF margin, or ROIC is below its 5-year average floor.",
    "⚠ ROIC < 10%": "Holdability ROIC is under 10% — 5y average when present, else "
                    "min(TTM, 5y median). Thin absolute returns.",
    "⚠ cycle": "Energy or Materials — earnings follow the commodity cycle.",
    "⚠ 5y FCF base": "The usual 5-year FCF year was unusable (often a COVID loss). "
                     "Growth was measured from a nearby year instead.",
}


def _row_tags(row) -> list:
    """Chips for one metrics row: sentiment tag + holdability warnings."""
    tags = []
    if bool(row.get("flag_beaten_but_delivering") or False):
        tags.append(("beaten", "#4e79a7"))
    if any(pd.notna(row.get(k)) and float(row.get(k)) < t for k, t in [
        ("brake_gm", 0.97), ("brake_fcf_margin", 0.95), ("brake_roic", 0.90),
    ]):
        tags.append(("⚠ margins", "#b07d2b"))
    roic = holdability_roic(row.get("roic"), row.get("roic_ttm"), row.get("roic_median_5y"))
    if roic is not None and float(roic) < 0.10:
        tags.append(("⚠ ROIC < 10%", "#8ea0b5"))
    if str(row.get("sector") or "") in ("Energy", "Materials"):
        tags.append(("⚠ cycle", "#76b7b2"))
    if bool(row.get("fcf_cagr5_base_fallback") or False):
        tags.append(("⚠ 5y FCF base", "#b07d2b"))
    return tags


def _chips_html(tags: list) -> str:
    return "".join(
        f"<span title='{CHIP_HELP.get(t, t)}' "
        f"style='font-size:.62em;padding:1px 7px;border-radius:9px;"
        f"color:{c};border:1px solid {c};margin-left:4px;white-space:nowrap;"
        f"cursor:help'>{t}</span>"
        for t, c in tags
    )


def _ev_hist_col(hist: pd.DataFrame, cash: str) -> str:
    """Weekly EV/FCF series for charts. Owner mode uses the D&A-based multiple
    when the scan shipped it; older artifacts fall back to reported FCF."""
    if cash == "owner" and "ev_fcf_owner" in hist.columns:
        return "ev_fcf_owner"
    return "ev_fcf"
def _spark_svg(values: list, w: int = 190, h: int = 42) -> str:
    """Tiny inline SVG line of a series with the last point marked. Lower line =
    cheaper, so green when the current point sits in the cheap half of its range."""
    vs = [float(v) for v in values if v is not None and pd.notna(v)]
    if len(vs) < 3:
        return f"<div style='height:{h}px;font-size:.75em;opacity:.4'>no history</div>"
    lo, hi = min(vs), max(vs)
    rng = (hi - lo) or 1.0
    n = len(vs)
    xs = [3 + i * (w - 8) / (n - 1) for i in range(n)]
    ys = [h - 5 - (v - lo) / rng * (h - 12) for v in vs]
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    color = "#59a14f" if vs[-1] <= lo + rng / 2 else "#d9a03c"
    return (
        f"<svg width='100%' height='{h}' viewBox='0 0 {w} {h}' "
        f"preserveAspectRatio='none' style='display:block'>"
        f"<polyline points='{pts}' fill='none' stroke='{color}' stroke-width='1.7' "
        f"stroke-linejoin='round' stroke-linecap='round' vector-effect='non-scaling-stroke'/>"
        f"<circle cx='{xs[-1]:.1f}' cy='{ys[-1]:.1f}' r='2.8' fill='{color}'/>"
        f"</svg>"
    )


def _score_bar(label: str, value, color: str) -> str:
    v = 0.0 if value is None or pd.isna(value) else float(value)
    return (
        f"<div style='display:flex;align-items:center;gap:8px;margin:3px 0'>"
        f"<span style='width:70px;font-size:.72em;opacity:.7'>{label}</span>"
        f"<div style='flex:1;background:rgba(128,128,128,.18);border-radius:4px;height:8px'>"
        f"<div style='width:{max(0.0, min(100.0, v)):.0f}%;background:{color};"
        f"border-radius:4px;height:8px'></div></div>"
        f"<span style='width:26px;text-align:right;font-size:.72em;opacity:.8'>{v:.0f}</span></div>"
    )


def _name_cards(df: pd.DataFrame, hist: pd.DataFrame, ov_by_t: pd.DataFrame,
                tags: dict, n: int = 8, cash: str = "reported") -> None:
    """Graphical cards for the zone names, most negative residual first: 5y EV/FCF
    sparkline (chart only — SELF never ranks), quality bar, and the cash figures."""
    cards = []
    ev_col = _ev_hist_col(hist, cash)
    for _, r in df.head(n).iterrows():
        y: list = []
        if not hist.empty and "ticker" in hist.columns and ev_col in hist.columns:
            g = hist[(hist["ticker"] == r["ticker"]) & hist[ev_col].notna()].sort_values("week")
            y = g[ev_col].tail(260).tolist()
        med = float(pd.Series(y).median()) if y else None
        price = r.get("price")
        price_s = f"${price:,.0f}" if pd.notna(price) else "—"
        ev = r.get("ev_fcf")
        # payback-color the multiple: ≤20x pays ≥5%/yr, ≤30x ≥3.3% (inverse of the old yield bands)
        ev_col = ("#2e7d32" if pd.notna(ev) and ev <= 20
                  else "#b07d2b" if pd.notna(ev) and ev <= 30 else "#888888")
        ev_s = f"{ev:.1f}x" if pd.notna(ev) else "—"
        med_s = f" · 5y median {med:.1f}x" if med else ""
        res = r.get("residual")
        res_s = f"residual {res:.2f}" if pd.notna(res) else ""
        t = r["ticker"]
        y95 = None
        if not ov_by_t.empty and t in ov_by_t.index:
            v = ov_by_t.loc[t, "fcf_yield_strike_95"]
            y95 = f"{v:.1%}" if pd.notna(v) else None
        assign = f" · at −5%: {y95}" if y95 else ""
        sector = str(r.get("sector") or "")[:20]
        name = str(r.get("name") or "")[:22]
        cards.append(
            f"<div style='background:rgba(128,128,128,.07);border:1px solid rgba(128,128,128,.22);"
            f"border-radius:12px;padding:12px 14px'>"
            f"<div style='font-size:1.05em'><b>{t}</b> "
            f"<span style='opacity:.65;font-size:.85em'>{name}</span>{_chips_html(tags.get(t, []))}</div>"
            f"<div style='font-size:.72em;opacity:.6;margin-bottom:6px'>{sector} · {price_s}</div>"
            f"{_spark_svg(y)}"
            f"<div style='margin-top:6px'>{_score_bar('Quality', r.get('quality_score'), '#59a14f')}</div>"
            f"<div style='font-size:.75em;margin-top:6px'>"
            f"<span style='color:{ev_col};font-weight:600'>EV/FCF {ev_s}</span>"
            f"<span style='opacity:.7'>{med_s} · {res_s}{assign}</span></div>"
            f"</div>"
        )
    st.markdown(
        "<div style='display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));"
        f"gap:12px'>{''.join(cards)}</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------- the charts ---
def _hunt_map(metrics: pd.DataFrame, hi: pd.DataFrame, cfg: dict, selected: str | None,
              floor: float | None = None, max_resid: float = 0.0) -> None:
    """Quality (y) × residual (x, plotted as −residual so cheaper sits RIGHT): grey =
    scored names with a residual, orange = the highlights under the ACTIVE cuts
    (sidebar sliders, defaulting to the scan's rule). The selected ticker is starred."""
    pts = metrics[metrics["quality_score"].notna() & metrics["residual"].notna()].copy()
    pts["x"] = -pts["residual"]          # cheaper (more negative residual) -> right
    member = set(hi["ticker"]) if not hi.empty else set()
    fig = go.Figure()
    rest = pts[~pts["ticker"].isin(member)]
    zone = pts[pts["ticker"].isin(member)]
    for sub, color, name, size, op in [
        (rest, "#8ea0b5", "scored names", 8, 0.45),
        (zone, "#e4572e", "in the hunt zone", 12, 0.95),
    ]:
        if sub.empty:
            continue
        hover = sub["ticker"] if "name" not in sub.columns else sub["ticker"] + " — " + sub["name"]
        is_zone = name == "in the hunt zone"
        fig.add_trace(go.Scatter(
            x=sub["x"], y=sub["quality_score"],
            mode="markers+text" if is_zone and len(sub) <= 12 else "markers",
            text=sub["ticker"] if is_zone and len(sub) <= 12 else None,
            textposition="top center", textfont=dict(size=9),
            name=name, hovertext=hover, hoverinfo="text",
            marker=dict(size=size, color=color, opacity=op, line=dict(width=1, color="white")),
        ))
    if selected and not pts.empty and (pts["ticker"] == selected).any():
        s = pts[pts["ticker"] == selected].iloc[0]
        star_c = "#e4572e" if selected in member else "#f28e2b"   # zone orange vs any-name amber
        fig.add_trace(go.Scatter(
            x=[s["x"]], y=[s["quality_score"]],
            mode="markers+text", text=[selected], textposition="bottom center",
            textfont=dict(size=11, color=star_c),
            showlegend=False, hoverinfo="skip",
            marker=dict(size=18, color=star_c, symbol="star",
                        line=dict(width=2, color="white")),
        ))
    thr = floor if floor is not None else cfg.get("quality_floor_threshold")
    if not pts.empty:
        pad = (pts["x"].max() - pts["x"].min()) * 0.05 or 0.1
        x0, x1 = float(pts["x"].min() - pad), float(pts["x"].max() + pad)
        if thr is not None and pd.notna(thr):
            fig.add_hline(y=thr, line_dash="dot", line_color="#666",
                          annotation_text=f"quality floor — ≥{thr:.0f}")
            # the zone under the active cuts: above the floor AND residual < max_resid
            z0 = -max_resid
            fig.add_shape(type="rect", x0=z0, x1=x1, y0=thr, y1=108,
                          fillcolor="rgba(228,87,46,0.07)", line_width=0)
            fig.add_annotation(x=(z0 + x1) / 2, y=107, text="the hunt zone", showarrow=False,
                               font=dict(color="#e4572e", size=11))
        fig.update_layout(xaxis_range=[x0, x1])
    fig.update_layout(
        height=520,
        xaxis_title="cheaper than the ROIC-implied multiple → (−residual)",
        yaxis_title="higher quality ↑ (QualityScore)",
        title=f"Quality vs price-for-quality — {len(zone)} names in the highlights",
        hovermode="closest",
    )
    st.plotly_chart(fig, width="stretch")
    st.caption(
        "Grey = scored names with a residual; the shaded region is the hunt zone under the "
        "ACTIVE cuts (floor + residual + ROIC + EV/FCF — adjust them in the sidebar). "
        "Orange is the tighter highlights list: cash-cheap and viable, not just under-priced "
        "vs peers. Those names continue below, most negative residual first."
    )


def _detail_charts(h: pd.DataFrame, sel: str, cash: str = "reported") -> None:
    """The four history charts from the persisted weekly frame (no scanner changes)."""
    ev_col = _ev_hist_col(h, cash)
    g1, g2 = st.columns(2)
    with g1:
        p = h["price"].dropna()
        if not p.empty:
            hi52 = float(p.tail(52).max())
            last = float(p.iloc[-1])
            dd = last / hi52 - 1 if hi52 > 0 else float("nan")
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=h.loc[p.index, "week"], y=p, name="price",
                                     line=dict(color="#4e79a7")))
            fig.add_hline(y=hi52, line_dash="dot", line_color="#666",
                          annotation_text=f"52w high ${hi52:,.0f}")
            fig.add_trace(go.Scatter(x=[h.loc[p.index[-1], "week"]], y=[last], mode="markers",
                                     showlegend=False, marker=dict(size=10, color="#e4572e"),
                                     hovertemplate=f"<b>{sel} ${last:,.2f} ({dd:.0%} off high)</b><extra></extra>"))
            fig.update_layout(height=300, title=f"Price — {dd:.0%} off the 52-week high",
                              yaxis_title="$")
            st.plotly_chart(fig, width="stretch")
        else:
            st.caption("No price history for this name.")
    with g2:
        e = h[ev_col].dropna() if ev_col in h.columns else pd.Series(dtype=float)
        if not e.empty:
            med = float(e.median())
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=h.loc[e.index, "week"], y=e, name="EV/FCF",
                                     line=dict(color="#76b7b2")))
            fig.add_hline(y=med, line_dash="dot", annotation_text=f"5y median {med:.1f}x")
            fig.add_hline(y=0.8 * med, line_dash="dash", line_color="#2ca02c",
                          annotation_text="0.8× median")
            cur = e.iloc[-1]
            fig.add_trace(go.Scatter(x=[h.loc[e.index[-1], "week"]], y=[cur], mode="markers",
                                     showlegend=False, marker=dict(size=10, color="#e4572e", symbol="diamond"),
                                     hovertemplate=f"<b>{sel} now: {cur:.1f}x</b><extra></extra>"))
            title = "EV/FCF vs its own 5y history"
            if cash == "owner":
                title += " (owner earnings)"
            fig.update_layout(height=300, title=title, yaxis_title="×")
            st.plotly_chart(fig, width="stretch")
        else:
            st.caption("No valid EV/FCF history for this name.")
    fcf_col = "fcf_owner_ttm" if cash == "owner" and "fcf_owner_ttm" in h.columns else "fcf_adj_ttm"
    fcf_name = ("Owner FCF (CFO − D&A − SBC, TTM)" if fcf_col == "fcf_owner_ttm"
                else "FCF (adj, TTM)")
    if fcf_col in h.columns and h[fcf_col].notna().any():
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=h["week"], y=h[fcf_col] / 1e9, name=fcf_name))
        fig.add_trace(go.Scatter(x=h["week"], y=h["ni_ttm"] / 1e9, name="Net income (TTM)"))
        fig.update_layout(height=300, title="Cash vs earnings (as-known TTM)", yaxis_title="$B")
        st.plotly_chart(fig, width="stretch")
    else:
        st.caption("No cash-flow history for this name.")


def _margin_snapshot(row) -> None:
    """brake_* ratios ARE TTM ÷ 5y average — a one-row snapshot, not a time series."""
    rows = []
    for label, key, floor in [
        ("Gross margin", "brake_gm", 0.97),
        ("FCF margin", "brake_fcf_margin", 0.95),
        ("ROIC", "brake_roic", 0.90),
    ]:
        v = row.get(key)
        ok = pd.notna(v) and float(v) >= floor
        rows.append({
            "margin": label,
            "TTM vs 5y avg": _fmt(v, "{:.2f}×"),
            "floor": f"{floor:.2f}×",
            "": "✓" if ok else ("⚠ below floor" if pd.notna(v) else "— no data"),
        })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    st.caption(
        "Each ratio is this year's margin ÷ its 5-year average; below the floor is what the "
        "⚠ margins chip on the card means — look before you leap."
    )


def _company_detail(sel: str, row, hist: pd.DataFrame, overlay: pd.DataFrame,
                    ov_by_t: pd.DataFrame, tags: dict, gs10, cash: str = "reported") -> None:
    h = (hist[(hist["ticker"] == sel)].sort_values("week")
         if not hist.empty and "ticker" in hist.columns else pd.DataFrame())
    ov_row = ov_by_t.loc[sel] if (not ov_by_t.empty and sel in ov_by_t.index) else None

    fin, opt = st.tabs(["Financials", "Options"])
    with fin:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Quality (0–100)", _fmt(row.get("quality_score"), "{:.0f}"),
                  "≥ 60 passes the gate",
                  help="Is this a business you'd be willing to own? Profitability (ROIC, "
                       "FCF margin), capped growth, and low debt — then multiplied by how "
                       "smooth the path was. Not a rank versus industry peers. ≥ 60 clears "
                       "the quality floor.")
        m2.metric("Residual", _fmt(row.get("residual"), "{:.2f}"),
                  "more negative = cheaper for the quality",
                  help="Is the stock cheap for this quality? Negative means it trades "
                       "below the multiple that this ROIC and industry usually get. "
                       "More negative = more of a discount. This is what ranks the highlights.")
        m3.metric("EV/FCF", _fmt(row.get("ev_fcf"), "{:.1f}×"),
                  "years of current FCF to buy the business",
                  help="The price of the whole business (equity + debt − cash) divided by "
                       "its current free cash flow — how many years of today's FCF pay it "
                       "back. The highlights require this low enough that the cash return "
                       "beats the higher of 4% and the 10-year Treasury (≈ ≤20× today).")
        m4.metric("EV/FCF vs own history", _fmt(row.get("ev_fcf_self_pct"), "{:.0f}"),
                  "0 = cheapest ever — chart, not a score",
                  help="Where today's valuation sits in this company's own last 5 years. "
                       "0 = cheapest it has been in that window. A chart, not a rank — "
                       "it does not decide who is in the highlights.")
        if tags.get(sel):
            st.markdown("This name: " + _chips_html(tags.get(sel, [])).replace(
                "font-size:.62em", "font-size:.8em"), unsafe_allow_html=True)

        st.markdown("**Assignment test** — FCF yield if you owned the shares at −5% / −10%. "
                    "That is the equity question, not the option premium.")
        if ov_row is not None:
            p = ov_row.get("price")
            y95, y90 = ov_row.get("fcf_yield_strike_95"), ov_row.get("fcf_yield_strike_90")
            d1, d2, d3 = st.columns(3)
            d1.metric("Own it at −5%?", _fmt(y95, "{:.1%}"),
                      f"strike ≈ ${p * 0.95:,.0f}" if pd.notna(p) else None,
                      help="The cash yield you'd earn if you owned the shares 5% below "
                           "today's price. That's the assignment question: is this a "
                           "business you'd be happy to buy at that level?")
            d2.metric("Own it at −10%?", _fmt(y90, "{:.1%}"),
                      f"strike ≈ ${p * 0.90:,.0f}" if pd.notna(p) else None,
                      help="Same as −5%, but if you owned it 10% cheaper. A second "
                           "look at whether assignment is acceptable.")
            ned_s = _next_earnings(sel, dt.date.today().isoformat())
            de = ((dt.date.fromisoformat(ned_s) - dt.date.today()).days if ned_s
                  else ov_row.get("days_to_earnings"))
            d3.metric("Days to earnings", _fmt(de, "{:.0f}"),
                      "event window" if pd.notna(de) and de <= 5 else None,
                      help="Days until the next earnings date. Inside 0–5 days the "
                           "Options tab also shows an event put — that quote is a "
                           "different book and is never turned into a yearly rate.")
        else:
            st.caption("No scan overlay row for strike-FCF yields (top quality-floor names only).")

        if not h.empty:
            _detail_charts(h, sel, cash)
        else:
            st.info("No weekly history for this name in this scan.")
        st.markdown("**Margins — still intact?**")
        _margin_snapshot(row)

    with opt:
        _options_section(sel, row.get("price"), gs10, ov_row)


# --------------------------------------------------------------- ideas page ---
def ideas_page():
    scan_dir = _latest_scan()
    if scan_dir is None or not (scan_dir / "config.json").exists():
        st.error("No scans on disk yet. Open **Data manager** and run a scan.")
        return
    cfg_path = scan_dir / "config.json"
    refreshed_at = dt.datetime.fromtimestamp(cfg_path.stat().st_mtime)
    metrics, cfg, hist, overlay = load_scan(scan_dir, cfg_path.stat().st_mtime)

    st.title("Ideas")
    sub = cfg.get("subset") or {}
    if sub.get("tickers"):
        sub_txt = f"subset of {len(sub['tickers'])} tickers"
    elif sub.get("limit"):
        sub_txt = f"subset: first {sub['limit']} names"
    else:
        sub_txt = "full standard group"
    st.caption(
        f"Scan as-of **{cfg.get('asof', scan_dir.name)}** · data as known at that date · "
        f"{sub_txt} · caches and refresh live on the Data manager page"
    )

    owner_ok = SC.owner_cash_available(metrics)
    cash_labels = {
        "reported": "Reported FCF (CFO − capex − SBC)",
        "owner": "Owner earnings (CFO − D&A − SBC)",
    }
    cash = st.radio(
        "Cash used for the FCF-CAGR gate, QualityScore FCF sleeves, residual, and the yield bar",
        options=["reported", "owner"],
        format_func=lambda k: cash_labels[k],
        index=0,
        horizontal=True,
        disabled=not owner_ok,
        key="cash_definition",
        help="Default is reported FCF. Owner earnings uses D&A as a maintenance-capex "
             "proxy so growth buildouts do not zero the operating cash engine. Overlay "
             "strike yields stay on reported FCF. One series at a time — never a blend.",
    )
    if not owner_ok:
        st.caption("This scan predates owner-earnings columns — run a fresh scan to enable the setting.")
        cash = "reported"
    if cash == "owner":
        metrics = SC.apply_cash_definition(metrics, "owner")
        metrics = FL.apply_flags(metrics, cfg.get("gs10"))

    # session-only hunt-zone cuts (defaults = the scan's saved rule)
    with st.sidebar:
        with st.expander("⚙️ Hunt-zone thresholds", expanded=False):
            d_floor = float(cfg.get("quality_floor_threshold") or 60.0)
            d_mult = round(1.0 / max(0.04, cfg.get("gs10") or 0.04), 1)
            z_floor = st.slider("Quality floor (score ≥)", 30.0, 90.0, d_floor, 1.0)
            z_resid = st.slider("Max residual", -1.5, 0.5, 0.0, 0.05,
                                help="0 = must be cheaper than its quality implies. "
                                     "Raise it to admit near-fair names.")
            z_roic = st.slider("Min holdability ROIC", 0, 30, 10, 1, format="%d%%")
            z_ev = st.slider("Max EV/FCF (years to pay back)", 5.0, 40.0, d_mult, 0.5)
            st.caption(f"Scan defaults: floor ≥ {d_floor:.0f} · residual < 0 · ROIC ≥ 10% · "
                       f"EV/FCF ≤ {d_mult:.1f}× (the 4%/10Y cash bar). Session-only — the "
                       "saved scan, gates and metrics are unchanged.")

    hi = _highlighted(metrics, cfg, floor=z_floor, max_resid=z_resid,
                      min_roic=z_roic / 100.0, max_ev_fcf=z_ev)
    tags = {r["ticker"]: _row_tags(r) for _, r in metrics.iterrows()}
    ov_by_t = overlay.set_index("ticker") if not overlay.empty else pd.DataFrame()

    floor_n = int(_gated(metrics)["quality_floor_pass"].fillna(False).sum())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Data as of", cfg.get("asof", scan_dir.name))
    c2.metric("Last refresh", f"{refreshed_at:%Y-%m-%d %H:%M}")
    c3.metric("10Y Treasury — the yield bar", _fmt(cfg.get("gs10"), "{:.2%}"))
    c4.metric("Names in the highlights", len(hi), f"of {floor_n} above the quality floor")

    # 1 — the map
    pick_now = st.session_state.get("idea_pick")
    _hunt_map(metrics, hi, cfg, pick_now, floor=z_floor, max_resid=z_resid)
    if pick_now:
        pr = metrics[metrics["ticker"] == pick_now]
        if not pr.empty and (pd.isna(pr.iloc[0].get("quality_score"))
                             or pd.isna(pr.iloc[0].get("residual"))):
            reasons = pr.iloc[0].get("gate_fail_reasons")
            st.info(
                f"**{pick_now}** has no position on the map — it is unscored in this scan"
                + (f" ({reasons})" if reasons is not None and str(reasons) not in ("[]", "nan") else "")
                + ". The detail below shows whatever the scanner could still assemble."
            )

    if hi.empty:
        st.info(
            "No names clear the highlights bar in this scan — quality floor, residual < 0, "
            "ROIC ≥ 10% and EV/FCF under the max(4%, 10Y) bar. Run a fresh scan from the Data manager page."
        )
        return

    # 2 — the cards + the compact table (the browse surface)
    st.markdown(
        f"**The highlights, cheapest for their quality first** — {len(hi)} names "
        "(quality floor · residual < 0 · ROIC ≥ 10% · EV/FCF low enough to beat the "
        "higher of 4% and the 10Y). "
        "`beaten` = sector-laggard price action with analyst support · "
        "`⚠ margins` = a margin ratio slipped below its floor · "
        "`⚠ ROIC < 10%` = thin returns in absolute terms · "
        "`⚠ cycle` = Energy or Materials — earnings follow the cycle · "
        "`⚠ 5y FCF base` = the 5-year FCF year was unusable (COVID/negative); "
        "CAGR used a nearby year."
    )
    _name_cards(hi, hist, ov_by_t, tags, n=8, cash=cash)

    table = hi.reset_index(drop=True)
    show = table[["ticker", "name", "sector", "price", "quality_score",
                  "residual", "ev_fcf"]].copy()
    show["yield at −5%"] = table["ticker"].map(
        lambda t: f"{ov_by_t.loc[t, 'fcf_yield_strike_95']:.1%}"
        if (not ov_by_t.empty and t in ov_by_t.index
            and pd.notna(ov_by_t.loc[t, "fcf_yield_strike_95"])) else "—")
    show["tags"] = table["ticker"].map(lambda t: " · ".join(x for x, _ in tags.get(t, [])) or "—")
    for c, f in {"price": "{:.2f}", "quality_score": "{:.0f}", "residual": "{:.2f}",
                 "ev_fcf": "{:.1f}"}.items():
        show[c] = show[c].map(lambda v, f=f: f.format(v) if pd.notna(v) else "—")
    col_cfg = {c: st.column_config.Column(label=c, help=COLUMN_DOCS.get(c)) for c in show.columns}
    event = st.dataframe(show, column_config=col_cfg, width="stretch", height=300,
                         hide_index=True, on_select="rerun", selection_mode="single-row",
                         key="ideas_table")

    picked = None
    try:
        rows = event.selection.rows
        if rows:
            picked = table.loc[rows[0], "ticker"]
    except Exception:
        picked = None
    tickers = table["ticker"].tolist()
    # the inspector reaches beyond the highlights: every scored name, then the
    # unscored ones (marked), so names like ORCL can still be inspected
    scored_all = sorted(set(metrics.loc[metrics["quality_score"].notna(), "ticker"]) - set(tickers))
    unscored = sorted(set(metrics["ticker"]) - set(tickers) - set(scored_all))
    options = tickers + scored_all + unscored
    unscored_set = set(unscored)
    if picked in tickers:
        st.session_state["idea_pick"] = picked
    if st.session_state.get("idea_pick") not in options:
        st.session_state["idea_pick"] = tickers[0]
    sel = st.selectbox(
        "Inspect a company — highlights first, then every scored name (unscored marked)",
        options, key="idea_pick",
        format_func=lambda t: f"{t} · unscored" if t in unscored_set else t,
    )

    # the map renders BEFORE this selector, and session_state only reflects a
    # widget's new value once the widget re-instantiates — so the star lags one
    # rerun. Redraw once when the pick changed (covers selectbox AND table clicks).
    if st.session_state.get("idea_pick_drawn") != sel:
        st.session_state["idea_pick_drawn"] = sel
        st.rerun()

    # 3 — the detail, under everything, only once a name is picked
    row = metrics[metrics["ticker"] == sel].iloc[0]
    st.divider()
    st.subheader(f"{sel} — {row.get('name') or ''}")
    _company_detail(sel, metrics[metrics["ticker"] == sel].iloc[0], hist, overlay, ov_by_t,
                    tags, cfg.get("gs10"), cash=cash)


# --------------------------------------------------------- data manager page --
def manager_page():
    import data_manager
    data_manager.render()


# ------------------------------------------------------------------ routing --
pg = st.navigation([
    st.Page(ideas_page, title="Ideas", icon="💡", default=True, url_path="ideas"),
    st.Page(manager_page, title="Data manager", icon="🗂️", url_path="data"),
])
pg.run()
