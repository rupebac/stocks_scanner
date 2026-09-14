"""Browser-persisted personal ideas; server memory is isolated to the Streamlit session."""
import datetime as dt
import math
import re
import uuid
from pathlib import Path
import streamlit as st
import streamlit.components.v1 as components

bridge = components.declare_component('personal_ideas', path=str(Path(__file__).parent/'components/browser_ideas'))


def validate(idea):
    ticker = str(idea.get('ticker', '')).strip().upper()
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9.-]{0,19}', ticker):
        raise ValueError('A valid ticker is required.')
    price = idea.get('buy_price')
    if price is not None:
        price = float(price)
        if not math.isfinite(price) or price <= 0:
            raise ValueError('Buy price must be positive, or left unset.')
    thesis = str(idea.get('thesis', '')).strip()
    if len(thesis) > 20000:
        raise ValueError('Please keep the ownership thesis under 20,000 characters.')
    return {'ticker':ticker, 'buy_price':price, 'thesis':thesis,
            'updated_at':str(idea.get('updated_at', ''))}


def sync():
    queue = st.session_state.setdefault('_idea_commands', [])
    event = bridge(command=queue[0] if queue else None, key='personal_ideas_bridge', default=None)
    if isinstance(event, dict) and event.get('event_id') != st.session_state.get('_idea_event'):
        st.session_state['_idea_event'] = event.get('event_id')
        if event.get('ready'):
            try:
                ideas = {v['ticker']:v for v in [validate(v) for v in event.get('ideas', {}).values()]}
                st.session_state['_personal_ideas'] = ideas
                st.session_state['_ideas_ready'] = True
                st.session_state.pop('_ideas_error', None)
            except (ValueError, TypeError, AttributeError):
                st.session_state['_ideas_error'] = 'Saved browser data is invalid. It has not been overwritten.'
                st.session_state['_ideas_ready'] = False
        else:
            st.session_state['_ideas_ready'] = False
            st.session_state['_ideas_error'] = event.get('error', 'Browser storage is unavailable.')
        if queue and event.get('ack') == queue[0]['id']:
            queue.pop(0)
            if event.get('ready'):
                st.toast('Saved in this browser')
            if queue:
                st.session_state["_ideas_flush"] = True
    if st.session_state.get('_ideas_error'):
        st.error('Personal ideas could not be saved or loaded: ' + st.session_state['_ideas_error'])
    if '_personal_ideas' not in st.session_state:
        if not st.session_state.get('_ideas_error'):
            st.caption('Loading your saved ideas from this browser…')
            st.stop()
        st.session_state['_personal_ideas'] = {}


def all_ideas():
    return dict(st.session_state.get('_personal_ideas', {}))


def _queue(action):
    if not st.session_state.get('_ideas_ready'):
        raise ValueError('Browser storage is unavailable. Enable site storage to save ideas.')
    st.session_state.setdefault('_idea_commands', []).append({'id':uuid.uuid4().hex, **action})


def save(ticker, buy_price=None, thesis=''):
    idea = validate({'ticker':ticker, 'buy_price':buy_price, 'thesis':thesis,
                     'updated_at':dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')})
    _queue({'kind':'save','idea':idea})


def remove(ticker):
    _queue({'kind':'remove','ticker':ticker})
