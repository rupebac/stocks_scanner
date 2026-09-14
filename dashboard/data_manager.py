"""Data manager page: one knob — re-download everything and re-scan (docs 01/08).

Operational only, no scan results are edited. The single action forces a fresh
copy of every upstream source (universe, DGS10, all SEC companyfacts; prices and
options are always fetched live by a scan) and re-runs the full scan. The job
log streams below the button.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

JOB_DIR = ROOT / "data" / "jobs"
PY = ROOT / ".venv" / "bin" / "python"


SOURCES = [
    # (source, used for, cache & freshness)
    ("Wikipedia — S&P 500 constituents",
     "Universe: symbols, CIKs, GICS sector/sub-industry, date added (→ the ≥1y index-tenure gate)",
     "data/derived/universe.csv · 7-day TTL · re-downloaded by the refresh button"),
    ("GICS reference map (local file)",
     "Sub-industry → industry group, defining the peer ladder behind every percentile and the residual fit",
     "data/reference/gics_subindustry_to_ig.csv · versioned with the code"),
    ("SEC EDGAR — XBRL companyfacts",
     "All fundamentals: revenue, margins, FCF & SBC, D&A, debt, cash, shares — point-in-time by filing date",
     "data/raw/sec/CIK*.json · auto-refetched when a new 10-K/10-Q appears; force-refetched by the button"),
    ("SEC EDGAR — submissions index",
     "Filing dates per company: decides which companyfacts caches are stale",
     "data/raw/sec/*_submissions.json · 12h TTL"),
    ("FRED — DGS10 (10Y Treasury)",
     "Rate anchor: the flagship requires FCF yield ≥ max(4%, DGS10)",
     "data/raw/fred_dgs10.csv · full CSV on refresh"),
    ("yfinance — weekly prices (5y)",
     "Price, dollar volume (ADV ≥ $20M gate), 12-1m momentum (the “beaten” arm)",
     "never cached — fetched live on every scan"),
    ("yfinance — shares outstanding",
     "Fallback share count when SEC cover-page shares are missing or stale (flagged shares_was_proxy)",
     "live · fallback only"),
    ("yfinance — financial statements",
     "Fundamentals fallback for sparse SEC filers (flagged facts_yf in the data-quality flags)",
     "live · fallback only"),
    ("yfinance — options chain & earnings dates",
     "Put overlay: ATM IV, open interest, bid-ask spread, days to next earnings (display-only, never ranks)",
     "never cached — live"),
    ("yfinance — analyst actions (ratings & price targets)",
     "90-day revision breadth (upgrades + target raises vs downgrades + lowers): the “delivering” arm of Beaten but Delivering",
     "never cached — live, point-in-time by event date"),
]


def _sources_panel():
    st.subheader("Data sources")
    rows = [{"Source": s, "Used for": u, "Cache & freshness": c} for s, u, c in SOURCES]
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    st.caption(
        "The refresh button re-downloads everything cacheable (Wikipedia, FRED, all SEC "
        "companyfacts); yfinance sources are always fetched live on every scan, and the GICS "
        "map is a versioned repo file. No source here is ever edited by hand."
    )


def render():
    st.header("Keep your research up to date.")
    st.caption("Refresh company financials, share prices and the stock shortlist. A full update can take 20–40 minutes.")
    _overview()
    with st.container(border=True):
        st.subheader("Refresh the research desk")
        st.write("Download the latest available data and rebuild the scan. You can follow progress below.")
        _run_panel()
        _job_panel()
    with st.expander("Where the numbers come from"):
        _sources_panel()


# ---------------------------------------------------------------- overview ---
@st.cache_data(ttl=300, show_spinner=False)
def _overview_data() -> dict:
    sec_dir = ROOT / "data" / "raw" / "sec"
    files = [p for p in sec_dir.glob("CIK*.json") if "submissions" not in p.name]
    total_mb = sum(p.stat().st_size for p in files) / 1e6
    uni = ROOT / "data" / "derived" / "universe.csv"
    uni_age = (dt.datetime.now() - dt.datetime.fromtimestamp(uni.stat().st_mtime)).total_seconds() / 86400 if uni.exists() else None
    uni_rows = len(pd.read_csv(uni)) if uni.exists() else 0
    gs10 = ROOT / "data" / "raw" / "fred_dgs10.csv"
    gs10_last = None
    if gs10.exists():
        try:
            g = pd.read_csv(gs10)
            gs10_last = g["date"].iloc[-1]
        except Exception:
            pass
    last_scan = None
    scans = ROOT / "data" / "scans"
    if scans.exists():
        dirs = sorted((d for d in scans.iterdir() if d.is_dir()), reverse=True)
        for d in dirs:
            try:
                summ = json.loads((d / "summary.json").read_text())
                last_scan = {"name": d.name, "scored": summ.get("scored"),
                             "flags": summ.get("flag_derated_quality")}
                break
            except Exception:
                continue
    return {"sec_n": len(files), "sec_mb": total_mb, "uni_age": uni_age,
            "uni_rows": uni_rows, "gs10_last": gs10_last, "last_scan": last_scan}


def _overview():
    d = _overview_data()
    ls = d["last_scan"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Company filings saved", f"{d['sec_n']}", f"{d['sec_mb']:.0f} MB")
    c2.metric("Companies tracked", f"{d['uni_rows']} names",
              f"{d['uni_age']:.1f} d old" if d["uni_age"] is not None else "missing")
    c3.metric("Treasury rate updated", str(d["gs10_last"] or "—"))
    c4.metric("Last scan", ls["name"] if ls else "—",
              f"{ls['scored']} scored · {ls['flags']} flags" if ls else None)


# --------------------------------------------------------------- the button ---
def _start_scan() -> int | None:
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    log = JOB_DIR / f"scan_{dt.datetime.now():%Y%m%d_%H%M%S}.log"
    cmd = [str(PY), "-u", "-m", "scanner.scan", "--refresh-data"]  # -u: unbuffered, so the log panel streams
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=open(log, "w"),
                            stderr=subprocess.STDOUT)
    (JOB_DIR / "latest.json").write_text(json.dumps(
        {"pid": proc.pid, "log": str(log),
         "started": dt.datetime.now().isoformat(timespec="seconds"),
         "cmd": " ".join(cmd)}))
    return proc.pid


def _run_panel():
    j = _job_state()
    running = bool(j and j.get("running"))
    if st.button(
        "Refresh data & rebuild shortlist",
        type="primary",
        disabled=running,
        help=("Runs: python -m scanner.scan --refresh-data — forces fresh universe, "
              "DGS10 and all SEC companyfacts, then a full scan with live prices "
              "and options."),
    ):
        pid = _start_scan()
        st.toast(f"full refresh started (pid {pid})", icon="🚀")
        st.rerun()
    if running:
        st.info("A refresh is already running — see the log below.")


# ------------------------------------------------------------- live job log ---
def _job_state() -> dict | None:
    f = JOB_DIR / "latest.json"
    if not f.exists():
        return None
    try:
        j = json.loads(f.read_text())
    except Exception:
        return None
    try:
        os.kill(int(j["pid"]), 0)
        j["running"] = True
    except OSError:
        j["running"] = False
    try:
        j["tail"] = Path(j["log"]).read_text(errors="replace").splitlines()[-25:]
    except Exception:
        j["tail"] = []
    return j


try:
    @st.fragment(run_every="5s")
    def _job_panel():
        _job_panel_body()
except Exception:  # older streamlit: manual refresh
    def _job_panel():
        _job_panel_body()


def _job_panel_body():
    st.subheader("Refresh job")
    j = _job_state()
    if j is None:
        st.info("No refresh has been launched from this page yet.")
        return
    status = "🟢 running" if j.get("running") else "⚪ finished"
    st.caption(f"{status} · started {j.get('started')} · pid {j.get('pid')}")
    if j.get("tail"):
        st.code("\n".join(j["tail"]), language=None)
    if not j.get("running"):
        st.caption("Log is static — a new refresh replaces it.")
