"""Display-only evidence and option comparisons; never modifies scanner scores."""
from __future__ import annotations

import datetime as dt
import math
import pandas as pd


def number(value):
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (ValueError, TypeError):
        return None


def bid_quote(row):
    bid, ask = number(row.get("bid")), number(row.get("ask"))
    if bid is None or bid <= 0:
        return None, "No positive bid"
    if ask is not None and ask < bid:
        return None, "Crossed quote"
    if ask is None:
        return bid, "Ask unavailable"
    if (ask-bid)/max((ask+bid)/2, .01) >= .5:
        return bid, "Wide spread"
    return bid, "Positive bid"


def choose_expirations(by_ticker: dict[str, list[str]], today: dt.date, target_days: int):
    """Prefer one exact expiration across all available chains; label fallback."""
    valid = {t: sorted({e for e in dates if 0 <= (dt.date.fromisoformat(e)-today).days <= 90})
             for t, dates in by_ticker.items()}
    available = [set(dates) for dates in valid.values() if dates]
    common = set.intersection(*available) if available else set()
    nearest = lambda dates: min(dates, key=lambda e: (abs((dt.date.fromisoformat(e)-today).days-target_days), e))
    if common:
        exp = nearest(common)
        return {t: exp if dates else None for t, dates in valid.items()}, True
    return {t: nearest(dates) if dates else None for t, dates in valid.items()}, False


def put_comparison(ticker, raw, expiry, today, discount, buy_price=None, fetched="", next_earnings=None):
    item = {"Stock": ticker, "Status": "No chain available", "Expiration": expiry,
            "Days": (dt.date.fromisoformat(expiry)-today).days if expiry else None, "Fetched": fetched}
    if not raw or not expiry:
        return item
    spot = number(raw.get("underlying_price"))
    item.update({"Share price": spot, "Share quote time": raw.get("underlying_time"), "My buy price": buy_price})
    if spot is None or spot <= 0:
        item["Status"] = "Share quote unavailable"
        return item
    puts = raw.get("puts", pd.DataFrame())
    if puts.empty:
        item["Status"] = "No puts available"
        return item
    strikes = pd.to_numeric(puts.strike, errors="coerce")
    valid = puts[strikes.notna() & (strikes > 0)].copy()
    if buy_price is not None:
        # A saved price is a ceiling, never round up to a more expensive strike.
        valid = valid[valid.strike <= buy_price]
    if valid.empty:
        item["Status"] = "No strike at or below my buy price"
        return item
    target = buy_price if buy_price is not None else spot*(1-discount/100)
    q = valid.loc[(valid.strike-target).abs().idxmin()]
    strike = float(q.strike)
    bid, status = bid_quote(q)
    item.update({"Status": status, "Strike": strike, "Cash required": strike*100,
                 "Premium": bid*100 if bid is not None else None,
                 "Return on cash": bid/strike if bid is not None else None,
                 "Premium / stock": bid/spot if bid is not None else None,
                 "Entry discount": 1-strike/spot,
                 "Net entry": strike-bid if bid is not None else None,
                 "Earnings": "Before expiry" if next_earnings and today <= next_earnings <= dt.date.fromisoformat(expiry)
                     else "After expiry" if next_earnings and next_earnings > dt.date.fromisoformat(expiry) else "Unknown"})
    return item


def research_universe(metrics):
    """Material reinvestment proxy plus growth; a research lane, not a quality gate."""
    out = metrics.copy()
    def col(key):
        return pd.to_numeric(out.get(key, pd.Series(float("nan"), index=out.index)), errors="coerce")
    revenue = col("revenue_ttm").where(col("revenue_ttm") > 0)
    out["investment_gap"] = (col("fcf_owner_ttm")-col("fcf_adj_ttm"))/revenue
    out["operating_margin"] = col("ebit_ttm")/revenue
    return out[(col("rev_cagr5") >= .05) & (col("fcf_owner_ttm") > 0) & (out.investment_gap >= .02)].sort_values("investment_gap", ascending=False)


def brief(row):
    q, rev = number(row.get("display_quality_score", row.get("quality_score"))), number(row.get("rev_cagr5"))
    cash, residual = number(row.get("fcf_yield")), number(row.get("residual"))
    debt, coverage = number(row.get("nd_ebitda")), number(row.get("input_coverage"))
    strength = f"Business quality {q:.0f}/100" if q is not None else "Business quality is unscored"
    if row.get("quality_score_status") == "Estimate":
        strength += " (research estimate; partial data)"
    if rev is not None:
        strength += f"; revenue grew {rev:.1%} a year over five years."
    price = ("Below the model’s valuation for its quality" if residual < 0 else "At or above the model’s valuation for its quality") if residual is not None else "Model valuation unavailable"
    if cash is not None:
        price += f"; reported business cash yield {cash:.1%}."
    if coverage is not None and coverage < .8:
        concern = f"Only {coverage:.0%} of scoring inputs are available."
    elif cash is not None and cash < 0:
        concern = "Reported cash flow is negative. Check how investment is being funded."
    elif debt is not None and debt >= 3:
        concern = f"Net debt is {debt:.1f}× EBITDA. Check funding needs and debt maturities."
    elif any(number(row.get(k)) is not None and number(row.get(k)) < floor
             for k, floor in [("brake_gm", .97), ("brake_fcf_margin", .95), ("brake_roic", .90)]):
        checks = [("Gross margin", "brake_gm", .97), ("Cash-flow margin", "brake_fcf_margin", .95), ("Return on capital", "brake_roic", .90)]
        label, key, _ = min((item for item in checks if number(row.get(item[1])) is not None and number(row.get(item[1])) < item[2]),
                            key=lambda item: number(row.get(item[1]))/item[2])
        concern = f"{label} is {number(row.get(key)):.0%} of its five-year average, below the scanner’s historical floor."
    elif q is None:
        concern = "The quality score is unavailable; inspect the financial history and missing inputs."
    else:
        concern = "Check the earnings date and option liquidity before choosing an entry."
    return {"Business strength": strength, "Price attractiveness": price, "Main concern": concern}
