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
    if universe == "all_nyse":
        return fetch_all_nyse(refresh=refresh)
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


# All-NYSE universe (doc 01 extension): the exchange has no GICS source, so
# sector comes from the SEC's own SIC code — mapped ONLY where the restricted-
# sector exclusion depends on it (financials/RE 6000-6999, utilities 4900-4999);
# everything else is "Unknown" and falls down the §2 ladder to the market group.
def _sic_sector(sic: int | None) -> str:
    if sic is None:
        return "Unknown"
    if 6000 <= sic <= 6999:
        return "Financials"
    if 4900 <= sic <= 4999:
        return "Utilities"
    return "Unknown"


def fetch_all_nyse(refresh: bool = False) -> pd.DataFrame:
    """Every NYSE-listed filer from the SEC exchange directory, with SIC-derived
    sectors. Cached 7 days like the index universes; the per-CIK submissions
    index (which carries the SIC) is fetched politely and reused by the scan."""
    from .sec_edgar import sic_for_cik

    path = config.DERIVED / "universe_all_nyse.csv"
    if not refresh and path.exists() and time.time() - path.stat().st_mtime < 7 * 86400:
        cached = pd.read_csv(path)
        if len(cached) >= 500:
            return cached

    from .company_lookup import company_directory
    directory = company_directory()
    nyse = directory[directory.exchange == "NYSE"].copy()
    if len(nyse) < 500:
        raise ValueError("NYSE directory looks incomplete")

    sics = [sic_for_cik(int(cik)) for cik in nyse["cik"]]
    nyse = nyse.assign(
        sector=[_sic_sector(s) for s, _ in sics],
        sub_industry=[desc or "" for _, desc in sics],
        date_added=pd.NaT,
        fetched_at=dt.date.today(),
    ).rename(columns={"ticker": "symbol"})
    # restricted sectors excluded at scan time like every universe; drop filers
    # with no SIC at all (shell/blank-check 6770s land in Financials anyway)
    nyse = nyse[["symbol", "name", "sector", "sub_industry", "date_added", "cik", "fetched_at"]]
    nyse = nyse.dropna(subset=["cik"]).drop_duplicates("symbol")
    nyse["sub_industry_norm"] = nyse.sub_industry.map(_norm_name)
    if len(nyse) < 500:
        raise ValueError(f"All-NYSE universe too small after SIC lookup: {len(nyse)}")

    history_path = config.DERIVED / "membership_history_all_nyse.csv"
    history = nyse.copy()
    if history_path.exists():
        old = pd.read_csv(history_path)
        history = pd.concat([old[old.fetched_at.astype(str) != str(dt.date.today())], history], ignore_index=True)
    history.to_csv(history_path, index=False)
    nyse.to_csv(path, index=False)
    return nyse
