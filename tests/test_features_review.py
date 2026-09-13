"""Unit tests for scanner/features.py (review fixes).

Pins (docs 02 §validity, 03 §brakes, 06 §3/§5/§8/§9, 08 §3):
  * _snap_quarter nearest-quarter-end bucketing (Apr 1 / late-Jan FY ends),
  * the as-known TTM timeline: prior-year comparative re-filings must NOT delay
    TTM visibility; genuine restatements re-base at their filing date (06 §3);
    a missing quarter -> NaN, never a sum over non-adjacent quarters (08 §3.3);
    filings after the as-of date are invisible (06 §3),
  * per-FY IC anchored at the filing that reported the FY-end balance sheet,
    not the last stack step before FY end + 120d (fast filers' Q1 10-Q),
  * build_features end-to-end: SELF history columns, 10y/5y = last 10/5 fiscal
    years, NaN paths for filers missing sbc / gross_profit (KMI-style),
    SELF null when the valid fraction of the window is < 60%.
Everything synthetic — no network.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from scanner import config
from scanner.features import (
    _ic_at, _snap_quarter, _stack_steps, _ttm_timeline, build_features,
)
from scanner.sec_edgar import instants_all

D = dt.date
TAGS = {  # canonical primary tags (doc 08 §2) — hardcoded, decoupled from spec dicts
    "revenue": "RevenueFromContractWithCustomerExcludingAssessedTax",
    "gross_profit": "GrossProfit",
    "ebit": "OperatingIncomeLoss",
    "cfo": "NetCashProvidedByUsedInOperatingActivities",
    "capex": "PaymentsToAcquirePropertyPlantAndEquipment",
    "sbc": "ShareBasedCompensation",
    "dna": "DepreciationDepletionAndAmortization",
    "net_income": "NetIncomeLoss",
    "tax_provision": "IncomeTaxExpenseBenefit",
    "pretax_income": ("IncomeLossFromContinuingOperationsBeforeIncomeTaxes"
                      "ExtraordinaryItemsNoncontrollingInterest"),
}
ITAGS = {
    "cash": "CashAndCashEquivalentsAtCarryingValue",
    "sti": "ShortTermInvestments",
    "debt_current": "LongTermDebtCurrent",
    "debt_noncurrent": "LongTermDebtNoncurrent",
    "equity": "StockholdersEquity",
}
DEI_SHARES = "EntityCommonStockSharesOutstanding"


def facts_with(durations: dict[str, list[tuple]], instants: dict[str, list[tuple]] | None = None,
               covers: list[tuple] | None = None) -> dict:
    """durations: field -> [(start, end, val, filed)]; instants: field -> [(end, val, filed)];
    covers: [(end, val, filed)] for dei cover shares."""
    instants = instants or {}
    usgaap = {}
    for f, rows in durations.items():
        usgaap[TAGS[f]] = {"label": f, "units": {"USD": [
            {"start": s.isoformat(), "end": e.isoformat(), "val": v, "accn": "a",
             "fy": 2024, "fp": "Q1", "form": "10-Q", "filed": fl.isoformat()}
            for s, e, v, fl in rows]}}
    for f, rows in instants.items():
        usgaap[ITAGS[f]] = {"label": f, "units": {"USD": [
            {"end": e.isoformat(), "val": v, "accn": "a",
             "fy": 2024, "fp": "Q1", "form": "10-Q", "filed": fl.isoformat()}
            for e, v, fl in rows]}}
    dei = {}
    if covers is not None:
        dei[DEI_SHARES] = {"label": "s", "units": {"shares": [
            {"end": e.isoformat(), "val": v, "accn": "a",
             "fy": 2024, "fp": "Q1", "form": "10-Q", "filed": fl.isoformat()}
            for e, v, fl in covers]}}
    return {"cik": 1, "entityName": "TEST CO", "facts": {"us-gaap": usgaap, "dei": dei}}


# ------------------------------------------------------------ snap quarters --
def test_snap_quarter_boundary_spillover():
    # a fiscal quarter ending just after a calendar boundary covers the PRIOR quarter
    assert _snap_quarter(D(2025, 4, 1)) == D(2025, 3, 31)
    assert _snap_quarter(D(2025, 4, 27)) == D(2025, 3, 31)   # NVDA Q1 (FY ends late Jan)
    assert _snap_quarter(D(2025, 1, 31)) == D(2024, 12, 31)  # CRM-style FY end
    assert _snap_quarter(D(2025, 7, 10)) == D(2025, 6, 30)
    # second half of a calendar quarter stays in its own bucket
    assert _snap_quarter(D(2025, 5, 20)) == D(2025, 6, 30)
    assert _snap_quarter(D(2025, 12, 31)) == D(2025, 12, 31)
    assert _snap_quarter(D(2025, 3, 31)) == D(2025, 3, 31)


def test_snap_quarter_no_collision_for_adjacent_fiscal_quarters():
    # 4-4-5-style drift: adjacent quarter ends (85-98d apart) must never collapse
    # onto the same snapped bucket (that would silently drop a quarter)
    ends = [D(2024, 3, 30), D(2024, 7, 3), D(2024, 10, 2), D(2025, 1, 4),
            D(2025, 4, 3), D(2025, 7, 3)]
    snapped = [_snap_quarter(e) for e in ends]
    assert len(set(snapped)) == len(snapped)
    for a, b in zip(snapped, snapped[1:]):
        assert 85 <= (b - a).days <= 97


# ----------------------------------------------------------- TTM timeline ----
def qtimeline_rows(extra=None):
    """5 standalone quarters Q1-Q4 2024 + Q1 2025 for cfo (values 10..14)."""
    rows = [
        (D(2024, 1, 1), D(2024, 3, 31), 10, D(2024, 4, 25)),
        (D(2024, 4, 1), D(2024, 6, 30), 11, D(2024, 7, 25)),
        (D(2024, 7, 1), D(2024, 9, 30), 12, D(2024, 10, 25)),
        (D(2024, 10, 1), D(2024, 12, 31), 13, D(2025, 2, 15)),
        (D(2025, 1, 1), D(2025, 3, 31), 14, D(2025, 4, 25)),
    ]
    return rows + (extra or [])


def test_ttm_comparative_refiling_does_not_delay_visibility():
    # Q1-2024 re-filed a year later as a prior-year comparative (same value, new filed)
    comp = [(D(2024, 1, 1), D(2024, 3, 31), 10, D(2025, 4, 27))]
    tl = _ttm_timeline(facts_with({"cfo": qtimeline_rows(comp)}), D(2025, 12, 31))
    s = tl[tl["cfo"].notna()]
    # the FY-2024 TTM (10+11+12+13 = 46) is visible at the 10-K filing, not at the comparative
    q4 = s[s["effective"] <= D(2025, 3, 1)].iloc[-1]
    assert q4["effective"] == D(2025, 2, 15)
    assert q4["cfo"] == 46.0
    # the comparative re-filing (2025-04-27) changes nothing vs the Q1-25 filing on
    # 2025-04-25 that rolled the window to Q2'24..Q1'25 = 50 — no delay, no re-base
    rolled = s[s["effective"] == D(2025, 4, 25)].iloc[-1]["cfo"]
    at_comp = s[s["effective"] == D(2025, 4, 27)].iloc[-1]["cfo"]
    assert rolled == 50.0 and at_comp == 50.0


def test_ttm_restatement_rebases_at_filing_date():
    # Q4 restated 13 -> 20 on 2025-06-01: before that date the rolled TTM (Q2'24..Q1'25
    # = 11+12+13+14 = 50) is what scans see; after it, the same window re-bases to 57
    rest = [(D(2024, 10, 1), D(2024, 12, 31), 20, D(2025, 6, 1))]
    tl = _ttm_timeline(facts_with({"cfo": qtimeline_rows(rest)}), D(2025, 12, 31))
    s = tl[tl["cfo"].notna()]
    assert s[s["effective"] <= D(2025, 5, 31)].iloc[-1]["cfo"] == 50.0
    assert s[s["effective"] >= D(2025, 6, 1)].iloc[0]["cfo"] == 57.0


def test_ttm_missing_quarter_is_nan_not_wrong_sum():
    # Q3-2024 never filed: the 4 newest quarters are NOT adjacent -> NaN (doc 08 §3.3)
    rows = [
        (D(2024, 1, 1), D(2024, 3, 31), 10, D(2024, 4, 25)),
        (D(2024, 4, 1), D(2024, 6, 30), 11, D(2024, 7, 25)),
        (D(2024, 10, 1), D(2024, 12, 31), 13, D(2025, 2, 15)),
        (D(2025, 1, 1), D(2025, 3, 31), 14, D(2025, 4, 25)),
    ]
    tl = _ttm_timeline(facts_with({"cfo": rows}), D(2025, 12, 31))
    assert "cfo" not in tl.columns or tl["cfo"].isna().all()  # gap -> NaN, never 10+11+13+14
    # filling the gap later restores a true 4-quarter sum
    rows2 = rows[:2] + [(D(2024, 7, 1), D(2024, 9, 30), 12, D(2025, 5, 1))] + rows[2:]
    tl2 = _ttm_timeline(facts_with({"cfo": rows2}), D(2025, 12, 31))
    assert tl2[tl2["cfo"].notna()].iloc[-1]["cfo"] == 50.0


def test_ttm_timeline_respects_asof_visibility():
    tl = _ttm_timeline(facts_with({"cfo": qtimeline_rows()}), D(2025, 3, 1))
    assert tl["effective"].max() <= D(2025, 3, 1)  # Q1-2025 (filed 2025-04-25) invisible
    s = tl[tl["cfo"].notna()]
    assert s.iloc[-1]["cfo"] == 46.0  # FY2024 TTM only


# ------------------------------------------------------------- stack / IC ----
def test_stack_steps_first_report_mid_history_and_net_cash():
    facts = facts_with({}, {
        "cash": [(D(2020, 12, 31), 50, D(2021, 2, 15)), (D(2021, 12, 31), 80, D(2022, 2, 15))],
        # debt appears for the first time in the 2021 10-K: 0 before, not NaN
        "debt_noncurrent": [(D(2021, 12, 31), 30, D(2022, 2, 15))],
    })
    st = _stack_steps({f: instants_all(facts, f) for f in ("cash", "debt_noncurrent")})
    early = st[st["effective"] <= pd.Timestamp(D(2021, 6, 1))].iloc[-1]
    assert early["stack"] == -50.0        # net cash flows through as a negative stack
    assert early["debt_noncurrent"] == 0.0
    late = st.iloc[-1]
    assert late["stack"] == 30.0 - 80.0   # debt - cash, still net cash


def test_ic_at_anchors_at_fy_end_balance_sheet_filing():
    eq = pd.DataFrame([
        {"end": D(2024, 12, 31), "val": 1000.0, "filed": D(2025, 2, 15)},   # the 10-K
        {"end": D(2024, 12, 31), "val": 1000.0, "filed": D(2025, 4, 25)},   # comparative
    ])
    stack = pd.DataFrame([
        {"effective": pd.Timestamp(D(2025, 2, 15)), "stack": 500.0},        # 10-K BS
        {"effective": pd.Timestamp(D(2025, 4, 25)), "stack": 900.0},        # Q1 BS (~116d after FY end)
    ])
    # the +120d cutoff must not pick the Q1 balance sheet
    assert _ic_at(eq, stack, D(2024, 12, 31)) == 1500.0
    # equity first filed too late -> null (doc 06 §10)
    late_eq = pd.DataFrame([{"end": D(2024, 12, 31), "val": 1.0, "filed": D(2025, 6, 30)}])
    assert _ic_at(late_eq, stack, D(2024, 12, 31)) is None


# ------------------------------------------------------- build_features ------
QVALS = {"revenue": 100.0, "gross_profit": 60.0, "ebit": 20.0, "dna": 2.0,
         "cfo": 25.0, "capex": 5.0, "sbc": 3.0, "net_income": 15.0,
         "tax_provision": 4.0, "pretax_income": 16.0}


def synthetic_company(start_year: int = 2013, end_year: int = 2025,
                      sbc_override_from: dt.date | None = None, sbc_override: float = 26.0,
                      with_gross_profit: bool = True, with_sbc: bool = True):
    """Calendar-DEC filer. Standalone 3-month durations filed +25d after quarter
    end, plus 12-month annual durations for the fiscal-year frame."""
    qdurs: dict[str, list[tuple]] = {
        "revenue": [], "ebit": [], "cfo": [], "capex": [], "net_income": [],
        "dna": [], "tax_provision": [], "pretax_income": [],
    }
    if with_gross_profit:
        qdurs["gross_profit"] = []
    if with_sbc:
        qdurs["sbc"] = []
    inst: dict[str, list[tuple]] = {"cash": [], "sti": [], "debt_noncurrent": [], "equity": []}
    covers: list[tuple] = []
    for y in range(start_year, end_year + 1):
        quarter_ends = [D(y, 3, 31), D(y, 6, 30), D(y, 9, 30), D(y, 12, 31)]
        vals = dict(QVALS)
        if sbc_override_from is not None and D(y, 1, 1) >= sbc_override_from and with_sbc:
            vals["sbc"] = sbc_override
        for end in quarter_ends:
            filed = end + dt.timedelta(days=25)
            for f, v in vals.items():
                if f in qdurs:
                    qdurs[f].append((end - dt.timedelta(days=89), end, v, filed))
            for f, v in (("cash", 50.0), ("sti", 10.0), ("debt_noncurrent", 100.0), ("equity", 200.0)):
                inst[f].append((end, v, filed))
            covers.append((end, 10.0, filed))
        # annual (12-month) durations feed the fiscal-year frame (300-400d rule)
        fy_filed = D(y + 1, 2, 15)
        for f, v in vals.items():
            if f in qdurs:
                qdurs[f].append((D(y, 1, 1), D(y, 12, 31), v * 4, fy_filed))
    return facts_with(qdurs, inst, covers)


def weekly_frame(asof: dt.date, weeks: int = 320, p0: float = 8.0, p1: float = 16.0):
    idx = pd.date_range(end=pd.Timestamp(asof), periods=weeks, freq="W-FRI")
    t = np.linspace(0, 1, weeks)
    price = p0 + (p1 - p0) * (0.5 - 0.5 * np.cos(2 * np.pi * t))  # smooth round trip
    return pd.DataFrame({"ticker": "TST", "week": idx, "close": price, "volume": 1e6})


ASOF = D(2026, 9, 11)


def test_build_features_end_to_end_columns_and_values():
    feat = build_features(synthetic_company(), weekly_frame(ASOF), ASOF)
    assert feat is not None
    hist = feat["history"]
    for c in ("week", "price", "mktcap", "ev", "fcf_adj_ttm", "ni_ttm", "ev_fcf"):
        assert c in hist.columns
    assert list(hist.columns)[:1] == ["asof"]
    # stack = debt 100 - cash 50 - sti 10 = 40; shares 10 -> ev = 10*price + 40
    last = hist.dropna().iloc[-1]
    assert last["ev"] == pytest.approx(10 * last["price"] + 40, rel=1e-9)
    assert last["fcf_adj_ttm"] == pytest.approx((25 - 5 - 3) * 4)   # 68
    assert last["ni_ttm"] == pytest.approx(60)
    assert last["ev_fcf"] == pytest.approx(last["ev"] / 68)
    # SELF stats on a fully valid window
    assert feat["ev_fcf_self_n"] == int(config.SELF_WINDOW_YEARS * 52)
    # windows = last 10 / last 5 fiscal years (2013..2025 = 13 FYs available)
    assert feat["gm"] == pytest.approx(0.6)
    assert feat["roic_years"] == 10        # capped at 10, not 11
    assert feat["fcf_pos_years"] == 10     # capped at 10, not 11
    assert feat["fcf_margin"] == pytest.approx((25 - 5 - 3) * 4 / 400)
    assert feat["brake_fcf_margin"] == pytest.approx(1.0)   # flat fundamentals
    assert feat["roic"] == pytest.approx(20 * 4 * 0.75 / (40 + 200))
    assert feat["roic_median_5y"] == pytest.approx(feat["roic"])
    assert feat["gm_stability"] == pytest.approx(0.0, abs=1e-9)
    assert feat.get("fcf_cov") is not None
    assert feat["rev_yoy_n"] >= 9
    assert feat["rev_pos_years"] == feat["rev_yoy_n"]   # flat synthetic is always up or flat?
    # owner earnings = CFO − D&A − SBC (growth capex does not zero the cash engine)
    assert feat["fcf_owner_ttm"] == pytest.approx((25 - 2 - 3) * 4)   # 80
    assert feat["fcf_owner_margin"] == pytest.approx(80 / 400)
    assert feat["fcf_owner_margin_ttm"] == pytest.approx(80 / 400)
    assert feat["fcf_owner_yield"] == pytest.approx(feat["fcf_owner_ttm"] / feat["ev"])
    assert feat["ev_fcf_owner"] == pytest.approx(feat["ev"] / 80)
    assert feat["ev_fcf_owner_self_n"] == feat["ev_fcf_self_n"]
    assert "fcf_owner_ttm" in hist.columns
    assert last["fcf_owner_ttm"] == pytest.approx(80)


def test_build_features_kmi_style_missing_sbc_and_gross_profit():
    feat = build_features(synthetic_company(with_gross_profit=False, with_sbc=False),
                          weekly_frame(ASOF), ASOF)
    assert feat is not None
    # v0.5.2 (owner-approved): SBC never tagged -> fcf_adj = plain fcf, dq-flagged
    assert feat["fcf_adj_ttm"] == pytest.approx(80)
    assert "sbc_unreported" in (feat.get("dq_flags") or "")
    assert feat["fcf_ttm"] == pytest.approx(80)   # plain fcf still alive
    # untagged SBC counts as zero for owner earnings too (same honesty as fcf_adj)
    assert feat["fcf_owner_ttm"] == pytest.approx((25 - 2) * 4)   # 92
    assert feat.get("gm") is None and feat.get("gm_ttm") is None   # no gross profit AND no cost tags
    assert feat["brake_gm"] is None


def test_build_features_self_null_when_window_mostly_invalid():
    # sbc jumps to 26 from 2022 -> fcf_adj TTM < 0 from late 2022 on: the 5y window
    # holds < 3y / < 60% valid observations -> SELF null (docs 02 / 06 §9)
    feat = build_features(synthetic_company(sbc_override_from=D(2022, 1, 1)),
                          weekly_frame(ASOF), ASOF)
    assert feat is not None
    assert feat["fcf_adj_ttm"] < 0
    assert all(k not in feat for k in
               ("ev_fcf_self_pct", "ev_fcf_self_z", "ev_fcf_self_ratio", "ev_fcf_self_n"))


def test_build_features_self_history_is_point_in_time():
    # fundamentals steps carry the as-known TTM: a flat company shows 68 in EVERY
    # valid week, whatever the filing/comparative alignment (no stale-year TTMs)
    feat = build_features(synthetic_company(), weekly_frame(ASOF), ASOF)
    hist = feat["history"]
    assert hist["fcf_adj_ttm"].dropna().iloc[-1] == pytest.approx(68)
    assert hist.loc[hist["ev_fcf"].notna(), "fcf_adj_ttm"].dropna().unique().tolist() == [68.0]
