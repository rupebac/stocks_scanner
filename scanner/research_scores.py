"""Explain unavailable scan scores and expose separately labeled research estimates.

Canonical scores, eligibility, peer ranks and shortlist inputs remain untouched.
A research estimate is never a replacement for unavailable financial observations.
"""
from __future__ import annotations

import pandas as pd

from . import scoring as SC, config
from .metrics import fixed_scale_yield, winsor_pct_rank


def add_research_scores(metrics: pd.DataFrame) -> pd.DataFrame:
    out = metrics.copy()
    if out.empty:
        return out
    # Absolute quality can be calculated without valuation-history coverage.
    candidates = SC.compute_scores(out.drop(columns=["residual", "residual_fitted", "residual_pct"], errors="ignore"))
    candidates = candidates.set_index("ticker").reindex(out.ticker)
    candidates.index = out.index
    cheap_parts = pd.DataFrame({
        "self_history": winsor_pct_rank(SC._col(out, "ev_fcf_self_pct"), higher_better=False),
        "cross_section": SC._col(out, "pct_ev_ebit").combine_first(SC._col(candidates, "pct_ev_ebit")),
        "absolute": SC._col(out, "fcf_yield").map(fixed_scale_yield),
    }, index=out.index)
    weights = pd.Series(config.CHEAPNESS_WEIGHTS)
    cheap_coverage = cheap_parts.notna().mul(weights).sum(axis=1)
    partial_cheap = cheap_parts.mul(weights).sum(axis=1)/cheap_coverage.where(cheap_coverage > 0)
    labels = {"self_history": "FCF valuation history", "cross_section": "operating-profit valuation versus peers", "absolute": "reported cash yield"}
    for kind in ("quality", "cheapness"):
        out[f"display_{kind}_score"] = SC._col(out, f"{kind}_score")
        out[f"{kind}_score_status"] = "Scan score"
        out[f"{kind}_score_note"] = "Original scanner score."
    for idx, row in out.iterrows():
        if pd.isna(out.at[idx, "display_quality_score"]):
            evidence = candidates.at[idx, "quality_component_values"]
            core = candidates.at[idx, "quality_weight_used"]
            # Require meaningful core coverage AND observed stability. A lone growth
            # series must not produce an apparently strong business-quality score.
            enough = core >= .7-1e-8 and evidence.get("profitability") is not None and evidence.get("stability") is not None
            value = candidates.at[idx, "quality_score"]
            missing = [label for key, label in [
                ("roic", "long-run return on capital"), ("fcf_cagr5", "five-year cash-flow growth"),
                ("gm_stability", "gross-margin stability")]
                if key not in row or pd.isna(row.get(key))]
            if enough and pd.notna(value):
                out.at[idx, "display_quality_score"] = value
                out.at[idx, "quality_score_status"] = "Estimate"
                out.at[idx, "quality_score_note"] = (
                    f"Research estimate using the existing quality formula; {core:.0%} of core category weight available. "
                    "The scan's broad coverage rule excluded this stock. Missing inputs within categories: "
                    + (", ".join(missing) or "see scan coverage details") + ". Eligibility is unchanged.")
            else:
                out.at[idx, "quality_score_status"] = "Insufficient data"
                absent = [k for k,v in evidence.items() if v is None]
                out.at[idx, "quality_score_note"] = (
                    "Not enough evidence for a quality estimate. Missing: " + (", ".join(absent) or "adequate core coverage") + ".")
        if pd.isna(out.at[idx, "display_cheapness_score"]):
            coverage = cheap_coverage.loc[idx]
            missing = [labels[k] for k,v in cheap_parts.loc[idx].items() if pd.isna(v)]
            if coverage >= .6-1e-8 and pd.notna(partial_cheap.loc[idx]):
                out.at[idx, "display_cheapness_score"] = partial_cheap.loc[idx]
                out.at[idx, "cheapness_score_status"] = "Estimate"
                out.at[idx, "cheapness_score_note"] = (
                    f"Research estimate from {coverage:.0%} of cheapness weight; available weights are renormalized. "
                    "Missing: " + (", ".join(missing) or "none; excluded by broad scan coverage") + ". "
                    "Existing peer ranks are retained; missing peer ranks use the full research universe. "
                    "Reported cash only; negative cash yield contributes zero, rather than being treated as cheap.")
            else:
                out.at[idx, "cheapness_score_status"] = "Insufficient data"
                out.at[idx, "cheapness_score_note"] = (
                    f"Only {coverage:.0%} of cheapness weight available; at least 60% is needed. Missing: " + ", ".join(missing) + ".")
    return out
