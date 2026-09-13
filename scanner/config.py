"""Central configuration — every threshold in one place, each with its spec reference.

Sources: docs/specs/02–08 (frozen v0.4 methodology + v0.5 data contract).
Changing a value here is a methodology change: log it in OPEN_QUESTIONS.md.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = DATA / "raw"
DERIVED = DATA / "derived"
REFERENCE = DATA / "reference"
SCANS = DATA / "scans"
for _d in (DATA, RAW, DERIVED, REFERENCE, SCANS):
    _d.mkdir(parents=True, exist_ok=True)

GICS_MAP_FILE = REFERENCE / "gics_subindustry_to_ig.csv"
UNIVERSE_FILE = DERIVED / "universe.csv"
MEMBERSHIP_FILE = DERIVED / "membership_history.csv"
GS10_FILE = RAW / "fred_dgs10.csv"
PRICES_CACHE = RAW / "prices_weekly.parquet"

SEC_HEADERS = {"User-Agent": os.environ.get("SCANNER_UA", "stocks_scanner/0.1 (contact@example.com)")}
SEC_RATE_SLEEP = 0.15  # <= 10 req/s (doc 08 §1)

# --- Universe (doc 01) -------------------------------------------------------
RESTRICTED_SECTORS = {"Financials", "Utilities", "Real Estate"}  # GICS 40/55/60, excluded MVP (Q2)
MIN_INDEX_TENURE_YEARS = 1.0  # doc 05 §1 gate

# --- Hard gates (doc 05 §1) --------------------------------------------------
GATE_ADV_USD = 20_000_000
GATE_MKTCAP_USD = 10_000_000_000
GATE_REV_CAGR5_MIN = 0.0        # growth floor, plain fcf/revenue basis
GATE_FCF_CAGR5_MIN = -0.05      # deceleration-tolerant (doc 03 §3.3)
MIN_SCORE_INPUT_COVERAGE = 0.8  # doc 05 §1: >=80% of MVP inputs else unscored

# --- Quality metrics (doc 03) ------------------------------------------------
ROIC_THRESHOLD = 0.10           # roic_years: years of last 10 with ROIC > 10%
ROIC_YEARS_MIN = 7              # display gate; scored as percentile (consistency)
FCF_POS_YEARS_MIN = 9           # display gate; scored as percentile
TEFF_MIN, TEFF_MAX = 0.0, 0.40  # doc 06 §6 winsorization band
CAGR_BASE_FLOOR = 0.05          # |base| < 5% of current revenue -> null (doc 06 §8)
CAGR5_YEARS_LO = 4.6            # primary 5y-CAGR window
CAGR5_YEARS_HI = 5.5
CAGR5_FALLBACK_YEARS_LO = 3.5   # FCF only: nearest valid year if the 5y base is unusable
GOODWILL_IC_PCT = 0.40          # roic_exgoodwill variant trigger (P1, doc 03 §3.1)

# --- Scoring (doc 05 §2) ------------------------------------------------------
QUALITY_WEIGHTS = {             # doc 05 §2.1 v0.5.9; core sleeves, then × stability
    "profitability": 0.40,
    "growth": 0.30,
    "balance": 0.30,
}
QUALITY_ROIC_CAP = 0.20         # 20% ROIC = 100 on the profitability bar
QUALITY_FCF_MARGIN_CAP = 0.25   # 25% FCF margin = 100
QUALITY_CAGR_CAP = 0.12         # 12% CAGR = 100; faster is not better
QUALITY_ND_EBITDA_ZERO = 3.0    # 3× net debt/EBITDA = 0; ≤0 (net cash) = 100
QUALITY_GM_COV_ZERO = 0.25      # GM CoV 25% → stability sleeve 0
QUALITY_FCF_COV_ZERO = 0.50     # FCF path is lumpier than GM
QUALITY_STABILITY_WEIGHTS = {   # path smoothness; FCF CoV is a minority check
    "revenue": 0.50,            # share of last-10y YoY revenue years that did not fall
    "gm": 0.25,
    "fcf": 0.25,
}
QUALITY_FLOOR_MIN = 60.0        # absolute quality floor (replaces top-20% cut)
QUALITY_FLOOR_ROIC_MIN = ROIC_THRESHOLD  # conservative ROIC when present; missing does not fail
QUALITY_FLOOR_ROIC_MAX = 1.0    # >100% = tiny-IC artifact; computed nonsense fails the floor
CHEAPNESS_WEIGHTS = {           # doc 05 §2.2
    "self_history": 0.40,       # ev_fcf SELF percentile-of-lowness
    "cross_section": 0.30,      # ev_ebit SECT on the peer ladder
    "absolute": 0.30,           # fcf_yield fixed scale
}
QUALITY_FLOOR_QUANTILE = 0.80   # unused since v0.5.8; kept so old scan config.json still loads
LADDER_MIN_NAMES = 8            # industry group -> sector -> market (doc 05 §2)
FCF_YIELD_SCALE_MAX = 0.10      # fixed scale clip(0,10%)/10% (doc 06 §9)

# --- SELF mode (docs 02/06) ---------------------------------------------------
SELF_WINDOW_YEARS = 5
SELF_MIN_HISTORY_YEARS = 3      # < 3y valid observations -> null
SELF_MIN_VALID_FRAC = 0.60      # < 60% of window valid -> null

# --- Flags (doc 05 §3, frozen v0.4) -------------------------------------------
FLAG_SELF_PCT = 20              # arm 2a: ev_fcf SELF <= 20th pct (new lows)
FLAG_SELF_Z = -1.0              # arm 2b: z (median-centered) <= -1.0
FLAG_MEDIAN_RATIO = 0.80        # arm 2c: EV/FCF <= 0.8x 5y median
FLAG_YIELD_FLOOR = 0.04         # arm 3a: fcf_yield >= max(4%, GS10)
FLAG_RESIDUAL_PCT = 20          # arm 3b: residual <= 20th pct among floor passers
FLAG_BRAKE_GM = 0.97            # gm_ttm >= 0.97 x 5y avg
FLAG_BRAKE_FCF_MARGIN = 0.95    # fcf_margin_ttm >= 0.95 x 5y avg
FLAG_BRAKE_ROIC = 0.90          # roic_ttm >= 0.90 x 5y avg
MOM_SECTOR_PCT = 25             # beaten_but_delivering: mom_12_1 <= sector 25th pct

# --- Residual (doc 05 §2.3) ---------------------------------------------------
RESIDUAL_ROIC_FLOOR = 0.01      # log(ROIC) with ROIC floored at 1%
RESIDUAL_PCT = 20               # residual <= 20th pct among passers (flag arm 3b)

# --- Put overlay (docs 07/08 §4) ----------------------------------------------
OVERLAY_STRIKES = (0.95, 0.90)  # strike prices as fraction of spot
OVERLAY_EVENT_WINDOW_DAYS = 10  # event_window / numbers_stale badges
OPTIONS_MIN_DTE = 21            # IV row: nearest expiry >= 21 DTE
OPTIONS_GATE_TARGET_DTE = 35    # liquidity gate: ~30-45 DTE (nearest 35)
OPTIONS_GATE_MIN_OI = 500
OPTIONS_GATE_MAX_SPREAD = 0.10  # (ask-bid)/mid
OVERLAY_TOP_N = 40              # option-chain columns for the top-N quality-floor names (v0.5.4)

# --- MVP score inputs (doc 08 §5, coverage rule) -------------------------------
MVP_SCORE_INPUTS = [
    "roic", "fcf_margin", "gm", "roic_years", "fcf_pos_years",
    "fcf_ni", "nd_ebitda", "ev_ebit", "ev_fcf_self_pct", "fcf_yield",
]
