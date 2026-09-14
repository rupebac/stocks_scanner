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

from scanner.universe_selection import UNIVERSES, available_scans
from scanner.refresh import SCOPES

JOB_DIR = ROOT / "data" / "jobs"
PY = ROOT / ".venv" / "bin" / "python"


SOURCES = [
    # (source, used for, cache & freshness)
    ("Wikipedia — S&P 500 / Nasdaq-100 constituents",
     "Index membership and industry classifications; SEC ticker directory supplies Nasdaq CIKs. Nasdaq membership dates are unknown.",
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


def render(universe="tracked"):
    st.header("Keep your research up to date.")
    st.caption("Update financials, prices, business-quality scores and cheapness scores.")
    if universe in UNIVERSES:
        _overview(universe)
    else:
        cols = st.columns(3)
        for col, (key, label) in zip(cols, UNIVERSES.items()):
            scans = available_scans(ROOT / "data/scans", key)
            col.metric(label, scans[-1].name[:10] if scans else "Not scanned")
        count = sum(1 for p in (ROOT / "data/company_research").glob("*/config.json"))
        cols[2].metric("Companies researched individually", count)
    if universe == "all":
        st.info("Covers both indices and the full NYSE/Nasdaq company directory. This can take many hours. Progress is saved per company; restarting reuses companies completed today.")
    elif universe == "tracked":
        st.caption("Refreshes both indices, plus companies already loaded for research. Personal saved lists stay in each browser. New searches also receive scores on demand.")
    st.caption("Scores require sufficient financial evidence. Financials, utilities and real estate still need dedicated scoring models.")
    with st.container(border=True):
        st.subheader("Refresh the research desk")
        st.write("Download the latest available data and rebuild the scan. You can follow progress below.")
        _run_panel(universe)
        _job_panel()
    with st.expander("Where the numbers come from"):
        _sources_panel()


# ---------------------------------------------------------------- overview ---
@st.cache_data(ttl=300, show_spinner=False)
def _overview_data(universe="sp500") -> dict:
    sec_dir = ROOT / "data" / "raw" / "sec"
    files = [p for p in sec_dir.glob("CIK*.json") if "submissions" not in p.name]
    total_mb = sum(p.stat().st_size for p in files) / 1e6
    uni = ROOT / "data" / "derived" / ("universe.csv" if universe == "sp500" else "universe_nasdaq100.csv")
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
        dirs = list(reversed(available_scans(scans, universe)))
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


def _overview(universe="sp500"):
    d = _overview_data(universe)
    ls = d["last_scan"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Company filings saved", f"{d['sec_n']}", f"{d['sec_mb']:.0f} MB")
    c2.metric("Companies tracked", f"{d['uni_rows']} names",
              f"{d['uni_age']:.1f} d old" if d["uni_age"] is not None else "missing")
    c3.metric("Treasury rate updated", str(d["gs10_last"] or "—"))
    c4.metric("Last scan", ls["name"] if ls else "—",
              f"{ls['scored']} scored · {ls['flags']} flags" if ls else None)


# --------------------------------------------------------------- the button ---
def _start_scan(universe="tracked", refresh=True) -> int | None:
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    log = JOB_DIR / f"scan_{dt.datetime.now():%Y%m%d_%H%M%S}.log"
    if universe not in SCOPES:
        raise ValueError("Unknown stock universe")
    result_file = log.with_suffix(".result.json")
    cmd = [str(PY), "-u", "-m", "scanner.refresh", "--scope", universe, "--result", str(result_file)]
    if not refresh:
        cmd.append("--use-cache")
    with log.open("w") as output:
        proc = subprocess.Popen(cmd, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
    (JOB_DIR / "latest.json").write_text(json.dumps(
        {"pid": proc.pid, "log": str(log), "result": str(result_file),
         "started": dt.datetime.now().isoformat(timespec="seconds"),
         "cmd": " ".join(cmd)}))
    return proc.pid


def _run_panel(universe="tracked"):
    j = _job_state()
    running = bool(j and j.get("running"))
    if st.button(
        "Refresh data & scores",
        type="primary",
        disabled=running,
        help=f"Update {SCOPES[universe]}. Option chains are fetched when you open them.",
    ):
        pid = _start_scan(universe)
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
    if j.get("result") and Path(j["result"]).exists():
        try:
            j["outcome"] = json.loads(Path(j["result"]).read_text())
            j["running"] = False
        except (OSError, ValueError):
            pass
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
    status = "🟢 running" if j.get("running") else j.get("outcome", {}).get("status", "stopped; check the log")
    if j.get("outcome"):
        result = j["outcome"]
        st.caption(f"Individual companies refreshed: {result.get('researched_companies', 0)} · quality scores: {result.get('quality_scores', 0)} · cheapness scores: {result.get('cheapness_scores', 0)}")
        if result.get("failures") or result.get("error"):
            st.warning("Some updates failed. See the log for affected companies.")
    st.caption(f"{status} · started {j.get('started')} · pid {j.get('pid')}")
    if j.get("tail"):
        st.code("\n".join(j["tail"]), language=None)
    if not j.get("running"):
        st.caption("Log is static — a new refresh replaces it.")
