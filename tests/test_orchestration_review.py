"""Orchestration review tests (docs 01 scan-artifacts, 05 §1/§4/§4b, 08 §1/§4):

- gate null semantics: nulls fail softly with an honest "n/a" reason, never silently
  pass and never masquerade as threshold misses
- coverage rule: the MVP_SCORE_INPUTS denominator is verified (renames warn loudly)
- share-class dedupe: one WHOLE row per CIK (no NaN-driven field mixing across classes)
- coverage.csv: feature failures + gate failures + coverage<80% names all land there
- presets: v0.4 brakes on Compounders on Sale; flagship residual ordering
- membership history: same-day re-fetch replaces, never duplicates
- DGS10: carry-forward, /100, and the FRED-unreachable path (scan must not die)
- analyst-action breadth: rating+target actions, 90d point-in-time window, no-signal events ignored
"""
import datetime as dt

import pandas as pd
import pytest

from scanner import config, market_data, rates_fx, scan as SCAN
from scanner import universe as UN

ASOF = dt.date(2026, 9, 11)


def _gate_frame(**over):
    base = {
        "ticker": "X", "adv_usd": 5e7, "mktcap": 5e10, "index_tenure_y": 5.0,
        "rev_cagr5": 0.05, "fcf_cagr5": 0.05,
        **{c: 0.5 for c in config.MVP_SCORE_INPUTS},
    }
    base.update(over)
    return pd.DataFrame([base])


# --- gates (doc 05 §1) ----------------------------------------------------------

def test_gates_clean_row_passes_full_coverage():
    out = SCAN._gates(_gate_frame(), ASOF)
    assert out.loc[0, "gates_pass"]
    assert out.loc[0, "gate_fail_reasons"] == []
    assert out.loc[0, "input_coverage"] == pytest.approx(1.0)


def test_gates_null_values_fail_softly_with_honest_reasons():
    df = _gate_frame(adv_usd=float("nan"), mktcap=float("nan"),
                     index_tenure_y=float("nan"), rev_cagr5=None, fcf_cagr5=None)
    out = SCAN._gates(df, ASOF)
    reasons = out.loc[0, "gate_fail_reasons"]
    assert not out.loc[0, "gates_pass"]
    for why in ("adv n/a", "mktcap n/a", "tenure n/a", "rev_cagr5 gate", "fcf_cagr5 gate"):
        assert why in reasons, reasons


def test_gates_threshold_misses_keep_their_own_labels():
    out = SCAN._gates(_gate_frame(adv_usd=1e7, mktcap=5e9, index_tenure_y=0.5), ASOF)
    reasons = out.loc[0, "gate_fail_reasons"]
    assert "adv<20M" in reasons and "mktcap<10B" in reasons and "tenure<1y" in reasons
    assert "n/a" not in "; ".join(reasons)


def test_missing_mvp_input_warns_and_shrinks_denominator(capsys):
    df = _gate_frame().drop(columns=["fcf_yield"])  # e.g. a renamed feature column
    out = SCAN._gates(df, ASOF)
    # the column vanishing inflates every stock's coverage back to 100% of a smaller
    # denominator — exactly the silent deflation the warning exists to catch
    assert out.loc[0, "input_coverage"] == pytest.approx(1.0)
    assert "fcf_yield" in capsys.readouterr().out


def test_null_mvp_input_counts_against_coverage():
    out = SCAN._gates(_gate_frame(fcf_yield=None, nd_ebitda=None), ASOF)
    assert out.loc[0, "input_coverage"] == pytest.approx(8 / 10)


# --- share-class dedupe (doc 01) ------------------------------------------------

def test_primary_share_class_one_whole_row_per_cik():
    sg = pd.DataFrame([
        {"symbol": "GOOGL", "cik": 1652044, "adv_usd": 7e9, "mom_12_1": float("nan")},
        {"symbol": "GOOG", "cik": 1652044, "adv_usd": 5e9, "mom_12_1": 0.20},
        {"symbol": "AAPL", "cik": 320193, "adv_usd": 2e10, "mom_12_1": 0.10},
        {"symbol": "NODATA", "cik": 999999, "adv_usd": float("nan"), "mom_12_1": float("nan")},
    ])
    out = SCAN._primary_share_class(sg)
    assert out["cik"].is_unique and len(out) == 3  # every CIK exactly once, incl. failed download
    primary = out[out["cik"] == 1652044].iloc[0]
    assert primary["symbol"] == "GOOGL" and primary["adv_usd"] == 7e9
    # groupby.first() would silently borrow GOOG's mom_12_1 into GOOGL's row
    assert pd.isna(primary["mom_12_1"])


# --- coverage.csv (docs 01/05 §4b) ----------------------------------------------

def test_coverage_records_include_low_coverage_and_gate_and_feature_failures():
    df = pd.DataFrame({
        "ticker": ["OK", "LOWCOV", "GATEFAIL"],
        "gate_fail_reasons": [[], ["coverage<80%"], ["mktcap<10B"]],
        "gates_pass": [True, True, False],
    })
    recs = SCAN._coverage_records(df, [("DEAD", "no companyfacts")])
    got = {r["ticker"]: r["reasons"] for r in recs}
    assert "LOWCOV" in got and got["LOWCOV"] == "coverage<80%"
    assert "GATEFAIL" in got and got["GATEFAIL"] == "mktcap<10B"
    assert "DEAD" in got and "OK" not in got


# --- presets (doc 05 §4) ---------------------------------------------------------

def _preset_row(ticker, **over):
    row = {
        "ticker": ticker, "gates_pass": True, "input_coverage": 1.0,
        "flag_derated_quality": False, "flag_beaten_but_delivering": False,
        "residual": -0.5, "cheapness_score": 50.0, "ev_fcf_self_pct": 25.0,
        "quality_floor_pass": True, "brake_gm": 1.0, "brake_fcf_margin": 1.0,
        "brake_roic": 1.0, "fcf_yield": 0.05, "residual_pct": 10.0,
        "quality_component_values": {"stability": 85.0, "profitability": 70.0,
                                     "growth": 60.0, "balance": 50.0},
    }
    row.update(over)
    return row


def test_compounders_on_sale_has_all_v04_brakes():
    rows = [
        _preset_row("GOOD"),
        _preset_row("LOWCONS", quality_component_values={"stability": 79.0}),
        _preset_row("NULLCONS", quality_component_values={"stability": None}),
        _preset_row("NANDICT", quality_component_values=float("nan")),  # never raises
        _preset_row("NOPCT", ev_fcf_self_pct=35.0),
        _preset_row("NOFLOOR", quality_floor_pass=False),
        _preset_row("BADGM", brake_gm=0.50),
        _preset_row("NANBRK", brake_roic=float("nan")),      # null brake = not intact
        _preset_row("NOARM", fcf_yield=0.03, residual_pct=90.0),  # neither yield nor residual
        _preset_row("RESIDARM", fcf_yield=0.03, residual_pct=15.0),  # residual OR arm carries it
        _preset_row("YIELDARM", fcf_yield=0.050, residual_pct=90.0),  # yield arm carries it (gs10=4.8%)
        _preset_row("GATED", gates_pass=False),
        _preset_row("LOWCOV", input_coverage=0.7),
    ]
    _, _, comp = SCAN._preset_frames(pd.DataFrame(rows), gs10=0.048)
    assert set(comp["ticker"]) == {"GOOD", "RESIDARM", "YIELDARM"}


def test_flagship_ranked_most_negative_residual_first():
    rows = [
        _preset_row("MID", flag_derated_quality=True, residual=-0.5),
        _preset_row("WORST", flag_derated_quality=True, residual=-0.9),
        _preset_row("MILD", flag_derated_quality=True, residual=-0.2),
        _preset_row("UNFLAGGED", flag_derated_quality=False, residual=-9.9),  # flag is the gate
    ]
    fl, _, _ = SCAN._preset_frames(pd.DataFrame(rows), gs10=0.04)
    assert fl["residual"].is_monotonic_increasing
    assert fl.iloc[0]["ticker"] == "WORST" and "UNFLAGGED" not in set(fl["ticker"])


def test_beaten_ranked_by_cheapness_descending():
    rows = [
        _preset_row("B1", flag_beaten_but_delivering=True, cheapness_score=40.0),
        _preset_row("B2", flag_beaten_but_delivering=True, cheapness_score=80.0),
    ]
    _, beaten, _ = SCAN._preset_frames(pd.DataFrame(rows), gs10=0.04)
    assert list(beaten["ticker"]) == ["B2", "B1"]


# --- membership history (doc 01) -------------------------------------------------

WIKI_HTML = """
<html><body><table id="constituents">
<tr><th>Symbol</th><th>Security</th><th>GICS Sector</th><th>GICS Sub-Industry</th>
<th>Date added</th><th>CIK</th></tr>
<tr><td>BRK.B</td><td>Berkshire Hathaway</td><td>Financials</td>
<td>Multi-Sector Holdings</td><td>1990-01-01</td><td>1067983</td></tr>
<tr><td>GOOGL</td><td>Alphabet Inc. Class A</td><td>Information Technology</td>
<td>Interactive Home Entertainment</td><td>2014-03-01</td><td>1652044</td></tr>
</table></body></html>
"""


def test_membership_history_same_day_refetch_replaces(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "UNIVERSE_FILE", tmp_path / "universe.csv")
    monkeypatch.setattr(config, "MEMBERSHIP_FILE", tmp_path / "membership.csv")

    class Resp:
        text = WIKI_HTML

    monkeypatch.setattr(UN.requests, "get", lambda *a, **k: Resp())
    u1 = UN.fetch_constituents(refresh=True)
    u2 = UN.fetch_constituents(refresh=True)  # same-day re-fetch must not duplicate
    h = pd.read_csv(config.MEMBERSHIP_FILE)
    assert len(u1) == len(u2) == 2
    assert not h.duplicated(subset=["fetched_at", "symbol"]).any()
    assert len(h) == 2
    assert set(h["symbol"]) == {"BRK-B", "GOOGL"}


# --- DGS10 (doc 08 §1) ------------------------------------------------------------

def test_gs10_none_when_fred_unreachable_and_no_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GS10_FILE", tmp_path / "dgs10.csv")

    def boom(*a, **k):
        raise ConnectionError("FRED down")

    monkeypatch.setattr(rates_fx.requests, "get", boom)
    assert rates_fx.gs10_asof(ASOF) is None  # never raises


def test_gs10_serves_short_cache_and_carries_forward_when_fred_down(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GS10_FILE", tmp_path / "dgs10.csv")
    pd.DataFrame({"date": ["2026-09-08", "2026-09-09"], "dgs10": [4.80, 4.83]}).to_csv(
        config.GS10_FILE, index=False
    )

    def boom(*a, **k):
        raise ConnectionError("FRED down")

    monkeypatch.setattr(rates_fx.requests, "get", boom)
    assert rates_fx.gs10_asof(dt.date(2026, 9, 12)) == pytest.approx(0.0483)  # weekend carry
    assert rates_fx.gs10_asof(dt.date(2026, 9, 1)) is None  # nothing at/before asof


def test_gs10_parses_drops_holiday_dots_and_converts(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GS10_FILE", tmp_path / "dgs10.csv")

    class Resp:
        text = "date,DGS10\n2026-09-09,4.85\n2026-09-10,.\n2026-09-11,4.90\n"

    monkeypatch.setattr(rates_fx.requests, "get", lambda *a, **k: Resp())
    assert rates_fx.gs10_asof(dt.date(2026, 9, 11)) == pytest.approx(0.0490)
    assert rates_fx.gs10_asof(dt.date(2026, 9, 10)) == pytest.approx(0.0485)  # "." carried over


# --- analyst-action breadth (doc 08 §4, v0.5.3: yfinance replaces FMP) ------------

def _actions(rows):
    """yfinance upgrades_downgrades-shaped frame: tz-aware GradeDate index."""
    idx = pd.to_datetime([r[0] for r in rows], utc=True)
    return pd.DataFrame(
        {k: [r[i] for r in rows] for i, k in enumerate(
            ["firm", "to", "from", "Action", "priceTargetAction"], start=1)},
        index=pd.DatetimeIndex(idx, name="GradeDate"),
    )


def test_breadth_counts_rating_and_target_actions():
    asof = dt.date(2026, 9, 11)
    ev = _actions([
        ("2026-08-01 10:00", "BofA", "Buy", "", "up", "Raises"),
        ("2026-07-15 10:00", "UBS", "Neutral", "Buy", "down", "Lowers"),
        ("2026-07-01 10:00", "Jefferies", "Buy", "", "main", "Raises"),   # main, but target raised
        ("2026-05-20 10:00", "Morgan", "", "", "init", None),             # init: no signal
        ("2026-04-01 10:00", "Wells", "Buy", "", "reit", "Keeps"),        # reit/keeps: no signal
    ])
    b = market_data.breadth_from_actions(ev, asof)
    assert b == {"net": 1, "ups": 2, "downs": 1}


def test_breadth_window_is_90_days_and_point_in_time():
    asof = dt.date(2026, 9, 11)
    ev = _actions([
        ("2026-09-11 09:00", "A", "Buy", "", "up", None),    # same-day asof: counts
        ("2026-06-14 09:00", "B", "Buy", "", "up", None),    # oldest in-window day: counts
        ("2026-06-13 09:00", "C", "Buy", "", "up", None),    # one day older: outside
        ("2026-09-20 09:00", "D", "Buy", "", "up", None),    # after asof: invisible
    ])
    assert market_data.breadth_from_actions(ev, asof) == {"net": 2, "ups": 2, "downs": 0}


def test_breadth_none_without_usable_events():
    asof = dt.date(2026, 9, 11)
    assert market_data.breadth_from_actions(None, asof) is None
    empty = _actions([])
    assert market_data.breadth_from_actions(empty, asof) is None
    quiet = _actions([("2026-09-01 09:00", "A", "Buy", "", "reit", "Keeps")])
    assert market_data.breadth_from_actions(quiet, asof) is None
