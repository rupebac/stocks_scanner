# stocks_scanner

S&P 500 scanner that looks for stocks whose fundamentals are strong but whose price
doesn't reflect them (quality–price divergence), plus a general filter engine for
standard screening (valuation, quality, sentiment) and a cash-secured-put overlay.

**Status: implemented.** Search methodology frozen at spec v0.4; data contract v0.5
(docs 01–08). All phases built: data layer, feature/self-history engine, scoring,
flags, put overlay, scan artifacts, tests, Streamlit UI.

## The UI

**Hunt** is a stock-first research workspace with three destinations:

- **Discover:** an interactive quality/value hunt map, clickable company cards,
  sector filters, and separate Quality & value, Investing for growth, and All
  companies views. Every stock remains searchable, including unscored names.
- **Company:** financial history and a focused put/call income calculator. New
  charts read cached SEC filings for revenue, operating profit, operating cash,
  capital spending, debt, cash reserves, and stock compensation. They respect the
  saved scan's filing-date cutoff; no full rescan is needed to view them.
- **Data & refresh:** refresh the scan, follow its progress, and inspect sources.

Compare up to six stocks' puts at a target duration of 7–90 days, an entry
price discount, and a cash budget. Each result shows its actual expiration,
positive bid premium, premium/share-price ratio, return on committed cash,
net entry price, earnings flag and fetch time. Quotes load only on request,
with a 90-second cache. Share-price comparisons use the clearly dated scan
price. Missing or crossed bids are not presented as collectible income.

The growth lane is a **research filter**, not a quality endorsement: positive
5-year revenue growth, positive cash before estimated growth investment, and
higher cash under the depreciation-as-upkeep proxy than after all capex.
Reported and proxy cash remain separate; the conservative shortlist and scanner
methodology are unchanged. Covered-call comparisons use the user's original
share cost and distinguish today's quote from a hypothetical future trade.


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
dashboard/app.py    Hunt discovery, company research, and option income UI
scanner/company_research.py  Display-only financial history from cached SEC facts
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
