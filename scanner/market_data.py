"""Market data: weekly price history (SELF windows + momentum), options, earnings dates.

Doc 08 §1/§4: yfinance with auto_adjust (split/dividend-consistent), polite pacing;
options and earnings are best-effort display inputs for the overlay subset.
Momentum uses the weekly grid (P(t-4w)/P(t-52w) = the doc 04 12m-1m construction:
iloc[-5] is 4 weekly bars before the last bar, iloc[-53] is 52 bars before it, so the
numerator/denominator gap is 48 weeks = 12m minus the skipped month).
"""
from __future__ import annotations

import datetime as dt
import math
import time

import numpy as np
import pandas as pd
import yfinance as yf

from . import config

_CHUNK = 40


def _ticker_frame(raw: pd.DataFrame, ticker: str, single: bool) -> pd.DataFrame | None:
    """One ticker's OHLCV sub-frame from a yf.download result, or None.

    Multi-ticker downloads give MultiIndex (ticker, field) columns with
    group_by='ticker', but single-ticker downloads return flat field-only
    columns in several yfinance versions -- attribute a flat frame to the
    ticker only when the chunk really had exactly one, so a lone ticker is
    never silently dropped. Returns the frame with NaN-close rows removed.
    """
    if isinstance(raw.columns, pd.MultiIndex):
        if ticker not in raw.columns.get_level_values(0):
            return None
        sub = raw[ticker]
    elif single:
        sub = raw  # flat columns: the whole frame IS the one ticker
    else:
        return None  # flat frame for a multi-ticker chunk: not attributable
    if not isinstance(sub, pd.DataFrame) or "Close" not in sub.columns:
        return None
    sub = sub.dropna(subset=["Close"])
    return None if sub.empty else sub


def download_weekly(tickers: list[str], years: int = 6, max_retries: int = 2) -> pd.DataFrame:
    """Long frame [ticker, week, close, volume] for the SELF window and momentum."""
    frames = []
    for i in range(0, len(tickers), _CHUNK):
        chunk = tickers[i : i + _CHUNK]
        raw = None
        for attempt in range(max_retries + 1):
            try:
                raw = yf.download(
                    chunk, period=f"{years}y", interval="1wk", auto_adjust=True,
                    group_by="ticker", progress=False, threads=True,
                )
                if raw is not None and not raw.empty:
                    break
            except Exception:
                raw = None
            if attempt < max_retries:
                time.sleep(2)  # backoff before the retry (doc 08 §1 politeness)
        if raw is None or raw.empty:
            print(f"[market] weekly download failed for {len(chunk)} ticker(s) after "
                  f"{max_retries + 1} attempts: {', '.join(chunk[:5])}"
                  f"{' ...' if len(chunk) > 5 else ''}")
            time.sleep(0.5)
            continue
        produced = []
        for t in chunk:
            try:
                sub = _ticker_frame(raw, t, single=len(chunk) == 1)
            except Exception:
                sub = None
            if sub is None:
                continue
            idx = pd.to_datetime(sub.index)
            if getattr(idx, "tz", None) is not None:
                idx = idx.tz_localize(None)  # yfinance weekly stamps may be tz-aware
            frames.append(pd.DataFrame({
                "ticker": t,
                "week": idx.normalize(),
                "close": sub["Close"].astype(float),
                "volume": sub["Volume"].astype(float) if "Volume" in sub.columns else np.nan,
            }))
            produced.append(t)
        missed = [t for t in chunk if t not in produced]
        if missed:
            print(f"[market] no weekly data for {len(missed)} ticker(s): {', '.join(missed[:5])}"
                  f"{' ...' if len(missed) > 5 else ''}")
        time.sleep(0.5)
    cols = ["ticker", "week", "close", "volume"]
    if not frames:
        return pd.DataFrame(columns=cols)
    out = pd.concat(frames, ignore_index=True).sort_values(["ticker", "week"], kind="stable")
    out = out.groupby(["ticker", "week"], as_index=False).last()  # dedupe any repeated weeks
    return out


def price_features(weekly: pd.DataFrame) -> pd.DataFrame:
    """Momentum / drawdown / liquidity per ticker on the weekly grid (doc 04 §4.1).

    mom_12_1 = P(t-4w)/P(t-52w) - 1 (iloc[-5] / iloc[-53]): the weekly-grid version
    of the doc's P(t-21d)/P(t-252d) -- a 48-week gap, i.e. 12m skipping the last
    month. ret_12m = P(t)/P(t-52w) - 1. dd_52w = price / 52w high - 1 with
    min_periods=40 (tolerates up to 12 missing weeks inside the window).
    adv_usd pairs each week's volume with that week's close (13-week mean of
    weekly dollar volume / 5): valuing 3-month-old volume at the latest close
    would overstate liquidity after a rally and understate it after a sell-off.
    Tickers with < 60 weekly bars are skipped -- the windows above need >= 53.
    """
    rows = []
    for t, g in weekly.groupby("ticker"):
        g = g.sort_values("week").reset_index(drop=True)
        if len(g) < 60:
            continue
        close = g["close"]
        last = float(close.iloc[-1])
        c_1m = close.iloc[-5]   # 4 weekly bars back = the skipped month
        c_12m = close.iloc[-53]  # 52 weekly bars back
        ok12 = pd.notna(c_12m) and c_12m > 0
        mom_12_1 = (float(c_1m) / float(c_12m) - 1.0) if ok12 and pd.notna(c_1m) else np.nan
        ret_12m = (last / float(c_12m) - 1.0) if ok12 else np.nan
        roll_max = close.rolling(52, min_periods=40).max().iloc[-1]
        dd_52w = last / roll_max - 1.0 if pd.notna(roll_max) and roll_max > 0 else np.nan
        wk = g.tail(13)
        adv = float((wk["volume"] * wk["close"]).mean() / 5.0)  # avg weekly $ volume / trading days
        rows.append({
            "ticker": t, "price": last,
            "mom_12_1": mom_12_1, "dd_52w": dd_52w, "ret_12m": ret_12m,
            "adv_usd": adv,
            "hi_52w": float(roll_max) if pd.notna(roll_max) else np.nan,
        })
    return pd.DataFrame(rows, columns=["ticker", "price", "mom_12_1", "dd_52w",
                                       "ret_12m", "adv_usd", "hi_52w"])


def shares_outstanding(ticker: str) -> float | None:
    """Current shares outstanding from yfinance — last-resort fallback for filers
    whose share tags don't survive companyfacts (STZ-style A/B classes dimension
    even the weighted-average tags). Best-effort; None on failure."""
    try:
        tk = yf.Ticker(ticker)
        for getter in (lambda: tk.fast_info.get("shares"),
                       lambda: tk.get_info().get("sharesOutstanding")):
            try:
                v = getter()
                if v and float(v) > 0:
                    return float(v)
            except Exception:
                continue
        return None
    except Exception:
        return None


def hv_30d(ticker: str, months: int = 3) -> float | None:
    """Annualized 30d realized vol: log-return std x sqrt(252) on the last 30
    daily closes (auto_adjust=True, consistent with the weekly grid). None on
    any failure."""
    try:
        df = yf.Ticker(ticker).history(period=f"{months}mo", interval="1d", auto_adjust=True)
        if df is None or len(df) < 25:
            return None
        c = pd.to_numeric(df["Close"], errors="coerce")
        r = np.log(c[c > 0]).diff().dropna().tail(30)  # drop non-positive closes: log(0) = -inf
        if len(r) < 20:
            return None
        s = r.std()
        return float(s * np.sqrt(252)) if pd.notna(s) else None
    except Exception:
        return None


def next_earnings_date(ticker: str, today: dt.date | None = None) -> dt.date | None:
    """Next scheduled earnings date as a plain dt.date (doc 08 §1: patchy ->
    None when unknown). limit=8 spans ~4 quarters ahead, always enough for the
    next report; unknown/failed -> None. `today` threads the scan's as-of date
    (doc 08 §4) instead of wall-clock time."""
    try:
        edf = yf.Ticker(ticker).get_earnings_dates(limit=8)
        if edf is None or len(edf) == 0:
            return None
        idx = pd.to_datetime(edf.index)
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_localize(None)
        today = today or dt.date.today()
        future = [d.date() for d in idx if d.date() >= today]
        return min(future) if future else None
    except Exception:
        return None


def breadth_from_actions(events: pd.DataFrame | None, asof: dt.date,
                         window_days: int = 90) -> dict | None:
    """Net analyst-action breadth over the trailing window (pure, testable).

    yfinance upgrades/downgrades rows carry a rating Action (up/down/init/reit/main)
    and a priceTargetAction (Raises/Lowers/Keeps). We count rating ups/downs AND
    target raises/lowers — a target change is a revision even when the rating holds.
    Everything else is ignored. Events after `asof` are invisible (point-in-time,
    doc 08 §4). Returns {"net": ups - downs, "ups": n, "downs": n} or None when
    the frame has no usable in-window events.
    """
    if events is None or len(events) == 0:
        return None
    idx = pd.to_datetime(events.index, utc=True, errors="coerce")
    asof_ts = pd.Timestamp(asof, tz="UTC") + pd.Timedelta(days=1)  # same-day events count
    lo = asof_ts - pd.Timedelta(days=window_days)
    act = events["Action"].astype("string").str.lower() if "Action" in events.columns else None
    pta = (events["priceTargetAction"].astype("string").str.lower()
           if "priceTargetAction" in events.columns else None)
    ups = downs = 0
    for i, ts in enumerate(idx):
        if pd.isna(ts) or not (lo < ts <= asof_ts):
            continue
        a = act.iloc[i] if act is not None else None
        p = pta.iloc[i] if pta is not None else None
        if a == "up" or p == "raises":
            ups += 1
        elif a == "down" or p == "lowers":
            downs += 1
    if ups == 0 and downs == 0:
        return None
    return {"net": ups - downs, "ups": ups, "downs": downs}


def analyst_revision_breadth(ticker: str, asof: dt.date) -> dict | None:
    """yfinance analyst-action history -> breadth_from_actions. Best-effort:
    None on any failure (the beaten flag then simply cannot fire for the name)."""
    try:
        ev = yf.Ticker(ticker).upgrades_downgrades
        return breadth_from_actions(ev, asof)
    except Exception:
        return None


def _atm_strike(strikes, spot: float) -> float | None:
    """Strike closest to spot; NaN/None strikes ignored."""
    ks = [float(s) for s in strikes if s is not None and pd.notna(s)]
    return min(ks, key=lambda s: abs(s - spot)) if ks else None


def options_atm(ticker: str, spot: float, today: dt.date | None = None) -> dict | None:
    """ATM IV row (nearest expiry >= OPTIONS_MIN_DTE (21), strike closest to
    spot, IV = mean of the put and call IV at that strike) + liquidity gate on
    the ~35 DTE expiry (OI >= 500, (ask-bid)/mid <= 10%). Best-effort; None on
    failure. The two rows are extracted independently so one bad chain leg
    doesn't kill the other. Doc 08 §4."""
    today = today or dt.date.today()
    if spot is None or not (float(spot) > 0):
        return None
    try:
        tk = yf.Ticker(ticker)
        expiries = [dt.date.fromisoformat(e) for e in tk.options]
        ok = [e for e in expiries if (e - today).days >= config.OPTIONS_MIN_DTE]
        if not expiries or not ok:
            return None
        out: dict = {}

        try:  # --- IV row: nearest expiry >= 21 DTE -------------------------
            exp = min(ok)
            oc = tk.option_chain(exp.isoformat())
            k = _atm_strike(oc.puts["strike"].tolist(), float(spot))
            if k is None:
                k = _atm_strike(oc.calls["strike"].tolist(), float(spot))
            if k is not None:
                prow = oc.puts[oc.puts["strike"] == k]
                crow = oc.calls[oc.calls["strike"] == k]
                if len(prow) and len(crow):
                    ivs = [float(v) for v in
                           (prow["impliedVolatility"].iloc[0], crow["impliedVolatility"].iloc[0])
                           if pd.notna(v)]
                    if ivs:  # average whichever side quotes an IV (NaN on stale legs)
                        out["iv"] = {
                            "iv_expiry": exp.isoformat(), "iv_strike": float(k),
                            "iv_atm": sum(ivs) / len(ivs),
                            "iv_dte": (exp - today).days,
                        }
        except Exception:
            pass

        try:  # --- liquidity gate: 30-45 DTE window, nearest to 35 (doc 08 §4) -----
            near35 = None
            gate_ok = [e for e in ok if 30 <= (e - today).days <= 45]
            if gate_ok:
                near35 = min(gate_ok, key=lambda e: abs((e - today).days - config.OPTIONS_GATE_TARGET_DTE))
                oc35 = tk.option_chain(near35.isoformat())
                k35 = _atm_strike(oc35.puts["strike"].tolist(), float(spot))
            else:
                k35 = None  # nothing listed in the 30-45 window -> no gate row, honestly
            if near35 is not None and k35 is not None:
                p = oc35.puts[oc35.puts["strike"] == k35].iloc[0]
                bid, ask = float(p["bid"]), float(p["ask"])
                mid = (bid + ask) / 2.0
                oi = float(p["openInterest"]) if pd.notna(p["openInterest"]) else 0.0
                spread = (ask - bid) / mid if mid > 0 else None  # mid<=0 (bid=ask=0, crossed) -> no spread
                out["gate"] = {
                    "gate_expiry": near35.isoformat(), "gate_strike": float(k35),
                    "gate_oi": oi, "gate_bid": bid, "gate_ask": ask,
                    "gate_spread_pct": spread,
                    "gate_pass": bool(
                        mid > 0
                        and oi >= config.OPTIONS_GATE_MIN_OI
                        and spread is not None
                        and spread <= config.OPTIONS_GATE_MAX_SPREAD
                    ),
                }
        except Exception:
            pass

        return out or None
    except Exception:
        return None


# ------------------------------------------------- live put panel (v1, 2026-09) --
# Live quotes for the selected ticker on the Ideas page. Never touches scoring,
# gates or the nightly overlay: options never change who is highlighted.

PUT_PANEL_MAX_DTE = 60


def strike_target_pct(dte: int) -> float:
    """Standing-put strike target vs spot by DTE bucket: <7d ATM (a 95% put at
    1 DTE is usually junk), 7-20d ~0.97, >=21d ~0.95."""
    if dte < 7:
        return 1.00
    if dte < 21:
        return 0.97
    return 0.95


def listed_expiries(ticker: str, today: dt.date | None = None,
                    max_dte: int = PUT_PANEL_MAX_DTE) -> list[dt.date]:
    """Listed expiry dates with DTE in 0..max_dte (0 allowed — OPTIONS_MIN_DTE
    is a nightly-overlay floor, not this panel's). Best-effort: [] on failure."""
    today = today or dt.date.today()
    try:
        dates = yf.Ticker(ticker).options or ()
        out = []
        for s in dates:
            try:
                d = dt.date.fromisoformat(str(s))
            except ValueError:
                continue
            if 0 <= (d - today).days <= max_dte:
                out.append(d)
        return sorted(set(out))
    except Exception:
        return []


def put_quote(ticker: str, spot: float, expiry: dt.date, target_pct: float,
              today: dt.date | None = None) -> dict | None:
    """One listed PUT nearest target_pct*spot on one expiry. Premium prefers a
    positive bid, falls back to mid, then last — bid=0 is marked weak_quote and
    never counts as premium 0. Best-effort: None on any failure."""
    today = today or dt.date.today()
    if spot is None or not (float(spot) > 0) or expiry is None:
        return None
    try:
        puts = yf.Ticker(ticker).option_chain(expiry.isoformat()).puts
        if puts is None or puts.empty:
            return None

        def _n(x):
            try:
                v = float(x)
                return v if pd.notna(v) else None
            except (TypeError, ValueError):
                return None

        strikes = pd.to_numeric(puts["strike"], errors="coerce").dropna()
        if strikes.empty:
            return None
        target = float(target_pct) * float(spot)
        row = puts.loc[(strikes - target).abs().idxmin()]
        bid, ask = _n(row.get("bid")), _n(row.get("ask"))
        last, oi = _n(row.get("lastPrice")), _n(row.get("openInterest"))
        iv = _n(row.get("impliedVolatility"))
        mid = ((bid + ask) / 2
               if (bid is not None and bid >= 0 and ask is not None and ask > 0) else None)
        premium = next((p for p in (bid, mid, last) if p is not None and p > 0), None)
        weak = premium is not None and (bid is None or bid <= 0)
        return {
            "strike": float(row["strike"]), "bid": bid, "ask": ask, "mid": mid,
            "last": last, "premium": premium, "oi": oi, "iv": iv,
            "expiry": expiry, "dte": (expiry - today).days, "weak_quote": weak,
        }
    except Exception:
        return None


def premium_pct_of_strike(quote: dict | None) -> float | None:
    """Cash % of strike for the standing put; None without a usable premium."""
    if not quote or quote.get("premium") is None:
        return None
    k = quote.get("strike") or 0
    return quote["premium"] / k if k > 0 else None


def annualized_premium(premium_pct: float | None, dte: int) -> float | None:
    """365-annualized cash % — only meaningful from ~a month out (>=21 DTE).
    Short-DTE annualization is a misleading big number and is suppressed."""
    if premium_pct is None or dte < 21:
        return None
    return premium_pct * 365.0 / dte


def _n(x):
    try:
        v = float(x)
        return v if pd.notna(v) else None
    except (TypeError, ValueError):
        return None


def _premium_from_quotes(bid, ask, last) -> tuple[float | None, float | None, bool]:
    """premium, mid, weak_quote. Bid=0 is not premium 0."""
    mid = ((bid + ask) / 2
           if (bid is not None and bid >= 0 and ask is not None and ask > 0) else None)
    premium = next((p for p in (bid, mid, last) if p is not None and p > 0), None)
    weak = premium is not None and (bid is None or bid <= 0)
    return premium, mid, weak


def fetch_option_chain(ticker: str, expiry: dt.date) -> dict | None:
    """Puts + calls for one expiry as normalized frames. Best-effort: None on failure."""
    try:
        oc = yf.Ticker(ticker).option_chain(expiry.isoformat())
    except Exception:
        return None

    def _prep(df: pd.DataFrame | None, side: str) -> pd.DataFrame:
        if df is None or df.empty or "strike" not in df.columns:
            return pd.DataFrame()
        out = pd.DataFrame({
            "side": side,
            "strike": pd.to_numeric(df["strike"], errors="coerce"),
            "bid": pd.to_numeric(df["bid"], errors="coerce") if "bid" in df.columns else np.nan,
            "ask": pd.to_numeric(df["ask"], errors="coerce") if "ask" in df.columns else np.nan,
            "last": pd.to_numeric(df["lastPrice"] if "lastPrice" in df.columns
                                 else df["last"], errors="coerce")
                    if ("lastPrice" in df.columns or "last" in df.columns) else np.nan,
            "volume": pd.to_numeric(df["volume"], errors="coerce") if "volume" in df.columns else np.nan,
            "oi": pd.to_numeric(df["openInterest"], errors="coerce") if "openInterest" in df.columns else np.nan,
            "iv": pd.to_numeric(df["impliedVolatility"], errors="coerce")
                  if "impliedVolatility" in df.columns else np.nan,
        })
        itm = df["inTheMoney"] if "inTheMoney" in df.columns else None
        out["in_the_money"] = itm.astype(bool) if itm is not None else pd.NA
        out = out.dropna(subset=["strike"]).sort_values("strike").reset_index(drop=True)
        return out

    try:
        underlying = getattr(oc, "underlying", None) or {}
        from .opportunities import number
        spot = number(underlying.get("regularMarketPrice"))
        timestamp = number(underlying.get("regularMarketTime"))
        quote_time = (dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).isoformat(timespec="seconds")
                      if timestamp is not None else None)
        return {"puts": _prep(getattr(oc, "puts", None), "put"),
                "calls": _prep(getattr(oc, "calls", None), "call"),
                "underlying_price": spot if spot is not None and spot > 0 else None,
                "underlying_time": quote_time,
                "underlying_currency": underlying.get("currency")}
    except Exception:
        return None


def pick_nearest_strike(df: pd.DataFrame, spot: float, target_pct: float) -> dict | None:
    """Row nearest target_pct*spot, plus premium/mid/weak_quote. None if empty."""
    if df is None or df.empty or spot is None or not (float(spot) > 0):
        return None
    strikes = pd.to_numeric(df["strike"], errors="coerce")
    valid = df.loc[strikes.notna()].copy()
    if valid.empty:
        return None
    target = float(target_pct) * float(spot)
    i = (valid["strike"] - target).abs().idxmin()
    row = valid.loc[i]
    bid, ask, last = _n(row.get("bid")), _n(row.get("ask")), _n(row.get("last"))
    premium, mid, weak = _premium_from_quotes(bid, ask, last)
    return {
        "side": row.get("side"), "strike": float(row["strike"]),
        "bid": bid, "ask": ask, "mid": mid, "last": last,
        "premium": premium, "oi": _n(row.get("oi")), "iv": _n(row.get("iv")),
        "volume": _n(row.get("volume")), "weak_quote": weak,
        "in_the_money": (bool(row["in_the_money"])
                         if "in_the_money" in row.index and pd.notna(row["in_the_money"]) else None),
    }


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black_scholes_greeks(side: str, spot: float, strike: float, dte: int,
                         iv: float | None, rate: float = 0.0) -> dict:
    """Long-option greeks from listed IV. Theta is per calendar day. Empty dict
    if IV/spot/strike unusable. 0 DTE uses T = 1/365 so the formula doesn't blow up."""
    if side not in ("put", "call"):
        return {}
    if any(v is None or not (float(v) > 0) for v in (spot, strike, iv)):
        return {}
    T = max(int(dte), 0) / 365.0 or (1.0 / 365.0)
    sig = float(iv)
    sqrtT = math.sqrt(T)
    try:
        d1 = (math.log(float(spot) / float(strike)) + (rate + 0.5 * sig * sig) * T) / (sig * sqrtT)
    except (ValueError, ZeroDivisionError):
        return {}
    d2 = d1 - sig * sqrtT
    nd1, npd1 = _norm_cdf(d1), _norm_pdf(d1)
    disc = math.exp(-rate * T)
    if side == "call":
        delta = nd1
        theta_yr = -float(spot) * npd1 * sig / (2 * sqrtT) - rate * float(strike) * disc * _norm_cdf(d2)
    else:
        delta = nd1 - 1.0
        theta_yr = -float(spot) * npd1 * sig / (2 * sqrtT) + rate * float(strike) * disc * _norm_cdf(-d2)
    gamma = npd1 / (float(spot) * sig * sqrtT)
    vega = float(spot) * npd1 * sqrtT / 100.0   # per 1 vol point
    return {"delta": delta, "gamma": gamma, "theta": theta_yr / 365.0, "vega": vega}


def contract_analytics(side: str, spot: float, strike: float, premium: float | None,
                       dte: int, iv: float | None, rate: float = 0.0) -> dict:
    """Breakeven + CSP lens + long greeks for one listed put or call. Short-option
    P&L is the opposite sign of long delta; breakeven is the same number (K±premium).

    CSP fields: cushion_pct = distance to breakeven as % of spot in the OTM
    direction (negative = already through BE); assignment_risk = |delta| as a
    rough Black-Scholes P(finish ITM); pay_vs_cushion = premium per 1
    percentage point of cushion (None when through BE or no premium)."""
    side = "put" if str(side).lower().startswith("p") else "call"
    out: dict = {"side": side, "spot": spot, "strike": strike, "premium": premium,
                 "dte": dte, "iv": iv}
    if premium is not None and strike and float(strike) > 0 and premium >= 0:
        out["breakeven"] = (float(strike) - float(premium) if side == "put"
                            else float(strike) + float(premium))
        out["cash_pct"] = float(premium) / float(strike)
        if spot and float(spot) > 0:
            out["be_vs_spot"] = out["breakeven"] / float(spot) - 1.0
            out["strike_vs_spot"] = float(strike) / float(spot) - 1.0
            out["intrinsic"] = (max(float(strike) - float(spot), 0.0) if side == "put"
                                else max(float(spot) - float(strike), 0.0))
            out["extrinsic"] = float(premium) - out["intrinsic"]
            out["cushion_pct"] = ((float(spot) - out["breakeven"]) / float(spot)
                                  if side == "put"
                                  else (out["breakeven"] - float(spot)) / float(spot))
            if out["cushion_pct"] > 0:
                out["pay_vs_cushion"] = float(premium) / (out["cushion_pct"] * 100.0)
    out.update(black_scholes_greeks(side, spot, strike, dte, iv, rate=rate or 0.0))
    if "delta" in out:
        out["assignment_risk"] = abs(out["delta"])
    # comparable income lens: cash % of strike (posted), and that income per unit
    # of assignment risk (|delta|, a BS estimate). Annualized ratio vs `rate` (the
    # 10Y) only exists from 21 DTE — short-DTE annualization would mislead.
    if out.get("cash_pct") is not None and out.get("assignment_risk", 0.0) > 0:
        out["income_per_risk"] = out["cash_pct"] / out["assignment_risk"]
    ann = annualized_premium(out.get("cash_pct"), dte)
    if ann is not None:
        out["annualized"] = ann
        if rate and float(rate) > 0:
            out["ann_vs_rate"] = ann / float(rate)
    return out


# --- wheel decision block (Ideas → Options, selected contract) -----------------
# Plain-English numbers for the selected put. Display-only: these never rank,
# filter, or change highlight membership. Yield math mirrors the overlay strike
# yields (doc 06 §7): fcf_adj / (strike × shares + stack).

def _num(v):
    """None-safe float: accepts numpy scalars, rejects NaN/inf/None/pd.NA."""
    if v is None or isinstance(v, bool):
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if not (math.isnan(v) or math.isinf(v)) else None


def breakeven_price(strike, premium) -> float | None:
    """Your cost per share if assigned: strike minus the premium you keep."""
    k, p = _num(strike), _num(premium)
    if k is None or p is None:
        return None
    return k - p


# legacy name kept for callers written before the plain-English relabel
net_basis = breakeven_price


def margin_needed(strike, contracts: int = 1) -> float | None:
    """Full cash to secure one (or N) contract: strike × 100. Not Reg-T /
    portfolio margin — this is the cash-secured number."""
    k = _num(strike)
    return None if k is None else k * 100 * int(contracts)


cash_locked = margin_needed


def fcf_yield_at_price(fcf, shares, stack, price) -> float | None:
    """TTM reported FCF over the whole-business value at that share price:
    fcf / (price × shares + non-equity stack). The assignment test prices the
    equity at the BREAKEVEN, not the strike — the put premium lowers your cost."""
    f, s, d, p = _num(fcf), _num(shares), _num(stack), _num(price)
    if None in (f, s, d, p) or s <= 0:
        return None
    ev = p * s + d
    return f / ev if ev > 0 else None


def own_verdict(fcf_yield, gs10: float = 0.0) -> str | None:
    """One-word answer vs the cash bar: max(4%, 10Y)."""
    y = _num(fcf_yield)
    if y is None:
        return None
    return "good to own" if y >= max(0.04, float(gs10 or 0.0)) else "thin"


def wait_return(premium, strike, dte: int,
                earnings_in_window: bool = False) -> dict:
    """Premium over cash locked, with the annualize-or-not rule: yearly only
    at ≥21 DTE and only when earnings are not 0–5 days out."""
    p, k = _num(premium), _num(strike)
    pct = p / k if (p is not None and k is not None and k > 0) else None
    ann = None
    note = None
    if pct is not None:
        if earnings_in_window:
            note = "earnings in window — do not yearly"
        elif int(dte) >= 21:
            ann = pct * 365.0 / int(dte)
        else:
            note = "too short to yearly"
    return {"pct": pct, "annualized": ann, "note": note}


def same_strike_call(calls, strike):
    """Exact-strike call row from a chain frame, or None. No fuzzy matching:
    the companion line quotes THIS strike or admits there is none."""
    if calls is None or getattr(calls, "empty", True):
        return None
    k = _num(strike)
    if k is None:
        return None
    hit = calls[pd.to_numeric(calls["strike"], errors="coerce") == k]
    return None if hit.empty else hit.iloc[0]


def called_away_keep(strike, basis, call_premium) -> float | None:
    """What you keep if the shares get called away: the put premium you already
    kept (strike − breakeven) plus the new call premium. The call caps the
    shares at the strike — there is no price gain above it."""
    k, b, c = _num(strike), _num(basis), _num(call_premium)
    if None in (k, b, c):
        return None
    return (k - b) + c
