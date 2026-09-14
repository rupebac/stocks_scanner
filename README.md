# stocks_scanner

S&P 500 scanner that looks for stocks whose fundamentals are strong but whose price
doesn't reflect them (quality–price divergence), plus a general filter engine for
standard screening (valuation, quality, sentiment) and a cash-secured-put overlay.

**Status: implemented.** Search methodology frozen at spec v0.4; data contract v0.5
(docs 01–08). All phases built: data layer, feature/self-history engine, scoring,
flags, put overlay, scan artifacts, tests, Streamlit UI.

## The UI

**Hunt** is a stock-first research workspace with four destinations:

- **Discover:** an interactive quality/value hunt map, clickable company cards,
  draggable quality/valuation hunt-zone cutoffs, synchronized sliders and reset,
  sector filters, and separate Quality & value, Investing for growth, and All
  companies views. Switch between the business map and an income map of entry
  discount versus premium return. Cards explain why a stock surfaced and what
  to investigate, with on-demand put previews. Every stock remains searchable.
- **Saved ideas:** persistent buy-price ceilings and ownership theses, with put
  comparisons against your prices. Ideas live in this browser profile's
  `localStorage`, survive scan refreshes, and are separate across browser profiles
  and devices. Use the export button to keep a backup: clearing site data removes
  them. The old shared SQLite file is preserved but is no longer read or imported
  automatically. Company research and scan data remain shared on the server.
- **Company:** a business/valuation/concern brief and a focused put/call income calculator. New
  charts read cached SEC filings for revenue, operating profit, operating cash,
  capital spending, debt, cash reserves, and stock compensation. They respect the
  saved scan's filing-date cutoff; no full rescan is needed to view them.
- **Data & refresh:** refresh the scan, follow its progress, and inspect sources.

Compare up to six stocks' puts at a target duration of 7–90 days, an entry
price discount, and a cash budget. Each result shows its actual expiration,
positive bid premium, premium/share-price ratio, return on committed cash,
net entry price, earnings flag and fetch time. Quotes load only on request,
with a 90-second cache. Comparisons prefer a shared expiration; when none exists,
the nearest individual dates are explicitly labeled. Share-price comparisons use
the underlying quote from the same chain response, with both its market timestamp
and the fetch time displayed. Missing underlying quotes pause these comparisons;
missing or crossed bids are not presented as collectible income. Saved buy prices
are strike ceilings: the chosen strike never rounds above them.

The growth lane is a **research filter**, not a quality endorsement: at least 5%
annual revenue growth over five years, positive cash under the depreciation-as-upkeep
proxy, and a proxy-versus-reported cash gap of at least 2% of revenue. Its map shows
revenue growth versus operating margin, with leverage colors and investment-gap
marker sizes. The company workspace separates Price & value, Growth & margins,
Cash & investment, and Debt & dilution into focused chart views.
Reported and proxy cash remain separate; the conservative shortlist and scanner
methodology are unchanged. Covered-call comparisons use the user's original
share cost and distinguish today's quote from a hypothetical future trade.


Scores marked **\*** are research estimates, with their input limitations available
on hover and in the company's always-visible valuation/data panel. They are stored
in separate display columns; the original scanner scores, gates and shortlist are
unchanged. Quality estimates require at least 70% of core category weight,
profitability and observed stability. Cheapness estimates require at least 60%
of component weight and renormalize the available components. Insufficient inputs
remain unavailable with an explanation. This lets companies such as ORCL be
researched despite the scan's broad 80% coverage exclusion, without inventing
missing cash-flow history or confusing an estimate with shortlist eligibility.

The draggable chart serves the installed Plotly bundle locally (copied to the
ignored `dashboard/components/hunt_map/plotly.min.js` at runtime). It needs no CDN.
Drag a dotted line or its handle; release to update the shortlist. Keyboard arrows
adjust a focused handle, Home/End reach its limits, and Escape cancels a drag.

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

### Discovery universes and company search

Discovery supports **S&P 500** and **Nasdaq-100**. The universe selector changes the
map, shortlist, cards, sectors and comparisons; manual company search stays independent.
NYSE, Nasdaq and NYSE American company listings come from the SEC exchange directory
(cached for seven days). Searching a company outside the saved scans loads financials
and prices on demand into `data/company_research/<ticker>/`, without changing either
index scan. On-demand scores are labeled research estimates using available evidence
and saved index peers; unsupported sectors and insufficient data do not receive invented
scores. This is company search, not a comprehensive ETF or derivatives directory.

Run `python -m scanner.scan --universe nasdaq100 --skip-options` or select the universe
on **Data & refresh**. S&P scans retain the legacy date directory; Nasdaq scans use
`data/scans/YYYY-MM-DD_nasdaq100/`. Each index has its own scoring cross-section, so
relative valuations can differ across indices. The Nasdaq list comes from Wikipedia's
[List of NASDAQ-100 companies](https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies),
with SEC identifiers and separate membership history. Non-overlapping ICB industries
use the existing broad-sector peer fallback rather than invented GICS classifications.
Nasdaq entry dates remain unknown; its maturity gate requires at least one year of
observed price history instead of S&P index tenure. The existing size, liquidity,
fundamentals and excluded-sector rules remain in force.

**Data & refresh** defaults to **Both indices + researched companies**. You can also
refresh either index, only researched/saved companies, or **All NYSE & Nasdaq companies**.
The full-directory job can take many hours and resumes companies with usable snapshots
completed today. Each additional company gets persisted research scores against saved
index peers, with missing evidence and unsupported sectors labeled explicitly. Failures
are isolated per company and included in the completion report. Refreshes leave option
chains on demand. CLI: `python -m scanner.refresh --scope tracked` (or `all`, `researched`,
`sp500`, `nasdaq100`); `--use-cache` avoids forcing source-cache downloads.

Discovery filters combine index, sector, minimum/maximum market capitalization (USD
billions), and minimum put open interest/trading volume. Zero disables a minimum;
maximum market cap zero means unlimited. **Check options activity** fetches one expiry
closest to 30 days within the next 90 days, considering strikes from 80% to 100% of
that chain's share price. Both activity minimums must hold at the same strike, not
across unrelated contracts. Checks last 15 minutes in the current session. Active
options filters exclude unchecked, stale and unavailable evidence; no options calls
are made just by adjusting filters. These are activity screens, not guarantees of
execution or tight spreads. Filters update all discovery views without recalculating
peer scores or restricting manual company search.
