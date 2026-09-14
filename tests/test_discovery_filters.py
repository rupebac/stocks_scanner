import pandas as pd
from scanner import discovery_filters as DF


def test_size_bounds_exclude_unknown_only_when_filtering():
    frame = pd.DataFrame({'ticker':['SMALL','LARGE','UNKNOWN'], 'mktcap':[2e9,50e9,float('nan')]})
    assert len(DF.size_filter(frame)) == 3
    assert DF.size_filter(frame,10,100).ticker.tolist() == ['LARGE']
    assert DF.size_filter(frame,0,5).ticker.tolist() == ['SMALL']


def test_activity_requires_same_contract_and_fresh_real_evidence():
    snapshot = {'checked_at':1000, 'status':'Checked','contracts':[{'oi':500,'volume':0},{'oi':5,'volume':100}]}
    assert DF.activity_status(snapshot,100,10,now=1000) == 'Below minimum'
    snapshot['contracts'].append({'oi':100,'volume':10})
    assert DF.activity_status(snapshot,100,10,now=1000) == 'Matches'
    assert DF.activity_status(snapshot,100,10,now=2000) == 'Stale'
    assert DF.activity_status(None,100,10,now=1000) == 'Not checked'
    snapshot['contracts'] = [{'oi':500,'volume':None}]
    assert DF.activity_status(snapshot,100,10,now=1000) == 'Unavailable'
    assert DF.activity_status(snapshot,100,0,now=1000) == 'Matches'


def test_disabled_activity_filter_keeps_unchecked_companies():
    frame = pd.DataFrame({'ticker':['A','B']})
    assert len(DF.apply_activity(frame,{})) == 2
    assert DF.apply_activity(frame,{},100).empty


def test_ui_size_filter_updates_discovery_and_reset():
    from streamlit.testing.v1 import AppTest
    from pathlib import Path
    at = AppTest.from_file(str(Path(__file__).resolve().parents[1]/'dashboard/app.py'), default_timeout=30).run()
    initial = int(next(m.value for m in at.metric if m.label=='Businesses to explore'))
    at.number_input('min_cap').set_value(200.).run()
    assert not at.exception
    assert int(next(m.value for m in at.metric if m.label=='Businesses to explore')) < initial
    at.number_input('min_put_oi').set_value(100).run()
    assert not at.exception
    assert next(m.value for m in at.metric if m.label=='Businesses to explore') == '0'
    next(b for b in at.button if b.label=='Reset filters').click().run()
    assert not at.exception
    assert int(next(m.value for m in at.metric if m.label=='Businesses to explore')) == initial
