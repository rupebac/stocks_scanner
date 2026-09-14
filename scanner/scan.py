"""Scan orchestration: gates -> features -> scores -> flags -> presets -> overlay ->
scan artifacts (docs 01 §scan-artifacts, 05 §4b). CLI: python -m scanner.scan.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import warnings

import pandas as pd

from . import config, flags as FL, market_data, overlay as OV, scoring as SC
from . import universe as UN
from .features import build_features
from .rates_fx import gs10_asof
from .sec_edgar import fetch_companyfacts

warnings.filterwarnings("ignore")


def _gates(df: pd.DataFrame, asof: dt.date) -> pd.DataFrame:
    """Hard gates (doc 05 §1). Null gate values fail softly with a reason — a null is
    recorded as "n/a", never silently passed and never mislabeled as a threshold miss."""
    def reasons(row):
        r = []
        adv = row.get("adv_usd")
        if adv is None or pd.isna(adv):
            r.append("adv n/a")
        elif not adv >= config.GATE_ADV_USD:
            r.append(f"adv<{config.GATE_ADV_USD/1e6:.0f}M")
        mc = row.get("mktcap")
        if mc is None or pd.isna(mc):
            r.append("mktcap n/a")
        elif not mc >= config.GATE_MKTCAP_USD:
            r.append(f"mktcap<{config.GATE_MKTCAP_USD/1e9:.0f}B")
        ten = row.get("index_tenure_y")
        if ten is None or pd.isna(ten):
            r.append("tenure n/a")
        elif ten < config.MIN_INDEX_TENURE_YEARS:
            r.append("tenure<1y")
        rc = row.get("rev_cagr5")
        if rc is None or pd.isna(rc) or rc < config.GATE_REV_CAGR5_MIN:
            r.append("rev_cagr5 gate")
        fc = row.get("fcf_cagr5")
        if fc is None or pd.isna(fc) or fc < config.GATE_FCF_CAGR5_MIN:
            r.append("fcf_cagr5 gate")
        return r

    df["gate_fail_reasons"] = df.apply(reasons, axis=1)
    df["gates_pass"] = df["gate_fail_reasons"].map(lambda r: len(r) == 0)

    cov_cols = [c for c in config.MVP_SCORE_INPUTS if c in df.columns]
    missing = [c for c in config.MVP_SCORE_INPUTS if c not in df.columns]
    if missing:
        # a renamed/removed feature column would silently deflate every stock's coverage
        print(f"[scan] WARNING: MVP score inputs missing from feature rows: {missing} "
              f"(coverage denominator = {len(cov_cols)}/{len(config.MVP_SCORE_INPUTS)})")
    df["input_coverage"] = df[cov_cols].notna().mean(axis=1)
    return df


def _primary_share_class(sg: pd.DataFrame) -> pd.DataFrame:
    """One row per CIK: the most liquid share class (doc 01). Whole-row selection via
    drop_duplicates — groupby(...).first() takes the first *non-null* value per column,
    which silently mixes fields of two share classes when the primary class has a NaN."""
    sg = sg.sort_values("adv_usd", ascending=False, na_position="last")
    return sg.drop_duplicates(subset="cik", keep="first").reset_index(drop=True)


def _coverage_records(df: pd.DataFrame, failures: list[tuple[str, str]]) -> list[dict]:
    """coverage.csv rows: feature failures + every recorded gate failure + the
    coverage<80% "listed separately" names (doc 05 §1)."""
    records = [{"ticker": t, "reasons": str(r)} for t, r in failures]
    if "gate_fail_reasons" in df.columns:
        failed = df[df["gate_fail_reasons"].map(lambda r: len(r or []) > 0)]
        records += [
            {"ticker": r["ticker"], "reasons": "; ".join(r["gate_fail_reasons"])}
            for _, r in failed.iterrows()
        ]
    return records


def _preset_frames(
    df: pd.DataFrame, gs10: float | None
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Preset screens (doc 05 §4). Gate failures and coverage<80% names never reach a
    preset (doc 05 §1: under-covered stocks are unscored)."""
    g = df[df["gates_pass"] & (df["input_coverage"] >= config.MIN_SCORE_INPUT_COVERAGE)]
    flagship = g[g["flag_derated_quality"]].sort_values("residual")
    beaten = g[g["flag_beaten_but_delivering"]].sort_values("cheapness_score", ascending=False)
    consistency = g["quality_component_values"].map(
        lambda c: (c.get("stability") if isinstance(c, dict) else None) or 0
    )
    compounders = g[
        (consistency >= 80)
        & (g["ev_fcf_self_pct"] <= 30)
        & g["quality_floor_pass"]
        & (g["brake_gm"] >= config.FLAG_BRAKE_GM)
        & (g["brake_fcf_margin"] >= config.FLAG_BRAKE_FCF_MARGIN)
        & (g["brake_roic"] >= config.FLAG_BRAKE_ROIC)
        & (
            (g["fcf_yield"] >= max(config.FLAG_YIELD_FLOOR, gs10 or 0))
            | (g["residual_pct"] <= config.FLAG_RESIDUAL_PCT)
        )
    ].sort_values("cheapness_score", ascending=False)
    return flagship, beaten, compounders


def run_scan(
    limit: int | None = None,
    tickers: list[str] | None = None,
    skip_options: bool = False,
    refresh_sec: bool = False,
    refresh_data: bool = False,
    asof: dt.date | None = None,
) -> dict:
    asof = asof or dt.date.today()
    print(f"[scan] asof={asof}")

    # 0. full data refresh: universe, DGS10 and every SEC cache re-downloaded
    #    before the scan; prices/options are always fetched fresh anyway.
    if refresh_data:
        print("[scan] refresh-data: forcing re-download of universe, DGS10 and SEC caches")
        UN.fetch_constituents(refresh=True)
        from .rates_fx import fetch_dgs10
        fetch_dgs10(refresh=True)
        refresh_sec = True

    # 1. universe (doc 01)
    uni_raw = UN.fetch_constituents()
    uni, unmapped = UN.with_industry_groups(uni_raw)
    sg = UN.standard_group(uni).copy()
    sg["index_tenure_y"] = sg.apply(lambda r: UN.index_tenure_years(r, asof), axis=1)
    if tickers:
        sg = sg[sg["symbol"].isin(tickers)]
    elif limit:
        sg = sg.head(limit)
    if sg.empty:
        raise SystemExit("empty universe after filtering")

    # 2. prices + primary share class per CIK (doc 01: most liquid class)
    symbols = sorted(sg["symbol"].unique())
    weekly = market_data.download_weekly(symbols)
    pxf = market_data.price_features(weekly)
    if {"ticker", "adv_usd"} <= set(pxf.columns):
        sg = sg.merge(pxf, left_on="symbol", right_on="ticker", how="left")
    else:
        sg["adv_usd"] = float("nan")  # total price outage: keep one row per CIK, gates record it
    sg = _primary_share_class(sg)
    print(f"[scan] universe rows={len(sg)} (unmapped sub-industries: {unmapped})")

    # 3. SEC fundamentals + features (per-stock isolation; failures land in coverage)
    rows, histories, failures = [], [], []
    for i, (_, u) in enumerate(sg.iterrows()):
        try:
            facts = fetch_companyfacts(int(u["cik"]), refresh=refresh_sec)
        except Exception as e:
            failures.append((u["symbol"], f"companyfacts error: {type(e).__name__}: {e}"))
            continue
        if not facts:
            failures.append((u["symbol"], "no companyfacts"))
            continue
        w = weekly[weekly["ticker"] == u["symbol"]]
        try:
            feat = build_features(facts, w, asof)
        except Exception as e:
            failures.append((u["symbol"], f"features error: {type(e).__name__}: {e}"))
            continue
        if feat is None:
            # last resort for no-shares-anywhere filers (STZ-style A/B classes that
            # dimension even the weighted-average tags): one yfinance share count,
            # then a constant-shares retry (dq-flagged inside)
            fb = market_data.shares_outstanding(u["symbol"])
            if fb:
                try:
                    feat = build_features(facts, w, asof, fallback_shares=fb)
                except Exception:
                    feat = None
        if feat is None and len(w) >= 60:
            # SEC-assembly failure (XOM's single-10-Q companyfacts, APA's missing
            # consolidated revenue tag): rebuild facts from yfinance statements and
            # reuse the whole pipeline — labeled proxy, flagged in dq_flags
            from .yf_facts import facts_from_yfinance
            yf_facts = facts_from_yfinance(u["symbol"])
            if yf_facts:
                try:
                    feat = build_features(yf_facts, w, asof)
                    if feat is not None:
                        feat["dq_flags"] = ";".join(
                            x for x in [feat.get("dq_flags", ""), "facts_yf"] if x)
                except Exception:
                    feat = None
        if feat is None:
            why = "insufficient data (prices/SEC)"
            if len(w) < 60:
                why = f"young listing ({len(w)} wks of prices; needs 60)"
            failures.append((u["symbol"], why))
            continue
        hist = feat.pop("history")
        hist.insert(0, "ticker", u["symbol"])
        histories.append(hist)
        row = {"ticker": u["symbol"], "cik": int(u["cik"]), "name": u["name"],
               "sector": u["sector"], "sub_industry": u["sub_industry"],
               "ig_name": u["ig_name"], "index_tenure_y": u.get("index_tenure_y"),
               "adv_usd": u.get("adv_usd"), "mom_12_1": u.get("mom_12_1"),
               "dd_52w": u.get("dd_52w"), "ret_12m": u.get("ret_12m")}
        row.update(feat)
        rows.append(row)
        if (i + 1) % 25 == 0:
            print(f"[scan]   features {i + 1}/{len(sg)}")

    base_cols = ["ticker", "cik", "name", "sector", "sub_industry", "ig_name",
                 "index_tenure_y", "adv_usd", "mom_12_1", "dd_52w", "ret_12m",
                 "gate_fail_reasons"]
    feat_rows = [r for r in rows if any(k not in base_cols for k in r)]
    df = pd.DataFrame(feat_rows)
    if df.empty:
        from collections import Counter
        print(f"[scan] feature failures ({len(failures)}):")
        for why, n in Counter(why for _, why in failures).most_common(8):
            print(f"[scan]   {n:4d}  {why}")
        raise SystemExit("no stocks produced features")

    # 4. gates, scores, flags
    gs10 = gs10_asof(asof)
    df = _gates(df, asof)
    # doc 05 §1: <80% of MVP score inputs -> unscored, listed separately — such names
    # get no scores/flags and no place in the percentile cross-section
    coverage_fail = df[df["input_coverage"] < config.MIN_SCORE_INPUT_COVERAGE]["ticker"].tolist()
    # doc 05 §2.3: the scoring cross-section (percentile peers, floor, residual FIT
    # universe) is every scoreable standard-group name — gates decide preset
    # eligibility afterwards; they do not shrink the fit (ORCL/NVDA-style gate
    # failures with valid EV/FCF+ROIC still inform the regression)
    scoreable = df[df["input_coverage"] >= config.MIN_SCORE_INPUT_COVERAGE].copy()

    scored = SC.compute_scores(scoreable)
    # "delivering" input for beaten_but_delivering: 90d analyst-action breadth
    # from yfinance (doc 04 §4.2, v0.5.3). Replaced the FMP vintaged-consensus
    # proxy: free-only constraint, and event history is observable immediately
    # instead of after 90 days of accumulated snapshots. Floor passers only —
    # one call per name, and the flag can't fire below the floor anyway.
    passers = scored[scored["quality_floor_pass"]]["ticker"].tolist()
    breadths = {t: market_data.analyst_revision_breadth(t, asof) for t in passers}
    nets = {t: float(b["net"]) for t, b in breadths.items() if b}
    scored["eps_rev_proxy"] = scored["ticker"].map(nets)
    scored = FL.apply_flags(scored, gs10)
    df = pd.concat(
        [scored, df[~df["ticker"].isin(scored["ticker"])].reindex(columns=scored.columns)],
        ignore_index=True,
    )
    for t in coverage_fail:
        df.loc[df["ticker"] == t, "gate_fail_reasons"] = df.loc[df["ticker"] == t, "gate_fail_reasons"].map(
            lambda r, t=t: (r or []) + ["coverage<80%"]
        )

    # 5. presets (doc 05 §4)
    flagship, beaten, compounders = _preset_frames(df, gs10)

    # 6. overlay on the quality-floor list (display-only, doc 07) — must never kill
    # the scan. v0.5.4: widened from flagship-only to the gated floor passers, the
    # names a put hopper would actually price; ranking still never sees options.
    floor_list = df[
        df["gates_pass"].fillna(False)
        & (df["input_coverage"] >= config.MIN_SCORE_INPUT_COVERAGE)
        & df["quality_floor_pass"].fillna(False)
    ].sort_values("cheapness_score", ascending=False)
    ov_df = pd.DataFrame()
    if not skip_options and len(floor_list):
        try:
            ov_df = OV.build_overlay(floor_list.head(config.OVERLAY_TOP_N), asof)
        except Exception as e:
            print(f"[scan] overlay failed ({type(e).__name__}: {e}) — continuing without it")
            ov_df = pd.DataFrame()

    # 7. artifacts (docs 01/05 §4b)
    out_dir = config.SCANS / asof.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = {
        "asof": asof.isoformat(), "spec": "v0.5 (search frozen v0.4)",
        "subset": {"limit": limit, "tickers": tickers},
        "gs10": gs10,
        "universe_version": (
            str(uni_raw["fetched_at"].iloc[0])
            if "fetched_at" in uni_raw.columns and len(uni_raw) else None
        ),
        "quality_floor_threshold": scored.attrs.get("quality_floor_threshold"),
        "residual_fit": scored.attrs.get("residual_fit"),
        "presets": {  # doc 05 §4: a preset's definition is data — recorded, hashed, versioned
            "flagship": {"flag": "derated_quality", "rank": "residual asc",
                         "requires": "gates_pass & coverage>=0.8"},
            "beaten_but_delivering": {"flag": "beaten_but_delivering", "rank": "cheapness_score desc",
                                      "requires": "gates_pass & coverage>=0.8"},
            "compounders_on_sale": {
                "rules": "stability>=80 & ev_fcf_self_pct<=30 & quality_floor & "
                         "brakes 0.97/0.95/0.90 & (fcf_yield>=max(4%,gs10) | residual_pct<=20)",
                "rank": "cheapness_score desc"},
        },
        "thresholds": {
            k: getattr(config, k) for k in [
                "GATE_ADV_USD", "GATE_MKTCAP_USD", "GATE_REV_CAGR5_MIN", "GATE_FCF_CAGR5_MIN",
                "MIN_INDEX_TENURE_YEARS", "MIN_SCORE_INPUT_COVERAGE",
                "QUALITY_FLOOR_MIN", "QUALITY_FLOOR_ROIC_MIN", "QUALITY_FLOOR_ROIC_MAX",
                "QUALITY_STABILITY_WEIGHTS",
                "FLAG_SELF_PCT", "FLAG_SELF_Z", "FLAG_MEDIAN_RATIO",
                "FLAG_YIELD_FLOOR", "FLAG_RESIDUAL_PCT", "FLAG_BRAKE_GM", "FLAG_BRAKE_FCF_MARGIN",
                "FLAG_BRAKE_ROIC",
            ]
        },
    }
    cfg["config_hash"] = hashlib.sha256(json.dumps(cfg, default=str, sort_keys=True).encode()).hexdigest()[:12]
    (out_dir / "config.json").write_text(json.dumps(cfg, indent=2, default=str))
    df.to_csv(out_dir / "metrics.csv", index=False)  # full per-stock output incl. component values (doc 05 §4b)
    cov_records = _coverage_records(df, failures)
    pd.DataFrame(cov_records, columns=["ticker", "reasons"]).to_csv(out_dir / "coverage.csv", index=False)
    pdir = out_dir / "presets"; pdir.mkdir(exist_ok=True)
    flagship.to_csv(pdir / "flagship.csv", index=False)
    beaten.to_csv(pdir / "beaten_but_delivering.csv", index=False)
    compounders.to_csv(pdir / "compounders_on_sale.csv", index=False)
    if len(ov_df):
        ov_df.to_csv(out_dir / "overlay.csv", index=False)
    if histories:
        pd.concat(histories, ignore_index=True).to_parquet(out_dir / "history.parquet", index=False)

    summary = {
        "asof": asof.isoformat(), "gs10": gs10,
        "universe_rows": len(sg), "scored": int(scored["quality_score"].notna().sum()),
        "floor_threshold": scored.attrs.get("quality_floor_threshold"),
        "floor_pass": int(scored["quality_floor_pass"].sum()),
        "flag_derated_quality": int(df["flag_derated_quality"].fillna(False).sum()),
        "flag_beaten": int(df["flag_beaten_but_delivering"].fillna(False).sum()),
        "flagship": flagship[["ticker", "residual", "fcf_yield", "ev_fcf_self_pct"]].head(10).to_dict("records"),
        "unmapped_subindustries": unmapped,
        "scan_dir": str(out_dir),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))

    print(f"[scan] scored={summary['scored']} floor_pass={summary['floor_pass']} "
          f"derated_quality={summary['flag_derated_quality']} gs10={gs10}")
    for r in summary["flagship"]:
        print(f"[scan]   {r['ticker']:<6} residual={r['residual']:.3f} "
              f"fcf_yield={r['fcf_yield']:.3f} self_pct={r['ev_fcf_self_pct']:.0f}")
    print(f"[scan] artifacts -> {out_dir}")
    return summary


def main():
    ap = argparse.ArgumentParser(description="S&P 500 quality/price-divergence scan")
    ap.add_argument("--limit", type=int, default=None, help="first N standard-group names (smoke runs)")
    ap.add_argument("--tickers", type=str, default=None, help="comma-separated symbols subset")
    ap.add_argument("--skip-options", action="store_true", help="skip put overlay (no options/earnings calls)")
    ap.add_argument("--refresh-sec", action="store_true", help="re-fetch companyfacts despite cache")
    ap.add_argument("--refresh-data", action="store_true",
                    help="re-download everything: universe, DGS10 and all SEC caches (implies --refresh-sec)")
    ap.add_argument("--asof", type=str, default=None, help="YYYY-MM-DD (default today)")
    args = ap.parse_args()
    run_scan(
        limit=args.limit,
        tickers=[t.strip().upper() for t in args.tickers.split(",")] if args.tickers else None,
        skip_options=args.skip_options,
        refresh_sec=args.refresh_sec,
        refresh_data=args.refresh_data,
        asof=dt.date.fromisoformat(args.asof) if args.asof else None,
    )


if __name__ == "__main__":
    main()
