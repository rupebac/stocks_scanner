"""Canonical formula layer — doc 06 (single source of truth, implemented).

Pure functions only; no fetching, no I/O. Direction conventions:
higher_better scores map to 0–100 with 100 = best.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config


def safe_div(a, b) -> float | None:
    if a is None or b is None or b == 0 or not np.isfinite(a) or not np.isfinite(b):
        return None
    return float(a) / float(b)


def ev(mktcap, debt, pref, mi, cash, sti) -> float | None:
    parts = [mktcap, debt, pref, mi, cash, sti]
    if any(p is None for p in parts):
        return None
    return float(mktcap + debt + pref + mi - cash - sti)  # doc 06 §4


def invested_capital(debt, pref, mi, equity, cash, sti) -> float | None:
    parts = [debt, pref, mi, equity, cash, sti]
    if any(p is None for p in parts):
        return None
    return float(debt + pref + mi + equity - cash - sti)  # doc 06 §5


def t_eff_winsorized(tax, pretax, fallback: float | None = None) -> float | None:
    """t_eff = tax/pretax, band [0%, 40%] (doc 06 §6). A value outside the band is an
    artifact (one-off charges, zero provision) -> 3y average fallback; still
    undefined -> None. Never silently clipped: we adjust explicitly or not at all."""
    if tax is None or pretax is None or pretax <= 0:
        return fallback
    t = tax / pretax
    if not np.isfinite(t) or t <= config.TEFF_MIN or t > config.TEFF_MAX:
        return fallback
    return float(t)


def nopat(ebit_val, t_eff) -> float | None:
    if ebit_val is None or t_eff is None:
        return None
    return float(ebit_val * (1.0 - t_eff))


def fcf(cfo, capex) -> float | None:
    if cfo is None or capex is None:
        return None
    return float(cfo - capex)


def fcf_adj(cfo, capex, sbc) -> float | None:
    base = fcf(cfo, capex)
    return None if base is None or sbc is None else float(base - sbc)


def ebitda(ebit_val, dna) -> float | None:
    if ebit_val is None or dna is None:
        return None
    return float(ebit_val + dna)


def cagr(cur, base, cur_rev: float | None = None, years: float = 5) -> float | None:
    """(cur/base)^(1/n)−1; null if base <= 0 or |base| < 5% of current revenue (doc 06 §8)."""
    if cur is None or base is None or base <= 0 or cur <= 0:
        return None
    if cur_rev is not None and abs(base) < config.CAGR_BASE_FLOOR * abs(cur_rev):
        return None
    n = float(years)
    if n <= 0:
        return None
    return float((cur / base) ** (1.0 / n) - 1.0)


def cagr5_from_series(s: pd.Series, cur_rev: float | None = None, *,
                      fallback: bool = False) -> tuple[float | None, bool]:
    """5y CAGR from an annual series indexed by period-end.

    Primary base: the date in [4.6, 5.5]y closest to a true 5.0y gap (same as
    the old inline helper). If that base is unusable (negative / too small) and
    ``fallback`` is set, take the valid base in [3.5, 5.5]y closest to 5.0y
    and return ``used_fallback=True`` — the COVID-year hole (EXPE). Revenue
    CAGR keeps ``fallback=False``.
    """
    s = pd.to_numeric(s, errors="coerce").dropna()
    if s.empty:
        return None, False
    s = s.copy()
    s.index = pd.to_datetime(s.index)
    cur_end, cur = s.index[-1], float(s.iloc[-1])
    if not (cur > 0):
        return None, False

    def in_window(lo: float, hi: float) -> list:
        return [e for e in s.index[:-1]
                if lo * 365 <= (cur_end - e).days <= hi * 365]

    primary = in_window(config.CAGR5_YEARS_LO, config.CAGR5_YEARS_HI)
    if primary:
        base = min(primary, key=lambda e: abs((cur_end - e).days - 5 * 365))
        v = cagr(cur, float(s.loc[base]), cur_rev=cur_rev, years=5)
        if v is not None:
            return v, False
    if not fallback:
        return None, False
    best = None
    for e in in_window(config.CAGR5_FALLBACK_YEARS_LO, config.CAGR5_YEARS_HI):
        days = (cur_end - e).days
        v = cagr(cur, float(s.loc[e]), cur_rev=cur_rev, years=days / 365.0)
        if v is None:
            continue
        dist = abs(days - 5 * 365)
        if best is None or dist < best[0]:
            best = (dist, v)
    return (best[1], True) if best else (None, False)


def winsor_pct_rank(s: pd.Series, higher_better: bool = True) -> pd.Series:
    """Winsorize at 1/99 within the group, then percentile rank 0–100 (avg ties).
    Direction normalized so 100 = best (doc 06 §9)."""
    s = pd.to_numeric(s, errors="coerce")
    lo, hi = s.quantile(0.01), s.quantile(0.99)
    if pd.notna(lo) and pd.notna(hi) and lo != hi:
        s = s.clip(lo, hi)
    pct = s.rank(pct=True, method="average") * 100.0
    return pct if higher_better else 100.0 - pct


def fixed_scale_yield(y: float | None) -> float | None:
    """fcf_yield -> clip(0,10%)/10% -> 0–100. Absolute metrics are NEVER cross-sectionally
    ranked (doc 06 §9)."""
    if y is None or not np.isfinite(y):
        return None
    return float(np.clip(y, 0.0, config.FCF_YIELD_SCALE_MAX) / config.FCF_YIELD_SCALE_MAX * 100.0)


def _finite(x) -> bool:
    return x is not None and np.isfinite(x)


def clip_scale(x, lo: float, hi: float) -> float | None:
    """Map x onto 0–100 with lo → 0 and hi → 100; clip outside. None stays None."""
    if not _finite(x) or hi <= lo:
        return None
    return float(np.clip((float(x) - lo) / (hi - lo), 0.0, 1.0) * 100.0)


def conservative_level(median=None, ttm=None, mean=None) -> float | None:
    """min(TTM, 5y median) so a boom year cannot lift the level; 5y mean only if both
    of those are missing (doc 05 §2.1 v0.5.8)."""
    present = [float(v) for v in (median, ttm) if _finite(v)]
    if present:
        return min(present)
    return float(mean) if _finite(mean) else None


def nd_ebitda_score(nd) -> float | None:
    """Net cash (≤ 0) = 100; 3.0× EBITDA = 0; linear between."""
    if not _finite(nd):
        return None
    if float(nd) <= 0:
        return 100.0
    return float(np.clip(1.0 - float(nd) / config.QUALITY_ND_EBITDA_ZERO, 0.0, 1.0) * 100.0)


def cagr_score(g) -> float | None:
    """clip(CAGR, 0, 12%) / 12% → 0–100. Negative growth scores 0, not 'below zero'."""
    if not _finite(g):
        return None
    cap = config.QUALITY_CAGR_CAP
    return float(np.clip(float(g), 0.0, cap) / cap * 100.0)


def cov_to_score(cov, *, zero_at: float) -> float | None:
    """Coefficient of variation → 0–100. CoV 0 = 100; CoV ≥ zero_at = 0."""
    if not _finite(cov) or zero_at <= 0:
        return None
    return float(np.clip(1.0 - float(cov) / zero_at, 0.0, 1.0) * 100.0)


def coeff_var(s: pd.Series, *, require_positive_mean: bool = False) -> float | None:
    """Population CoV (std/|mean|) over a FY series. Needs ≥ 3 points."""
    s = pd.to_numeric(s, errors="coerce").dropna()
    if len(s) < 3:
        return None
    mu = float(s.mean())
    if require_positive_mean and not (mu > 0):
        return None
    if mu == 0:
        return None
    return float(s.std(ddof=0) / abs(mu))


def rev_up_years(revenue: pd.Series) -> tuple[int | None, int | None]:
    """Count of non-negative YoY revenue changes (did not fall) and the pair count.

    Flat years count as stable; only declines haircut the stability sleeve.
    """
    s = pd.to_numeric(revenue, errors="coerce").dropna().sort_index()
    if len(s) < 2:
        return None, None
    chg = s.diff().iloc[1:]
    return int((chg >= 0).sum()), int(len(chg))


def fy_window_quality(F: pd.DataFrame, cur_rev: float | None = None) -> dict:
    """5y/10y averages, medians, and path metrics from a fiscal-year frame.

    Windows are by DATE (doc 03): 9.3y / 4.3y back from the latest FY end so a gap
    in the series cannot fake a 10th/5th year. Path fields feed QualityScore
    stability (doc 05 §2.1 v0.5.8). `cur_rev` is TTM revenue for the CAGR base
    floor (doc 06 §8).
    """
    out: dict = {}
    if F is None or not isinstance(F, pd.DataFrame) or F.empty:
        return out
    idx = pd.to_datetime(F.index)
    F = F.copy()
    F.index = idx
    latest = max(F.index)

    def window(years: float) -> pd.DataFrame:
        return F.loc[[e for e in F.index if (latest - e).days <= years * 366]]

    W10, W5 = window(9.3), window(4.3)
    if "roic" in W10 and W10["roic"].notna().any():
        out["roic_years"] = int((W10["roic"] > config.ROIC_THRESHOLD).sum())
    if "fcf" in W10 and W10["fcf"].notna().any():
        out["fcf_pos_years"] = int((W10["fcf"] > 0).sum())
    if "gm" in W5 and W5["gm"].notna().sum() >= 3:
        out["gm"] = float(W5["gm"].dropna().mean())
        out["gm_stability"] = coeff_var(W5["gm"])
    if "fcf_margin" in W5 and W5["fcf_margin"].notna().sum() >= 3:
        m = W5["fcf_margin"].dropna()
        out["fcf_margin"] = float(m.mean())
        out["fcf_margin_median_5y"] = float(m.median())
    if "roic" in W5 and W5["roic"].notna().sum() >= 3:
        r = W5["roic"].dropna()
        out["roic"] = float(r.mean())
        out["roic_median_5y"] = float(r.median())
    if "fcf" in W5:
        out["fcf_cov"] = coeff_var(W5["fcf"], require_positive_mean=True)
    if "revenue" in W10:
        pos, n = rev_up_years(W10["revenue"])
        out["rev_pos_years"] = pos
        out["rev_yoy_n"] = n
    out["fcf_cagr5_base_fallback"] = False
    if "revenue" in F:
        out["rev_cagr5"], _ = cagr5_from_series(F["revenue"].dropna(), cur_rev=cur_rev)
    if "fcf" in F:
        val, fb = cagr5_from_series(F["fcf"].dropna(), cur_rev=cur_rev, fallback=True)
        out["fcf_cagr5"] = val
        out["fcf_cagr5_base_fallback"] = bool(fb)
    return out


def self_stats(series: pd.Series) -> dict | None:
    """SELF percentile / median-centered z / median-ratio + validity rules
    (docs 02, 06 §9): >= 3y of valid observations AND >= 60% of the window valid,
    else null.

    `series`: the stock's own trailing-window observations of the multiple
    INCLUDING invalid weeks (NaN) — valid observations form the distribution,
    the full window length is the validity denominator. The current value is
    the LAST observation of the window and must itself be valid (an invalid
    current multiple has no rank).

    Returns pct of the current value within its valid window (0 = cheapest
    ever; ties -> average rank, doc 06 §9), median-centered z, median-ratio.
    """
    s_full = pd.to_numeric(series, errors="coerce")
    if s_full.size == 0 or pd.isna(s_full.iloc[-1]):
        return None
    n_window = int(s_full.size)
    s = s_full.dropna()
    n_valid = int(len(s))
    if n_valid < int(config.SELF_MIN_HISTORY_YEARS * 52):
        return None
    if n_valid < config.SELF_MIN_VALID_FRAC * n_window:
        return None
    cur = float(s.iloc[-1])
    med = float(s.median())
    std = float(s.std(ddof=0))
    pct = float(s.rank(pct=True, method="average").iloc[-1] * 100.0)
    return {
        "pct": pct,
        "z": float((cur - med) / std) if std > 0 else None,
        "median_ratio": float(cur / med) if med > 0 else None,
        "n_valid": n_valid,
        "n_window": n_window,
    }
