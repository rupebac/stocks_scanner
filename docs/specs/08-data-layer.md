# 08 — Data-Layer Contract (Fetch, Assemble, Fallback)

Version 0.5 (2026-09-11). Status: draft.

Docs 02–07 are the frozen search methodology (frozen at v0.4). This document is the
**fetch contract**: what to pull, from which endpoint, which exact field, and how raw
fields assemble into the formulas of doc 06. An implementer must not have to invent a
field name, a tag choice, or a TTM rule — if it isn't here, it isn't MVP.

## 1. Endpoints & politeness

| Source | Endpoint / access | Rate rule | Notes |
|---|---|---|---|
| SEC XBRL | `data.sec.gov/api/xbrl/companyfacts/CIK{10-digit}.json` (full), `companyconcept/.../{taxonomy}/{Tag}.json` (single-tag patch), `submissions/CIK...json` (filing index) | User-Agent header with contact required; ≤ 10 req/s; backoff on 403/429 | pull once per CIK, cache raw; refetch only when `submissions` shows a new 10-K/10-Q |
| Prices | yfinance daily history, `auto_adjust=True` | polite pacing (unofficial API) | split/dividend-consistent closes for returns and levels (06 §2) |
| 10Y Treasury | FRED **`DGS10`** (daily) via `fredgraph.csv` | none meaningful | **DGS10, not GS10** — nightly scans need the daily series; last observation carried on holidays, date recorded |
| FX | FRED `DEX*` daily series per currency | none meaningful | quote directions differ per series (DEXUSEU = USD/EUR, DEXJPUS = JPY/USD…): direction check per series; no daily DEX → last obs with flag |
| Estimates | FMP free tier `/analyst/estimates/{symbol}` | 250 calls/day — nightly consensus snapshot only | vintaged: we persist consensus each night; revision proxy derived from our own history (§5) |
| Earnings dates | yfinance `calendar` / `get_earnings_dates` | polite pacing | patchy beyond ~1 quarter; unknown → null, badge suppressed |
| Options chain | yfinance `option_chain(expiry)` | polite pacing | display-only (doc 07); ATM rule in §5 |
| Constituents + GICS sector/sub-industry | Wikipedia S&P 500 table | weekly | dedupe by CIK (01) |
| GICS industry groups | static map `data/reference/gics_subindustry_to_ig.csv` | reviewed on GICS reclassification announcements | columns: sub_industry_name (Wikipedia wording), industry_group_code, industry_group_name, sector; source: public MSCI/S&P GICS structure; name normalization documented in-file |

## 2. XBRL tag map (us-gaap; `dei` where noted)

Order matters: first tag with data wins; a metric whose chain is exhausted → null (never
zero). Sign conventions: cash-flow payments are positive outflows; interest tags vary in
sign — normalize to positive expense.

### Income statement (duration; usually YTD in filings — §3 rules apply)

| Field | Primary tag | Fallbacks |
|---|---|---|
| Revenue | `RevenueFromContractWithCustomerExcludingAssessedTax` | `Revenues` → `SalesRevenueNet` (legacy pre-2018) |
| Gross profit | `GrossProfit` | — (absent → `gm` null) |
| EBIT (operating income) | `OperatingIncomeLoss` | — |
| Interest expense | `InterestExpense` | `InterestExpenseNonoperating` → `InterestIncomeExpenseNet` (sign check) |
| Pre-tax income | `IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest` | — |
| Tax provision | `IncomeTaxExpenseBenefit` | — |
| Net income (parent) | `NetIncomeLoss` | — |
| Diluted EPS | `EarningsPerShareDiluted` | — |
| Diluted WAS | `WeightedAverageNumberOfDilutedSharesOutstanding` | — |
| Basic WAS | `WeightedAverageNumberOfSharesOutstandingBasic` | — |

### Cash flow statement (duration; **always YTD** — §3 differencing mandatory)

| Field | Primary tag | Fallbacks |
|---|---|---|
| CFO | `NetCashProvidedByUsedInOperatingActivities` | `NetCashProvidedByUsedInOperatingActivitiesContinuingOperations` |
| Capex | `PaymentsToAcquirePropertyPlantAndEquipment` | `PaymentsToAcquireProductiveAssets` (legacy) |
| SBC | `ShareBasedCompensation` | — |
| D&A | `DepreciationDepletionAndAmortization` | `DepreciationAmortizationAndAccretionNet` → `Depreciation` + `AmortizationOfIntangibleAssets` |

### Balance sheet (instant)

| Field | Primary tag | Fallbacks |
|---|---|---|
| Cash & equivalents | `CashAndCashEquivalentsAtCarryingValue` | — |
| ST investments | `ShortTermInvestments` | `MarketableSecuritiesCurrent` |
| Debt, current | `LongTermDebtCurrent` + `ShortTermBorrowings` | `DebtCurrent` |
| Debt, noncurrent | `LongTermDebtNoncurrent` | `LongTermDebt` (total; then subtract current portion) |
| Finance leases | `FinanceLeaseLiabilityCurrent` + `FinanceLeaseLiabilityNoncurrent` | — |
| Operating leases | `OperatingLeaseLiabilityCurrent` + `OperatingLeaseLiabilityNoncurrent` | — (leases counted as debt: 06 §4) |
| Total equity | `StockholdersEquity` | `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest` |
| Minority interest | `MinorityInterest` | `NoncontrollingInterest` |
| Preferred | `PreferredStockValue` | absent → 0 + flag (rare in standard group) |
| Goodwill | `Goodwill` | — |
| Acquired intangibles | `FiniteLivedIntangibleAssetsNet` + `IndefiniteLivedIntangibleAssetsNet` | `IntangibleAssetsNetExcludingGoodwill` |
| Total / current assets | `Assets` / `AssetsCurrent` | — |
| Current liabilities | `LiabilitiesCurrent` | — |

### Cover page (instant, `dei` taxonomy)

| Field | Primary tag | Notes |
|---|---|---|
| Shares outstanding | `dei:EntityCommonStockSharesOutstanding` | latest cover date ≤ scan date; market cap basis (06 §2) |

IFRS filers (`ifrs-full` taxonomy): not mapped in MVP → affected metrics null; no
standard-group S&P 500 member currently requires it.

## 3. Assembly rules (raw facts → doc 06 inputs)

1. **Visibility**: a fact is usable only if its `filed` date ≤ scan date (the report-date
   rule, 06 §3). Press-release 8-K numbers are **not** in `companyfacts` — TTM updates at
   the 10-Q/10-K filing, days-to-weeks after the print. Accepted; the `numbers_stale`
   badge marks the window.
2. **Quarterly flows from YTD**: cash-flow (and where needed income) facts are cumulative
   YTD. Quarterly value = YTD(q) − YTD(q−1) within the fiscal year; Q1 = YTD directly.
   Validate YTD is non-decreasing within the year; a violation → null + data-quality flag.
3. **TTM** = sum of the last 4 derived quarters; fewer than 4 valid quarters → null.
4. **Instants** (balance sheet, cover): latest `end` ≤ scan date among facts with
   `filed` ≤ scan date.
5. **Restatements/dedup**: same period, multiple `filed` dates → keep the latest `filed`
   (live rule, Q6). Vintaged snapshots (05 §5) keep what the scan of that date saw.
6. **Fiscal-year change**: YTD series rebases; treat as a new series, flag the name.
7. **Frames**: `frame` attributes are a cross-check, never a source (avoid double counts).

## 4. Non-XBRL field map

| Field | Source & rule |
|---|---|
| Prices, returns, 52w high/low, dollar volume | yfinance daily history, `auto_adjust=True`; `mom_12_1`, `rs_12m`, `dd_52w`, `vol_1y` per 04 formulas |
| Shares cross-check | `dei` cover shares vs. yfinance `sharesOutstanding`; divergence > 5% → flag (01 data-quality) |
| GS10 | FRED `DGS10`, last daily observation (§1) |
| FX conversion | FRED `DEX*` at report date (§1) |
| Next earnings date | yfinance calendar; null if unknown |
| ATM option row | expiry = nearest expiry with DTE ≥ 21; strike = closest to spot; IV = that row's `impliedVolatility` |
| Option liquidity gate (doc 07) | ~30–45 DTE expiry (nearest 35), ATM put: **OI ≥ 500 contracts** and **(ask − bid)/mid ≤ 10%** |
| Revision proxy (MVP) | nightly FMP consensus EPS (next FY) snapshotted; `eps_rev_breadth_3m` proxy = sign(consensus_today − consensus_90d_ago) ∈ {−1, 0, +1}; true up/down-revision counts need paid data — proxy is labeled as such, consistent with MVP\* status |
| Short interest | yfinance; context only (04) |

## 5. MVP availability contract (score/flag inputs that ship at MVP)

Everything not listed is P1+ and **must not** block: components renormalize over
implemented inputs, and the renormalization is stated in scan output.

| Layer | MVP inputs | P1 gaps (renormalized out at MVP) |
|---|---|---|
| CheapnessScore | `ev_fcf` SELF, `ev_ebit` SECT, `fcf_yield` fixed-scale | — (complete) |
| `derated_quality` | floor, SELF OR-arm (percentile/z/median-ratio), yield/residual OR, `gm_ttm`, `fcf_margin_ttm`, `roic_ttm` brakes | — (complete) |
| QualityScore | conservative ROIC & FCF margin (min of TTM, 5y median), `rev_cagr5`, `fcf_cagr5`, `nd_ebitda`; path: `gm_stability`, `fcf_cov`, `rev_pos_years`/`rev_yoy_n` | `gm` *level*, `fcf_ni`, `roic_years`, `fcf_pos_years` (display); `accruals`, `int_cov`, `roic_exgoodwill` |
| Sentiment/gates | `mom_12_1`, `rs_12m`, `dd_52w`, `adv_usd`, `mktcap`, `index_tenure`, `days_to_earnings` | `eps_rev_breadth_3m` (proxy, §4), short interest |
| Overlay (07) | strike yields, DTE badge, IV/HV display, option gate | — (complete, display-only) |

QualityScore (v0.5.9): core = profitability 40% / growth 30% / balance 30% (renormalize
over present sleeves), then × stability/100. Stability weights: revenue 50% / GM 25% /
FCF 25% (renormalize if a slot is missing). Floor = Quality ≥ 60 and conservative ROIC
in [10%, 100%] when ROIC is known (>100% fails; missing does not). See doc 05 §2.1.
Scan rows also ship `fcf_owner_*` / `ev_fcf_owner*` (CFO − D&A − SBC) so Ideas can remap
the FCF sleeves, residual, FCF-CAGR gate, and yield bar without a silent blend (v0.5.11).

## 6. Data-quality hooks (extends doc 01)

- YTD monotonicity violation → null + flag (§3.2)
- `filed`-date visibility filter applied before any assembly (§3.1)
- capex share of CFO absurd (< 0 or > 100% of CFO for a cash-generative business) → flag
- tag-chain exhaustion (all fallbacks empty) → null with the metric id recorded —
  coverage is reported per scan, never silently filled
