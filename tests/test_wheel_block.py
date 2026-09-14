"""Wheel decision block (Ideas → Options, selected put): pure helpers only.
Plain-English numbers for the selected contract — breakeven, margin needed,
FCF yield at the breakeven price, good-to-own verdict, wait return with the
annualize-or-not rule, same-strike call lookup, and called-away keep.
No network here; chains are passed in as frames."""

import pandas as pd
import pytest

from scanner import market_data as MD


class TestBreakeven:
    def test_strike_minus_premium(self):
        assert MD.breakeven_price(175.0, 7.80) == pytest.approx(167.20)

    def test_missing_premium_means_no_breakeven(self):
        assert MD.breakeven_price(175.0, None) is None

    def test_nan_inputs_are_none(self):
        assert MD.breakeven_price(float("nan"), 1.0) is None
        assert MD.breakeven_price(175.0, float("nan")) is None

    def test_legacy_alias(self):
        assert MD.net_basis(175.0, 7.80) == pytest.approx(167.20)


class TestMarginNeeded:
    def test_one_contract_is_100_shares(self):
        assert MD.margin_needed(175.0) == pytest.approx(17_500.0)

    def test_contracts_scale(self):
        assert MD.margin_needed(95.0, contracts=3) == pytest.approx(28_500.0)

    def test_none_strike(self):
        assert MD.margin_needed(None) is None


class TestFcfYieldAtPrice:
    # fcf / (price * shares + stack) — the assignment test prices the equity
    # at the BREAKEVEN, not the strike: the put premium lowers the cost.
    def test_math_at_breakeven(self):
        y = MD.fcf_yield_at_price(10e9, 2.8e9, 30e9, 167.20)
        assert y == pytest.approx(10e9 / (167.20 * 2.8e9 + 30e9))

    def test_yield_is_higher_at_breakeven_than_at_strike(self):
        f, s, d, k, prem = 10e9, 2.8e9, 30e9, 175.0, 7.80
        basis = MD.breakeven_price(k, prem)
        assert MD.fcf_yield_at_price(f, s, d, basis) > MD.fcf_yield_at_price(f, s, d, k)

    def test_zero_ev_is_none(self):
        assert MD.fcf_yield_at_price(10e9, 2.8e9, -167.2 * 2.8e9, 167.2) is None

    def test_missing_inputs_are_none(self):
        assert MD.fcf_yield_at_price(None, 2.8e9, 30e9, 167.2) is None
        assert MD.fcf_yield_at_price(10e9, float("nan"), 30e9, 167.2) is None


class TestOwnVerdict:
    def test_good_to_own_at_bar(self):
        assert MD.own_verdict(0.043, 0.042) == "good to own"

    def test_thin_below_bar(self):
        assert MD.own_verdict(0.041, 0.042) == "thin"

    def test_bar_is_max_of_4pct_and_gs10(self):
        assert MD.own_verdict(0.039, 0.010) == "thin"    # 4% floor binds
        assert MD.own_verdict(0.041, 0.010) == "good to own"

    def test_none_yield(self):
        assert MD.own_verdict(None, 0.042) is None


class TestWaitReturn:
    def test_pct_and_annualized_at_21dte_plus(self):
        w = MD.wait_return(2.90, 95.0, 30)
        assert w["pct"] == pytest.approx(2.90 / 95.0)
        assert w["annualized"] == pytest.approx(2.90 / 95.0 * 365 / 30)
        assert w["note"] is None

    def test_too_short_to_yearly_under_21(self):
        w = MD.wait_return(1.20, 95.0, 10)
        assert w["pct"] == pytest.approx(1.20 / 95.0)
        assert w["annualized"] is None
        assert w["note"] == "too short to yearly"

    def test_earnings_in_window_blocks_annualization(self):
        w = MD.wait_return(2.90, 95.0, 30, earnings_in_window=True)
        assert w["annualized"] is None
        assert w["note"] == "earnings in window — do not yearly"

    def test_no_premium(self):
        assert MD.wait_return(None, 95.0, 30)["pct"] is None


class TestSameStrikeCall:
    def _calls(self):
        return pd.DataFrame({
            "strike": [170.0, 175.0, 180.0],
            "bid": [1.10, 2.20, 3.90],
            "ask": [1.25, 2.40, 4.10],
            "last": [1.18, 2.30, 4.00],
        })

    def test_exact_strike_found(self):
        row = MD.same_strike_call(self._calls(), 175.0)
        assert row is not None and float(row["strike"]) == 175.0

    def test_no_fuzzy_match(self):
        assert MD.same_strike_call(self._calls(), 176.0) is None

    def test_empty_or_none_chain(self):
        assert MD.same_strike_call(None, 175.0) is None
        assert MD.same_strike_call(pd.DataFrame(), 175.0) is None


class TestCalledAwayKeep:
    def test_put_premium_plus_call_premium(self):
        # assigned at 167.20 breakeven, called away at 175 with a 14.10 call:
        # keep (175 - 167.20) + 14.10 = 7.80 + 14.10
        assert MD.called_away_keep(175.0, 167.20, 14.10) == pytest.approx(21.90)

    def test_missing_pieces(self):
        assert MD.called_away_keep(175.0, None, 14.10) is None
