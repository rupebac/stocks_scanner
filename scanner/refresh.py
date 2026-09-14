"""Refresh index scans and exchange-wide company research in a background job."""
import argparse
import datetime as dt
import json
from pathlib import Path

import pandas as pd
from . import config, company_lookup as CL
from .scan import run_scan
from .universe_selection import UNIVERSES, available_scans

SCOPES = {
    "tracked": "Both indices + researched companies",
    "sp500": "S&P 500",
    "nasdaq100": "Nasdaq-100",
    "researched": "Researched companies",
    "all": "All NYSE & Nasdaq companies",
}


def reference_data():
    frames = []
    for universe in UNIVERSES:
        scans = available_scans(config.SCANS, universe)
        if scans:
            frames.append(pd.read_csv(scans[-1] / "metrics.csv"))
    return pd.concat(frames, ignore_index=True).drop_duplicates("ticker") if frames else pd.DataFrame()


def research_targets(scope, directory, reference):
    if scope == "all":
        targets = directory
    else:
        cached = {p.name for p in (config.DATA / "company_research").glob("*") if (p / "config.json").exists()}
        targets = directory[directory.ticker.isin(cached)]
    # Index members already received updated financials and scores in this job.
    if scope in {"all", "tracked"} and not reference.empty:
        targets = targets[~targets.ticker.isin(reference.ticker)]
    return targets


def refresh(scope="tracked", force=True):
    if scope not in SCOPES:
        raise ValueError("Unknown refresh scope")
    failures, completed = [], []
    indices = list(UNIVERSES) if scope in {"tracked", "all"} else [scope] if scope in UNIVERSES else []
    for universe in indices:
        try:
            print(f"[refresh] Updating {UNIVERSES[universe]} and scores", flush=True)
            run_scan(universe=universe, refresh_data=force, skip_options=True)
            completed.append(universe)
        except (Exception, SystemExit) as exc:
            failures.append({"target": universe, "reason": str(exc)})
            print(f"[refresh] Failed {universe}: {exc}", flush=True)
    reference = reference_data()
    scored_rows = []
    if scope not in UNIVERSES:
        directory = CL.company_directory(refresh=force)
        targets = research_targets(scope, directory, reference if len(completed) == len(indices) else pd.DataFrame())
        for n, (_, listing) in enumerate(targets.iterrows(), 1):
            ticker = listing.ticker
            try:
                path = CL.snapshot_path(ticker)
                # Broad jobs can span hours: restart them without repeating today's completed companies.
                ready = False
                if scope == "all" and (path / "config.json").exists():
                    cfg = json.loads((path / "config.json").read_text())
                    ready = cfg.get("asof") == dt.date.today().isoformat() and cfg.get("financials_available", False) and cfg.get("financial_version") == config.FINANCIAL_VERSION
                if not ready:
                    path = CL.build_snapshot(listing.to_dict(), refresh=force)
                raw = pd.read_csv(path / "metrics.csv")
                scores = CL.score_snapshot(raw, reference)
                scores.to_csv(path / "research_scores.csv", index=False)
                scored_rows.append(scores)
                print(f"[refresh] {n}/{len(targets)} {ticker}: quality {scores.quality_score_status.iloc[0]}, cheapness {scores.cheapness_score_status.iloc[0]}", flush=True)
            except Exception as exc:
                failures.append({"target": ticker, "reason": str(exc)})
                print(f"[refresh] {n}/{len(targets)} {ticker} failed: {exc}", flush=True)
    result = {"completed_indices": completed, "researched_companies": len(scored_rows), "failures": failures}
    if scored_rows:
        scores = pd.concat(scored_rows, ignore_index=True)
        result.update({f"{kind}_scores": int(scores[f"display_{kind}_score"].notna().sum()) for kind in ("quality", "cheapness")})
    print("[refresh] Complete: " + json.dumps(result), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=list(SCOPES), default="tracked")
    parser.add_argument("--use-cache", action="store_true")
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    try:
        result = refresh(args.scope, force=not args.use_cache)
        result["status"] = "completed with errors" if result["failures"] else "completed"
    except Exception as exc:
        result = {"status": "failed", "error": str(exc)}
        print(f"[refresh] Failed: {exc}", flush=True)
    if args.result:
        args.result.write_text(json.dumps(result))
    if result["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
