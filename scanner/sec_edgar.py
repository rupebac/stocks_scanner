"""SEC EDGAR XBRL: fetch/cache companyfacts and assemble durations/instants into
quarters, TTM, and fiscal-year series — the doc 08 fetch contract.

The load-bearing rules (doc 08 §2/§3):
  * tag chains resolve per period (first tag in the chain that has the period wins),
  * same period restated -> latest `filed` <= scan date wins (Q6 live rule),
  * cash-flow facts are cumulative YTD -> quarters are DIFFERENCES of YTD points,
  * visibility: a fact is usable only if `filed` <= as-of date.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from . import config

# field -> {"tags": [fallback chain]}, {"sum": [[chain], [chain]]} (components add;
# a missing component contributes 0, e.g. no ST borrowings), or {"alt": [spec, spec]}
# (ordered per-period alternatives; later specs only fill periods earlier ones lack).
# Doc 08 §2.
DURATION_FIELDS = {
    "revenue": {"tags": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",  # CRWD/APA-style filers
        "Revenues",
        "SalesRevenueNet",
    ]},
    "gross_profit": {"tags": ["GrossProfit"]},
    # GM fallback (owner-approved v0.5.2): ~half the standard group never presents a
    # gross-profit line; gm is then derived as (revenue - cost of revenue)/revenue
    "cost_revenue": {"tags": [
        "CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold", "CostOfServices",
    ]},
    "ebit": {"tags": ["OperatingIncomeLoss"]},
    "interest_expense": {"tags": ["InterestExpense", "InterestExpenseNonoperating", "InterestIncomeExpenseNet"]},
    "pretax_income": {"tags": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"]},
    "tax_provision": {"tags": ["IncomeTaxExpenseBenefit"]},
    "net_income": {"tags": ["NetIncomeLoss"]},
    "eps_diluted": {"tags": ["EarningsPerShareDiluted"]},
    "shares_diluted_was": {"tags": ["WeightedAverageNumberOfDilutedSharesOutstanding"]},
    "shares_basic_was": {"tags": ["WeightedAverageNumberOfSharesOutstandingBasic"]},
    "cfo": {"tags": ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]},
    "capex": {"tags": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"]},
    "sbc": {"tags": ["ShareBasedCompensation"]},
    "dna": {"tags": ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet"]},
}
# Doc 08 §2: Depreciation + AmortizationOfIntangibleAssets is a FALLBACK after the
# combined tag, not an addition — filers reporting the combined tag also report the
# pieces (Adobe: DDA 818M + Amort 310M for FY2025 must stay 818M). "alt" = ordered
# alternatives per period; a later alternative only fills periods the earlier ones
# never reported.
DURATION_FIELDS["dna"] = {"alt": [
    {"tags": DURATION_FIELDS["dna"]["tags"]},
    {"sum": [["Depreciation"], ["AmortizationOfIntangibleAssets"]]},
]}

INSTANT_FIELDS = {
    # GEV/XOM-style filers tag only the cash-flow-statement total (incl. restricted
    # cash) on the balance sheet — slight EV understatement beats no EV at all
    "cash": {"tags": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ]},
    "sti": {"tags": ["ShortTermInvestments", "MarketableSecuritiesCurrent"]},
    "debt_current": {"alt": [
        {"tags": ["DebtCurrent"]},
        {"sum": [["LongTermDebtCurrent", "NotesPayableCurrent"], ["ShortTermBorrowings"]]},
    ]},
    "debt_noncurrent": {"tags": ["LongTermDebtNoncurrent", "LongTermNotesPayable"]},
    "debt_total_tag": {"tags": ["LongTermDebt", "DebtLongtermAndShorttermCombinedAmount"]},
    "finance_lease": {"sum": [["FinanceLeaseLiabilityCurrent"], ["FinanceLeaseLiabilityNoncurrent"]]},
    "operating_lease": {"sum": [["OperatingLeaseLiabilityCurrent"], ["OperatingLeaseLiabilityNoncurrent"]]},
    "equity": {"tags": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]},
    "minority_interest": {"tags": ["MinorityInterest", "NoncontrollingInterest"]},
    "preferred": {"tags": ["PreferredStockValue"]},
    "goodwill": {"tags": ["Goodwill"]},
    # IndefiniteLivedIntangibleAssetsNet is never populated in cached companyfacts;
    # filers use ...ExcludingGoodwill instead (24 of 56 cached CIKs).
    "intangibles": {"sum": [
        ["FiniteLivedIntangibleAssetsNet"],
        ["IndefiniteLivedIntangibleAssetsNet", "IndefiniteLivedIntangibleAssetsExcludingGoodwill"],
    ]},
    "intangibles_alt": {"tags": ["IntangibleAssetsNetExcludingGoodwill"]},
    "assets": {"tags": ["Assets"]},
    "assets_current": {"tags": ["AssetsCurrent"]},
    "liabilities_current": {"tags": ["LiabilitiesCurrent"]},
}
DEI_SHARES = "EntityCommonStockSharesOutstanding"


def _cache_path(cik: int) -> Path:
    return config.RAW / "sec" / f"CIK{int(cik):010d}.json"


def _submissions_path(cik: int) -> Path:
    return config.RAW / "sec" / f"CIK{int(cik):010d}_submissions.json"


_SUBMISSIONS_TTL = 12 * 3600  # doc 08 §1: submissions index checked per nightly run


def _latest_filing_date(cik: int) -> dt.date | None:
    """Latest 10-K/10-Q filing date from the submissions index (cached ~12h)."""
    path = _submissions_path(cik)
    stale = not path.exists() or (time.time() - path.stat().st_mtime) > _SUBMISSIONS_TTL
    data = None
    if not stale:
        try:
            data = json.loads(path.read_text())
        except Exception:
            data = None
    if data is None:
        try:
            r = requests.get(
                f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json",
                headers=config.SEC_HEADERS, timeout=30,
            )
            time.sleep(config.SEC_RATE_SLEEP)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(r.text)
            data = json.loads(r.text)
        except Exception:
            return None
    try:
        recent = data.get("filings", {}).get("recent", {})
        dates, forms = recent.get("filingDate", []), recent.get("form", [])
        picks = [d for d, f in zip(dates, forms) if f in ("10-K", "10-Q", "10-K/A", "10-Q/A")]
        return dt.date.fromisoformat(max(picks)) if picks else None
    except Exception:
        return None


def sic_for_cik(cik: int) -> tuple[int | None, str | None]:
    """(sic, sicDescription) from the same cached submissions index; fetches it
    when absent. Best-effort: (None, None) on any failure."""
    path = _submissions_path(cik)
    data = None
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except Exception:
            data = None
    if data is None:
        try:
            r = requests.get(
                f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json",
                headers=config.SEC_HEADERS, timeout=30,
            )
            time.sleep(config.SEC_RATE_SLEEP)
            if r.status_code == 404:
                return (None, None)
            r.raise_for_status()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(r.text)
            data = json.loads(r.text)
        except Exception:
            return (None, None)
    sic = data.get("sic")
    try:
        sic = int(sic) if sic not in (None, "") else None   # SEC sends "2834", not 2834
    except (TypeError, ValueError):
        sic = None
    return sic, data.get("sicDescription")


def _cached_max_filed(facts: dict) -> dt.date | None:
    """Latest `filed` date inside a cached companyfacts payload (any taxonomy)."""
    best = None
    for taxonomy in facts.get("facts", {}).values():
        for tag in taxonomy.values():
            for rows in tag.get("units", {}).values():
                for row in rows:
                    f = row.get("filed")
                    if f and (best is None or f > best):
                        best = f
    return dt.date.fromisoformat(best) if best else None


_ua_warned = False


def fetch_companyfacts(cik: int, refresh: bool | None = None) -> dict | None:
    """Companyfacts with submissions-based cache invalidation (doc 08 §1): the raw
    JSON is cached, and refetched only when the submissions index shows a 10-K/10-Q
    filed after the cache's newest fact — nightly scans never replay stale TTM.
    refresh=True forces; refresh=None (default) = auto."""
    global _ua_warned
    if not _ua_warned and config.SEC_HEADERS["User-Agent"].endswith("example.com"):
        print("[sec] WARNING: SCANNER_UA is the placeholder default — SEC may 403; "
              "set a real contact in .env (doc 08 §1)")
        _ua_warned = True

    path = _cache_path(cik)
    if path.exists():
        try:
            cached = json.loads(path.read_text())
        except Exception:
            cached = None
        if cached is not None:
            if refresh is not True:  # cached is fine unless a new filing exists
                latest_filing = _latest_filing_date(cik)
                max_filed = _cached_max_filed(cached)
                if latest_filing is None or max_filed is None or max_filed >= latest_filing:
                    return cached
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json"
    try:
        r = requests.get(url, headers=config.SEC_HEADERS, timeout=90)
        time.sleep(config.SEC_RATE_SLEEP)
        if r.status_code == 404:
            return None
        r.raise_for_status()
    except Exception:
        # network failed mid-refresh: serve the stale cache rather than nothing
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                return None
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(r.text)
    return json.loads(r.text)


def _unit_rows(facts: dict, taxonomy: str, tag: str) -> list[dict]:
    node = facts.get("facts", {}).get(taxonomy, {}).get(tag, {})
    for unit in ("USD", "shares", "USD/shares", "pure"):
        rows = node.get("units", {}).get(unit)
        if rows:
            return rows
    return []


def _resolve(rows: pd.DataFrame) -> pd.DataFrame:
    """Chain-per-period resolution keeping REVISIONS: best (lowest) tag rank per
    (period, filed date). Live-known 'latest filed wins' is applied downstream by
    whoever needs a point value; as-known timelines need the revisions preserved."""
    if rows.empty:
        return rows
    rows = rows.sort_values(["period", "filed", "rank"])
    return rows.groupby(["period", "filed"], as_index=False).first()


def _field_rows(facts: dict, field: str, spec: dict, kind: str) -> pd.DataFrame:
    """All candidate facts for a field -> [start, end, val, filed] with chain resolved.

    * {"tags": chain} — per (period, filed), lowest tag rank wins; revisions kept.
    * {"sum": [chain, ...]} — components add per period. Point-in-time: a component
      row contributes only if its `filed` <= the merged row's `filed`. Durations add
      only same-period component rows; instants hold the component's last known value
      with end <= the row's end (step rule). A component with nothing visible
      contributes 0 (never NaN, never "skip the whole row").
    * {"alt": [spec, ...]} — ordered alternatives per period: a later alternative only
      fills periods every earlier alternative lacks (dna: combined tag beats
      Depreciation + AmortizationOfIntangibleAssets; the pieces are a subset of the
      combined line for filers that report both).
    """
    gaap = facts.get("facts", {}).get("us-gaap", {})
    if "alt" in spec:
        frames, covered = [], set()
        for sub in spec["alt"]:
            fr = _field_rows(facts, field, sub, kind)
            if fr is None or fr.empty:
                continue
            fr = fr[~fr["period"].isin(covered)]
            covered |= set(fr["period"])
            frames.append(fr)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True).sort_values(["period", "filed"])

    def component_rows(chain):
        parts = []
        for rank, tag in enumerate(chain):
            raw = _unit_rows(facts, "us-gaap", tag) if tag in gaap else []
            if not raw:
                continue
            df = pd.DataFrame(raw)
            df["filed"] = pd.to_datetime(df["filed"]).dt.date
            if kind == "duration":
                df["start"] = pd.to_datetime(df["start"]).dt.date
                df["end"] = pd.to_datetime(df["end"]).dt.date
                df["period"] = df["start"].astype(str) + "/" + df["end"].astype(str)
                cols = ["start", "end", "val", "filed", "period", "rank"]
            else:
                df["end"] = pd.to_datetime(df["end"]).dt.date
                df["period"] = df["end"].astype(str)
                cols = ["end", "val", "filed", "period", "rank"]
            df["rank"] = rank
            keep = [c for c in cols if c in df.columns] + (["accn"] if "accn" in df.columns else [])
            parts.append(df[keep])
        if not parts:
            return None
        return _resolve(pd.concat(parts, ignore_index=True))

    if "tags" in spec:
        rows = component_rows(spec["tags"])
        return rows if rows is not None else pd.DataFrame()
    comps = [c for c in (component_rows(ch) for ch in spec["sum"]) if c is not None]
    if not comps:
        return pd.DataFrame()
    if len(comps) == 1:
        return comps[0]
    # sum on the UNION of component (period, filed) keys: every component contributes
    # what was visible at that key (durations: same period only; instants: latest
    # balance with end <= row end); a component with nothing visible contributes 0.
    key_cols = ["period", "end", "filed"] + (["start"] if kind == "duration" else [])
    extra_cols = [c for c in comps[0].columns if c not in key_cols + ["val"]]
    merged = pd.concat([c[key_cols + extra_cols] for c in comps], ignore_index=True)
    merged = merged.drop_duplicates(subset=["period", "filed"], keep="first")
    total = [0.0] * len(merged)
    for comp in comps:
        crows = list(zip(comp["period"], comp["end"], comp["filed"], comp["val"].astype(float)))
        by_period = {}
        for cp, _ce, cf, cv in crows:  # (period, filed)-sorted by _resolve
            by_period.setdefault(cp, []).append((cf, cv))
        for k, (period, end, filed) in enumerate(zip(merged["period"], merged["end"], merged["filed"])):
            v = None
            if kind == "duration":
                # last row with filed <= the merged row's filed = latest visible revision
                for cf, cv in by_period.get(period, ()):
                    if cf <= filed:
                        v = cv
            else:
                # step: latest component balance (end, then filed) visible at `filed`
                best = None
                for _cp, ce, cf, cv in crows:
                    if ce <= end and cf <= filed and (best is None or (ce, cf) > (best[0], best[1])):
                        best = (ce, cf, cv)
                if best is not None:
                    v = best[2]
            if v is not None and pd.notna(v):
                total[k] += float(v)
    merged = merged.copy()
    merged["val"] = total
    return merged.sort_values(["period", "filed"]).reset_index(drop=True)


# -- public assembly API --------------------------------------------------------

def durations_all(facts: dict, field: str) -> pd.DataFrame:
    """Every resolved duration fact [start, end, val, filed] (revisions kept via filed)."""
    return _field_rows(facts, field, DURATION_FIELDS[field], "duration")


def instants_all(facts: dict, field: str) -> pd.DataFrame:
    """Every resolved instant fact [end, val, filed]."""
    return _field_rows(facts, field, INSTANT_FIELDS[field], "instant")


def cover_shares_all(facts: dict) -> pd.DataFrame:
    """Cover-page shares with REVISIONS KEPT and a fallback chain. Primary: dei
    `EntityCommonStockSharesOutstanding` — but companyfacts DROPS it for filers that
    tag it per share class (dimensioned contexts: META A/B, GOOGL A/B/C — ~24 S&P
    names), so we fall back to us-gaap `CommonStockSharesOutstanding` instants (the
    un-dimensioned consolidated total). Dual-class rule on the primary source:
    dedupe identical values (re-filed totals), then if several distinct values share
    one (end, filed) and none dominates (max/sum <= 0.8) they are share classes ->
    SUM (GOOGL A+B+C); otherwise take the max."""
    raw = _unit_rows(facts, "dei", DEI_SHARES)
    if not raw:
        ins = _field_rows(facts, "cso_fallback",
                          {"tags": ["CommonStockSharesOutstanding"]}, "instant")
        if ins.empty:
            return pd.DataFrame(columns=["end", "val", "filed"])
        df = pd.DataFrame({"end": ins["end"], "val": ins["val"].astype(float),
                           "filed": ins["filed"]})
        df = df[df["val"] > 0]  # IPO-era covers sometimes tag 0 shares (DDOG): never real
        df = df.drop_duplicates(subset=["end", "filed", "val"])
        return df.sort_values(["end", "filed"]).reset_index(drop=True)
    df = pd.DataFrame(raw)
    df["end"] = pd.to_datetime(df["end"]).dt.date
    df["filed"] = pd.to_datetime(df["filed"]).dt.date
    df["val"] = df["val"].astype(float)
    df = df[df["val"] > 0]  # zero shares on a cover page are never real
    if df.empty:
        return pd.DataFrame(columns=["end", "val", "filed"])
    rows = []
    for (end, filed), g in df.groupby(["end", "filed"]):
        vals = sorted({float(v) for v in g["val"]})
        v = sum(vals) if len(vals) > 1 and max(vals) / sum(vals) <= 0.8 else max(vals)
        rows.append({"end": end, "filed": filed, "val": v, "multi": len(vals) > 1})
    out = pd.DataFrame(rows).sort_values(["end", "filed"])
    return out.reset_index(drop=True)


def quarters_all(durs: pd.DataFrame) -> pd.DataFrame:
    """Derive single-quarter values from YTD duration facts (doc 08 §3.2).

    YTD chains share a START (the fiscal-year start) and their ENDs step by quarters:
    q_n = YTD_n − YTD_{n−1} where both start within ±14d and ends are 60–125d apart.
    The predecessor must ALREADY be visible when the fact was filed (filed <= this
    fact's filed — no leaking later comparatives into earlier rows), and among
    revisions of the same predecessor end the latest visible one wins. A fact with
    duration <= 130d and no YTD predecessor is a standalone quarter (Q1, or a filer
    presenting true 3-month columns); when a filing tags BOTH a YTD and a 3-month
    column for the same end, the YTD-differenced value wins (same basis as the other
    quarters). Revisions are KEPT (one row per end/filed); as-of filtering happens
    downstream (point-in-time rule).
    """
    if durs.empty:
        return pd.DataFrame(columns=["end", "filed", "value"])
    d = durs.sort_values(["end", "filed"])
    starts = d["start"].tolist()
    ends = d["end"].tolist()
    fileds = d["filed"].tolist()
    vals = d["val"].astype(float).tolist()
    diff_rows, standalone_rows = [], []
    seen = set()
    for i in range(len(d)):
        s, e, f, v = starts[i], ends[i], fileds[i], vals[i]
        best = None  # (end, filed, val): latest visible revision of the prior YTD end
        for j in range(len(d)):
            if j == i or fileds[j] > f:               # predecessor visible when filed
                continue
            if abs((starts[j] - s).days) <= 14:                      # same chain start
                gap = (e - ends[j]).days
                if 60 <= gap <= 125:
                    cand = (ends[j], fileds[j], vals[j])
                    if best is None or cand[:2] > best[:2]:
                        best = cand
        if best is not None:
            diff_rows.append({"end": e, "filed": f, "value": v - best[2]})
            seen.add((e, f))
        elif (e - s).days <= 130:
            standalone_rows.append({"end": e, "filed": f, "value": v})
    out = pd.DataFrame(diff_rows + [r for r in standalone_rows if (r["end"], r["filed"]) not in seen])
    if out.empty:
        return pd.DataFrame(columns=["end", "filed", "value"])
    return out.drop_duplicates(subset=["end", "filed"], keep="first").sort_values(["end", "filed"])


def _as_known(quarters: pd.DataFrame, asof: dt.date) -> pd.DataFrame:
    """Quarters visible at `asof` (filed <= asof), one value per end (latest filed).

    Ends closer than 60d collapse to ONE quarter (debris from re-tagged comparatives,
    e.g. NVIDIA 2010-07-31 alongside 2010-08-01, or Best Buy quarter ends 28d apart);
    the latest end of each cluster wins. Without this, a rolling TTM window can hold
    two rows for the same fiscal quarter and double-count it."""
    if quarters.empty:
        return quarters
    q = quarters[quarters["filed"] <= asof]
    if q.empty:
        return q
    q = q.sort_values(["end", "filed"]).groupby("end", as_index=False).last().sort_values("end")
    ends = q["end"].tolist()
    keep = []
    for k in range(1, len(ends)):
        if (ends[k] - ends[k - 1]).days < 60:
            continue                      # same fiscal quarter as ends[k-1]
        keep.append(k - 1)                # last end of the previous cluster
    keep.append(len(ends) - 1)
    return q.iloc[keep].reset_index(drop=True)


def ttm_series(quarters: pd.DataFrame, asof: dt.date) -> pd.DataFrame:
    """Rolling 4-quarter TTM at each quarter end (as-known at `asof`), with the date
    the value became visible = max filed of its 4 quarters (point-in-time timeline)."""
    q = _as_known(quarters, asof)
    if len(q) < 4:
        return pd.DataFrame(columns=["end", "known_from", "value"])
    vals, ends, known = [], [], []
    qv = q["value"].to_numpy(float)
    qe = q["end"].tolist()
    qf = q["filed"].tolist()
    for i in range(3, len(q)):
        if (qe[i] - qe[i - 3]).days <= 400:
            vals.append(float(qv[i - 3 : i + 1].sum()))
            ends.append(qe[i])
            known.append(max(qf[i - 3 : i + 1]))
    return pd.DataFrame({"end": ends, "known_from": known, "value": vals})


def ttm_value(quarters: pd.DataFrame, asof: dt.date) -> float | None:
    s = ttm_series(quarters, asof)
    if s.empty:
        return None
    last = s.iloc[-1]
    if (asof - last["end"]).days > 400:
        return None
    return float(last["value"])


def fiscal_years(durs: pd.DataFrame) -> pd.DataFrame:
    """12-month duration facts = fiscal-year values [end, value] (300–400d)."""
    if durs.empty:
        return pd.DataFrame(columns=["end", "value"])
    d = durs.copy()
    d["len"] = [(e - s).days for s, e in zip(d["start"], d["end"])]
    fy = d[(d["len"] >= 300) & (d["len"] <= 400)]
    fy = fy.sort_values(["end", "filed"]).groupby("end", as_index=False).last()
    return fy[["end", "val"]].rename(columns={"val": "value"}).sort_values("end")


def instant_asof(instants: pd.DataFrame, asof: dt.date) -> tuple[dt.date | None, float | None]:
    """Latest instant (end <= asof, filed <= asof, revisions -> latest filed)."""
    if instants.empty:
        return None, None
    ins = instants[(instants["end"] <= asof) & (instants["filed"] <= asof)]
    if ins.empty:
        return None, None
    ins = ins.sort_values(["end", "filed"]).groupby("end", as_index=False).last()
    last = ins.sort_values("end").iloc[-1]
    return last["end"], float(last["val"])
