"""Named cash setting: reported FCF (CFO − capex − SBC) vs owner earnings
(CFO − D&A − SBC). One series at a time — never a blend. Docs 05/06 v0.5.11.
"""
import numpy as np
import pandas as pd

from scanner import config, scoring as SC
from tests.test_scoring_flags import make_frame


def _with_owner(df: pd.DataFrame) -> pd.DataFrame:
    """Owner columns that differ from reported FCF (Oracle-style: reported CAGR dead,
    owner CAGR alive; reported SELF missing, owner SELF present)."""
    out = df.copy()
    out["fcf_cagr5"] = -0.20
    out["fcf_owner_cagr5"] = 0.10
    out["fcf_owner_margin"] = out["fcf_margin"]
    out["fcf_owner_margin_ttm"] = out["fcf_margin"]
    out["fcf_owner_margin_median_5y"] = out["fcf_margin"]
    out["fcf_owner_cov"] = 0.10
    out["fcf_owner_yield"] = 0.08
    out["ev_fcf_owner"] = out["ev_fcf"]
    out["ev_fcf_owner_self_pct"] = 40.0
    out["ev_fcf_owner_self_z"] = -0.5
    out["ev_fcf_owner_self_ratio"] = 0.9
    out["ev_fcf_self_pct"] = np.nan
    out["gate_fail_reasons"] = [["fcf_cagr5 gate"]] * len(out)
    out["gates_pass"] = False
    out["input_coverage"] = 0.7
    return out


def test_reported_mode_leaves_fcf_cagr_and_gates_alone():
    df = _with_owner(make_frame())
    out = SC.apply_cash_definition(df, "reported")
    assert np.allclose(out["fcf_cagr5"], -0.20)
    assert not out["gates_pass"].any()
    assert out.attrs.get("cash_definition") == "reported"


def test_owner_mode_unblocks_fcf_cagr_gate_and_rescores():
    df = _with_owner(make_frame())
    out = SC.apply_cash_definition(df, "owner")
    assert np.allclose(out["fcf_cagr5"], 0.10)
    assert np.allclose(out["fcf_yield"], 0.08)
    assert np.allclose(out["ev_fcf_self_pct"], 40.0)
    assert out["gates_pass"].all()
    assert "fcf_cagr5 gate" not in " ".join(
        ";".join(r) for r in out["gate_fail_reasons"]
    )
    assert out["quality_score"].notna().all()
    assert out["residual"].notna().any()
    assert out.attrs.get("cash_definition") == "owner"
    # coverage uses the mapped SELF slot (was missing on reported)
    assert (out["input_coverage"] >= config.MIN_SCORE_INPUT_COVERAGE).all()


def test_owner_mode_without_owner_columns_stays_reported():
    df = make_frame()
    df["fcf_cagr5"] = -0.20
    out = SC.apply_cash_definition(df, "owner")
    assert np.allclose(out["fcf_cagr5"], -0.20)
    assert out.attrs.get("cash_definition") == "reported"


def test_owner_mode_clears_stale_coverage_reason_when_self_fills():
    """Scan stamps coverage<80% onto reasons after gates. Owner SELF can lift
    coverage to 80% — that leftover must not keep gates_pass False."""
    df = _with_owner(make_frame())
    df["gate_fail_reasons"] = [["fcf_cagr5 gate", "coverage<80%"]] * len(df)
    out = SC.apply_cash_definition(df, "owner")
    assert out["gates_pass"].all()
    blob = " ".join(";".join(r) for r in out["gate_fail_reasons"])
    assert "coverage<80%" not in blob
    assert "fcf_cagr5 gate" not in blob


def test_owner_mode_does_not_invent_size_gate_fails():
    """Unit frames have no adv/mktcap/tenure — do not stamp 'adv n/a' onto them."""
    df = _with_owner(make_frame())
    out = SC.apply_cash_definition(df, "owner")
    reasons = " ".join(";".join(r) for r in out["gate_fail_reasons"])
    assert "adv n/a" not in reasons
    assert "mktcap n/a" not in reasons
    assert "tenure n/a" not in reasons


def test_highlighted_uses_conservative_roic_when_five_year_avg_is_missing():
    """Oracle-style: 5y ROIC blank, TTM 21% — holdability can still clear 10%."""
    n = 12
    df = pd.DataFrame({
        "ticker": [f"A{i:02d}" for i in range(n)],
        "quality_score": 70.0,
        "gates_pass": True,
        "input_coverage": 1.0,
        "quality_floor_pass": True,
        "residual": -0.2,
        "roic": [np.nan] * n,
        "roic_ttm": 0.21,
        "roic_median_5y": np.nan,
        "fcf_yield": 0.08,
    })
    hi = SC.highlighted(df, gs10=0.04)
    assert set(hi["ticker"]) == set(df["ticker"])

    missing = df.copy()
    missing["roic_ttm"] = np.nan
    assert SC.highlighted(missing, gs10=0.04).empty

    five_y_wins = df.copy()
    five_y_wins["roic"] = 0.08
    assert SC.highlighted(five_y_wins, gs10=0.04).empty
