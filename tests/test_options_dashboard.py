"""Dashboard-level tests for the Options tab wheel block (AppTest, mock chains,
no network). The app script is executed fresh, so the yfinance-touching
delegates (listed_expiries / fetch_option_chain / next_earnings_date) are
patched on the shared scanner.market_data module that app.py imports.
st.cache_data caches live for the whole process, so they are cleared per test —
otherwise one test's mocked chain leaks into the next."""

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from scanner import market_data as MD

APP_FILE = Path(__file__).resolve().parents[1] / "dashboard" / "app.py"
EXPIRY = (dt.date.today() + dt.timedelta(days=32)).isoformat()

# fixed mock premiums per strike; the app keeps the BID when bid > 0
PUT_PREM = {140: 0.20, 145: 0.35, 150: 0.55, 155: 0.80, 160: 1.50, 165: 2.90,
            170: 4.40, 175: 7.80, 180: 12.00, 185: 17.50, 190: 24.00, 195: 32.00,
            200: 41.00, 205: 52.00, 210: 64.00, 215: 77.00, 220: 92.00}
CALL_PREM = {k: round(max(1.0, 5.0 + (180 - k) * 0.6), 2) for k in PUT_PREM}


def _bid(table: dict, k: float) -> float:
    return round(table[round(k)] * 0.98, 2)


def _rows(side: str) -> list[dict]:
    table = PUT_PREM if side == "put" else CALL_PREM
    out = []
    for k, p in table.items():
        bid = round(p * 0.98, 2)
        out.append({
            "side": side, "strike": float(k), "bid": bid, "ask": round(p * 1.04, 2),
            "last": bid, "volume": 10, "oi": 50, "iv": 0.32,
            "in_the_money": (k >= 180) if side == "put" else (k <= 180),
        })
    return out


def _run_app(monkeypatch, sel="ACN", with_calls=True, zero_bid=False):
    monkeypatch.setattr(MD, "listed_expiries", lambda ticker, today, max_dte=90: [dt.date.fromisoformat(EXPIRY)])
    monkeypatch.setattr(MD, "next_earnings_date", lambda ticker, today: None)
    def fake_chain(ticker, expiry):
        puts = pd.DataFrame(_rows("put"))
        if zero_bid:
            puts["bid"] = 0.
        return {"puts": puts, "calls": pd.DataFrame(_rows("call") if with_calls else []),
                "underlying_price": 180., "underlying_time": "2026-09-14T15:30:00+00:00"}
    monkeypatch.setattr(MD, "fetch_option_chain", fake_chain)
    st.cache_data.clear()
    at = AppTest.from_file(str(APP_FILE), default_timeout=120)
    at.session_state["idea_pick"] = sel
    at.session_state["workspace"] = "Company"
    at.session_state["company_tab"] = "Put & call income"
    at.run()
    assert not at.exception
    return at


def metric(at, label):
    return next((m.value for m in at.metric if m.label == label), None)


def selected_strike(at):
    return next(s.value for s in at.selectbox if s.label == "Buy at this price")


def test_put_cash_and_income_use_bid(monkeypatch):
    at = _run_app(monkeypatch)
    k = selected_strike(at)
    assert metric(at, "Net cost if assigned") == f"${k-_bid(PUT_PREM,k):,.2f}"
    assert metric(at, "Cash to set aside") == f"${k*100:,.0f}"
    assert metric(at, "Premium · 1 contract") == f"${_bid(PUT_PREM,k)*100:,.0f}"
    assert metric(at, "Return on committed cash") == f"{_bid(PUT_PREM,k)/k:.2%}"


def test_changing_entry_and_selecting_strike_updates_income(monkeypatch):
    at = _run_app(monkeypatch)
    k = selected_strike(at)
    at.number_input("opt_target_ACN").set_value(90.).run()
    assert selected_strike(at) != k
    next(s for s in at.selectbox if s.label == "Buy at this price").select(180.).run()
    assert not at.exception
    assert metric(at, "Net cost if assigned") == f"${180-_bid(PUT_PREM,180):,.2f}"


def test_missing_call_is_honest_about_future_premium(monkeypatch):
    at = _run_app(monkeypatch, with_calls=False)
    assert any("No positive same-strike" in m.value for m in at.markdown)
    assert any("different expiration and premium" in c.value for c in at.caption)


def test_no_bid_never_uses_last_trade_as_income(monkeypatch):
    at = _run_app(monkeypatch, zero_bid=True)
    assert metric(at, "Premium · 1 contract") == "—"
    assert metric(at, "Net cost if assigned") == "—"
    assert any("No positive bid" in w.value for w in at.warning)


def test_covered_call_uses_original_cost(monkeypatch):
    at = _run_app(monkeypatch)
    at.selectbox("side_ACN").select("Sell a covered call").run()
    at.number_input("cost_ACN").set_value(200.).run()
    assert not at.exception
    k = next(s.value for s in at.selectbox if s.label == "Sell shares at this price")
    assert metric(at, "Cash to set aside") is None
    assert metric(at, "Shares required") == "100"
    assert metric(at, "Share gain / loss + this premium if called away") == f"${(k-200+_bid(CALL_PREM,k))*100:,.2f}"


def test_search_unscored_company_and_return_to_discovery(monkeypatch):
    at = _run_app(monkeypatch)
    at.radio("workspace").set_value("Discover").run()
    assert not at.exception
    at.selectbox("company_search").select("ORCL").run()
    assert not at.exception
    assert at.session_state["idea_pick"] == "ORCL"
    assert at.session_state["workspace"] == "Company"
    at.radio("workspace").set_value("Discover").run()
    assert not at.exception
    assert metric(at, "Quality & value shortlist") is not None


def test_cards_navigate_without_network(monkeypatch):
    monkeypatch.setattr(MD, "listed_expiries", lambda *a, **k: pytest.fail("Discovery must not load options"))
    at = AppTest.from_file(str(APP_FILE), default_timeout=120).run()
    next(b for b in at.button if b.key and b.key.startswith("card_")).click().run()
    assert not at.exception
    assert at.session_state["workspace"] == "Company"
    assert metric(at, "Reported free cash flow") is not None


def test_income_comparison_shows_actual_bid_returns(monkeypatch):
    at = _run_app(monkeypatch)
    at.radio("workspace").set_value("Discover").run()
    next(b for b in at.button if b.label == "Compare put income").click().run()
    assert not at.exception
    results = at.session_state["income_results"][1]
    assert len(results) == 3
    for r in results:
        assert r["Return on cash"] == pytest.approx(_bid(PUT_PREM,r["Strike"])/r["Strike"])
        assert r["Cash required"] == r["Strike"]*100


def test_expiry_window_includes_90_days_excludes_later(monkeypatch):
    class Ticker:
        def __init__(self, ticker): pass
        options = tuple((dt.date.today()+dt.timedelta(days=d)).isoformat() for d in [0,60,90,91])
    monkeypatch.setattr(MD.yf, "Ticker", Ticker)
    out = MD.listed_expiries("X", dt.date.today(), max_dte=90)
    assert [(d-dt.date.today()).days for d in out] == [0,60,90]
