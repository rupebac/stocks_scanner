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


def _run_app(monkeypatch, sel: str = "ACN", with_calls: bool = True) -> AppTest:
    monkeypatch.setattr(MD, "listed_expiries",
                        lambda ticker, today: [dt.date.fromisoformat(EXPIRY)])
    monkeypatch.setattr(MD, "next_earnings_date", lambda ticker, today: None)

    def fake_chain(ticker, expiry):
        assert expiry.isoformat() == EXPIRY
        calls = _rows("call") if with_calls else []
        return {"puts": pd.DataFrame(_rows("put")),
                "calls": pd.DataFrame(calls)}

    monkeypatch.setattr(MD, "fetch_option_chain", fake_chain)
    st.cache_data.clear()
    at = AppTest.from_file(str(APP_FILE), default_timeout=120)
    at.session_state["idea_pick"] = sel
    at.run()
    return at


def _all_text(at) -> str:
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption]
    return "\n".join(parts).replace("\\$", "$")


def _selected_strike(at) -> float | None:
    for m in at.markdown:
        v = (m.value or "")
        if "**This put**" in v:
            for tok in v.replace(",", "").split():
                if tok.startswith("$") and tok[1:].replace(".", "").isdigit():
                    return float(tok[1:])
    return None


def _metric(at, label: str):
    for m in at.metric:
        if m.label == label:
            return m
    return None


def test_default_side_is_puts_and_block_renders(monkeypatch):
    at = _run_app(monkeypatch)
    text = _all_text(at)
    assert "Sell this put:" in text, "puts must be the landing side"
    assert "not the put you sell first" not in text
    k = _selected_strike(at)
    assert k is not None, "selected put header not found"
    # breakeven / margin needed use the same words as the sentence
    be, margin = _metric(at, "Breakeven"), _metric(at, "Margin needed")
    assert be is not None and margin is not None
    assert be.value == f"${k - _bid(PUT_PREM, k):,.2f}"
    assert margin.value == f"${k * 100:,.0f}"
    # the four-number row is the primary block; no duplicate 'You own it at'
    assert _metric(at, "Yield if you own it there") is not None
    assert _metric(at, "Paid to wait") is not None
    assert _metric(at, "You own it at") is None, "duplicate of Breakeven must not exist"
    # next-step line quotes the same-strike call at its bid
    assert f"sell the **${k:,.0f} call** for **${_bid(CALL_PREM, k):,.2f}**" \
        in text.replace(",", "")


def test_reselecting_a_put_row_updates_the_block(monkeypatch):
    at = _run_app(monkeypatch)
    k1 = _selected_strike(at)
    # moving the strike target re-seeds the suggested row — the same code path
    # a chain-table click lands on (view → picked_i → decision block)
    at.number_input("opt_target_ACN").set_value(90.0)
    at.run()
    k2 = _selected_strike(at)
    assert k2 is not None and k2 != k1, "reselected row must be a different strike"
    be, margin = _metric(at, "Breakeven"), _metric(at, "Margin needed")
    assert be.value == f"${k2 - _bid(PUT_PREM, k2):,.2f}"
    assert margin.value == f"${k2 * 100:,.0f}"
    text = _all_text(at)
    assert f"sell the **${k2:,.0f} call**" in text.replace(",", "")
    # called-away keep = (strike − breakeven) + call premium, spelled out
    assert "strike − breakeven" in text


def test_missing_same_strike_call_says_just_hold(monkeypatch):
    at = _run_app(monkeypatch, with_calls=False)
    assert "No call at this strike — if assigned, just hold." in _all_text(at)


def test_calls_side_shows_no_csp_block(monkeypatch):
    at = _run_app(monkeypatch)
    at.session_state["opt_side_ACN"] = "Calls"
    at.run()
    text = _all_text(at)
    assert "not the put you sell first" in text
    assert "Sell this put:" not in text
    assert _metric(at, "Margin needed") is None


def test_highlights_and_funnel_unchanged(monkeypatch):
    at = _run_app(monkeypatch)
    # the page still leads with the scan's own counts; options never touch them
    assert _metric(at, "Names in the highlights") is not None
    assert _metric(at, "Data as of") is not None
    assert "Sell this put:" in _all_text(at)
