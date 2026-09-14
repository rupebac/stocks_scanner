import json
import pandas as pd
from scanner import refresh as RF, company_lookup as CL


def test_tracked_refresh_runs_both_indices_and_only_extra_research(tmp_path, monkeypatch):
    monkeypatch.setattr(RF.config, "DATA", tmp_path)
    path = tmp_path / "company_research" / "EXTRA"
    path.mkdir(parents=True)
    (path / "config.json").write_text('{}')
    directory = pd.DataFrame({"ticker": ["INDEX", "EXTRA", "SAVED", "UNSEEN"]})
    reference = pd.DataFrame({"ticker": ["INDEX"]})
    assert RF.research_targets("tracked", directory, reference).ticker.tolist() == ["EXTRA"]
    assert RF.research_targets("all", directory, reference).ticker.tolist() == ["EXTRA", "SAVED", "UNSEEN"]
    calls = []
    monkeypatch.setattr(RF, "run_scan", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(RF, "reference_data", lambda: reference)
    monkeypatch.setattr(CL, "company_directory", lambda **kwargs: directory.iloc[:1])
    result = RF.refresh("tracked")
    assert [c["universe"] for c in calls] == ["sp500", "nasdaq100"]
    assert all(c["refresh_data"] and c["skip_options"] for c in calls)
    assert result["failures"] == []


def test_company_failure_does_not_stop_remaining_refreshes(tmp_path, monkeypatch):
    monkeypatch.setattr(RF.config, "DATA", tmp_path)
    for ticker in ["BAD", "GOOD"]:
        path = tmp_path / "company_research" / ticker
        path.mkdir(parents=True)
        (path / "config.json").write_text("{}")
    monkeypatch.setattr(RF, "reference_data", lambda: pd.DataFrame())
    monkeypatch.setattr(CL, "company_directory", lambda **kwargs: pd.DataFrame({"ticker": ["BAD", "GOOD"]}))
    def build(listing, refresh):
        if listing['ticker'] == 'BAD':
            raise RuntimeError('source unavailable')
        path = tmp_path / 'GOOD'
        path.mkdir()
        pd.DataFrame({'ticker':['GOOD']}).to_csv(path/'metrics.csv',index=False)
        return path
    monkeypatch.setattr(CL, "build_snapshot", build)
    monkeypatch.setattr(CL, "score_snapshot", lambda raw, ref: pd.DataFrame({"quality_score_status": ["Estimate"], "cheapness_score_status": ["Insufficient data"], "display_quality_score": [55.], "display_cheapness_score": [float('nan')]}))
    result = RF.refresh('researched')
    assert result['researched_companies'] == 1
    assert result['quality_scores'] == 1 and result['cheapness_scores'] == 0
    assert result['failures'][0]['target'] == 'BAD'
    assert (tmp_path/'GOOD/research_scores.csv').exists()


def test_data_page_defaults_to_both_indices_and_researched_companies():
    from streamlit.testing.v1 import AppTest
    from pathlib import Path
    at = AppTest.from_file(str(Path(__file__).resolve().parents[1]/'dashboard/app.py'), default_timeout=30).run()
    at.radio('workspace').set_value('Data & refresh').run()
    assert not at.exception
    assert at.selectbox('refresh_universe').value == 'tracked'
    assert 'All NYSE & Nasdaq companies' in at.selectbox('refresh_universe').options
    assert any(b.label == 'Refresh data & scores' for b in at.button)
