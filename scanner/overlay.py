"""Put overlay — doc 07 / doc 08 §4. Display columns + liquidity gate on the flagship
list; never ranks, never scores. Two opposite badges on the same DTE number:
`numbers_stale` (equity view) and `event_window` (put view).
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from . import config, market_data


def build_overlay(flagship: pd.DataFrame, today: dt.date | None = None,
                  rate: float = 0.0) -> pd.DataFrame:
    """Overlay rows for the flagship list. Per-name isolation: one bad options
    chain / earnings lookup degrades that row only (doc 07: the overlay never
    blocks or ranks anything). `rate` (the 10Y, gs10) feeds the assignment
    probability's risk-neutral drift term — same rate used everywhere else in
    the scan, so the overlay isn't silently assuming 0%."""
    today = today or dt.date.today()
    rows = []
    for _, r in flagship.iterrows():
        try:
            rows.append(_overlay_row(r, today, rate))
        except Exception as e:
            rows.append({"ticker": r["ticker"], "price": r.get("price"),
                         "overlay_error": f"{type(e).__name__}: {e}"})
    return pd.DataFrame(rows)


def _overlay_row(r: pd.Series, today: dt.date, rate: float = 0.0) -> dict:
    row = {"ticker": r["ticker"], "price": r["price"]}
    ned = market_data.next_earnings_date(r["ticker"], today)
    dte = (ned - today).days if ned else None
    row["next_earnings"] = ned.isoformat() if ned else None
    row["days_to_earnings"] = dte
    # same threshold, two opposite readings on the same number (doc 04 §4.4):
    # equity view (estimates/filings lag) vs put view (the premium window)
    row["badge_numbers_stale"] = bool(dte is not None and dte < config.OVERLAY_EVENT_WINDOW_DAYS)
    row["badge_event_window"] = row["badge_numbers_stale"]

    # assignment test (doc 06 §7, doc 08 §4): fcf_yield_strike = fcf_adj / EV_strike,
    # EV_strike = strike x basic shares (shares_cover) + the non-equity stack
    # (= EV - mktcap), strike = price x {0.95, 0.90} rounded to cents.
    shares, stack, fcf = r.get("shares_cover"), r.get("stack"), r.get("fcf_adj_ttm")
    usable = (
        shares is not None and pd.notna(shares) and float(shares) > 0
        and stack is not None and pd.notna(stack)
        and fcf is not None and pd.notna(fcf)
    )
    if usable:
        for pct in config.OVERLAY_STRIKES:
            strike = round(float(r["price"]) * pct, 2)
            ev_strike = strike * float(shares) + float(stack)
            row[f"fcf_yield_strike_{round(pct * 100)}"] = (
                float(fcf) / ev_strike if ev_strike > 0 else None
            )

    hv = market_data.hv_30d(r["ticker"])
    row["hv_30d"] = hv
    div_y = market_data.dividend_yield(r["ticker"])
    opt = market_data.options_atm(
        r["ticker"], float(r["price"]), today,
        rate=rate or 0.0, div_yield=div_y,
        assignment_strikes=config.OVERLAY_STRIKES,
    )
    if opt:
        if "iv" in opt:
            row["iv_atm"] = opt["iv"]["iv_atm"]
            row["iv_expiry"] = opt["iv"]["iv_expiry"]
            row["iv_vs_hv"] = (
                opt["iv"]["iv_atm"] / hv - 1.0
                if hv and pd.notna(opt["iv"]["iv_atm"]) else None
            )
        if "gate" in opt:
            row["gate_oi"] = opt["gate"]["gate_oi"]
            row["gate_spread_pct"] = opt["gate"]["gate_spread_pct"]
            row["gate_pass"] = opt["gate"]["gate_pass"]  # doc 07 overlay liquidity gate
        # real assignment probability at the same 95%/90% strikes as the FCF
        # yield test above — each strike's OWN listed IV (skew-aware), not a
        # flat ATM number; the FCF yield answers "is this a price I'd want to
        # own it at", this answers "how likely am I to actually get there".
        for pct_key, a in (opt.get("assignment") or {}).items():
            row[f"assign_iv_{pct_key}"] = a["iv"]
            row[f"prob_assigned_{pct_key}"] = a["prob_assigned"]
    return row
