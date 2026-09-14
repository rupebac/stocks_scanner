"""Display filters with explicit, short-lived options evidence."""
import datetime as dt
import time
import pandas as pd
from . import market_data as MD
from .opportunities import number

MAX_AGE_SECONDS = 15 * 60


def size_filter(frame, minimum=0., maximum=0.):
    if minimum == 0 and maximum == 0:
        return frame.copy()
    cap = pd.to_numeric(frame.mktcap, errors="coerce")
    mask = cap.notna() & (cap >= minimum * 1e9)
    if maximum > 0:
        mask &= cap <= maximum * 1e9
    return frame.loc[mask].copy()


def fetch_activity(ticker, today=None):
    today = today or dt.date.today()
    result = {"checked_at": time.time(), "contracts": [], "status": "Unavailable"}
    try:
        dates = MD.listed_expiries(ticker, today, max_dte=90)
        if not dates:
            return result
        expiry = min(dates, key=lambda d: (abs((d-today).days - 30), d))
        raw = MD.fetch_option_chain(ticker, expiry)
        spot = number((raw or {}).get("underlying_price"))
        if not raw or spot is None or spot <= 0:
            return result
        puts = raw.get("puts", pd.DataFrame())
        if puts.empty:
            return result
        puts = puts[(puts.strike >= spot * .8) & (puts.strike <= spot)]
        result.update(status="Checked", expiry=expiry.isoformat(), spot=spot,
                      contracts=[{"oi": number(r.get("oi")), "volume": number(r.get("volume"))} for _, r in puts.iterrows()])
    except Exception:
        pass
    return result


def activity_status(snapshot, min_oi=0, min_volume=0, now=None):
    if not snapshot:
        return "Not checked"
    if (now if now is not None else time.time()) - snapshot.get("checked_at", 0) > MAX_AGE_SECONDS:
        return "Stale"
    if snapshot.get("status") != "Checked":
        return "Unavailable"
    contracts = snapshot.get("contracts", [])
    def meets(contract):
        return all(threshold == 0 or (number(contract.get(key)) is not None and number(contract.get(key)) >= threshold)
                   for key, threshold in [("oi", min_oi), ("volume", min_volume)])
    if any(meets(c) for c in contracts):
        return "Matches"
    if any(any(threshold > 0 and number(c.get(key)) is None for key, threshold in [("oi", min_oi), ("volume", min_volume)]) for c in contracts):
        return "Unavailable"
    return "Below minimum"


def apply_activity(frame, snapshots, min_oi=0, min_volume=0, now=None):
    out = frame.copy()
    out["options_activity"] = [activity_status(snapshots.get(t), min_oi, min_volume, now) for t in out.ticker]
    if min_oi or min_volume:
        return out[out.options_activity == "Matches"]
    return out
