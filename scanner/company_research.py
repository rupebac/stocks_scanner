"""Additional display-only company history from cached SEC facts.

Reuses the scanner's filing-date timeline; never changes eligibility or scores.
No network calls: older scans can gain charts without a full universe refresh.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd

from .features import _ttm_timeline, _stack_steps, _STACK_FIELDS
from .sec_edgar import instants_all


def research_history(facts: dict, asof: dt.date) -> tuple[pd.DataFrame, pd.DataFrame]:
    flows = _ttm_timeline(facts, asof)
    instants = {}
    for field in _STACK_FIELDS:
        values = instants_all(facts, field)
        if not values.empty:
            instants[field] = values[values.filed <= asof]
    balance = _stack_steps(instants)
    if balance is None:
        balance = pd.DataFrame()
    elif not balance.empty:
        balance["debt"] = balance.debt_current + balance.debt_noncurrent
        # Missing cash must remain unknown, not the stack helper's zero fallback.
        if "cash" not in instants or instants["cash"].empty:
            balance["cash"] = float("nan")
        else:
            first_cash = pd.Timestamp(instants["cash"].filed.min())
            balance.loc[balance.effective < first_cash, "cash"] = float("nan")
        if not any(k in instants and not instants[k].empty for k in ["debt_current", "debt_noncurrent", "debt_total_tag"]):
            balance["debt"] = float("nan")
    start = pd.Timestamp(asof) - pd.DateOffset(years=5)
    for frame in (flows, balance):
        if not frame.empty:
            frame["effective"] = pd.to_datetime(frame.effective)
    return (flows[flows.effective >= start].copy() if not flows.empty else flows,
            balance[balance.effective >= start].copy() if not balance.empty else balance)


def cached_research(path: str, asof: str):
    """Read only an explicit local cache; callers own freshness/cache keys."""
    try:
        facts = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return pd.DataFrame(), pd.DataFrame()
    return research_history(facts, dt.date.fromisoformat(asof))
