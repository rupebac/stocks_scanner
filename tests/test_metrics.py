"""Doc 06 formula tests: tax winsorization, CAGR base rule, fixed scale, SELF stats."""
import numpy as np
import pandas as pd
import pytest

from scanner import config
from scanner.metrics import (
    cagr, cagr5_from_series, cagr_score, clip_scale, coeff_var, conservative_level,
    cov_to_score, ev, fixed_scale_yield, fy_window_quality, invested_capital,
    nd_ebitda_score, rev_up_years, self_stats, t_eff_winsorized, winsor_pct_rank,
)


def test_teff_winsorized():
    assert t_eff_winsorized(25, 100) == 0.25
    # outside [0%, 40%] -> 3y average fallback, never a silent clip (doc 06 §6)
    assert t_eff_winsorized(60, 100) is None
    assert t_eff_winsorized(60, 100, fallback=0.2) == 0.2
    assert t_eff_winsorized(40, 100) == 0.40            # boundary is in-band
    assert t_eff_winsorized(10, -50) is None             # pretax <= 0, no fallback
    assert t_eff_winsorized(10, -50, fallback=0.2) == 0.2
    assert t_eff_winsorized(0, 100, fallback=0.25) == 0.25   # t = 0 is outside


def test_cagr_base_rule():
    assert cagr(200, 100) == 2.0 ** 0.2 - 1
    assert cagr(200, 100, cur_rev=1000) is not None
    # base < 5% of current revenue -> null (doc 06 §8)
    assert cagr(200, 30, cur_rev=1000) is None
    assert cagr(200, 49, cur_rev=1000) is None
    assert cagr(200, 60, cur_rev=1000) is not None
    assert cagr(200, -100) is None


def _fcf_years(*pairs):
    """Annual FCF series: (date, value) with the last date as current."""
    idx = pd.to_datetime([d for d, _ in pairs])
    return pd.Series([v for _, v in pairs], index=idx)


def test_cagr5_uses_closest_5y_base():
    s = _fcf_years(
        ("2020-12-31", 100),
        ("2021-12-31", 110),
        ("2025-12-31", 200),
    )
    v, fb = cagr5_from_series(s)
    assert fb is False
    assert v == pytest.approx(cagr(200, 100))


def test_cagr5_fallback_when_5y_base_negative():
    """COVID-style: the ~5y FCF year is negative so the log CAGR is undefined;
    pick the nearest valid year in 3.5–5.5y and flag the fallback."""
    s = _fcf_years(
        ("2020-12-31", -50),   # ~5.0y — unusable
        ("2021-12-31", 100),   # ~4.0y — valid
        ("2025-12-31", 200),
    )
    none, fb0 = cagr5_from_series(s, fallback=False)
    assert none is None and fb0 is False
    v, fb = cagr5_from_series(s, fallback=True)
    assert fb is True
    years = (pd.Timestamp("2025-12-31") - pd.Timestamp("2021-12-31")).days / 365.0
    assert v == pytest.approx(cagr(200, 100, years=years))


def test_cagr5_fallback_still_none_if_no_valid_base():
    s = _fcf_years(
        ("2020-12-31", -50),
        ("2021-12-31", -10),
        ("2025-12-31", 200),
    )
    v, fb = cagr5_from_series(s, fallback=True)
    assert v is None and fb is False


def test_ev_ic_identity():
    e = ev(1000, 300, 10, 5, 80, 20)
    assert e == 1000 + 300 + 10 + 5 - 80 - 20
    ic = invested_capital(300, 10, 5, 700, 80, 20)
    # IC = debt + pref + MI + equity − cash − sti; EV−mktcap and IC−equity share the stack
    assert ic == e - 1000 + 700


def test_fixed_scale_yield():
    assert fixed_scale_yield(0.0) == 0.0
    assert fixed_scale_yield(0.05) == 50.0
    assert fixed_scale_yield(0.15) == 100.0  # clipped at 10%
    assert fixed_scale_yield(None) is None
    assert fixed_scale_yield(-0.05) == 0.0   # negative yield pinned to 0


def test_winsor_pct_rank_direction_and_ties():
    s = pd.Series([1.0, 2, 3, 4, 100, 200])
    hi = winsor_pct_rank(s, higher_better=True)
    lo = winsor_pct_rank(s, higher_better=False)
    # direction: 100 = best in every scored column, either way round. The ECDF
    # percentile rank tops out at 100 exactly for higher_better and at
    # 100*(1-1/n) for lower_better (rank/n convention, doc 06 §9); what must
    # hold is that the best value maps to the column maximum in both directions.
    assert hi.iloc[5] == hi.max() == 100.0
    assert lo.iloc[0] == lo.max() > lo.iloc[5] == 0.0
    # winsorization at 1/99: the extreme is capped, order is preserved
    w = winsor_pct_rank(pd.Series([1.0, 2, 3, 4, 100, 200]), higher_better=True)
    assert list(w.argsort()) == [0, 1, 2, 3, 4, 5]
    # naturally tied values share the average rank (doc 06 §9)
    t = winsor_pct_rank(pd.Series([1.0, 1.0, 2.0, 3.0]), higher_better=True)
    assert t.iloc[0] == t.iloc[1] == 100.0 * 1.5 / 4
    # NaN never scores
    na = winsor_pct_rank(pd.Series([1.0, np.nan, 3.0]), higher_better=True)
    assert pd.isna(na.iloc[1])


def _mk_series(values):
    idx = pd.date_range("2021-01-03", periods=len(values), freq="7D")
    return pd.Series(values, index=idx)


def test_self_stats_validity():
    assert self_stats(_mk_series(np.linspace(20, 30, 100))) is None  # < 3y valid
    s = self_stats(_mk_series(np.linspace(30, 20, 200)))
    assert s is not None
    assert s["pct"] == pytest.approx(0.5)     # cheapest ever: rank-average incl. itself
    assert s["median_ratio"] < 1.0
    assert s["z"] < 0


def test_self_stats_window_validity_frac():
    # doc 06 §9: >= 60% of the window must be valid — enforced against the FULL
    # window (NaN weeks count against it), not just the valid observations.
    vals = list(np.linspace(30, 20, 200))
    too_gappy = vals[:100] + [np.nan] * 140 + vals[100:]   # 200/340 = 59% < 60%
    assert self_stats(_mk_series(too_gappy)) is None
    ok = vals[:100] + [np.nan] * 100 + vals[100:]          # 200/300 = 67% >= 60%
    s = self_stats(_mk_series(ok))
    assert s is not None and s["n_valid"] == 200 and s["n_window"] == 300


def test_self_stats_current_must_be_valid():
    vals = list(np.linspace(30, 20, 200))
    # latest week has an invalid multiple (FCF <= 0) -> there is no current value to rank
    assert self_stats(_mk_series(vals + [np.nan])) is None


def test_self_stats_ties_average_rank():
    s = self_stats(_mk_series([25.0] * 200))               # all tied
    assert s["pct"] == pytest.approx(100.0 * 100.5 / 200)  # (n+1)/2 average rank (doc 06 §9)
    assert s["z"] is None and s["median_ratio"] == 1.0


def test_self_stats_mid_range():
    vals = list(np.linspace(30, 20, 199)) + [25.0]  # last value mid-pack
    s = self_stats(_mk_series(vals))
    assert 0 < s["pct"] < 100
    assert abs(s["median_ratio"] - 1.0) < 0.05


# --- QualityScore v0.5.8 fixed bars ------------------------------------------
def test_clip_scale_caps_and_floors():
    assert clip_scale(0.20, 0.0, 0.20) == 100
    assert clip_scale(0.10, 0.0, 0.20) == 50
    assert clip_scale(0.40, 0.0, 0.20) == 100
    assert clip_scale(-0.05, 0.0, 0.20) == 0
    assert clip_scale(None, 0.0, 0.20) is None


def test_conservative_level_min_of_ttm_and_median():
    assert conservative_level(median=0.22, ttm=0.18, mean=0.30) == 0.18
    assert conservative_level(median=0.22, ttm=None, mean=0.30) == 0.22
    assert conservative_level(median=None, ttm=None, mean=0.30) == 0.30
    assert conservative_level(None, None, None) is None


def test_nd_ebitda_score_net_cash_is_full_marks():
    assert nd_ebitda_score(-0.5) == 100
    assert nd_ebitda_score(0.0) == 100
    assert nd_ebitda_score(1.5) == 50
    assert nd_ebitda_score(3.0) == 0
    assert nd_ebitda_score(4.0) == 0
    assert nd_ebitda_score(None) is None


def test_cagr_score_caps_at_12pct():
    assert cagr_score(0.12) == 100
    assert cagr_score(0.06) == 50
    assert cagr_score(0.40) == 100
    assert cagr_score(-0.02) == 0
    assert cagr_score(None) is None


def test_cov_to_score_zero_at_threshold():
    assert cov_to_score(0.0, zero_at=0.25) == 100
    assert cov_to_score(0.125, zero_at=0.25) == 50
    assert cov_to_score(0.25, zero_at=0.25) == 0
    assert cov_to_score(0.50, zero_at=0.25) == 0
    assert cov_to_score(None, zero_at=0.25) is None


def test_coeff_var_needs_three_points_and_positive_mean():
    assert coeff_var(pd.Series([1.0, 1.0])) is None
    assert coeff_var(pd.Series([-1.0, -2.0, -3.0]), require_positive_mean=True) is None
    s = pd.Series([1.2, 4.3, 1.7, 2.0, 1.8])  # CF-like FCF path
    cv = coeff_var(s, require_positive_mean=True)
    assert cv is not None and cv > 0.40


def test_rev_up_years_counts_positive_yoy_pairs():
    idx = pd.to_datetime(["2019-12-31", "2020-12-31", "2021-12-31",
                          "2022-12-31", "2023-12-31", "2024-12-31"])
    rev = pd.Series([100, 110, 90, 200, 180, 190], index=idx)
    pos, n = rev_up_years(rev)
    assert n == 5
    assert pos == 3  # +10, -20, +110, -20, +10
    flat = pd.Series([100.0] * 6, index=idx)
    pos_f, n_f = rev_up_years(flat)
    assert n_f == 5 and pos_f == 5   # flat is stable, not a haircut


def test_fy_window_quality_medians_and_path():
    idx = pd.to_datetime([f"{y}-12-31" for y in range(2016, 2026)])
    fcf = [1.0, 1.1, 1.2, 1.3, 1.2, 1.2, 4.3, 1.7, 2.0, 1.8]  # boom in 2022
    gm = [0.40] * 10
    roic = [0.10, 0.11, 0.12, 0.13, 0.14, 0.15, 0.35, 0.18, 0.17, 0.16]
    rev = [100, 110, 120, 130, 140, 150, 280, 160, 170, 180]
    F = pd.DataFrame({"fcf": fcf, "gm": gm, "roic": roic, "revenue": rev,
                      "fcf_margin": [a / b for a, b in zip(fcf, rev)]},
                     index=idx)
    out = fy_window_quality(F)
    assert out["roic_years"] == 9                       # 2016 ROIC = 10% is not > 10%
    assert out["fcf_pos_years"] == 10
    assert out["roic_median_5y"] < out["roic"]          # mean lifted by 2022
    assert out["fcf_cov"] > 0.40
    assert out["gm_stability"] == pytest.approx(0.0, abs=1e-9)
    assert out["rev_yoy_n"] == 9
    assert out["rev_pos_years"] == 8                    # only 2023 is a down year
