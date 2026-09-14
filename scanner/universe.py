"""Universe: S&P 500 constituents from Wikipedia, share-class dedupe, membership history.

Doc 01 (universe, share classes by CIK, weekly refresh, membership history) and
doc 08 §1 (endpoints). Restricted sectors excluded at scan time, not here.
"""
from __future__ import annotations

import datetime as dt
import io
import time

import pandas as pd
import requests

from . import config


def _norm_name(s: str) -> str:
    return " ".join(str(s).lower().replace("&", "and").split())


def fetch_constituents(refresh: bool = False) -> pd.DataFrame:
    """Fetch (or serve cached, <= 7 days old) the Wikipedia constituents table."""
    if not refresh and config.UNIVERSE_FILE.exists():
        age = time.time() - config.UNIVERSE_FILE.stat().st_mtime
        if age < 7 * 86400:
            df = pd.read_csv(config.UNIVERSE_FILE)
            if len(df) > 400:
                return df

    html = requests.get(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        headers={"User-Agent": config.SEC_HEADERS["User-Agent"]},
        timeout=30,
    ).text
    table = pd.read_html(io.StringIO(html), attrs={"id": "constituents"})[0]
    table = table.rename(
        columns={
            "Symbol": "symbol", "Security": "name", "GICS Sector": "sector",
            "GICS Sub-Industry": "sub_industry", "Date added": "date_added", "CIK": "cik",
        }
    )[["symbol", "name", "sector", "sub_industry", "date_added", "cik"]]
    table["symbol"] = table["symbol"].str.strip().str.replace(".", "-", regex=False)
    table["sub_industry_norm"] = table["sub_industry"].map(_norm_name)
    table["date_added"] = pd.to_datetime(table["date_added"], errors="coerce")
    table["fetched_at"] = dt.date.today()

    # membership history: append today's row-set, keyed by (symbol, fetched_at) (doc 01).
    # A same-day re-fetch REPLACES today's rows: filter the whole column (position-free —
    # peeking only at the last row duplicates if today's rows are not file-last).
    hist = table[["fetched_at", "symbol", "name", "sector", "sub_industry", "cik"]]
    if config.MEMBERSHIP_FILE.exists():
        old = pd.read_csv(config.MEMBERSHIP_FILE)
        old = old[old["fetched_at"].astype(str) != str(dt.date.today())]
        hist = pd.concat([old, hist], ignore_index=True)
    hist.to_csv(config.MEMBERSHIP_FILE, index=False)

    table.to_csv(config.UNIVERSE_FILE, index=False)
    return table


def with_industry_groups(universe: pd.DataFrame) -> pd.DataFrame:
    """Attach industry group via the static map; unmapped sub-industries fall to
    a pseudo-IG equal to the sector (the scoring ladder still works; doc 05 §2)."""
    gmap = pd.read_csv(config.GICS_MAP_FILE)
    gmap["sub_industry_norm"] = gmap["sub_industry"].map(_norm_name)
    merged = universe.merge(
        gmap[["sub_industry_norm", "ig_code", "ig_name"]], on="sub_industry_norm", how="left"
    )
    unmapped = merged["ig_code"].isna().sum()
    if unmapped:
        merged["ig_code"] = merged["ig_code"].fillna(-1).astype(int)
        merged["ig_name"] = merged["ig_name"].fillna(merged["sector"] + " (sector fallback)")
    return merged, int(unmapped)


def standard_group(universe: pd.DataFrame) -> pd.DataFrame:
    """Standard sector group = everything except GICS 40/55/60 (doc 01, Q2)."""
    return universe[~universe["sector"].isin(config.RESTRICTED_SECTORS)].copy()


def index_tenure_years(row, asof: dt.date) -> float | None:
    if pd.isna(row.get("date_added")):
        return None
    added = pd.Timestamp(row["date_added"]).date()
    return (asof - added).days / 365.25


# Nasdaq publishes ICB classifications; retain the original subsector and use
# broad GICS-compatible sectors only for the existing sector fallback ladder.
ICB_SECTORS = {
    "Technology": "Information Technology", "Telecommunications": "Communication Services",
    "Consumer Discretionary": "Consumer Discretionary", "Consumer Staples": "Consumer Staples",
    "Health Care": "Health Care", "Industrials": "Industrials", "Basic Materials": "Materials",
    "Energy": "Energy", "Utilities": "Utilities", "Financials": "Financials", "Real Estate": "Real Estate",
}


def normalize_nasdaq(table, sec_tickers, sp500):
    table = table.rename(columns={c: str(c).split("[")[0].strip() for c in table.columns})
    table = table.rename(columns={"Ticker": "symbol", "Company": "name", "ICB Industry": "sector", "ICB Subsector": "sub_industry"}).copy()
    table["symbol"] = table.symbol.str.strip().str.replace(".", "-", regex=False)
    table["sector"] = table.sector.map(ICB_SECTORS).fillna("Unknown")
    ciks = {r["ticker"].replace(".", "-"): r["cik_str"] for r in sec_tickers.values()}
    table["cik"] = table.symbol.map(ciks)
    known = sp500.set_index("symbol")
    for col in ("cik", "sector", "sub_industry"):
        table[col] = table.symbol.map(known[col]).combine_first(table[col])
    # S&P membership dates are not Nasdaq membership dates. Never borrow them.
    table["date_added"] = pd.NaT
    table["sub_industry_norm"] = table.sub_industry.map(_norm_name)
    table["fetched_at"] = dt.date.today()
    missing = table.loc[table.cik.isna(), "symbol"].tolist()
    if missing:
        raise ValueError(f"Nasdaq constituents missing SEC identifiers: {', '.join(missing)}")
    table["cik"] = table.cik.astype(int)
    return table


def fetch_universe(universe="sp500", refresh=False):
    if universe == "sp500":
        return fetch_constituents(refresh=refresh)
    if universe != "nasdaq100":
        raise ValueError(f"Unknown universe: {universe}")
    path = config.DERIVED / "universe_nasdaq100.csv"
    if not refresh and path.exists() and time.time() - path.stat().st_mtime < 7 * 86400:
        cached = pd.read_csv(path)
        if len(cached) >= 90:
            return cached
    response = requests.get("https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies",
                            headers=config.SEC_HEADERS, timeout=30)
    response.raise_for_status()
    tables = pd.read_html(io.StringIO(response.text), flavor="lxml")
    table = next(t for t in tables if "Ticker" in t.columns and "Company" in t.columns)
    response = requests.get("https://www.sec.gov/files/company_tickers.json", headers=config.SEC_HEADERS, timeout=30)
    response.raise_for_status()
    table = normalize_nasdaq(table, response.json(), fetch_constituents(refresh=refresh))
    if len(table) < 90 or table.symbol.duplicated().any():
        raise ValueError("Incomplete or duplicate Nasdaq-100 membership data")
    history_path = config.DERIVED / "membership_history_nasdaq100.csv"
    history = table.copy()
    if history_path.exists():
        old = pd.read_csv(history_path)
        history = pd.concat([old[old.fetched_at.astype(str) != str(dt.date.today())], history], ignore_index=True)
    history.to_csv(history_path, index=False)
    table.to_csv(path, index=False)
    return table
