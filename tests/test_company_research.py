"""Display charts must preserve filing-date visibility and missing-data semantics."""
import datetime as dt
import pandas as pd
from scanner.company_research import research_history, cached_research
from test_features_review import facts_with, qtimeline_rows


def test_research_excludes_future_cashflows_and_balance_sheets():
    facts = facts_with({"cfo": qtimeline_rows()}, {
        "cash": [(dt.date(2024,12,31), 25, dt.date(2025,2,15)),
                 (dt.date(2025,3,31), 99, dt.date(2025,4,25))],
        "debt_noncurrent": [(dt.date(2024,12,31), 60, dt.date(2025,2,15))]})
    flows, balance = research_history(facts, dt.date(2025,3,1))
    assert flows.effective.max() <= pd.Timestamp("2025-03-01")
    assert flows.cfo.dropna().iloc[-1] == 46
    assert balance.cash.iloc[-1] == 25
    assert balance.debt.iloc[-1] == 60


def test_missing_cash_is_not_displayed_as_zero():
    facts = facts_with({}, {"debt_noncurrent": [(dt.date(2024,12,31), 60, dt.date(2025,2,15))]})
    _, balance = research_history(facts, dt.date(2025,3,1))
    assert balance.cash.isna().all()


def test_missing_or_invalid_cache_returns_empty_frames(tmp_path):
    path = tmp_path / "missing.json"
    assert all(f.empty for f in cached_research(str(path), "2025-03-01"))
    path.write_text("invalid")
    assert all(f.empty for f in cached_research(str(path), "2025-03-01"))
