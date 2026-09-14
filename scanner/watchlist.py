"""Local saved research ideas. SQLite transactions preserve concurrent saves."""
from __future__ import annotations

from contextlib import contextmanager
import datetime as dt
import math
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "data/user/watchlist.sqlite3"


@contextmanager
def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS ideas (ticker TEXT PRIMARY KEY, buy_price REAL, thesis TEXT NOT NULL, updated_at TEXT NOT NULL)")
    try:
        with db:
            yield db
    finally:
        db.close()


def all_ideas() -> dict[str, dict]:
    with _connect() as db:
        return {r["ticker"]: dict(r) for r in db.execute("SELECT * FROM ideas ORDER BY ticker")}


def save(ticker: str, buy_price: float | None = None, thesis: str = ""):
    ticker = ticker.strip().upper()
    if not ticker or len(ticker) > 20:
        raise ValueError("A valid ticker is required.")
    if buy_price is not None and (not math.isfinite(buy_price) or buy_price <= 0):
        raise ValueError("Buy price must be positive, or left unset.")
    with _connect() as db:
        db.execute("INSERT INTO ideas VALUES (?, ?, ?, ?) ON CONFLICT(ticker) DO UPDATE SET buy_price=excluded.buy_price, thesis=excluded.thesis, updated_at=excluded.updated_at",
                   (ticker, buy_price, thesis.strip(), dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")))


def remove(ticker: str):
    with _connect() as db:
        db.execute("DELETE FROM ideas WHERE ticker = ?", (ticker,))
