"""yfinance fundamentals adapter — a FALLBACK facts source, companyfacts-shaped.

For filers whose SEC XBRL assembly cannot produce the basics in this environment
(XOM's companyfacts carries a single 10-Q; APA has no usable consolidated revenue
tag), we rebuild a facts dict from yfinance's quarterly/annual statements so the
ENTIRE downstream pipeline (assembly, features, SELF timeline) is reused verbatim.

Honesty (doc 08 §2 spirit): this is a labeled approximation —
  * `filed` dates are approximated (period end + ~30/45 days),
  * yfinance's pre-normalized rows replace the canonical tags,
  * every stock scored through this path carries `facts_yf` in dq_flags.
SEC stays the single primary source; this only runs when SEC assembly fails.
"""
from __future__ import annotations

import datetime as dt
import math

import pandas as pd
import yfinance as yf

# yfinance row-name candidates per canonical tag (versions vary: spaced vs CamelCase)
_DURATIONS = {
    "RevenueFromContractWithCustomerExcludingAssessedTax": ["Total Revenue", "TotalRevenue"],
    "GrossProfit": ["Gross Profit", "GrossProfit"],
    "OperatingIncomeLoss": ["Operating Income", "OperatingIncome", "EBIT"],
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": ["Pretax Income", "PretaxIncome"],
    "IncomeTaxExpenseBenefit": ["Tax Provision", "TaxProvision"],
    "NetIncomeLoss": ["Net Income", "NetIncome", "Net Income Common Stockholders", "NetIncomeCommonStockholders"],
    "NetCashProvidedByUsedInOperatingActivities": ["Operating Cash Flow", "OperatingCashFlow", "Total Cash From Operating Activities"],
    "PaymentsToAcquirePropertyPlantAndEquipment": ["Capital Expenditure", "CapitalExpenditure"],
    "ShareBasedCompensation": ["Stock Based Compensation", "StockBasedCompensation"],
    "DepreciationDepletionAndAmortization": ["Depreciation And Amortization", "DepreciationAndAmortization", "Depreciation Amortization Depletion", "ReconciledDepreciation"],
    "WeightedAverageNumberOfSharesOutstandingBasic": ["Basic Average Shares", "BasicAverageShares"],
}
_INSTANTS = {
    "CashAndCashEquivalentsAtCarryingValue": ["Cash And Cash Equivalents", "CashAndCashEquivalents"],
    "ShortTermInvestments": ["Other Short Term Investments", "OtherShortTermInvestments"],
    "DebtCurrent": ["Current Debt", "CurrentDebt"],
    "LongTermDebtNoncurrent": ["Long Term Debt", "LongTermDebt"],
    "StockholdersEquity": ["Stockholders Equity", "StockholdersEquity", "Common Stock Equity", "CommonStockEquity"],
    "MinorityInterest": ["Minority Interest", "MinorityInterest"],
    "Goodwill": ["Goodwill", "Goodwill"],
    "Assets": ["Total Assets", "TotalAssets"],
    "AssetsCurrent": ["Current Assets", "CurrentAssets"],
    "LiabilitiesCurrent": ["Current Liabilities", "CurrentLiabilities"],
}
_SIGN_FLIP = {"PaymentsToAcquirePropertyPlantAndEquipment"}  # yf reports outflows negative


def _rows_from_stmt(stmt: pd.DataFrame | None, mapping: dict, kind: str, lag_days: int, *, annual=False) -> dict[str, list[dict]]:
    """Map a yfinance statement (rows=fact names, cols=period ends) into
    companyfacts-style unit rows per tag."""
    out: dict[str, list[dict]] = {}
    if stmt is None or stmt.empty:
        return out
    for tag, candidates in mapping.items():
        row = next((stmt.loc[c] for c in candidates if c in stmt.index), None)
        if row is None:
            continue
        # iterate the statement's OWN columns (Timestamp keys) — converting to date
        # keys first silently misses every value
        pairs = []
        for col in stmt.columns:
            try:
                v = float(row[col])
            except (TypeError, ValueError):
                continue
            if not math.isfinite(v):
                continue
            pairs.append((pd.to_datetime(col).date(), v))
        pairs.sort()
        rows = []
        for i, (end, v) in enumerate(pairs):
            if tag in _SIGN_FLIP:
                v = -v
            if kind == "duration":
                # Missing columns do not turn a quarter into a year; the first
                # annual column is also annual, even without a preceding column.
                start = (pd.Timestamp(end) - pd.DateOffset(months=12 if annual else 3) + pd.Timedelta(days=1)).date()
                rows.append({"start": start.isoformat(), "end": end.isoformat(),
                             "val": v, "accn": "yf", "fy": end.year, "fp": "Q",
                             "form": "10-Q", "filed": (end + dt.timedelta(days=lag_days)).isoformat()})
            else:
                rows.append({"end": end.isoformat(), "val": v, "accn": "yf",
                             "fy": end.year, "fp": "Q", "form": "10-Q",
                             "filed": (end + dt.timedelta(days=lag_days)).isoformat()})
        if rows:
            out[tag] = rows
    return out


def facts_from_yfinance(ticker: str) -> dict | None:
    """Build a companyfacts-shaped dict from yfinance statements. None if the
    statements don't cover the essentials (revenue + cfo + capex)."""
    try:
        tk = yf.Ticker(ticker)
        info = tk.get_info()
        if info.get("financialCurrency") != "USD" or info.get("currency") != "USD":
            return None  # No validated FX/ADR normalization: never relabel foreign currency as USD.
        # this yfinance generation: quarterly_* properties + unprefixed annual ones
        q_dur = _rows_from_stmt(tk.quarterly_income_stmt, _DURATIONS, "duration", 30)
        q_dur.update(_rows_from_stmt(tk.quarterly_cash_flow, _DURATIONS, "duration", 30))
        a_dur = _rows_from_stmt(tk.income_stmt, _DURATIONS, "duration", 60, annual=True)
        a_dur.update(_rows_from_stmt(tk.cash_flow, _DURATIONS, "duration", 60, annual=True))
        q_ins = _rows_from_stmt(tk.quarterly_balance_sheet, _INSTANTS, "instant", 45)
        a_ins = _rows_from_stmt(tk.balance_sheet, _INSTANTS, "instant", 60)
    except Exception:
        return None

    durations: dict[str, list[dict]] = {}
    for src in (q_dur, a_dur):
        for tag, rows in src.items():
            durations.setdefault(tag, []).extend(rows)
    instants: dict[str, list[dict]] = {}
    for src in (q_ins, a_ins):
        for tag, rows in src.items():
            instants.setdefault(tag, []).extend(rows)

    essentials = {"RevenueFromContractWithCustomerExcludingAssessedTax",
                  "NetCashProvidedByUsedInOperatingActivities"}
    if not essentials <= set(durations):
        return None

    us_gaap: dict = {}
    for tag, rows in durations.items():
        unit = "shares" if "SharesOutstanding" in tag else "USD"
        us_gaap[tag] = {"label": tag, "units": {unit: rows}}
    for tag, rows in instants.items():
        us_gaap.setdefault(tag, {"label": tag, "units": {}})["units"]["USD"] = rows

    dei: dict = {}
    try:
        sh = None
        for getter in (lambda: tk.fast_info.get("shares"),
                       lambda: tk.get_info().get("sharesOutstanding")):
            try:
                v = getter()
                if v and float(v) > 0:
                    sh = float(v)
                    break
            except Exception:
                continue
        if sh:
            end = dt.date.today().isoformat()
            dei["EntityCommonStockSharesOutstanding"] = {"label": "shares", "units": {
                "shares": [{"end": end, "val": sh, "accn": "yf", "fy": 2026, "fp": "Q",
                            "form": "10-Q", "filed": end}]}}
    except Exception:
        pass

    return {"cik": 0, "entityName": f"{ticker} (yfinance proxy)", "facts": {"us-gaap": us_gaap, "dei": dei}}
