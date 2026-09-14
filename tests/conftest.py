import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import copy
import uuid
import pytest


@pytest.fixture(autouse=True)
def browser_storage(monkeypatch):
    """AppTest has no JS iframe: emulate the browser transport, never SQLite."""
    from dashboard import browser_ideas
    saved = {}
    seen = set()
    def bridge(command=None, **kwargs):
        if command and command['id'] not in seen:
            seen.add(command['id'])
            if command['kind'] == 'save':
                saved[command['idea']['ticker']] = copy.deepcopy(command['idea'])
            elif command['kind'] == 'remove':
                saved.pop(command['ticker'], None)
        return {'ready':True,'ideas':copy.deepcopy(saved),'ack':command['id'] if command else None,'event_id':uuid.uuid4().hex}
    monkeypatch.setattr(browser_ideas, 'bridge', bridge)
    return saved
