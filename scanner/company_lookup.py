"""Exchange-wide company lookup and isolated, on-demand research snapshots."""
import datetime as dt
import json
import re
import time

import pandas as pd
import requests

from . import config, market_data as MD


def company_directory(refresh=False):
    path = config.DERIVED / "company_directory.csv"
    if not refresh and path.exists() and time.time() - path.stat().st_mtime < 7 * 86400:
        return pd.read_csv(path, keep_default_na=False)
    response = requests.get("https://www.sec.gov/files/company_tickers_exchange.json", headers=config.SEC_HEADERS, timeout=30)
    response.raise_for_status()
    payload = response.json()
    frame = pd.DataFrame(payload["data"], columns=payload["fields"])
    frame = frame[frame.exchange.isin(["NYSE", "Nasdaq", "NYSE American"])].copy()
    frame["ticker"] = frame.ticker.str.replace(".", "-", regex=False)
    frame = frame.drop_duplicates("ticker").sort_values("ticker")
    if frame.empty:
        raise ValueError("The exchange directory is empty")
    frame.to_csv(path, index=False)
    return frame


def snapshot_path(ticker):
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{0,14}", ticker):
        raise ValueError("Invalid company symbol")
    return config.DATA / "company_research" / ticker


def unsupported_security(ticker, info):
    kind = info.get("quoteType")
    name = str(info.get("longName", info.get("shortName", ""))).lower()
    return bool((kind and kind != "EQUITY") or re.search(r"-(?:P[A-Z]?|WS|WT|U)$", ticker)
                or re.search(r"\b(?:warrants?|preferred (?:stock|shares)|depositary shares)\b", name))


def build_snapshot(listing, refresh=False):
    """Build raw company evidence, leaving relative scoring to a real peer pool."""
    from .features import build_features
    from .sec_edgar import fetch_companyfacts
    from .yf_facts import facts_from_yfinance
    from .scan import _gates
    from .rates_fx import gs10_asof
    ticker = listing["ticker"]
    path = snapshot_path(ticker)
    today = dt.date.today()
    weekly = MD.download_weekly([ticker])
    try:
        facts = fetch_companyfacts(int(listing["cik"]), **({"refresh": True} if refresh else {}))
    except Exception:
        facts = None
    feat = None
    if facts:
        try:
            feat = build_features(facts, weekly, today)
            if feat is None:
                feat = build_features(facts, weekly, today, fallback_shares=MD.shares_outstanding(ticker))
        except Exception:
            feat = None
    if feat is None:
        try:
            proxy = facts_from_yfinance(ticker)
            feat = build_features(proxy, weekly, today) if proxy else None
            if feat:
                feat["dq_flags"] = str(feat.get("dq_flags", "")) + ";facts_yf"
        except Exception:
            feat = None
    if refresh and feat is None and (path / "config.json").exists():
        raise ValueError("No usable financial update; the previous company snapshot was retained")
    try:
        info = MD.yf.Ticker(ticker).get_info()
    except Exception:
        info = {}
    unsupported = unsupported_security(ticker, info)
    if unsupported:
        feat = None
    sector = {"Technology": "Information Technology", "Consumer Cyclical": "Consumer Discretionary",
              "Consumer Defensive": "Consumer Staples", "Healthcare": "Health Care",
              "Basic Materials": "Materials", "Financial Services": "Financials"}.get(info.get("sector"), info.get("sector", "Unknown"))
    row = {"ticker": ticker, "name": listing["name"], "cik": listing["cik"], "sector": sector,
           "sub_industry": info.get("industry", "Unknown"), "ig_name": sector + " (sector fallback)",
           "quality_score": float("nan"), "cheapness_score": float("nan"), "residual": float("nan"),
           "price": info.get("currentPrice"), "manual_research": True, "unsupported_security": unsupported}
    history = weekly.rename(columns={"close": "price"}).copy()
    if feat:
        history = feat.pop("history")
        history.insert(0, "ticker", ticker)
        row.update(feat)
    else:
        row["dq_flags"] = "Insufficient financial history; scores unavailable"
    prices = MD.price_features(weekly)
    if not prices.empty:
        row.update(prices.iloc[0].to_dict())
    frame = _gates(pd.DataFrame([row]), today, universe="nasdaq100")
    frame["gates_pass"] = False  # Research only; never inserted into index shortlists.
    cfg = {"asof": today.isoformat(), "research_only": True, "financial_version": config.FINANCIAL_VERSION, "financials_available": feat is not None, "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "gs10": gs10_asof(today)}
    path.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path / "metrics.csv", index=False)
    history.to_parquet(path / "history.parquet", index=False)
    (path / "config.json").write_text(json.dumps(cfg))
    return path


def score_snapshot(raw, reference):
    """Use a real reference pool; keep on-demand estimates separate from scan scores."""
    from .research_scores import add_research_scores
    ticker = raw.ticker.iloc[0]
    peers = reference[reference.ticker != ticker] if not reference.empty else reference
    pool = pd.concat([peers, raw], ignore_index=True)
    frame = add_research_scores(pool)
    frame = frame[frame.ticker == ticker].copy()
    for kind in ("quality", "cheapness"):
        if frame[f"{kind}_score_status"].iloc[0] == "Estimate":
            frame[f"{kind}_score_note"] = "On-demand research estimate using available fundamentals and saved index peers. " + str(frame[f"{kind}_score_note"].iloc[0])
    if peers.empty:
        frame["display_cheapness_score"] = float("nan")
        frame["cheapness_score_status"] = "Insufficient data"
        frame["cheapness_score_note"] = "Refresh an index first to provide valuation peers."
    unsupported = raw.get("unsupported_security", pd.Series([False])).iloc[0]
    unsupported = pd.notna(unsupported) and str(unsupported).lower() == "true"
    if unsupported or raw.sector.iloc[0] in config.RESTRICTED_SECTORS:
        for kind in ("quality", "cheapness"):
            frame[f"display_{kind}_score"] = float("nan")
            frame[f"{kind}_score_status"] = "Not supported"
            frame[f"{kind}_score_note"] = ("This instrument is not supported by common-stock company scoring." if unsupported else "This sector requires a different valuation model. Financials and options remain available for research.")
    return frame
