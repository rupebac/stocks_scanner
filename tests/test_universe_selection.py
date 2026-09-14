import datetime as dt
import json

import pandas as pd
from scanner import universe as UN
from scanner import universe_selection as US
from scanner.scan import _gates


def test_nasdaq_normalization_keeps_membership_dates_unknown():
    raw = pd.DataFrame({"Ticker": ["A.B", "NEW"], "Company": ["A", "N"],
                        "ICB Industry[1]": ["Technology", "Basic Materials"],
                        "ICB Subsector[1]": ["Software", "Mining"]})
    sp = pd.DataFrame({"symbol": ["A-B"], "cik": [123], "sector": ["Information Technology"],
                       "sub_industry": ["Application Software"], "date_added": ["1990-01-01"]})
    result = UN.normalize_nasdaq(raw, {"0": {"ticker": "NEW", "cik_str": 456}}, sp)
    assert result.symbol.tolist() == ["A-B", "NEW"]
    assert result.cik.tolist() == [123, 456]
    assert result.sector.tolist() == ["Information Technology", "Materials"]
    assert result.date_added.isna().all()
    assert result.sub_industry_norm.iloc[0] == "application software"


def test_scan_selection_isolates_indices_and_supports_legacy(tmp_path, monkeypatch):
    monkeypatch.setattr(US.config, "SCANS", tmp_path)
    for name, cfg in [("2026-01-01", {"asof": "2026-01-01"}),
                      ("2026-01-02_nasdaq100", {"asof": "2026-01-02", "universe": "nasdaq100"})]:
        path = tmp_path / name
        path.mkdir()
        (path / "config.json").write_text(json.dumps(cfg))
        (path / "metrics.csv").write_text("ticker\nAAPL\n")
    assert [p.name for p in US.available_scans(tmp_path, "sp500")] == ["2026-01-01"]
    assert [p.name for p in US.available_scans(tmp_path, "nasdaq100")] == ["2026-01-02_nasdaq100"]
    date = dt.date(2026, 1, 1)
    assert US.scan_directory(date, "sp500") != US.scan_directory(date, "nasdaq100")


def test_nasdaq_maturity_uses_observed_history_not_unknown_membership():
    base = {"adv_usd": 5e7, "mktcap": 5e10, "rev_cagr5": .1, "fcf_cagr5": .1,
            "index_tenure_y": float("nan")}
    frame = pd.DataFrame([{**base, "trading_history_y": years} for years in [2., .5, float("nan")]])
    out = _gates(frame.copy(), dt.date.today(), universe="nasdaq100")
    assert out.gates_pass.tolist() == [True, False, False]
    assert "trading history<1y" in out.gate_fail_reasons.iloc[1]
    assert "trading history n/a" in out.gate_fail_reasons.iloc[2]
    assert not _gates(frame.copy(), dt.date.today()).gates_pass.any()


def test_selector_handles_missing_universe_without_showing_sp500(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from pathlib import Path
    real = US.available_scans
    monkeypatch.setattr(US, "available_scans", lambda root, universe: [] if universe == "nasdaq100" else real(root, universe))
    at = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "dashboard/app.py"), default_timeout=30).run()
    at.selectbox("stock_universe").set_value("nasdaq100").run()
    assert not at.exception
    assert any("Nasdaq-100 has no saved scan" in msg.value for msg in at.info)
    assert not any(m.label == "Quality & value shortlist" for m in at.metric)
    at.selectbox("stock_universe").set_value("sp500").run()
    assert not at.exception
    assert any(m.label == "Quality & value shortlist" for m in at.metric)


def test_manual_search_is_independent_of_discovery_index(monkeypatch):
    from streamlit.testing.v1 import AppTest
    from pathlib import Path
    from scanner import company_lookup as CL
    # This NYSE company is absent from Nasdaq; it must still be searchable there.
    monkeypatch.setattr(CL, "company_directory", lambda: pd.DataFrame([
        {"ticker": "ORCL", "name": "Oracle", "cik": 1341439, "exchange": "NYSE"}]))
    at = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "dashboard/app.py"), default_timeout=30).run()
    at.selectbox("stock_universe").select("nasdaq100").run()
    at.selectbox("company_search").select("ORCL").run()
    assert not at.exception
    assert at.session_state["idea_pick"] == "ORCL"
    assert any("ORCL ·" in m.value for m in at.markdown)
    assert not any(s.label == "Discovery universe" for s in at.selectbox)


def test_directory_preserves_na_ticker_in_cache(tmp_path, monkeypatch):
    from scanner import company_lookup as CL
    monkeypatch.setattr(CL.config, "DERIVED", tmp_path)
    (tmp_path / "company_directory.csv").write_text("ticker,name,cik,exchange\nNA,Example Company,123,Nasdaq\n")
    assert CL.company_directory().ticker.tolist() == ["NA"]


def test_manual_snapshot_keeps_raw_evidence_without_inventing_peer_scores(tmp_path, monkeypatch):
    from scanner import company_lookup as CL, features, sec_edgar, rates_fx
    monkeypatch.setattr(CL.config, "DATA", tmp_path)
    weekly = pd.DataFrame({"ticker": ["EXAMPLE"], "week": [pd.Timestamp("2026-01-01")], "close": [25.], "volume": [100.]})
    monkeypatch.setattr(CL.MD, "download_weekly", lambda tickers: weekly)
    monkeypatch.setattr(CL.MD, "price_features", lambda weekly: pd.DataFrame())
    monkeypatch.setattr(sec_edgar, "fetch_companyfacts", lambda cik: {"facts": {}})
    monkeypatch.setattr(features, "build_features", lambda *args, **kwargs: {"price": 25., "fcf_yield": .06,
                          "history": pd.DataFrame({"week": [pd.Timestamp("2026-01-01")], "price": [25.]})})
    monkeypatch.setattr(rates_fx, "gs10_asof", lambda date: .04)
    class Quote:
        def get_info(self):
            return {"sector": "Technology", "industry": "Software"}
    monkeypatch.setattr(CL.MD.yf, "Ticker", lambda ticker: Quote())
    path = CL.build_snapshot({"ticker": "EXAMPLE", "name": "Example", "cik": 123})
    frame = pd.read_csv(path / "metrics.csv")
    assert frame.quality_score.isna().all() and frame.cheapness_score.isna().all()
    assert frame.residual.isna().all() and not frame.gates_pass.any()
    assert frame.price.iloc[0] == 25.
    assert json.loads((path / "config.json").read_text())["research_only"]
    assert not (tmp_path / "scans").exists()
