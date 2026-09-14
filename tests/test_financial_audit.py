import datetime as dt
import pandas as pd
import pytest
from scanner.features import _ttm_timeline, _stack_steps, build_features
from scanner.sec_edgar import instants_all
from scanner.scoring import compute_scores
from scanner.company_research import financial_snapshot
from scanner.opportunities import quote_is_stale
from test_features_review import synthetic_company, weekly_frame, ASOF


def test_weighted_average_shares_are_not_summed_or_ytd_differenced():
    dates = ['2024-03-31','2024-06-30','2024-09-30','2024-12-31']
    facts = {'facts': {'us-gaap': {'WeightedAverageNumberOfSharesOutstandingBasic': {'units': {'shares': [
        {'start':'2024-01-01','end':end,'filed':(pd.Timestamp(end)+pd.Timedelta(days=25)).date().isoformat(),
         'val':100.,'form':'10-Q','fy':2024,'fp':'Q1'} for end in dates]}}}}}
    timeline = _ttm_timeline(facts,dt.date(2025,3,1))
    assert timeline.shares_basic_was.dropna().tolist() == [100.,100.,100.,100.]


def test_notes_payable_and_aggregate_current_debt_are_not_omitted_or_doubled():
    def tag(value):
        return {'units':{'USD':[{'end':'2026-05-31','filed':'2026-06-22','val':value,'form':'10-K'}]}}
    facts = {'facts':{'us-gaap':{'LongTermNotesPayable':tag(122342), 'DebtCurrent':tag(7199),
                                'ShortTermBorrowings':tag(1468)}}}
    nc = instants_all(facts,'debt_noncurrent')
    current = instants_all(facts,'debt_current')
    assert nc.val.iloc[-1] == 122342
    assert current.val.iloc[-1] == 7199
    assert _stack_steps({'debt_noncurrent':nc,'debt_current':current})['stack'].iloc[-1] == 129541


def test_negative_ebitda_cannot_earn_net_cash_quality_credit():
    frame = pd.DataFrame({'ticker':['LOSS','CASH'], 'sector':['Tech']*2, 'ig_name':['Software']*2,
                          'ebitda_ttm':[-10.,10.], 'nd_ebitda':[-2.,-2.]})
    scored = compute_scores(frame).set_index('ticker')
    assert pd.isna(scored.loc['LOSS','nd_ebitda'])
    assert scored.loc['LOSS','quality_component_values']['balance'] == 0
    assert scored.loc['CASH','quality_component_values']['balance'] == 100


def test_features_ignore_future_prices():
    facts = synthetic_company()
    weekly = weekly_frame(ASOF)
    base = build_features(facts,weekly,ASOF)
    extra = pd.DataFrame({'ticker':['TST'],'week':[pd.Timestamp(ASOF)+pd.Timedelta(days=7)],'close':[99999.],'volume':[1e6]})
    later = build_features(facts,pd.concat([weekly,extra]),ASOF)
    assert later['price'] == base['price']
    assert later['mktcap'] == base['mktcap']


def test_financial_snapshot_marks_old_period_unknown():
    flows = pd.DataFrame({'effective':[pd.Timestamp('2026-01-01')], 'cfo':[10.], 'qend_cfo':[pd.Timestamp('2023-12-31')]})
    row = financial_snapshot(flows,'2026-09-14').iloc[0]
    assert row.Status == 'Stale' and pd.isna(row.Value)


def test_quote_age_flags_unknown_old_and_future_timestamps():
    now = dt.datetime(2026,9,14,12,tzinfo=dt.timezone.utc)
    assert not quote_is_stale('2026-09-14T11:59:00+00:00',now)
    assert quote_is_stale('2026-09-14T11:00:00+00:00',now)
    assert quote_is_stale('2026-09-14T13:00:00+00:00',now)
    assert quote_is_stale(None,now)


def test_yfinance_annual_and_gapped_quarters_have_correct_periods():
    from scanner.yf_facts import _rows_from_stmt
    stmt = pd.DataFrame([[100.,120.]], index=['Revenue'], columns=pd.to_datetime(['2023-12-31','2024-12-31']))
    annual = _rows_from_stmt(stmt,{'Revenue':['Revenue']},'duration',60,annual=True)['Revenue']
    assert all(360 <= (dt.date.fromisoformat(r['end'])-dt.date.fromisoformat(r['start'])).days <= 366 for r in annual)
    quarterly = _rows_from_stmt(stmt,{'Revenue':['Revenue']},'duration',30)['Revenue']
    assert all(85 <= (dt.date.fromisoformat(r['end'])-dt.date.fromisoformat(r['start'])).days <= 95 for r in quarterly)


def test_yfinance_does_not_label_foreign_statements_as_usd(monkeypatch):
    from scanner import yf_facts
    class Foreign:
        def get_info(self):
            return {'financialCurrency':'JPY','currency':'USD'}
        @property
        def quarterly_income_stmt(self):
            pytest.fail('Foreign statements must not enter USD assembly')
    monkeypatch.setattr(yf_facts.yf,'Ticker',lambda ticker: Foreign())
    assert yf_facts.facts_from_yfinance('ADR') is None


def test_preferred_shares_and_warrants_do_not_get_common_stock_scores():
    from scanner.company_lookup import unsupported_security
    assert unsupported_security('ORCL-PD',{'quoteType':'EQUITY'})
    assert unsupported_security('FMSTW',{'quoteType':'EQUITY','longName':'Foremost warrants'})
    assert unsupported_security('ETF',{'quoteType':'ETF'})
    assert not unsupported_security('BRK-B',{'quoteType':'EQUITY','longName':'Berkshire Hathaway'})


def test_zero_operating_return_is_not_missing():
    facts = synthetic_company()
    for row in facts['facts']['us-gaap']['OperatingIncomeLoss']['units']['USD']:
        row['val'] = 0.
    feat = build_features(facts, weekly_frame(ASOF), ASOF)
    assert feat['roic_ttm'] == 0.
