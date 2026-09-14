"""Scoring: peer ladder percentiles (cheapness / momentum only), QualityScore
(absolute bars × path stability), CheapnessScore, quality floor, valuation
residual, composite. Doc 05 §2 (v0.5.9 quality) + doc 06 §9 mechanics.
"""
from __future__ import annotations

import ast

import numpy as np
import pandas as pd

from . import config
from .metrics import (
    cagr_score, clip_scale, conservative_level, cov_to_score,
    fixed_scale_yield, holdability_roic, nd_ebitda_score,
    weighted_available_mean, winsor_pct_rank,
)

_PCT_COLS = {
    # column -> higher_better (doc 05 §2.1 / §2.2); ranked on the peer ladder
    "pct_roic": ("roic", True),
    "pct_fcf_margin": ("fcf_margin", True),
    "pct_gm": ("gm", True),
    "pct_roic_years": ("roic_years", True),
    "pct_fcf_pos_years": ("fcf_pos_years", True),
    "pct_fcf_ni": ("fcf_ni", True),
    "pct_nd_ebitda": ("nd_ebitda", False),      # lower leverage = better
    "pct_ev_ebit": ("ev_ebit", False),          # cheaper = better
}


def assign_peer_set(df: pd.DataFrame) -> pd.DataFrame:
    """Ladder (doc 05 §2): industry group when it has >= LADDER_MIN_NAMES names with a
    valid ev_ebit, else sector (same bar), else market. The chosen level is recorded
    per stock so the output can state which peer set produced each percentile."""
    if df.empty:
        df["peer_level"] = pd.Series(dtype=object)
        df["peer_set"] = pd.Series(dtype=object)
        return df
    valid = df  # rung sizing counts standard-group NAMES (doc 05 §2 ">= 8 names"), not just valid-ev_ebit rows
    ig_n = valid.groupby("ig_name")["ticker"].count()
    sec_n = valid.groupby("sector")["ticker"].count()

    def level(row):
        ig = row.get("ig_name")
        if ig is not None and pd.notna(ig) and ig in ig_n.index and ig_n[ig] >= config.LADDER_MIN_NAMES:
            return ("ig", ig)
        sec = row.get("sector")
        if sec is not None and pd.notna(sec) and sec in sec_n.index and sec_n[sec] >= config.LADDER_MIN_NAMES:
            return ("sector", sec)
        return ("market", "MARKET")

    sets = df.apply(level, axis=1)
    df["peer_level"], df["peer_set"] = zip(*sets)
    return df


def _group_pct(df: pd.DataFrame, col: str, higher_better: bool) -> pd.Series:
    """Winsorize at 1/99 then percentile-rank at each stock's chosen ladder
    granularity (doc 06 §9). The sector set is the stock's whole sector
    cross-section and the market set the whole frame — the ladder picks the
    granularity, and the peer set at that granularity is all of its names
    (doc 03 caveats: Energy falls back to "~20 names", not to a leftover bucket)."""
    out = pd.Series(np.nan, index=df.index, dtype="float64")
    ig_mask = df["peer_level"] == "ig"
    if ig_mask.any():
        ranked = df.groupby("ig_name")[col].transform(winsor_pct_rank, higher_better=higher_better)
        out.loc[ig_mask] = ranked.loc[ig_mask]
    sec_mask = df["peer_level"] == "sector"
    if sec_mask.any():
        ranked = df.groupby("sector")[col].transform(winsor_pct_rank, higher_better=higher_better)
        out.loc[sec_mask] = ranked.loc[sec_mask]
    mkt_mask = df["peer_level"] == "market"
    if mkt_mask.any():
        # the market rung ranks against the FULL frame (every standard-group name),
        # not just the other market-level leftovers — same rule the sector rung
        # already follows (review: market fallback was a leftover bucket)
        out.loc[mkt_mask] = winsor_pct_rank(df[col], higher_better=higher_better).loc[mkt_mask]
    return out


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    """Column as numeric, or all-NaN when the input never shipped (doc 06 §10)."""
    if name in df.columns:
        return pd.to_numeric(df[name], errors="coerce")
    return pd.Series(np.nan, index=df.index, dtype="float64")


def compute_scores(df: pd.DataFrame) -> pd.DataFrame:
    df = assign_peer_set(df.copy())
    for out, (src, hb) in _PCT_COLS.items():
        if src in df.columns:
            df[out] = _group_pct(df, src, hb)

    # momentum percentile for `beaten_but_delivering` is SECTOR-ranked (doc 05 §3:
    # "mom_12_1 <= sector 25th pct"), not ladder-ranked like the multiples.
    if "mom_12_1" in df.columns and "sector" in df.columns:
        df["pct_mom_sector"] = df.groupby("sector")["mom_12_1"].transform(
            winsor_pct_rank, higher_better=True)

    # --- QualityScore (doc 05 §2.1 v0.5.9): absolute bars × path stability ------
    def _levels(median_col, ttm_col, mean_col):
        return pd.Series(
            [conservative_level(m, t, n) for m, t, n in zip(
                _col(df, median_col), _col(df, ttm_col), _col(df, mean_col))],
            index=df.index,
        )

    roic_level = _levels("roic_median_5y", "roic_ttm", "roic")
    roic_s = roic_level.map(
        lambda v: clip_scale(v, 0.0, config.QUALITY_ROIC_CAP))
    fcf_s = _levels("fcf_margin_median_5y", "fcf_margin_ttm", "fcf_margin").map(
        lambda v: clip_scale(v, 0.0, config.QUALITY_FCF_MARGIN_CAP))
    profit = pd.concat([roic_s, fcf_s], axis=1).mean(axis=1, skipna=True)

    growth = pd.concat([
        _col(df, "rev_cagr5").map(cagr_score),
        _col(df, "fcf_cagr5").map(cagr_score),
    ], axis=1).mean(axis=1, skipna=True)
    balance = _col(df, "nd_ebitda").map(nd_ebitda_score)

    gm_s = _col(df, "gm_stability").map(
        lambda v: cov_to_score(v, zero_at=config.QUALITY_GM_COV_ZERO))
    fcf_path = _col(df, "fcf_cov").map(
        lambda v: cov_to_score(v, zero_at=config.QUALITY_FCF_COV_ZERO))
    rev_n = _col(df, "rev_yoy_n")
    rev_pos = _col(df, "rev_pos_years")
    rev_s = pd.Series(np.nan, index=df.index, dtype="float64")
    ok = rev_n.gt(0)
    rev_s.loc[ok] = (rev_pos.loc[ok] / rev_n.loc[ok] * 100.0).clip(0.0, 100.0)
    stability = weighted_available_mean(
        pd.DataFrame({"revenue": rev_s, "gm": gm_s, "fcf": fcf_path}, index=df.index),
        config.QUALITY_STABILITY_WEIGHTS,
    )

    comp = pd.DataFrame(
        {"profitability": profit, "growth": growth, "balance": balance},
        index=df.index,
    )
    w = pd.Series({name: config.QUALITY_WEIGHTS[name] for name in comp.columns})
    avail = comp.notna()
    df["quality_weight_used"] = avail.mul(w, axis=1).sum(axis=1)   # core sleeves; stated
    core = (
        (comp.mul(w, axis=1).fillna(0.0).sum(axis=1)
         / df["quality_weight_used"].replace(0, np.nan))
        .where(df["quality_weight_used"] > 0)
    )
    # missing path data does not haircut (×1); a computed 0 does
    df["quality_score"] = core * (stability / 100.0).fillna(1.0)
    evidence = comp.copy()
    evidence["stability"] = stability
    df["quality_component_values"] = [   # evidence (doc 05 §4b)
        {n: (None if pd.isna(v) else round(float(v), 1)) for n, v in row.items()}
        for row in evidence.round(1).to_dict("records")
    ]

    # --- CheapnessScore (doc 05 §2.2) --------------------------------------------
    # SELF input is ranked against the MARKET cross-section (doc 05 §2: "SELF-mode
    # inputs are first converted to own-history percentiles, then ranked against the
    # market cross-section") — the raw own-history percentile is not distribution-
    # comparable with the ladder-ranked ev_ebit component it is weighted against.
    self_cheap = winsor_pct_rank(_col(df, "ev_fcf_self_pct"), higher_better=False)
    cross_cheap = _col(df, "pct_ev_ebit")                # already 100 = cheap
    abs_cheap = _col(df, "fcf_yield").map(fixed_scale_yield)  # fixed scale, never ranked
    parts = pd.DataFrame({"self": self_cheap, "cross": cross_cheap, "abs": abs_cheap})
    wc = config.CHEAPNESS_WEIGHTS
    # all three required: doc 08 §5 lists CheapnessScore inputs as "(complete)" — no
    # renormalization contract exists, and doc 06 §10 forbids neutral fills.
    df["cheapness_score"] = (
        parts["self"] * wc["self_history"]
        + parts["cross"] * wc["cross_section"]
        + parts["abs"] * wc["absolute"]
    ).where(parts.notna().all(axis=1))

    # --- quality floor: absolute bar + conservative ROIC band (doc 05 §2.3) ------
    thr = float(config.QUALITY_FLOOR_MIN)
    # missing ROIC does not fail; a computed value must sit in [10%, 100%].
    # >100% (NOPAT > invested capital) is a tiny-IC artifact, not holdability.
    roic_ok = roic_level.isna() | (
        (roic_level >= config.QUALITY_FLOOR_ROIC_MIN)
        & (roic_level <= config.QUALITY_FLOOR_ROIC_MAX)
    )
    df["quality_floor_pass"] = (
        df["quality_score"].notna() & (df["quality_score"] >= thr) & roic_ok
    )

    # --- valuation residual (doc 05 §2.3) ------------------------------------------
    # Fit on ALL gated rows with valid ev_fcf > 0 and ROIC > 0 (ROIC <= 0 excluded
    # from the fit; 0 < ROIC < 1% floored to 1% for the log), everybody fits,
    # floor-passers rank.
    fit = df[_col(df, "ev_fcf").gt(0) & _col(df, "roic").gt(0)].copy()
    fit_stats: dict = {}
    if len(fit) >= config.LADDER_MIN_NAMES:
        fit["y"] = np.log(fit["ev_fcf"])
        fit["x"] = np.log(fit["roic"].clip(lower=config.RESIDUAL_ROIC_FLOOR))
        for c in ("y", "x"):   # winsorize BEFORE the fit (robust fit, doc 05 §2.3)
            lo, hi = fit[c].quantile(0.01), fit[c].quantile(0.99)
            fit[c] = fit[c].clip(lo, hi)
        # dummy groups: tiny ig groups fall back down the §2 ladder (ig -> sector -> market)
        has_sector = "sector" in fit.columns
        ig_n = fit["ig_name"].value_counts() if "ig_name" in fit.columns else pd.Series(dtype=int)
        sec_n = fit["sector"].value_counts() if has_sector else pd.Series(dtype=int)

        def res_group(r):
            if "ig_name" in r.index:
                ig = r["ig_name"]
                if pd.notna(ig) and ig_n.get(ig, 0) >= config.LADDER_MIN_NAMES:
                    return f"ig:{ig}"
            if has_sector:
                sec = r["sector"]
                if pd.notna(sec) and sec_n.get(sec, 0) >= config.LADDER_MIN_NAMES:
                    return f"sector:{sec}"
            return "market:MARKET"

        fit["res_group"] = fit.apply(res_group, axis=1)
        dummies = pd.get_dummies(fit["res_group"], drop_first=True, dtype=float)
        X = pd.concat([pd.Series(1.0, index=fit.index, name="const"), fit["x"], dummies], axis=1)
        beta, *_ = np.linalg.lstsq(X.to_numpy(), fit["y"].to_numpy(), rcond=None)
        fitted = X.to_numpy() @ beta
        fit["residual"] = fit["y"].to_numpy() - fitted          # more negative = cheaper
        fit["residual_fitted"] = np.exp(fitted)                  # fitted EV/FCF (display)
        df = df.merge(fit[["ticker", "residual", "residual_fitted"]], on="ticker", how="left")
        fit_stats = {
            "n": int(len(fit)),
            "slope_log_roic": float(beta[1]),
            "dummies": {str(k): float(b) for k, b in zip(dummies.columns, beta[2:])},
            "groups": {str(k): int(v) for k, v in fit["res_group"].value_counts().items()},
        }
    else:
        df["residual"] = np.nan
        df["residual_fitted"] = np.nan
    passers = df["quality_floor_pass"] & df["residual"].notna()
    df["residual_pct"] = np.nan
    if int(passers.sum()) >= 2:
        # midrank percentile (rank−0.5)/n: continuous at tiny n, so the 20th-pct arm
        # stays reachable in small scans — rank/n needed n>=5 before the cheapest
        # passer could even reach the 20th percentile (the v0.4 false-negative trap)
        r = df.loc[passers, "residual"].rank(method="average")
        df.loc[passers, "residual_pct"] = (r - 0.5) / int(passers.sum()) * 100.0

    # --- composite (display only, doc 05 §2.3) --------------------------------------
    df["composite"] = 0.5 * df["quality_score"] + 0.5 * df["cheapness_score"]

    # attrs last: pandas merge returns a new frame without attrs, so everything the
    # scan artifact needs is set on the object we return.
    df.attrs["quality_floor_threshold"] = thr
    df.attrs["residual_fit"] = fit_stats
    return df


# --- named cash setting (v0.5.11) ----------------------------------------------
# Default remains reported FCF (CFO − capex − SBC). Owner earnings (CFO − D&A −
# SBC) is a named switch, never a blend: D&A stands in for maintenance capex so
# growth buildouts do not zero the operating cash engine. Overlay strike yields
# stay on reported FCF.

_OWNER_TO_ACTIVE = {
    "fcf_margin": "fcf_owner_margin",
    "fcf_margin_ttm": "fcf_owner_margin_ttm",
    "fcf_margin_median_5y": "fcf_owner_margin_median_5y",
    "fcf_cagr5": "fcf_owner_cagr5",
    "fcf_cagr5_base_fallback": "fcf_owner_cagr5_base_fallback",
    "fcf_cov": "fcf_owner_cov",
    "fcf_yield": "fcf_owner_yield",
    "ev_fcf": "ev_fcf_owner",
    "ev_fcf_self_pct": "ev_fcf_owner_self_pct",
    "ev_fcf_self_z": "ev_fcf_owner_self_z",
    "ev_fcf_self_ratio": "ev_fcf_owner_self_ratio",
    "ev_fcf_self_n": "ev_fcf_owner_self_n",
    "brake_fcf_margin": "brake_fcf_owner_margin",
}

_OWNER_SOURCE_COLS = (
    "fcf_owner_cagr5", "fcf_owner_yield", "fcf_owner_ttm", "fcf_owner_margin",
)


def owner_cash_available(df: pd.DataFrame) -> bool:
    return any(c in df.columns for c in _OWNER_SOURCE_COLS)


def _reasons_list(val) -> list:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return []
    if isinstance(val, (list, tuple)):
        return [str(x) for x in val]
    s = str(val).strip()
    if not s or s.lower() in ("[]", "nan", "none"):
        return []
    try:
        parsed = ast.literal_eval(s)
        if isinstance(parsed, (list, tuple)):
            return [str(x) for x in parsed]
    except (ValueError, SyntaxError):
        pass
    return [s]


def _refresh_fcf_cagr_gate(df: pd.DataFrame) -> pd.DataFrame:
    """Re-evaluate only the FCF-CAGR gate after a cash-series swap. Size/tenure
    columns missing in unit tests must not invent 'adv n/a' failures.
    Stale `coverage<80%` tags (stamped after gates at scan time) are stripped
    here; coverage is re-applied after the mapped SELF slot is known."""
    if "gate_fail_reasons" not in df.columns:
        return df

    def refresh(row):
        reasons = [r for r in _reasons_list(row.get("gate_fail_reasons"))
                   if r not in ("fcf_cagr5 gate", "coverage<80%")]
        fc = row.get("fcf_cagr5")
        if fc is None or pd.isna(fc) or fc < config.GATE_FCF_CAGR5_MIN:
            reasons.append("fcf_cagr5 gate")
        return reasons

    df["gate_fail_reasons"] = df.apply(refresh, axis=1)
    df["gates_pass"] = df["gate_fail_reasons"].map(lambda r: len(r) == 0)
    return df


def _stamp_coverage_reason(df: pd.DataFrame) -> pd.DataFrame:
    if "input_coverage" not in df.columns or "gate_fail_reasons" not in df.columns:
        return df

    def stamp(row):
        reasons = [r for r in _reasons_list(row.get("gate_fail_reasons"))
                   if r != "coverage<80%"]
        cov = row.get("input_coverage")
        if cov is None or pd.isna(cov) or cov < config.MIN_SCORE_INPUT_COVERAGE:
            reasons.append("coverage<80%")
        return reasons

    df["gate_fail_reasons"] = df.apply(stamp, axis=1)
    return df


def apply_cash_definition(df: pd.DataFrame, mode: str = "reported") -> pd.DataFrame:
    """Map owner-earnings columns onto the active FCF slots and rescore, or leave
    reported FCF alone. Old scans without owner columns stay reported.
    After the swap, only coverage-passers are rescored (same rule as the scan)."""
    out = df.copy()
    out.attrs.update(getattr(df, "attrs", {}))
    if mode != "owner" or not owner_cash_available(out):
        out.attrs["cash_definition"] = "reported"
        return out
    for dest, src in _OWNER_TO_ACTIVE.items():
        if src in out.columns:
            out[dest] = out[src]
    out = _refresh_fcf_cagr_gate(out)
    cov_cols = [c for c in config.MVP_SCORE_INPUTS if c in out.columns]
    if cov_cols:
        out["input_coverage"] = out[cov_cols].notna().mean(axis=1)
        out = _stamp_coverage_reason(out)
    drop = [c for c in (
        "residual", "residual_fitted", "residual_pct",
        "quality_score", "quality_floor_pass", "cheapness_score", "composite",
        "quality_weight_used", "quality_component_values",
    ) if c in out.columns]
    if drop:
        out = out.drop(columns=drop)
    if "input_coverage" in out.columns:
        scoreable = out[out["input_coverage"] >= config.MIN_SCORE_INPUT_COVERAGE].copy()
    else:
        scoreable = out.copy()
    if scoreable.empty:
        out.attrs["cash_definition"] = "owner"
        return out
    scored = compute_scores(scoreable)
    if "ticker" in out.columns:
        rest = out[~out["ticker"].isin(scored["ticker"])].reindex(columns=scored.columns)
        out = pd.concat([scored, rest], ignore_index=True)
    else:
        out = scored
    out.attrs["cash_definition"] = "owner"
    return out


def highlighted(df: pd.DataFrame, gs10: float | None = None,
                yield_floor: float = 0.04, *, quality_min: float | None = None,
                max_residual: float = 0.0) -> pd.DataFrame:
    """Ideas highlights: gated + quality floor + residual < 0 + holdability.
    Holdability ROIC is the 5y average when present, else conservative
    min(TTM, 5y median). Missing both fails. Yield uses the active cash series."""
    bar = max(yield_floor, gs10 or 0)
    if df.empty or "quality_score" not in df.columns:
        return df.iloc[0:0].copy()
    g = df[
        df["quality_score"].notna()
        & df["gates_pass"].fillna(False)
        & (df["input_coverage"] >= config.MIN_SCORE_INPUT_COVERAGE)
    ].copy()
    if g.empty:
        return g
    roic_h = pd.Series(
        [holdability_roic(r.get("roic"), r.get("roic_ttm"), r.get("roic_median_5y"))
         for _, r in g.iterrows()],
        index=g.index,
    )
    floor_pass = g["quality_floor_pass"].fillna(False)
    if quality_min is not None:
        # Interactive display override; retain the scanner's conservative ROIC band.
        roic_level = pd.Series([
            conservative_level(r.get("roic_median_5y"), r.get("roic_ttm"), r.get("roic"))
            for _, r in g.iterrows()], index=g.index)
        roic_ok = roic_level.isna() | roic_level.between(config.QUALITY_FLOOR_ROIC_MIN, config.QUALITY_FLOOR_ROIC_MAX)
        floor_pass = (g["quality_score"] >= quality_min) & roic_ok
    hi = g[
        floor_pass
        & g["residual"].notna() & (g["residual"] < max_residual)
        & roic_h.notna() & (roic_h >= config.ROIC_THRESHOLD)
        & g["fcf_yield"].notna() & (g["fcf_yield"] >= bar)
    ]
    return hi.sort_values("residual", ascending=True)
