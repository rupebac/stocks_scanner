# Dashboard and financial review — 14 September 2026

## Main conclusion

The highest-priority problems were in financial extraction, not visual styling.
Oracle's non-current notes payable were missing from the debt mapping. This materially
understated enterprise value and leverage, and overstated return on invested capital.
Old quality and cheapness estimates based on those inputs should not be relied upon.

The corrected Oracle company snapshot, using the currently cached filings and fetched
prices, gives net debt including leases of approximately $137.4B and net debt/EBITDA
of 4.05x, compared with approximately 0.43x in the earlier scan. Its display estimates
are approximately 21/100 quality and 20/100 cheapness. These are model outputs, not
investment recommendations; peer estimates can change when the index refresh completes.

## Findings and implemented changes

| Priority | Finding | Change |
|---|---|---|
| Critical | Non-current notes payable were not recognized. Oracle's FY2026 disclosure contains $122.342B in that category. | Added notes-payable and combined-debt fallback tags. Prefer aggregate current debt over adding short-term borrowings to that aggregate. Regression test reproduces the $129.541B total in Oracle's May disclosure. |
| High | Negative EBITDA could produce a negative debt ratio and earn net-cash quality credit. | Ratio unavailable for non-positive EBITDA; balance-quality component receives zero credit. Company UI explains why the multiple is unavailable. |
| High | A weighted-average share fallback was processed as an additive cash flow. | Preserve reported share averages as levels; never sum four quarters or difference YTD average shares. |
| High | The fallback statement adapter could label non-USD accounts as USD. | Require verified USD reporting and trading currencies for that adapter. Foreign statements require a separate FX/ADR implementation. |
| High | First annual fallback columns were interpreted as quarters; missing quarter columns could change inferred period lengths. | Explicit annual/quarterly periods, independent of the preceding available column. |
| Medium | Cash after stock compensation was labeled “reported free cash flow.” | Company metrics distinguish cash after capex and stock pay; cash charts reconcile operating cash, capex, stock pay and the remainder. |
| Medium | Financial chart observations lacked readily inspectable period ends. | Added trailing-year financial figures with period ends and stale-data status; the cash reconciliation requires matching periods. Disclose missing stock pay, proxy shares and fallback statement sources. |
| Medium | The debt/dilution view showed stock pay but no actual share-count trend. | Added reported basic/diluted average shares and separate lease liabilities in debt charts. Missing debt before the first debt observation remains unknown. |
| Medium | Comparisons and preview cards could retain old option quotes through a long session. | Date-aware comparison signatures and explicit old-quote messages after 15 minutes, including unknown timestamps. |
| Medium | Zero operating profit could disappear from current ROIC through a truthiness check. Future price rows could enter an as-of calculation. | Preserve zero ROIC and exclude future/non-positive prices from feature assembly. |
| Medium | Saved scans had no indication that financial calculation rules had changed. | Versioned financial calculations; older company scans and shortlist explanations disclose that a refresh is needed. Full-directory resume requires the current calculation version. |
| Medium | Company search includes preferred shares, warrants and funds that cannot use issuer common-stock valuation. | Added guards for identified non-equity quote types, preferred tickers and warrant/preferred names. Broader security-master verification remains necessary. |
| Operational | A refresh launched from a short-lived process could terminate with its parent. | Detached refresh subprocesses from the launching session. |

## What was checked

- Discovery filters, collapsed-panel layout, index-independent company search, saved
  ideas, hunt-line dragging and selection state.
- Put premium divided by gross strike cash, saved entry-price ceilings, missing bids,
  crossed/wide quotes, expiration alignment, earnings flags and covered-call calculations.
- SEC duration/instant extraction, financial-history timelines, share-count fallbacks,
  current leverage, historical valuation inputs, score components and missing-data rules.
- Refresh scopes, partial failures, on-demand snapshots, source versions and resumability.
- Existing automated suite plus targeted financial regression tests; an actual corrected
  Oracle snapshot and the corresponding SEC debt disclosure.

This is a code/data-path review and targeted issuer reconciliation, not an audit of
every issuer's accounts or a guarantee that all taxonomy variations are covered.

## Next improvements, in priority order

1. **Funding resilience:** current debt maturities, cash plus short-term investments,
   interest coverage with a verified gross-interest expense tag, and committed capital
   spending. Particularly valuable for investment-heavy companies. D&A is only a
   maintenance-spending proxy and should never be presented as freely available cash.
2. **Broader option execution screening:** inspect several expirations and include
   bid/ask spread, quote timestamps and contract deliverables. The current discovery
   activity check samples one expiry nearest 30 days and strikes from 80–100% of spot;
   it does not prove liquidity throughout the full 90-day window.
3. **One “all researched companies” discovery pool:** full-directory refresh currently
   supports company research, but map discovery remains index-based. A combined pool
   needs a deliberately defined peer-scoring basis and clear duplicate handling.
4. **Issuer coverage checks:** reconcile extracted debt to reported totals and attach
   source periods/accessions to each important number. Review finance-lease treatment,
   restricted cash, unusual issuer tags, split adjustments and ADR share ratios before
   extending valuation coverage further. Unsupported sectors need separate models.
5. **Cash valuation methodology:** show conventional CFO-minus-capex free cash flow
   alongside the stock-pay adjustment. The existing adjusted cash/EV metric is a
   heuristic: it combines a post-interest operating-cash measure with enterprise value.
   A true unlevered cash-flow model or a consistent equity cash-yield model needs its
   own explicit specification rather than a silent formula change.
6. **Atomic artifact publication:** index refresh currently writes multiple files into
   the dated scan directory. Publish complete snapshots atomically so a new session
   cannot encounter mixed artifacts during the brief write window.
7. **Stable integration fixtures:** some integration tests use mutable saved scans.
   Keep live-data smoke checks, but move deterministic UI assertions to fixed fixtures.

## Sources

- [Oracle FY2026 notes payable and borrowings, SEC disclosure](https://www.sec.gov/Archives/edgar/data/1341439/000119312526277521/R17.htm)
- [SEC non-GAAP guidance, Question 102.07: free cash flow definitions](https://www.sec.gov/rules-regulations/staff-guidance/corporation-finance-interpretations/non-gaap-financial-measures)
- [FINRA options guidance: premium, assignment and covered calls](https://www.finra.org/investors/investing/investment-products/options)

## Validation and refreshed data

- Full automated suite: **253 passed**; additional focused financial checks passed.
- Oracle's extracted May 2026 current plus non-current debt reconciles exactly to
  **$129.541B** in its filing.
- Refreshed S&P outputs contain no debt/EBITDA ratios where EBITDA is non-positive.
- S&P 500 and Nasdaq-100 refreshed with financial version `2026-09-14.2`; three
  individually researched companies were refreshed, with no job failures. Two had
  sufficient data for quality and cheapness estimates. SPCX remains insufficient.
- The refreshed S&P peer pool puts Oracle's display estimates near **21/100 quality**
  and **22/100 cheapness**; the earlier standalone estimate used the previous peer pool.
