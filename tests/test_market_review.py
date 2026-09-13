"""Unit tests for scanner/market_data.py + scanner/overlay.py (review fixes).

Everything here is synthetic pandas frames / fake yfinance objects — no network.
Pins: single-ticker download shapes, price_features index math (doc 04 §4.1),
options gate arithmetic (doc 08 §4), overlay strike-yield formula (doc 06 §7).
"""
from __future__ import annotations

import datetime as dt
import types

import numpy as np
import pandas as pd
import pytest

from scanner import config, market_data as md, overlay as ov

TODAY = dt.date(2026, 9, 11)


# ---------------------------------------------------------------- fakes -------
class _FakeDownloadYf:
    """Stands in for the yfinance module for download_weekly."""

    def __init__(self, result=None, exc=None):
        self._result, self._exc = result, exc
        self.calls = []

    def download(self, tickers, **kw):
        self.calls.append((list(tickers), kw))
        if self._exc is not None:
            raise self._exc
        return self._result() if callable(self._result) else self._result


class _FakeTickerYf:
    """Stands in for the yfinance module for Ticker-based calls."""

    def __init__(self, history=None, history_exc=None, earnings=None, earnings_exc=None,
                 options=(), chains=None, chain_exc_expiries=()):
        self._args = {k: v for k, v in locals().items() if k != "self"}
        self.tickers = []

    def Ticker(self, ticker):
        self.tickers.append(ticker)
        a = self._args
        return types.SimpleNamespace(
            history=self._history, get_earnings_dates=self._earnings,
            options=a["options"], option_chain=self._chain,
        )

    def _history(self, **kw):
        self.hist_kwargs = kw
        a = self._args
        if a["history_exc"]:
            raise a["history_exc"]
        return a["history"]

    def _earnings(self, limit=None):
        a = self._args
        if a["earnings_exc"]:
            raise a["earnings_exc"]
        return a["earnings"]

    def _chain(self, expiry):
        a = self._args
        if expiry in a["chain_exc_expiries"]:
            raise RuntimeError("chain blowup")
        return a["chains"][expiry]


def _no_sleep(monkeypatch, module):
    sleeps = []
    monkeypatch.setattr(module, "time", types.SimpleNamespace(sleep=sleeps.append))
    return sleeps


# ------------------------------------------------------- download_weekly -----
def _flat_weekly(n=6, start="2025-01-03", tz=None):
    idx = pd.date_range(start, periods=n, freq="7D")
    if tz:
        idx = idx.tz_localize(tz)
    return pd.DataFrame(
        {"Open": 1.0, "High": 2.0, "Low": 0.5,
         "Close": [10.0 + i for i in range(n)], "Volume": [100.0 + i for i in range(n)]},
        index=idx,
    )


def test_download_weekly_single_ticker_flat_columns(monkeypatch):
    """Flat field-only columns (single-ticker download on several yfinance
    versions): the ticker must NOT be silently dropped."""
    sleeps = _no_sleep(monkeypatch, md)
    fake = _FakeDownloadYf(result=_flat_weekly(6))
    monkeypatch.setattr(md, "yf", fake)
    out = md.download_weekly(["ADBE"])
    assert len(out) == 6 and out["ticker"].eq("ADBE").all()
    assert out["close"].tolist() == [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]
    assert out["volume"].tolist() == [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]
    assert out["week"].tolist() == pd.date_range("2025-01-03", periods=6, freq="7D").tolist()
    assert fake.calls[0][1]["auto_adjust"] is True  # total-return basis (doc 04 §4.1)
    assert sleeps  # politeness sleep after the chunk


def test_download_weekly_single_ticker_multiindex_columns(monkeypatch):
    flat = _flat_weekly(6)
    flat.columns = pd.MultiIndex.from_product([["ADBE"], flat.columns])
    monkeypatch.setattr(md, "yf", _FakeDownloadYf(result=flat))
    _no_sleep(monkeypatch, md)
    out = md.download_weekly(["ADBE"])
    assert len(out) == 6 and out["ticker"].eq("ADBE").all()


def test_download_weekly_multi_ticker_partial_and_dedupe(monkeypatch):
    idx = pd.date_range("2025-01-03", periods=6, freq="7D")
    dup_idx = idx.append(pd.DatetimeIndex([idx[-1]]))  # repeated week
    cols = pd.MultiIndex.from_product([["AAA", "BBB"], ["Close", "Volume"]])
    raw = pd.DataFrame(
        {("AAA", "Close"): [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 6.5],
         ("AAA", "Volume"): [10.0] * 6 + [99.0],
         ("BBB", "Close"): [np.nan] * 7,   # all-NaN ticker: dropped
         ("BBB", "Volume"): [1.0] * 7},
        index=dup_idx, columns=cols,
    )
    monkeypatch.setattr(md, "yf", _FakeDownloadYf(result=raw))
    _no_sleep(monkeypatch, md)
    out = md.download_weekly(["AAA", "BBB"])
    assert out["ticker"].unique().tolist() == ["AAA"]
    assert len(out) == 6                       # repeated week deduped
    assert out["volume"].iloc[-1] == 99.0      # dedupe keeps the later row


def test_download_weekly_tz_aware_index_stripped(monkeypatch):
    monkeypatch.setattr(md, "yf", _FakeDownloadYf(result=_flat_weekly(4, tz="America/New_York")))
    _no_sleep(monkeypatch, md)
    out = md.download_weekly(["ADBE"])
    assert str(out["week"].dtype).startswith("datetime64") and "tz" not in str(out["week"].dtype)
    assert out["week"].iloc[0] == pd.Timestamp("2025-01-03")


def test_download_weekly_flat_frame_multi_ticker_chunk_not_attributed(monkeypatch):
    monkeypatch.setattr(md, "yf", _FakeDownloadYf(result=_flat_weekly(4)))
    _no_sleep(monkeypatch, md)
    out = md.download_weekly(["AAA", "BBB"])
    assert out.empty and out.columns.tolist() == ["ticker", "week", "close", "volume"]


def test_download_weekly_chunk_failure_logged_not_silent(monkeypatch, capsys):
    sleeps = _no_sleep(monkeypatch, md)
    monkeypatch.setattr(md, "yf", _FakeDownloadYf(exc=RuntimeError("net down")))
    out = md.download_weekly(["ADBE", "MSFT"])
    assert out.empty and out.columns.tolist() == ["ticker", "week", "close", "volume"]
    assert "[market]" in capsys.readouterr().out
    # backoff between attempts + politeness after the failed chunk; no pointless
    # sleep after the final attempt
    assert sleeps == [2, 2, 0.5]


def test_download_weekly_retries_on_empty_result(monkeypatch):
    _no_sleep(monkeypatch, md)
    state = {"n": 0}

    def result():
        state["n"] += 1
        return pd.DataFrame() if state["n"] == 1 else _flat_weekly(4)

    fake = _FakeDownloadYf(result=result)
    monkeypatch.setattr(md, "yf", fake)
    out = md.download_weekly(["ADBE"])
    assert len(fake.calls) == 2 and len(out) == 4


def test_download_weekly_politeness_between_chunks(monkeypatch):
    sleeps = _no_sleep(monkeypatch, md)
    monkeypatch.setattr(md, "_CHUNK", 1)
    monkeypatch.setattr(md, "yf", _FakeDownloadYf(result=_flat_weekly(3)))
    md.download_weekly(["AAA", "BBB"])
    assert sleeps == [0.5, 0.5]


def test_download_weekly_empty_input():
    out = md.download_weekly([])
    assert out.empty and out.columns.tolist() == ["ticker", "week", "close", "volume"]


# -------------------------------------------------------- price_features -----
def _closes():
    # 60 weekly closes: rise 100->198 over 49 weeks, then slide to 188
    return [100.0 + 2.0 * i for i in range(50)] + [198.0 - (i - 49) for i in range(50, 60)]


def _weekly_df(closes, volume=1_000_000.0, start="2025-08-01"):
    return pd.DataFrame({
        "ticker": "TEST",
        "week": pd.date_range(start, periods=len(closes), freq="7D"),
        "close": closes,
        "volume": [volume] * len(closes),
    })


def test_price_features_index_math():
    out = md.price_features(_weekly_df(_closes())).set_index("ticker")
    r = out.loc["TEST"]
    closes = _closes()
    # mom_12_1 = P(t-4w)/P(t-52w) - 1: iloc[-5] (=index 55) over iloc[-53] (=index 7)
    assert closes[55] == 192.0 and closes[7] == 114.0
    assert r["mom_12_1"] == pytest.approx(192.0 / 114.0 - 1.0)
    assert r["ret_12m"] == pytest.approx(closes[59] / closes[7] - 1.0)
    # dd_52w vs the 52w high (198, hit at week 49)
    assert r["hi_52w"] == 198.0
    assert r["dd_52w"] == pytest.approx(188.0 / 198.0 - 1.0)
    assert r["price"] == 188.0
    # adv_usd: each week's volume valued at that week's close (honest dollar
    # volume), last 13 weeks, /5 trading days
    last13 = closes[-13:]
    assert sum(last13) == 2513.0
    assert r["adv_usd"] == pytest.approx(1_000_000.0 * sum(last13) / 13.0 / 5.0)
    assert r["adv_usd"] != pytest.approx(1_000_000.0 * 188.0 / 5.0)  # not last-close pairing


def test_price_features_short_history_dropped():
    weekly = pd.concat([_weekly_df(_closes()), _weekly_df([100.0] * 59)], ignore_index=True)
    weekly.loc[weekly.index[-59:], "ticker"] = "SHORT"
    out = md.price_features(weekly)
    assert out["ticker"].tolist() == ["TEST"]


def test_price_features_rolling_min_periods_40():
    closes = _closes()
    closes[57] = np.nan  # gap inside the 52w window: 51 valid >= 40 -> hi survives
    r = md.price_features(_weekly_df(closes)).set_index("ticker").loc["TEST"]
    assert r["hi_52w"] == 198.0 and r["dd_52w"] == pytest.approx(188.0 / 198.0 - 1.0)


def test_price_features_empty_and_missing_columns():
    out = md.price_features(pd.DataFrame(columns=["ticker", "week", "close", "volume"]))
    assert out.empty
    assert out.columns.tolist() == ["ticker", "price", "mom_12_1", "dd_52w",
                                    "ret_12m", "adv_usd", "hi_52w"]


# ---------------------------------------------------------------- hv_30d -----
def test_hv_30d_math_and_auto_adjust(monkeypatch):
    eps = [0.01 if i % 2 == 0 else -0.01 for i in range(44)]
    closes = 100.0 * np.exp(np.cumsum([0.0] + eps))
    df = pd.DataFrame({"Close": closes}, index=pd.date_range("2026-05-01", periods=45, freq="1D"))
    fake = _FakeTickerYf(history=df)
    monkeypatch.setattr(md, "yf", fake)
    got = md.hv_30d("ADBE")
    expected = float(np.std(eps[-30:], ddof=1) * np.sqrt(252))
    assert got == pytest.approx(expected)
    assert fake._args  # sanity
    assert fake.hist_kwargs.get("auto_adjust") is True


def test_hv_30d_nonpositive_close_not_inf(monkeypatch):
    closes = [100.0 * np.exp(np.cumsum([0.0] + [0.01 if i % 2 == 0 else -0.01 for i in range(43)])[j])
              for j in range(44)] + [0.0]  # one dead close
    df = pd.DataFrame({"Close": closes}, index=pd.date_range("2026-05-01", periods=45, freq="1D"))
    monkeypatch.setattr(md, "yf", _FakeTickerYf(history=df))
    got = md.hv_30d("ADBE")
    assert got is not None and np.isfinite(got)


def test_hv_30d_failures_return_none(monkeypatch):
    short = pd.DataFrame({"Close": [1.0] * 10}, index=pd.date_range("2026-08-01", periods=10))
    monkeypatch.setattr(md, "yf", _FakeTickerYf(history=short))
    assert md.hv_30d("ADBE") is None
    monkeypatch.setattr(md, "yf", _FakeTickerYf(history_exc=RuntimeError("boom")))
    assert md.hv_30d("ADBE") is None


# ------------------------------------------------------ next_earnings_date ---
def _earnings_df(days):
    idx = pd.DatetimeIndex([dt.datetime.combine(TODAY + dt.timedelta(d), dt.time(12))
                            for d in days]).tz_localize("UTC")
    return pd.DataFrame({"Surprise": [0.0] * len(idx)}, index=idx)


def test_next_earnings_date_picks_nearest_future_as_date(monkeypatch):
    monkeypatch.setattr(md, "yf", _FakeTickerYf(earnings=_earnings_df([-40, -10, 5, 35])))
    got = md.next_earnings_date("ADBE")
    assert got == TODAY + dt.timedelta(5)
    assert type(got) is dt.date  # a date, never a Timestamp


def test_next_earnings_date_all_past_or_failures(monkeypatch):
    monkeypatch.setattr(md, "yf", _FakeTickerYf(earnings=_earnings_df([-40, -10])))
    assert md.next_earnings_date("ADBE") is None
    monkeypatch.setattr(md, "yf", _FakeTickerYf(earnings=pd.DataFrame()))
    assert md.next_earnings_date("ADBE") is None
    monkeypatch.setattr(md, "yf", _FakeTickerYf(earnings_exc=RuntimeError("boom")))
    assert md.next_earnings_date("ADBE") is None


# ------------------------------------------------------------ options_atm ----
def _chain(iv=0.30, call_iv=0.40, bid=4.9, ask=5.1, oi=5000.0, nan_iv=False):
    return types.SimpleNamespace(
        puts=pd.DataFrame({
            "strike": [90.0, 95.0, 100.0, 105.0],
            "impliedVolatility": [np.nan if nan_iv else iv] * 4,
            "bid": [bid] * 4, "ask": [ask] * 4, "openInterest": [oi] * 4,
        }),
        calls=pd.DataFrame({
            "strike": [90.0, 95.0, 100.0, 105.0],
            "impliedVolatility": [call_iv] * 4,
        }),
    )


def _options_fake(chains=None, **kw):
    days = kw.pop("days", [10, 25, 35, 60])
    expiries = [(TODAY + dt.timedelta(d)).isoformat() for d in days]
    chains = chains or {e: _chain() for e in expiries}
    return _FakeTickerYf(options=expiries, chains=chains, **kw)


def test_options_atm_iv_row_selection_and_averaging(monkeypatch):
    monkeypatch.setattr(md, "yf", _options_fake())
    got = md.options_atm("ADBE", 100.0, TODAY)
    # IV row: nearest expiry >= 21 DTE (25, not 10), strike closest to spot
    assert got["iv"]["iv_expiry"] == (TODAY + dt.timedelta(25)).isoformat()
    assert got["iv"]["iv_strike"] == 100.0
    assert got["iv"]["iv_atm"] == pytest.approx((0.30 + 0.40) / 2)
    assert got["iv"]["iv_dte"] == 25
    # gate: nearest-to-35 DTE expiry, ATM put OI/spread
    assert got["gate"]["gate_expiry"] == (TODAY + dt.timedelta(35)).isoformat()
    assert got["gate"]["gate_oi"] == 5000.0
    assert got["gate"]["gate_spread_pct"] == pytest.approx(0.2 / 5.0)
    assert got["gate"]["gate_pass"] is True


def test_options_atm_gate_fail_modes(monkeypatch):
    expiries = [(TODAY + dt.timedelta(35)).isoformat()]
    # OI below 500
    fake = _FakeTickerYf(options=expiries, chains={expiries[0]: _chain(oi=499.0)})
    monkeypatch.setattr(md, "yf", fake)
    assert md.options_atm("ADBE", 100.0, TODAY)["gate"]["gate_pass"] is False
    # spread above 10%
    fake = _FakeTickerYf(options=expiries, chains={expiries[0]: _chain(bid=4.0, ask=5.0)})
    monkeypatch.setattr(md, "yf", fake)
    g = md.options_atm("ADBE", 100.0, TODAY)["gate"]
    assert g["gate_pass"] is False and g["gate_spread_pct"] == pytest.approx(1.0 / 4.5)
    # bid = ask = 0: mid = 0 -> no spread value, no division error, gate fails
    fake = _FakeTickerYf(options=expiries, chains={expiries[0]: _chain(bid=0.0, ask=0.0)})
    monkeypatch.setattr(md, "yf", fake)
    g = md.options_atm("ADBE", 100.0, TODAY)["gate"]
    assert g["gate_spread_pct"] is None and g["gate_pass"] is False
    # locked market bid == ask > 0: spread 0 passes with sufficient OI
    fake = _FakeTickerYf(options=expiries, chains={expiries[0]: _chain(bid=5.0, ask=5.0, oi=600.0)})
    monkeypatch.setattr(md, "yf", fake)
    assert md.options_atm("ADBE", 100.0, TODAY)["gate"]["gate_pass"] is True


def test_options_atm_iv_nan_legs(monkeypatch):
    expiries = [(TODAY + dt.timedelta(25)).isoformat(), (TODAY + dt.timedelta(35)).isoformat()]
    # put IV NaN, call IV fine -> mean of the available side
    chains = {expiries[0]: _chain(nan_iv=True), expiries[1]: _chain()}
    monkeypatch.setattr(md, "yf", _FakeTickerYf(options=expiries, chains=chains))
    got = md.options_atm("ADBE", 100.0, TODAY)
    assert got["iv"]["iv_atm"] == pytest.approx(0.40)


def test_options_atm_independent_legs(monkeypatch):
    d25, d35 = (TODAY + dt.timedelta(25)).isoformat(), (TODAY + dt.timedelta(35)).isoformat()
    chains = {d25: _chain(), d35: _chain()}
    # the ~35 DTE chain blowing up must not kill the IV row (and vice versa)
    fake = _FakeTickerYf(options=[d25, d35], chains=chains, chain_exc_expiries=(d35,))
    monkeypatch.setattr(md, "yf", fake)
    got = md.options_atm("ADBE", 100.0, TODAY)
    assert "iv" in got and "gate" not in got
    fake = _FakeTickerYf(options=[d25, d35], chains=chains, chain_exc_expiries=(d25,))
    monkeypatch.setattr(md, "yf", fake)
    got = md.options_atm("ADBE", 100.0, TODAY)
    assert "gate" in got and "iv" not in got


def test_options_atm_none_paths(monkeypatch):
    monkeypatch.setattr(md, "yf", _FakeTickerYf(options=()))
    assert md.options_atm("ADBE", 100.0, TODAY) is None          # no expiries
    monkeypatch.setattr(md, "yf", _options_fake(days=[10]))      # all < 21 DTE
    assert md.options_atm("ADBE", 100.0, TODAY) is None
    assert md.options_atm("ADBE", float("nan"), TODAY) is None   # bad spot
    assert md.options_atm("ADBE", 0.0, TODAY) is None


# ---------------------------------------------------------------- overlay ----
def _flagship(**over):
    row = {"ticker": "TEST", "price": 100.0, "shares_cover": 1e9,
           "stack": 5e9, "fcf_adj_ttm": 4e9}
    row.update(over)
    return pd.DataFrame([row])


def _patch_market(monkeypatch, ned=dt.date(2026, 9, 18), hv=0.25,
                  opt=None, opt_exc_for=()):
    opt = opt if opt is not None else {
        "iv": {"iv_atm": 0.30, "iv_expiry": "2026-10-16"},
        "gate": {"gate_oi": 5000.0, "gate_spread_pct": 0.04, "gate_pass": True},
    }

    def fake_ned(t, today=None):
        return ned

    def fake_opt(t, spot, today=None, **kwargs):
        if t in opt_exc_for:
            raise RuntimeError("chain blowup")
        return opt

    monkeypatch.setattr(md, "next_earnings_date", fake_ned)
    monkeypatch.setattr(md, "hv_30d", lambda t: hv)
    monkeypatch.setattr(md, "options_atm", fake_opt)
    monkeypatch.setattr(md, "dividend_yield", lambda t: 0.0)


def test_overlay_strike_yield_math_and_columns(monkeypatch):
    _patch_market(monkeypatch)
    out = ov.build_overlay(_flagship(), TODAY)
    r = out.iloc[0]
    # doc 06 §7: EV_strike = strike x basic shares + non-equity stack (EV - mktcap)
    for pct in config.OVERLAY_STRIKES:
        strike = round(100.0 * pct, 2)
        col = f"fcf_yield_strike_{round(pct * 100)}"
        assert r[col] == pytest.approx(4e9 / (strike * 1e9 + 5e9)), col
    assert r["fcf_yield_strike_95"] == pytest.approx(0.04)
    assert r["fcf_yield_strike_90"] == pytest.approx(4.0 / 95.0)
    # badges: same threshold, separate columns, both firing at dte=7 (<10)
    assert r["days_to_earnings"] == 7
    assert bool(r["badge_numbers_stale"]) and bool(r["badge_event_window"])
    assert isinstance(r["badge_numbers_stale"], (bool, np.bool_))
    # iv/hv display + gate propagation
    assert r["hv_30d"] == 0.25
    assert r["iv_atm"] == 0.30 and r["iv_expiry"] == "2026-10-16"
    assert r["iv_vs_hv"] == pytest.approx(0.30 / 0.25 - 1.0)
    assert r["gate_oi"] == 5000.0 and bool(r["gate_pass"])


def test_overlay_badge_thresholds_and_null_earnings(monkeypatch):
    _patch_market(monkeypatch, ned=TODAY + dt.timedelta(10))  # dte=10: not < 10
    out = ov.build_overlay(_flagship(), TODAY)
    assert not out.iloc[0]["badge_numbers_stale"]
    assert not out.iloc[0]["badge_event_window"]
    _patch_market(monkeypatch, ned=None)  # unknown -> badge suppressed (doc 08 §1)
    out = ov.build_overlay(_flagship(), TODAY)
    r = out.iloc[0]
    assert r["days_to_earnings"] is None
    assert not r["badge_numbers_stale"] and not r["badge_event_window"]


def test_overlay_zero_and_missing_fcf_inputs(monkeypatch):
    _patch_market(monkeypatch)
    # fcf_adj_ttm == 0.0 is a real value, not "missing": yields must exist
    out = ov.build_overlay(_flagship(fcf_adj_ttm=0.0), TODAY)
    assert out["fcf_yield_strike_95"].iloc[0] == 0.0
    # missing/NaN fundamentals -> no yield columns, no crash
    for bad in ({"shares_cover": np.nan}, {"stack": np.nan}, {"fcf_adj_ttm": np.nan}):
        df = _flagship(**bad)
        out = ov.build_overlay(df, TODAY)
        assert "fcf_yield_strike_95" not in out.columns or pd.isna(out["fcf_yield_strike_95"].iloc[0])


def test_overlay_iv_vs_hv_none_safety(monkeypatch):
    _patch_market(monkeypatch, hv=None)  # hv unknown -> iv_vs_hv None, no crash
    out = ov.build_overlay(_flagship(), TODAY)
    assert pd.isna(out["iv_vs_hv"].iloc[0])
    _patch_market(monkeypatch, hv=0.0)
    out = ov.build_overlay(_flagship(), TODAY)
    assert pd.isna(out["iv_vs_hv"].iloc[0])


def test_overlay_per_name_isolation(monkeypatch):
    flagship = pd.concat([
        _flagship(),
        _flagship(ticker="BAD", price=50.0),
        _flagship(ticker="NOFUND", shares_cover=np.nan, stack=np.nan, fcf_adj_ttm=np.nan),
    ], ignore_index=True)
    _patch_market(monkeypatch, opt_exc_for=("BAD",))
    out = ov.build_overlay(flagship, TODAY)
    assert out["ticker"].tolist() == ["TEST", "BAD", "NOFUND"]
    assert len(out) == 3                                   # one bad chain killed nothing
    assert "overlay_error" in out.columns
    assert out[out["ticker"] == "BAD"]["overlay_error"].notna().iloc[0]
    good = out[out["ticker"] == "TEST"].iloc[0]
    assert bool(good["gate_pass"]) and good["fcf_yield_strike_95"] == pytest.approx(0.04)
    nofund = out[out["ticker"] == "NOFUND"].iloc[0]
    assert "fcf_yield_strike_95" not in out.columns or pd.isna(nofund.get("fcf_yield_strike_95"))


def test_overlay_assignment_probability_columns(monkeypatch):
    """Real Prob-OTM at the 95/90 strikes rides alongside the FCF yield test,
    each using its own strike's IV — not present unless options_atm returns
    an "assignment" block (best-effort: absent chain -> no columns, no crash)."""
    opt = {
        "iv": {"iv_atm": 0.30, "iv_expiry": "2026-10-16"},
        "gate": {"gate_oi": 5000.0, "gate_spread_pct": 0.04, "gate_pass": True},
        "assignment": {
            "95": {"strike": 95.0, "iv": 0.32, "prob_otm": 0.71, "prob_assigned": 0.29},
            "90": {"strike": 90.0, "iv": 0.35, "prob_otm": 0.80, "prob_assigned": 0.20},
        },
    }
    _patch_market(monkeypatch, opt=opt)
    out = ov.build_overlay(_flagship(), TODAY)
    r = out.iloc[0]
    assert r["prob_assigned_95"] == pytest.approx(0.29)
    assert r["prob_assigned_90"] == pytest.approx(0.20)
    assert r["assign_iv_95"] == pytest.approx(0.32) and r["assign_iv_90"] == pytest.approx(0.35)

    # no "assignment" key at all (e.g. nothing listed in the 30-45 DTE window) -> no crash, no columns
    _patch_market(monkeypatch, opt={"iv": opt["iv"], "gate": opt["gate"]})
    out = ov.build_overlay(_flagship(), TODAY)
    assert "prob_assigned_95" not in out.columns
