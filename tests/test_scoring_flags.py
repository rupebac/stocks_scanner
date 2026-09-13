"""Scoring + flags integration: peer ladder, floor, residual, and the flag matrix —
including the spec's canonical cases (Adobe passes, PayPal-early fails, regime de-rating
does not fire). Docs 05 §2–§3.
"""
import json

import numpy as np
import pandas as pd
import pytest

from scanner import config, flags as FL, scoring as SC


def make_frame(n_per_group: int = 12) -> pd.DataFrame:
    """Two industry groups of filler names; known test rows appended by callers."""
    rng = np.random.default_rng(7)
    rows = []
    for ig in ("Software and Services", "Media and Entertainment"):
        sector = "Information Technology" if ig == "Software and Services" else "Communication Services"
        for i in range(n_per_group):
            rows.append({
                "ticker": f"F{ig[:2]}{i:02d}", "sector": sector, "ig_name": ig,
                "ev_ebit": 12 + rng.uniform(0, 14),
                "roic": 0.10 + rng.uniform(0, 0.25),
                "fcf_margin": 0.12 + rng.uniform(0, 0.15),
                "gm": 0.55 + rng.uniform(0, 0.25),
                "roic_years": 7 + int(rng.integers(0, 4)),
                "fcf_pos_years": 10,
                "fcf_ni": 0.9 + rng.uniform(0, 0.5),
                "nd_ebitda": rng.uniform(0.3, 2.2),
                "ev_fcf": 18 + rng.uniform(0, 22),
                "ev_fcf_self_pct": rng.uniform(15, 85),
                "ev_fcf_self_z": rng.uniform(-1.5, 1.5),
                "ev_fcf_self_ratio": rng.uniform(0.7, 1.3),
                "fcf_yield": rng.uniform(0.02, 0.07),
                "mom_12_1": rng.uniform(-0.4, 0.4),
                "brake_gm": 1.0, "brake_fcf_margin": 1.0, "brake_roic": 1.0,
                "rev_cagr5": 0.08, "fcf_cagr5": 0.08,
            })
    df = pd.DataFrame(rows)
    return df


def _row(ticker, **kw):
    base = {
        "ticker": ticker, "sector": "Information Technology", "ig_name": "Software and Services",
        "ev_ebit": 14, "roic": 0.30, "fcf_margin": 0.30, "gm": 0.87, "roic_years": 10,
        "fcf_pos_years": 10, "fcf_ni": 1.2, "nd_ebitda": 1.2, "ev_fcf": 26,
        "ev_fcf_self_pct": 40, "ev_fcf_self_z": -0.6, "ev_fcf_self_ratio": 0.95,
        "fcf_yield": 0.036, "mom_12_1": -0.25, "brake_gm": 1.0, "brake_fcf_margin": 1.0,
        "brake_roic": 1.0, "rev_cagr5": 0.10, "fcf_cagr5": 0.10,
    }
    base.update(kw)
    return base


def scored_with_cases():
    df = make_frame()
    cases = [
        # 1. Adobe-2024 case: no new lows, yield below the gate, but residual says cheap
        #    (standout quality with a modest multiple -> very negative residual)
        _row("ADBX", roic=0.45, ev_fcf=15, ev_ebit=11,
             ev_fcf_self_pct=35, ev_fcf_self_ratio=0.82, ev_fcf_self_z=-1.2, fcf_yield=0.036),
        # 2. Adobe-2022 case: new SELF lows + bond-competitive yield
        _row("ADBY", ev_fcf_self_pct=8, fcf_yield=0.052),
        # 3. PayPal-early: cheap and floor-passing but margins bleeding -> brakes fail
        _row("PYPLX", ev_fcf_self_pct=8, fcf_yield=0.055, brake_gm=0.90),
        # 4. Regime de-rating: cheap vs history, margins fine, but neither yield nor residual arm
        _row("REGME", ev_fcf_self_pct=10, fcf_yield=0.031),
    ]
    df = pd.concat([df, pd.DataFrame(cases)], ignore_index=True)
    scored = SC.compute_scores(df)
    scored = FL.apply_flags(scored, gs10=0.041)
    return scored.set_index("ticker")


def test_peer_ladder_levels():
    scored = SC.compute_scores(make_frame())
    assert set(scored["peer_level"]) == {"ig"}
    # tiny frame: 2 names per IG, 2 per sector -> both bars missed -> market ladder
    scored = SC.compute_scores(make_frame(2))
    assert (scored["peer_level"] == "market").all()


def test_quality_floor_is_absolute_60():
    scored = SC.compute_scores(make_frame(12))
    assert scored.attrs["quality_floor_threshold"] == pytest.approx(config.QUALITY_FLOOR_MIN)
    assert scored["quality_floor_pass"].sum() >= 1
    assert (scored.loc[scored["quality_floor_pass"], "quality_score"]
            >= config.QUALITY_FLOOR_MIN).all()
    below = scored[(~scored["quality_floor_pass"]) & scored["quality_score"].notna()]
    assert (below["quality_score"] < config.QUALITY_FLOOR_MIN).all()


def test_quality_floor_tiny_sample_still_uses_absolute_bar():
    scored = SC.compute_scores(make_frame(3))
    assert scored.attrs["quality_floor_threshold"] == pytest.approx(config.QUALITY_FLOOR_MIN)
    # names can pass; the old n<10 block is gone
    assert scored["quality_floor_pass"].any() or scored["quality_score"].notna().any()


def test_residual_ranks_cheap_high_roic():
    df = make_frame()
    # standout quality at a modest multiple: top marks on every quality input
    df.loc[df["ticker"] == "FSo00", ["roic", "ev_fcf"]] = [0.45, 16.0]
    df.loc[df["ticker"] == "FSo00", ["fcf_margin", "gm"]] = [0.40, 0.92]
    df.loc[df["ticker"] == "FSo00", ["roic_years", "fcf_pos_years", "fcf_ni"]] = [10, 10, 1.6]
    df.loc[df["ticker"] == "FSo00", "nd_ebitda"] = 0.1
    scored = SC.compute_scores(df).set_index("ticker")
    assert scored.loc["FSo00", "residual"] < 0
    assert scored.loc["FSo00", "residual"] < scored["residual"].median()
    # residual_pct ranks among floor passers only; the standout quality name passes
    assert scored.loc["FSo00", "quality_floor_pass"]
    assert scored.loc["FSo00", "residual_pct"] <= 20


def test_flag_matrix_canonical_cases():
    s = scored_with_cases()
    adbx, adby = s.loc["ADBX"], s.loc["ADBY"]
    pyplx, regme = s.loc["PYPLX"], s.loc["REGME"]
    # floor: all four cases must clear the quality floor (they are top-of-book names)
    assert adbx["quality_floor_pass"] and adby["quality_floor_pass"]
    assert pyplx["quality_floor_pass"] and regme["quality_floor_pass"]
    # 1: fires via residual arm (yield 3.6% < max(4%, 4.1%))
    assert adbx["flag_derated_quality"], "residual arm must carry the 2024-Adobe case"
    ev = json.loads(adbx["evidence_derated"])
    assert ev["residual_arm"] and not ev["yield_arm"]
    assert ev["depressed_arms"]["self_z"]          # z <= -1.0 arm carries the OR-group
    assert not ev["depressed_arms"]["self_pct"] and not ev["depressed_arms"]["median_ratio"]
    # 2: fires via SELF + yield arms
    assert adby["flag_derated_quality"]
    ev = json.loads(adby["evidence_derated"])
    assert ev["depressed_arms"]["self_pct"] and ev["yield_arm"]
    # 3: margin brake blocks the bleed even though it is cheap on both axes
    assert not pyplx["flag_derated_quality"]
    ev = json.loads(pyplx["evidence_derated"])
    assert ev["depressed_arms"]["self_pct"] and ev["yield_arm"]
    assert not ev["brakes"]["gm"] and ev["brakes"]["fcf_margin"] and ev["brakes"]["roic"]
    # 4: regime de-rating must NOT fire (cheap vs history but neither yield nor residual)
    assert not regme["flag_derated_quality"]
    ev = json.loads(regme["evidence_derated"])
    assert ev["depressed_arms"]["self_pct"]
    assert not ev["yield_arm"] and not ev["residual_arm"]


def test_beaten_but_delivering_requires_floor_and_breadth():
    s = scored_with_cases()
    row = s.loc["ADBX"]
    assert not row["flag_beaten_but_delivering"]  # no eps_rev_proxy in frame -> cannot fire


def test_beaten_but_delivering_fires_and_blocks():
    df = make_frame()
    df["mom_12_1"] = np.linspace(-0.6, 0.5, len(df))   # dispersed momentum
    df["eps_rev_proxy"] = 1.0
    scored = FL.apply_flags(SC.compute_scores(df), gs10=0.03)
    fired = scored[scored["flag_beaten_but_delivering"]]
    assert len(fired) >= 1
    assert (fired["pct_mom_sector"] <= config.MOM_SECTOR_PCT).all()
    assert fired["quality_floor_pass"].all()
    # negative revision breadth never fires (doc 05 §3: breadth > 0)
    df2 = df.copy()
    df2["eps_rev_proxy"] = -1.0
    scored2 = FL.apply_flags(SC.compute_scores(df2), gs10=0.03)
    assert not scored2["flag_beaten_but_delivering"].any()
