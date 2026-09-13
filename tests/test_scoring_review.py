"""Review tests for the scoring core — doc 05 §2–§3, doc 06 §9, doc 03 caveats.
Granular unit tests for: winsorize-then-rank on the peer ladder, the ladder itself
(counted on valid ev_ebit), QualityScore renormalization (doc 08 §5 MVP sleeves),
CheapnessScore all-three-required, the fixed top-20% floor, the valuation residual
(fit universe, ROIC floor, tiny-dummy fallback, recoverability), and flag NaN paths.
"""
import json

import numpy as np
import pandas as pd
import pytest

from scanner import config, flags as FL, scoring as SC
from scanner.metrics import fixed_scale_yield, self_stats, winsor_pct_rank


# ---------------------------------------------------------------- helpers ----
def _row(t, ig="IGA", sec="SA", **kw):
    base = {
        "ticker": t, "ig_name": ig, "sector": sec,
        "ev_ebit": 14.0, "roic": 0.20, "fcf_margin": 0.20, "gm": 0.70,
        "roic_years": 9, "fcf_pos_years": 10, "fcf_ni": 1.1, "nd_ebitda": 1.0,
        "ev_fcf": 24.0, "ev_fcf_self_pct": 50.0, "ev_fcf_self_z": -0.2,
        "ev_fcf_self_ratio": 1.0, "fcf_yield": 0.04, "mom_12_1": 0.0,
        "brake_gm": 1.0, "brake_fcf_margin": 1.0, "brake_roic": 1.0,
        "rev_cagr5": 0.08, "fcf_cagr5": 0.08,
    }
    base.update(kw)
    return base


def _frame(n_iga=8, n_igb=2, **spread):
    """IGA gets n_iga names (>= LADDER_MIN_NAMES -> ig level), IGB n_igb fall-through
    names in the SAME sector SA; all remaining sectors sized to stay off the ig bar."""
    rows = []
    for i in range(n_iga):
        rows.append(_row(f"A{i:02d}"))
    for i in range(n_igb):
        rows.append(_row(f"B{i:02d}", ig="IGB", ev_ebit=13.0 + 2.0 * i))
    return pd.DataFrame(rows)


# ------------------------------------------------- (1)(2) ladder & ranking ----
def test_ladder_counts_names_not_valid_rows():
    # doc 05 §2: ">= 8 standard-group NAMES" — rung sizing counts names, so IGA with
    # 8 names (7 with valid ev_ebit) still scores at ig level (integration m9)
    rows = [_row(f"A{i:02d}") for i in range(7)] + [_row("A07", ev_ebit=np.nan)]
    rows += [_row("B0", ig="IGB"), _row("B1", ig="IGB")]
    df = SC.assign_peer_set(pd.DataFrame(rows))
    assert (df.loc[df["ig_name"] == "IGA", "peer_level"] == "ig").all()
    # IGB has 2 names -> sector SA has 9 -> sector level
    assert (df.loc[df["ig_name"] == "IGB", "peer_level"] == "sector").all()


def test_ladder_falls_to_market_when_sector_tiny():
    rows = [_row("A", ig="IGX", sec="SX"), _row("B", ig="IGY", sec="SX")]
    df = SC.assign_peer_set(pd.DataFrame(rows))
    assert (df["peer_level"] == "market").all()
    assert (df["peer_set"] == "MARKET").all()


def test_sector_percentile_uses_whole_sector_cross_section():
    # B00 falls through to the SECTOR set: its percentile must be computed against
    # all of SA (A-names included), not against the two IGB leftovers only.
    rows = [_row(f"A{i:02d}", ev_ebit=10.0 + 2.0 * i) for i in range(8)]   # 10..24
    rows += [_row("B00", ig="IGB", ev_ebit=13.0), _row("B01", ig="IGB", ev_ebit=15.0)]
    df = SC.compute_scores(pd.DataFrame(rows)).set_index("ticker")
    assert df.loc["B00", "peer_level"] == "sector"
    sector_vals = pd.Series([10.0 + 2.0 * i for i in range(8)] + [13.0, 15.0])
    expected = winsor_pct_rank(sector_vals, higher_better=False).iloc[8]
    assert df.loc["B00", "pct_ev_ebit"] == pytest.approx(expected)
    # and the ig-level names rank within their own industry group only
    ig_vals = pd.Series([10.0 + 2.0 * i for i in range(8)])
    assert df.loc["A00", "pct_ev_ebit"] == pytest.approx(
        winsor_pct_rank(ig_vals, higher_better=False).iloc[0])


def test_cheap_column_direction_100_is_best():
    df = _frame()
    df.loc[df["ticker"] == "A00", "ev_ebit"] = df["ev_ebit"].min() - 5      # cheapest
    df.loc[df["ticker"] == "A00", "roic"] = df["roic"].max() + 0.1          # most profitable
    df.loc[df["ticker"] == "A01", "nd_ebitda"] = df["nd_ebitda"].max() + 1  # most levered
    s = SC.compute_scores(df).set_index("ticker")
    assert s.loc["A00", "pct_ev_ebit"] == s["pct_ev_ebit"].max()
    assert s.loc["A00", "pct_roic"] == s["pct_roic"].max()
    assert s.loc["A01", "pct_nd_ebitda"] == s["pct_nd_ebitda"].min()        # lower = better


def test_mom_percentile_is_sector_ranked():
    df = _frame(12, n_igb=0)
    df["mom_12_1"] = np.linspace(-0.5, 0.5, len(df))
    s = SC.compute_scores(df).set_index("ticker")
    assert s.loc["A00", "pct_mom_sector"] < 10            # most beaten in its sector
    assert s.loc[s.index[-1], "pct_mom_sector"] > 90


# ------------------------------------------------- (3) QualityScore ----------
def test_quality_weights_are_core_sleeves():
    assert config.QUALITY_WEIGHTS == {
        "profitability": 0.40, "growth": 0.30, "balance": 0.30,
    }
    s = SC.compute_scores(_frame()).set_index("ticker")
    comp = s.loc["A00", "quality_component_values"]
    assert set(comp) == {"profitability", "growth", "balance", "stability"}


def test_quality_is_core_times_stability():
    """Jumpy path cannot average away: stability is a multiplier, not a 25% sleeve."""
    df = _frame(n_igb=0)
    # same core inputs; A00 smooth, A01 jumpy FCF only (GM + revenue still fine)
    for t in ("A00", "A01"):
        df.loc[df["ticker"] == t, ["roic", "fcf_margin", "nd_ebitda"]] = [0.22, 0.30, 0.5]
        df.loc[df["ticker"] == t, ["rev_cagr5", "fcf_cagr5"]] = [0.11, 0.14]
        df.loc[df["ticker"] == t, ["gm_stability", "rev_pos_years", "rev_yoy_n"]] = [0.04, 8, 9]
    df.loc[df["ticker"] == "A00", "fcf_cov"] = 0.08
    df.loc[df["ticker"] == "A01", "fcf_cov"] = 0.55  # past the 50% FCF CoV zero
    s = SC.compute_scores(df).set_index("ticker")
    c0, c1 = s.loc["A00", "quality_component_values"], s.loc["A01", "quality_component_values"]
    assert c0["profitability"] == c1["profitability"]
    assert c0["growth"] == c1["growth"]
    assert c0["balance"] == c1["balance"]
    assert c0["stability"] > c1["stability"]
    # A01's score is core × (stability/100), not a blend that still lands in the 70s
    assert s.loc["A00", "quality_score"] > s.loc["A01", "quality_score"] + 15
    assert s.loc["A00", "quality_floor_pass"]


def test_stability_weights_revenue_half_gm_fcf_quarter():
    """Path = operating-business smoothness; FCF CoV is a minority check, not an equal vote."""
    assert config.QUALITY_STABILITY_WEIGHTS == {
        "revenue": 0.50, "gm": 0.25, "fcf": 0.25,
    }


def test_stability_missing_gm_does_not_double_fcf_vote():
    """A missing GM slot must not promote FCF CoV to 50% of the path (equal-mean trap)."""
    df = _frame(n_igb=0)
    for t in ("A00", "A01"):
        df.loc[df["ticker"] == t, ["roic", "fcf_margin", "nd_ebitda"]] = [0.20, 0.25, 0.0]
        df.loc[df["ticker"] == t, ["rev_cagr5", "fcf_cagr5"]] = [0.12, 0.12]
        df.loc[df["ticker"] == t, ["rev_pos_years", "rev_yoy_n"]] = [9, 9]
        df.loc[df["ticker"] == t, "gm_stability"] = np.nan
    df.loc[df["ticker"] == "A00", "fcf_cov"] = 0.30  # lumpy cash, every revenue year up
    df.loc[df["ticker"] == "A01", "fcf_cov"] = 0.0
    s = SC.compute_scores(df).set_index("ticker")
    # rev=100; A00 FCF CoV 30% → 40; missing GM → (0.50*100 + 0.25*40) / 0.75 = 80
    # equal-mean of present would have been 70 and often dump this class under 60
    assert s.loc["A00", "quality_component_values"]["stability"] == pytest.approx(80.0, abs=0.6)
    assert s.loc["A01", "quality_component_values"]["stability"] == pytest.approx(100.0, abs=0.6)
    assert s.loc["A00", "quality_score"] >= config.QUALITY_FLOOR_MIN
    assert s.loc["A00", "quality_floor_pass"]


def test_stability_jumpy_on_all_three_slots_still_fails_floor():
    """Boom/bust on GM + FCF + revenue declines still fails. Minority FCF weight is not amnesty."""
    df = _frame(n_igb=0)
    df.loc[df["ticker"] == "A00", ["roic", "fcf_margin", "nd_ebitda"]] = [0.22, 0.30, 0.0]
    df.loc[df["ticker"] == "A00", ["rev_cagr5", "fcf_cagr5"]] = [0.11, 0.14]
    df.loc[df["ticker"] == "A00", ["gm_stability", "fcf_cov", "rev_pos_years", "rev_yoy_n"]] = [
        0.16, 0.26, 6, 9]
    s = SC.compute_scores(df).set_index("ticker")
    assert s.loc["A00", "quality_score"] < config.QUALITY_FLOOR_MIN
    assert not s.loc["A00", "quality_floor_pass"]


def test_floor_requires_conservative_roic_when_present():
    """Collapsed current ROIC cannot average past the floor via growth + cash + FCF margin.

    Same 10% holdability bar as Ideas / roic_years, applied to min(TTM, 5y median).
    """
    df = _frame(n_igb=0)
    df.loc[df["ticker"] == "A00",
           ["fcf_margin", "fcf_margin_ttm", "fcf_margin_median_5y"]] = [0.21, 0.21, 0.21]
    df.loc[df["ticker"] == "A00", ["rev_cagr5", "fcf_cagr5", "nd_ebitda"]] = [0.12, 0.12, -1.0]
    df.loc[df["ticker"] == "A00", ["gm_stability", "fcf_cov", "rev_pos_years", "rev_yoy_n"]] = [
        0.0, 0.0, 9, 9]
    df.loc[df["ticker"] == "A00", ["roic", "roic_ttm", "roic_median_5y"]] = [0.106, 0.019, 0.113]
    s = SC.compute_scores(df).set_index("ticker")
    assert s.loc["A00", "quality_score"] >= config.QUALITY_FLOOR_MIN
    assert not s.loc["A00", "quality_floor_pass"]


def test_floor_missing_roic_does_not_fail():
    """Null ROIC is not a silent fail — same honesty rule as the rest of the floor."""
    df = _frame(n_igb=0)
    df.loc[df["ticker"] == "A00", ["roic", "fcf_margin", "nd_ebitda"]] = [np.nan, 0.25, 0.0]
    df.loc[df["ticker"] == "A00",
           ["roic_ttm", "roic_median_5y", "fcf_margin_ttm", "fcf_margin_median_5y"]] = [
        np.nan, np.nan, 0.25, 0.25]
    df.loc[df["ticker"] == "A00", ["rev_cagr5", "fcf_cagr5"]] = [0.12, 0.12]
    df.loc[df["ticker"] == "A00", ["gm_stability", "fcf_cov", "rev_pos_years", "rev_yoy_n"]] = [
        0.0, 0.0, 9, 9]
    s = SC.compute_scores(df).set_index("ticker")
    assert s.loc["A00", "quality_score"] >= config.QUALITY_FLOOR_MIN
    assert s.loc["A00", "quality_floor_pass"]


def test_floor_roic_above_100pct_is_unreliable():
    """NOPAT > invested capital is not a 10% holdability pass — tiny-IC artifact.

    Missing ROIC still does not fail; a *computed* 250% does. 82–85% (buyback-
    shrunk IC that is still a real denominator) stays eligible.
    """
    df = _frame(n_igb=0)
    for t in ("A00", "A01"):
        df.loc[df["ticker"] == t,
               ["fcf_margin", "fcf_margin_ttm", "fcf_margin_median_5y"]] = [0.25, 0.25, 0.25]
        df.loc[df["ticker"] == t, ["rev_cagr5", "fcf_cagr5", "nd_ebitda"]] = [0.12, 0.12, -1.0]
        df.loc[df["ticker"] == t, ["gm_stability", "fcf_cov", "rev_pos_years", "rev_yoy_n"]] = [
            0.0, 0.0, 9, 9]
    df.loc[df["ticker"] == "A00", ["roic", "roic_ttm", "roic_median_5y"]] = [2.13, 3.55, 2.58]
    df.loc[df["ticker"] == "A01", ["roic", "roic_ttm", "roic_median_5y"]] = [0.84, 0.91, 0.82]
    s = SC.compute_scores(df).set_index("ticker")
    assert s.loc["A00", "quality_score"] >= config.QUALITY_FLOOR_MIN
    assert not s.loc["A00", "quality_floor_pass"]
    assert s.loc["A01", "quality_score"] >= config.QUALITY_FLOOR_MIN
    assert s.loc["A01", "quality_floor_pass"]
    assert config.QUALITY_FLOOR_ROIC_MAX == pytest.approx(1.0)


def test_quality_renormalizes_over_available_core_sleeves():
    df = _frame()
    df.loc[df["ticker"] == "A00", "nd_ebitda"] = np.nan
    df.loc[df["ticker"] == "A01", ["roic", "fcf_margin", "roic_ttm", "fcf_margin_ttm",
                                   "roic_median_5y", "fcf_margin_median_5y"]] = np.nan
    s = SC.compute_scores(df).set_index("ticker")
    c, w = s.loc["A00", "quality_component_values"], config.QUALITY_WEIGHTS
    assert c["balance"] is None
    assert s.loc["A00", "quality_weight_used"] == pytest.approx(1.0 - w["balance"])
    assert pd.notna(s.loc["A00", "quality_score"])
    assert s.loc["A01", "quality_weight_used"] == pytest.approx(1.0 - w["profitability"])


def test_quality_all_components_missing_is_null():
    df = _frame(n_igb=0)
    df.loc[df["ticker"] == "A00", [
        "roic", "fcf_margin", "roic_ttm", "fcf_margin_ttm",
        "roic_median_5y", "fcf_margin_median_5y",
        "rev_cagr5", "fcf_cagr5", "nd_ebitda",
    ]] = np.nan
    s = SC.compute_scores(df).set_index("ticker")
    assert pd.isna(s.loc["A00", "quality_score"])
    assert not s.loc["A00", "quality_floor_pass"]


def test_quality_cagr_cap_does_not_reward_hypergrowth():
    df = _frame(n_igb=0)
    df.loc[df["ticker"] == "A00", ["rev_cagr5", "fcf_cagr5"]] = [0.12, 0.12]
    df.loc[df["ticker"] == "A01", ["rev_cagr5", "fcf_cagr5"]] = [0.40, 0.40]
    s = SC.compute_scores(df).set_index("ticker")
    assert s.loc["A00", "quality_component_values"]["growth"] == pytest.approx(100.0)
    assert s.loc["A01", "quality_component_values"]["growth"] == pytest.approx(100.0)


def test_quality_uses_median_not_mean_when_boom_in_window():
    df = _frame(n_igb=0)
    # 5y mean looks fat (CF); median and TTM are ordinary
    df.loc[df["ticker"] == "A00", "fcf_margin"] = 0.31
    df.loc[df["ticker"] == "A00", "fcf_margin_median_5y"] = 0.18
    df.loc[df["ticker"] == "A00", "fcf_margin_ttm"] = 0.16
    df.loc[df["ticker"] == "A00", "roic"] = 0.22
    df.loc[df["ticker"] == "A00", ["roic_median_5y", "roic_ttm"]] = [0.22, 0.22]
    s = SC.compute_scores(df).set_index("ticker")
    # FCF sleeve must score 16% / 25% = 64, not 31%/25% = 100
    # profitability = mean(ROIC 22/20→100, FCF 16/25→64) = 82
    assert s.loc["A00", "quality_component_values"]["profitability"] == pytest.approx(82.0, abs=0.6)


# ------------------------------------------------- (4) CheapnessScore --------
def test_cheapness_requires_all_three_components():
    df = _frame(n_igb=0)
    df.loc[df["ticker"] == "A00", "fcf_yield"] = np.nan
    df.loc[df["ticker"] == "A01", "ev_fcf_self_pct"] = np.nan
    s = SC.compute_scores(df).set_index("ticker")
    assert pd.isna(s.loc["A00", "cheapness_score"])
    assert pd.isna(s.loc["A01", "cheapness_score"])
    assert s["cheapness_score"].notna().sum() == len(s) - 2


def test_cheapness_weights_40_30_30():
    assert config.CHEAPNESS_WEIGHTS == {
        "self_history": 0.40, "cross_section": 0.30, "absolute": 0.30,
    }
    df = _frame(n_igb=0)
    s = SC.compute_scores(df).set_index("ticker")
    row = s.loc["A00"]
    # integration M1: the SELF input is market-cross-section ranked (doc 05 §2),
    # not the raw 100 - own-history percentile
    self_ranked = winsor_pct_rank(s["ev_fcf_self_pct"], higher_better=False).loc["A00"]
    expected = (
        0.40 * self_ranked
        + 0.30 * row["pct_ev_ebit"]
        + 0.30 * fixed_scale_yield(row["fcf_yield"])
    )
    assert row["cheapness_score"] == pytest.approx(expected)
    assert row["composite"] == pytest.approx(
        0.5 * row["quality_score"] + 0.5 * row["cheapness_score"])


def test_absolute_anchor_never_ranked():
    # every stock has the SAME yield -> a sector rank would give ~50; the fixed scale
    # must map it directly (doc 06 §9: ABS yields are never cross-sectionally ranked)
    df = _frame(n_igb=0)
    df["fcf_yield"] = 0.07
    s = SC.compute_scores(df).set_index("ticker")
    assert (s["cheapness_score"].notna()).all()
    assert np.allclose(
        [fixed_scale_yield(0.07)] * len(s),
        [70.0] * len(s))


# ------------------------------------------------- (5) quality floor ---------
def test_floor_threshold_survives_compute_and_flags():
    df = _frame(12, 12)
    scored = SC.compute_scores(df)
    assert scored.attrs["quality_floor_threshold"] == pytest.approx(config.QUALITY_FLOOR_MIN)
    flagged = FL.apply_flags(scored, gs10=0.04)
    assert flagged.attrs["quality_floor_threshold"] == scored.attrs["quality_floor_threshold"]


def test_floor_is_absolute_not_top_20pct():
    """A name at 60 passes even in a tiny sample; 59 does not. No n≥10 guard."""
    df = _frame(2, 2)
    scored = SC.compute_scores(df)
    assert scored.attrs["quality_floor_threshold"] == pytest.approx(config.QUALITY_FLOOR_MIN)
    # force one name just below / just at the bar via a known core × stability=1
    df.loc[df["ticker"] == "A00", ["roic", "fcf_margin", "nd_ebitda",
                                   "rev_cagr5", "fcf_cagr5"]] = [0.20, 0.25, 0.0, 0.12, 0.12]
    df.loc[df["ticker"] == "A00", ["gm_stability", "fcf_cov", "rev_pos_years", "rev_yoy_n"]] = [
        0.0, 0.0, 9, 9]
    df.loc[df["ticker"] == "A01", ["roic", "fcf_margin", "nd_ebitda",
                                   "rev_cagr5", "fcf_cagr5"]] = [0.05, 0.04, 2.5, 0.01, 0.01]
    scored = SC.compute_scores(df).set_index("ticker")
    assert scored.loc["A00", "quality_score"] >= config.QUALITY_FLOOR_MIN
    assert scored.loc["A00", "quality_floor_pass"]
    assert scored.loc["A01", "quality_score"] < config.QUALITY_FLOOR_MIN
    assert not scored.loc["A01", "quality_floor_pass"]


# ------------------------------------------------- (6) valuation residual ----
def test_residual_fit_universe_and_roic_floor():
    df = _frame(12, 12)
    df.loc[df["ticker"] == "A00", "roic"] = -0.05      # ROIC <= 0: excluded from the fit
    df.loc[df["ticker"] == "A01", "ev_fcf"] = np.nan   # invalid multiple: excluded
    s = SC.compute_scores(df).set_index("ticker")
    assert pd.isna(s.loc["A00", "residual"]) and pd.isna(s.loc["A01", "residual"])
    assert s["residual"].notna().sum() == len(s) - 2
    # low ROIC floored at 1% still enters the fit with log(0.01)
    df2 = _frame(12, 12)
    df2.loc[df["ticker"] == "A02", "roic"] = 0.001
    s2 = SC.compute_scores(df2).set_index("ticker")
    assert pd.notna(s2.loc["A02", "residual"])


def test_residual_is_quality_adjusted_and_recoverable():
    df = _frame(12, 12)
    df["roic"] = np.linspace(0.08, 0.40, len(df))        # dispersed quality for the slope
    df["ev_fcf"] = 15.0 + 50.0 * df["roic"]              # multiple rises with quality
    # same multiple, different ROIC -> the higher-ROIC name is the cheaper-for-quality
    df.loc[df["ticker"] == "A00", ["ev_fcf", "roic"]] = [24.0, 0.45]
    df.loc[df["ticker"] == "A01", ["ev_fcf", "roic"]] = [24.0, 0.10]
    s = SC.compute_scores(df).set_index("ticker")
    assert s.loc["A00", "residual"] < s.loc["A01", "residual"]
    # fitted vs actual is recoverable from the stored pair (doc 05 §2.3 display rule)
    mid = s.loc["A05"]
    assert mid["residual_fitted"] == pytest.approx(
        np.exp(np.log(mid["ev_fcf"]) - mid["residual"]), rel=1e-6)
    # industry dummies recorded next to the residual (regime vs idiosyncratic discount)
    fit = SC.compute_scores(df).attrs["residual_fit"]
    assert fit["n"] == len(df)
    assert set(fit["groups"]) == {"ig:IGA", "ig:IGB"}
    assert fit["slope_log_roic"] > 0    # higher ROIC justifies a higher multiple


def test_residual_tiny_dummy_group_falls_down_ladder():
    # 12 IGA names in the fit, 1 IGB name + a 7-name IGC (below the bar):
    # IGC/IGB dummies would overfit -> they merge onto sector SB / market
    rows = [_row(f"A{i:02d}") for i in range(12)]
    rows += [_row("B0", ig="IGB", sec="SB")]
    rows += [_row(f"C{i}", ig="IGC", sec="SB") for i in range(7)]
    s = SC.compute_scores(pd.DataFrame(rows))
    groups = s.attrs["residual_fit"]["groups"]
    assert "ig:IGA" in groups
    assert "ig:IGB" not in groups and "ig:IGC" not in groups
    assert "sector:SB" in groups            # IGB+IGC names pooled on the sector


def test_residual_pct_among_passers_only():
    df = _frame(12, 12)
    s = SC.compute_scores(df).set_index("ticker")
    passers = s["quality_floor_pass"]
    assert s.loc[passers, "residual_pct"].notna().all()
    assert s.loc[~passers, "residual_pct"].isna().all()
    # more negative residual = cheaper = lower percentile (flag arm 3b reads <= 20)
    assert s["residual_pct"].idxmin() == s["residual"].idxmin()


# ------------------------------------------------- (7) flags -----------------
def _flag_row(**kw):
    row = {
        "quality_floor_pass": True,
        "ev_fcf_self_pct": 5.0, "ev_fcf_self_z": -2.0, "ev_fcf_self_ratio": 0.5,
        "fcf_yield": 0.06, "residual_pct": 10.0,
        "brake_gm": 1.0, "brake_fcf_margin": 1.0, "brake_roic": 1.0,
    }
    row.update(kw)
    return row


def test_flag_self_arms_are_or():
    # each SELF arm alone can carry condition 2
    for arm, kw in (
        ("self_pct", {"ev_fcf_self_z": -0.2, "ev_fcf_self_ratio": 1.1}),
        ("self_z", {"ev_fcf_self_pct": 50.0, "ev_fcf_self_ratio": 1.1}),
        ("median_ratio", {"ev_fcf_self_pct": 50.0, "ev_fcf_self_z": -0.2}),
    ):
        fired, ev = FL.derated_quality(_flag_row(**kw), gs10=0.03)
        assert fired, arm
        assert ev["depressed_arms"][arm] and sum(ev["depressed_arms"].values()) == 1


def test_flag_yield_arm_vs_gs10():
    base = _flag_row(ev_fcf_self_pct=50.0, ev_fcf_self_z=-0.2, ev_fcf_self_ratio=1.1,
                     residual_pct=90.0)
    _, ev = FL.derated_quality(dict(base, fcf_yield=0.045), gs10=0.048)
    assert not ev["yield_arm"]                    # 4.5% < max(4%, 4.8%)
    _, ev = FL.derated_quality(dict(base, fcf_yield=0.049), gs10=0.048)
    assert ev["yield_arm"]
    _, ev = FL.derated_quality(dict(base, fcf_yield=0.041), gs10=None)
    assert ev["yield_arm"] and ev["yield_threshold"] == 0.04   # GS10 unavailable -> 4%
    _, ev = FL.derated_quality(dict(base, fcf_yield=0.039), gs10=0.02)
    assert not ev["yield_arm"]                    # hard 4% floor even when GS10 is lower


def test_flag_residual_arm_threshold():
    base = _flag_row(ev_fcf_self_pct=50.0, ev_fcf_self_z=-0.2, ev_fcf_self_ratio=1.1,
                     fcf_yield=0.01)
    _, ev = FL.derated_quality(dict(base, residual_pct=20.0), gs10=0.03)
    assert ev["residual_arm"]                     # <= 20th pct among passers
    _, ev = FL.derated_quality(dict(base, residual_pct=21.0), gs10=0.03)
    assert not ev["residual_arm"]
    _, ev = FL.derated_quality(dict(base, residual_pct=np.nan), gs10=0.03)
    assert not ev["residual_arm"]                 # NaN residual never fires the arm


def test_flag_brakes_are_hard_and():
    ok = _flag_row()
    fired, _ = FL.derated_quality(ok, gs10=0.03)
    assert fired
    for brake, val in (("brake_gm", 0.96), ("brake_fcf_margin", 0.94), ("brake_roic", 0.89)):
        fired, ev = FL.derated_quality(_flag_row(**{brake: val}), gs10=0.03)
        assert not fired and not ev["brakes"][brake.replace("brake_", "")], brake
    # boundaries pass (0.97 / 0.95 / 0.90 are >=, doc 05 §3)
    fired, ev = FL.derated_quality(_flag_row(brake_gm=0.97, brake_fcf_margin=0.95,
                                             brake_roic=0.90), gs10=0.03)
    assert fired and all(ev["brakes"].values())
    # missing/NaN brakes must fail a hard AND (doc 06 §10: never a neutral fill)
    fired, ev = FL.derated_quality(_flag_row(brake_gm=np.nan), gs10=0.03)
    assert not fired and not ev["brakes"]["gm"]
    fired, _ = FL.derated_quality({"quality_floor_pass": True, "fcf_yield": 0.06,
                                   "ev_fcf_self_pct": 5.0}, gs10=0.03)
    assert not fired


def test_flag_floor_nan_is_not_a_pass():
    for bad in (np.nan, None):
        fired, ev = FL.derated_quality(_flag_row(quality_floor_pass=bad), gs10=0.03)
        assert not fired and not ev["floor"]
    fired, _ = FL.derated_quality(_flag_row(quality_floor_pass=False), gs10=0.03)
    assert not fired


def test_flag_evidence_json_completeness():
    fired, ev = FL.derated_quality(_flag_row(), gs10=0.041)
    dumped = json.loads(json.dumps(ev))
    for key in ("floor", "depressed_arms", "yield_arm", "residual_arm", "brakes",
                "self_pct", "self_z", "median_ratio", "fcf_yield", "yield_threshold",
                "gs10", "residual_pct", "brake_values"):
        assert key in dumped, key
    assert dumped["gs10"] == 0.041 and dumped["yield_threshold"] == 0.041
    assert all(k in dumped["depressed_arms"] for k in ("self_pct", "self_z", "median_ratio"))
    assert all(k in dumped["brake_values"] for k in ("gm", "fcf_margin", "roic"))


def test_flag_no_self_stats_cannot_fire_on_yield_alone():
    # depressed condition 2 requires at least one SELF arm; yield alone is not enough
    row = _flag_row(ev_fcf_self_pct=None, ev_fcf_self_z=None, ev_fcf_self_ratio=None)
    fired, ev = FL.derated_quality(row, gs10=0.03)
    assert not fired and ev["yield_arm"] and not any(ev["depressed_arms"].values())


# ------------------------------------------------- (8) SELF stats ------------
def test_self_stats_pct_semantics():
    # SELF <= 20th pct == current cheaper than ~80% of its own window
    vals = list(np.linspace(30, 24, 200)) + [20.0]   # current is the cheapest
    s = self_stats(pd.Series(vals))
    assert s["pct"] == pytest.approx(100.0 / 201)
    vals = list(np.linspace(20, 26, 200)) + [30.0]   # current is the most expensive
    s = self_stats(pd.Series(vals))
    assert s["pct"] == pytest.approx(100.0)
