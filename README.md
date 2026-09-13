# stocks_scanner

S&P 500 scanner that looks for stocks whose fundamentals are strong but whose price
doesn't reflect them (quality–price divergence), plus a general filter engine for
standard screening (valuation, quality, sentiment) and a cash-secured-put overlay.

**Status: implemented.** Search methodology frozen at spec v0.4; data contract v0.5
(docs 01–08). All phases built: data layer, feature/self-history engine, scoring,
flags, put overlay, scan artifacts, tests, Streamlit UI.

## The UI

Two pages. **Ideas** (default) is one scrolling page for the manual
cash-secured-put workflow: the hunt map (quality vs the valuation residual —
cheaper-than-quality to the right), tagged cards for the highlights — quality
floor + residual < 0 + ROIC ≥ 10% + FCF yield ≥ max(4%, 10Y), most negative
residual first, with FCF yield now and at −5% — a compact table, and a company
detail that appears under the cards once you pick a ticker (assignment test,
price/EV-FCF/FCF-yield/cash charts, margin snapshot). A **cash setting** chooses
reported FCF (CFO − capex − SBC, default) or owner earnings (CFO − D&A − SBC)
for the FCF-CAGR gate, QualityScore FCF sleeves, residual, and the yield bar;
overlay strike yields stay reported. **Data manager**
re-downloads everything and re-scans, shows the caches and the data-source
inventory. Every number is display-only — the scanner ranks, the human decides.

## Quick start

```bash
./control.sh start      # dashboard on http://localhost:8501 (venv auto-created first run)
./control.sh status     # pid, health, served scan
./control.sh restart    # pick up code changes / fresh artifacts
./control.sh stop

PORT=9000 ./control.sh start   # custom port
```

Run a scan: from the dashboard's **Data manager** page ("Re-download everything & re-scan" — forces fresh universe, DGS10 and SEC caches), or:

```bash
.venv/bin/python -m scanner.scan                 # full standard group
.venv/bin/python -m scanner.scan --tickers ADBE,ORCL --skip-options
```

Manual equivalent:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# smoke run (subset, no options calls)
.venv/bin/python -m scanner.scan --tickers ADBE,ORCL,MSFT,NVDA,CRM --skip-options

# full standard-group scan (~380 names; first run downloads all SEC companyfacts,
# ~20–40 min; subsequent runs reuse the cache)
.venv/bin/python -m scanner.scan

# dashboard
.venv/bin/streamlit run dashboard/app.py

# tests
.venv/bin/python -m pytest tests/ -q
```

Optional: set `SCANNER_UA` in `.env` (SEC asks for a self-identifying User-Agent with
contact info). No API keys are needed anywhere — analyst revisions come from yfinance's
analyst-action feed (v0.5.3, replaced the FMP free-tier proxy).

## CLI

```
python -m scanner.scan [--limit N] [--tickers A,B,...] [--skip-options]
                       [--refresh-sec] [--no-fmp] [--asof YYYY-MM-DD]
```

Each run writes scan artifacts to `data/scans/<date>/` (doc 05 §4b): `config.json`
(hashed thresholds), `metrics.csv` (per-stock metrics, scores, flags, evidence),
`presets/*.csv` (flagship / beaten_but_delivering / compounders_on_sale),
`overlay.csv` (put overlay, display-only), `history.parquet` (weekly SELF timelines),
`coverage.csv` (unscored names + reasons), `summary.json`.

## What it computes (spec summary — full detail in docs/specs/)

- **Quality is the gate**: QualityScore is absolute (profitability × capped growth ×
  low debt, multiplied by path stability). Floor is Quality ≥ 60 and conservative
  ROIC in [10%, 100%] when ROIC is known (>100% fails; missing does not). No price inputs.
  Peer-ladder percentiles are not used for quality. Ideas can remap the FCF sleeves
  onto owner earnings (CFO − D&A − SBC) via a named setting; default stays reported FCF.
- **Cheapness is the rank**: CheapnessScore (EV/FCF vs own 5y history, EV/EBIT vs
  industry-group peers, FCF yield on a fixed 0–10% scale) and the flagship rank key —
  the valuation residual `log(EV/FCF) ~ log(ROIC) + industry group`.
- **Sentiment is the flag**: `derated_quality` (depressed-multiple OR-arm incl.
  median-ratio, yield/residual OR, margin-slope brakes), `beaten_but_delivering`.
- **Data honesty**: filing-date visibility (no look-ahead), YTD→TTM differencing with
  revision history preserved, vintaged-scan artifacts, SEC XBRL as the single
  fundamentals source (doc 08).

## Layout

```
scanner/            package (config = every spec threshold)
  universe.py       Wikipedia constituents, GICS IG map, share-class dedupe
  sec_edgar.py      companyfacts fetch/cache, tag map, YTD→TTM assembly
  market_data.py    yfinance weekly prices, options ATM, earnings dates
  rates_fx.py       FRED DGS10
  metrics.py        doc 06 formulas (EV/IC/NOPAT/FCF variants/SELF stats)
  features.py       per-stock features + weekly SELF timeline (as-known fundamentals)
  scoring.py        peer ladder, QualityScore/CheapnessScore, floor, residual
  flags.py          derated_quality / beaten_but_delivering with evidence
  overlay.py        put overlay (strike-implied FCF yields, DTE badges, IV/HV, gate)
  fmp.py            vintaged consensus snapshots (revision proxy)
  scan.py           orchestration + CLI + artifacts
dashboard/app.py    Streamlit UI (Ideas page: hunt map, cards, detail; Data manager)
tests/              assembly / metrics / scoring+flags (incl. Adobe-pass, PayPal-fail)
data/reference/     GICS sub-industry → industry-group map
docs/specs/         the frozen specifications (01–08 + decision log)
```

## Notes & known edges

- Payment processors (PYPL, V, MA…) are excluded automatically: GICS moved them to
  Financials in 2023 (restricted group, doc 01).
- Names with sparse SEC history (e.g. XOM in some environments) land in `coverage.csv`
  as unscored rather than being scored on partial data — null policy, doc 06 §10.
- `fcf_adj` (SBC-adjusted) drives multiples/margins; plain `fcf` drives positivity
  counts and CAGR gates (Q8).
- Thresholds live in `scanner/config.py` with spec references; changing one is a
  methodology change — log it in `docs/specs/OPEN_QUESTIONS.md`.
