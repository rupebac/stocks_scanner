"""Research workflow: quote consistency, strike ceilings, and persistent ideas."""
import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from scanner import opportunities as OP, watchlist as WL, market_data as MD

TODAY = dt.date(2026, 9, 14)


@pytest.fixture
def ideas_db(tmp_path, monkeypatch):
    monkeypatch.setattr(WL, "DB_PATH", tmp_path / "user/ideas.sqlite3")
    return WL.DB_PATH


def test_watchlist_persists_updates_and_independent_saves(ideas_db):
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda t: WL.save(t, 120., "Durable revenue"), ["ACN", "ORCL", "ADBE"]))
    assert set(WL.all_ideas()) == {"ACN", "ORCL", "ADBE"}
    WL.save("ORCL", 110., "Investment must earn a return")
    assert WL.all_ideas()["ORCL"]["buy_price"] == 110.
    assert WL.all_ideas()["ACN"]["thesis"] == "Durable revenue"
    WL.remove("ORCL")
    assert set(WL.all_ideas()) == {"ACN", "ADBE"}


@pytest.mark.parametrize("price", [float("nan"), float("inf"), -1., 0.])
def test_invalid_buy_price_rejected(ideas_db, price):
    with pytest.raises(ValueError):
        WL.save("ORCL", price)
    assert not WL.all_ideas()


def test_prefers_shared_expiration_and_marks_mixed_fallback():
    chosen, shared = OP.choose_expirations({"A": ["2026-09-21", "2026-10-16"], "B": ["2026-10-16", "2026-10-23"], "C": []}, TODAY, 7)
    assert chosen == {"A": "2026-10-16", "B": "2026-10-16", "C": None}
    assert shared
    chosen, shared = OP.choose_expirations({"A": ["2026-09-21"], "B": ["2026-10-16"]}, TODAY, 30)
    assert not shared
    assert chosen == {"A": "2026-09-21", "B": "2026-10-16"}


def test_expiration_selection_enforces_horizon():
    chosen, _ = OP.choose_expirations({"A": ["2026-09-13", "2026-12-14", "2026-12-13"]}, TODAY, 90)
    assert chosen["A"] == "2026-12-13"


def raw_chain(spot=200.):
    return {"underlying_price": spot, "underlying_time": "2026-09-14T15:30:00Z",
            "puts": pd.DataFrame({"strike": [170., 180., 190.], "bid": [1., 2., 3.], "ask": [1.1, 2.1, 3.1]})}


def test_comparison_uses_chain_price_and_honors_buy_price_ceiling():
    # Closest to 178 is 180, but a saved price must never suggest paying above 178.
    r = OP.put_comparison("X", raw_chain(), "2026-10-16", TODAY, 5, buy_price=178.)
    assert r["Strike"] == 170.
    assert r["Premium / stock"] == pytest.approx(1/200)
    assert r["Entry discount"] == pytest.approx(.15)
    assert r["Cash required"] == 17000
    assert OP.put_comparison("X", raw_chain(), "2026-10-16", TODAY, 5)["Strike"] == 190.


def test_no_strike_below_target_and_no_underlying_are_explicit():
    r = OP.put_comparison("X", raw_chain(), "2026-10-16", TODAY, 5, buy_price=100.)
    assert r["Status"] == "No strike at or below my buy price"
    r = OP.put_comparison("X", raw_chain(None), "2026-10-16", TODAY, 5)
    assert r["Status"] == "Share quote unavailable"
    assert "Return on cash" not in r


@pytest.mark.parametrize("bid,ask", [(0., 2.), (float('nan'), 2.), (3., 2.)])
def test_unusable_bids_never_become_income(bid, ask):
    raw = raw_chain()
    raw["puts"]["bid"] = bid
    raw["puts"]["ask"] = ask
    r = OP.put_comparison("X", raw, "2026-10-16", TODAY, 5)
    assert r["Premium"] is None
    assert r["Net entry"] is None


def test_chain_retains_underlying_quote_from_same_response(monkeypatch):
    oc = SimpleNamespace(puts=pd.DataFrame({"strike": [100], "bid": [2], "ask": [2.1]}), calls=pd.DataFrame(),
                         underlying={"regularMarketPrice": 105., "regularMarketTime": 1789398000, "currency": "USD"})
    monkeypatch.setattr(MD.yf, "Ticker", lambda t: SimpleNamespace(option_chain=lambda e: oc))
    raw = MD.fetch_option_chain("X", TODAY)
    assert raw["underlying_price"] == 105.
    assert raw["underlying_time"].endswith("+00:00")
    assert raw["puts"].bid.iloc[0] == 2.


def test_growth_lane_requires_material_investment_without_changing_scores():
    df = pd.DataFrame({"ticker": ["GROW", "SMALL", "SLOW"], "revenue_ttm": [100.,100.,100.],
                       "fcf_owner_ttm": [20.,20.,20.], "fcf_adj_ttm": [-5.,19.,-5.],
                       "rev_cagr5": [.1,.1,.01], "ebit_ttm": [15.,15.,15.], "quality_score": [None,70.,70.]})
    original = df.copy(deep=True)
    out = OP.research_universe(df)
    assert out.ticker.tolist() == ["GROW"]
    assert out.investment_gap.iloc[0] == .25
    assert out.operating_margin.iloc[0] == .15
    pd.testing.assert_frame_equal(df, original)


def test_saved_idea_survives_new_app_session_and_removal_can_be_undone(ideas_db):
    app = str(Path(__file__).resolve().parents[1] / 'dashboard/app.py')
    at = AppTest.from_file(app, default_timeout=30).run()
    at.selectbox('company_search').select('ORCL').run()
    at.number_input('buy_ORCL').set_value(150.)
    at.text_area('thesis_ORCL').set_value('Growth must produce durable operating cash.')
    next(b for b in at.button if b.label == 'Save idea').click().run()
    assert not at.exception
    fresh = AppTest.from_file(app, default_timeout=30).run()
    fresh.radio('workspace').set_value('Saved ideas').run()
    assert not fresh.exception
    assert any('Growth must produce' in t.value for t in fresh.text)
    fresh.button('remove_ORCL').click().run()
    assert not WL.all_ideas()
    next(b for b in fresh.button if b.label == 'Undo last removal').click().run()
    assert WL.all_ideas()['ORCL']['buy_price'] == 150.


def test_options_use_saved_ceiling_and_handle_missing_underlying(ideas_db, monkeypatch):
    from test_options_dashboard import _run_app, selected_strike
    WL.save('ACN', 178., 'Good business')
    at = _run_app(monkeypatch)
    assert selected_strike(at) <= 178.
    at.toggle('ceiling_ACN').set_value(False).run()
    assert not at.exception
    # Recreate the cached chain without an underlying quote.
    import streamlit as st
    monkeypatch.setattr(MD, 'fetch_option_chain', lambda *a: raw_chain(None))
    st.cache_data.clear()
    at.run()
    assert not at.exception
    assert any('underlying share quote is unavailable' in i.value for i in at.info)


def test_growth_and_company_research_views_render(ideas_db):
    at = AppTest.from_file(str(Path(__file__).resolve().parents[1]/'dashboard/app.py'), default_timeout=30).run()
    at.session_state['lens'] = 'Investing for growth'
    at.run()
    assert not at.exception
    assert at.session_state['map_view'] == 'Growth economics'
    at.selectbox('company_search').select('ORCL').run()
    for focus in ['Growth & margins', 'Cash & investment', 'Debt & dilution', 'Price & value']:
        at.session_state['research_focus'] = focus
        at.run()
        assert not at.exception
        assert len(at.get('plotly_chart')) <= 2


def test_income_map_and_preview_render_with_quote_metadata(ideas_db, monkeypatch):
    from test_options_dashboard import _run_app
    at = _run_app(monkeypatch)
    at.radio('workspace').set_value('Discover').run()
    at.session_state['map_view'] = 'Put income'
    at.run()
    next(b for b in at.button if b.label == 'Compare put income').click().run()
    assert not at.exception
    assert len(at.get('plotly_chart')) == 1
    assert all(r['Share price'] == 180. for r in at.session_state['income_results'][1])
    at.button('preview_ACN').click().run()
    assert not at.exception
    assert at.session_state['card_quotes']['ACN']['Share quote time'] == '2026-09-14T15:30:00+00:00'
