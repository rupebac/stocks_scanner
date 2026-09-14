"""Partial research scores never replace canonical scores or invent missing inputs."""
from pathlib import Path
import pandas as pd
import pytest
from scanner import scoring as SC
from scanner.research_scores import add_research_scores
from scanner.universe_selection import available_scans


@pytest.fixture
def metrics():
    # Match the dashboard integration tests against the workspace's saved scan.
    path = available_scans(Path(__file__).resolve().parents[1]/'data/scans', 'sp500')[-1] / 'metrics.csv'
    return pd.read_csv(path)


def test_estimates_do_not_change_canonical_scores_or_shortlist(metrics):
    before = metrics.copy(deep=True)
    out = add_research_scores(metrics)
    pd.testing.assert_frame_equal(metrics, before)
    pd.testing.assert_frame_equal(out[metrics.columns], metrics)
    assert SC.highlighted(metrics).ticker.tolist() == SC.highlighted(out).ticker.tolist()
    for key in ['quality', 'cheapness']:
        known = metrics[f'{key}_score'].notna()
        pd.testing.assert_series_equal(out.loc[known,f'display_{key}_score'], metrics.loc[known,f'{key}_score'], check_names=False)


def test_negative_cash_growth_business_gets_labeled_estimates(metrics):
    row = add_research_scores(metrics).set_index('ticker').loc['ORCL']
    assert pd.isna(row.quality_score) and pd.isna(row.cheapness_score)
    assert row.quality_score_status == 'Estimate'
    assert row.cheapness_score_status == 'Estimate'
    assert 0 <= row.display_quality_score <= 100
    assert 0 <= row.display_cheapness_score <= 100
    assert 'FCF valuation history' in row.cheapness_score_note
    assert not row.gates_pass


def test_no_fake_quality_score_from_growth_alone(metrics):
    frame = metrics.copy()
    idx = frame.index[frame.ticker=='ORCL'][0]
    fields = ['quality_score', 'roic', 'roic_ttm','roic_median_5y', 'fcf_margin', 'fcf_margin_ttm',
              'fcf_margin_median_5y', 'nd_ebitda', 'fcf_cov', 'gm_stability', 'rev_yoy_n']
    frame.loc[idx,fields] = float('nan')
    row = add_research_scores(frame).loc[idx]
    assert pd.isna(row.display_quality_score)
    assert row.quality_score_status == 'Insufficient data'


def test_no_cheapness_estimate_from_one_input(metrics):
    frame = metrics.copy()
    idx = frame.index[frame.ticker=='ORCL'][0]
    frame.loc[idx,['cheapness_score','ev_fcf_self_pct','pct_ev_ebit','ev_ebit']] = float('nan')
    row = add_research_scores(frame).loc[idx]
    assert pd.isna(row.display_cheapness_score)
    assert row.cheapness_score_status == 'Insufficient data'
    assert '30%' in row.cheapness_score_note
