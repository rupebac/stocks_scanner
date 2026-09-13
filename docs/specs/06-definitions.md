# 06 — Canonical Definitions (Formula Appendix)

Version 0.5 (2026-09-11). v0.5.11: `fcf_owner` (CFO − D&A − SBC) as a named Ideas cash setting; default remains `fcf_adj`. v0.5: EBITDA defined; FX source pinned; field-level rules live in the doc 08 fetch contract.

Single source of truth for every computed quantity. Docs 02–05 reference these formulas;
any implementation must match this doc or link to it. The classic failure mode of this
kind of project is two modules computing "EV" or "FCF" slightly differently — this doc
exists to make that bug impossible to introduce silently.

## 1. Identity

- **Company key: CIK.** One company per CIK regardless of share classes (see 01, Universe).
- **Primary ticker**: the share class with the highest `adv_usd` at selection time; used
  for display and price-series joins.
- **All quantities USD.** Non-USD reporters are converted at the report-date FX rate
  (FRED `DEX*` daily series; conventions in doc 08 §1).

## 2. Prices and shares

- **Return series** (momentum, relative strength, volatility): dividend- and
  split-adjusted closes (total-return basis).
- **Valuation levels** (price, market cap): latest close × shares outstanding.
- **Shares outstanding** (for market cap): current outstanding across all classes.
- **Per-share fundamentals** (EPS): diluted weighted-average shares for the period.
- **Buyback yield**: −Δ(**basic** shares outstanding, YoY) — share count, never announced
  buyback programs. Basic, not diluted weighted-average: diluted counts grow with SBC and
  understate executed buybacks in high-SBC names.

## 3. Reporting periods and the as-of rule

- **TTM** = sum of the last four reported fiscal quarters.
- **As-of rule**: a quarter is visible to a scan only after its **filing date**
  (10-Q/10-K/8-K), never its period-end date. Report lag runs 3–10 weeks; using
  period-end dates is the most common look-ahead bug in fundamental screens.
- A fiscal-year change or restated quarter re-bases TTM at its (new) filing date.

## 4. Enterprise value (EV)

```
EV = market cap (all classes)
   + total debt (short + long term, incl. finance leases and capitalized operating leases)
   + preferred stock + minority interest
   − cash & equivalents − short-term investments
```

## 5. Invested capital (IC)

```
IC = total debt (as above) + preferred stock + minority interest + total equity
   − cash & equivalents − short-term investments
```

EV and IC share the identical non-equity stack by design — no drift between valuation
and quality metrics.

Goodwill and acquired intangibles stay **in** IC for `roic`. For acquisition-heavy names
(goodwill + acquired intangibles > 40% of IC), score the companion `roic_exgoodwill` =
NOPAT / (IC − goodwill − acquired intangibles) instead — Oracle-style compounders get an
unjustified ROIC haircut from deal goodwill otherwise (doc 03 §3.1).

## 6. NOPAT and the tax rate

```
NOPAT = EBIT × (1 − t_eff)
t_eff = TTM income tax provision / TTM pre-tax income
```

- `t_eff` winsorized to [0%, 40%]; outside the range → 3y average fallback; still
  undefined → null.
- **EBIT = operating income as reported**, not the company's "adjusted"/non-GAAP figure.
  We adjust ourselves, explicitly, or not at all.
- **EBITDA** = EBIT + D&A (TTM). D&A from the cash-flow supplemental tags
  (`DepreciationDepletionAndAmortization` chain; tag map: doc 08 §2). `nd_ebitda` and
  `ev_ebitda` use TTM EBITDA.

## 7. Free cash flow variants

| ID | Formula | Use |
|---|---|---|
| `fcf` | CFO − capex | **positivity counts** (`fcf_pos_years`), **CAGR gates** (`fcf_cagr5`) on the default (reported) series, display |
| `fcf_adj` | CFO − capex − stock-based compensation | **default** multiples and margins (`ev_fcf`, `fcf_yield`, `fcf_margin`, `fcf_margin_ttm`) |
| `fcf_owner` | CFO − D&A − SBC (untagged SBC = 0, same honesty as `fcf_adj`) | **named Ideas setting** (v0.5.11): FCF-CAGR gate, QualityScore FCF sleeves, residual EV/FCF, Ideas yield bar. D&A is the maintenance-capex proxy. Never blended with `fcf_adj`. |

Rationale: SBC is a real economic cost that happens to be non-cash; plain FCF flatters
heavy-SBC companies — precisely our target universe (large-cap tech). Buybacks that only
offset SBC dilution stay visible via the share-count-based `buyback_yield`, not via
SBC-inflated FCF. The split-by-use rule (Q8, v0.3) exists because the SBC haircut applied
to a 9-of-10 positivity count can eject the software compounders we hunt.

**Strike-implied FCF yield** (put overlay, doc 07): `fcf_adj / EV_strike`, where
`EV_strike` = (strike × basic shares outstanding) + the same non-equity stack as §4. It
uses the same (possibly stale) TTM `fcf_adj` as everything else — the `numbers_stale`
badge covers it.

## 8. Growth and margin metrics

- **CAGR (n years)** = (value_t / value_{t−n})^(1/n) − 1, on fiscal-year values.
- Base year negative or near-zero (|base| < 5% of current revenue) → null, not a wild CAGR.
- **FCF CAGR fallback (v0.5.7):** if the 5y FCF base is unusable, use the nearest
  valid year in 3.5–5.5y and set `fcf_cagr5_base_fallback` (Ideas chip `⚠ 5y FCF base`).
  Revenue CAGR does not fall back. Still-null CAGR still fails the gate.
- **Margins**: TTM and 5y average of the respective numerator / revenue.

## 9. Percentile mechanics (scoring)

- Winsorization at 1st/99th percentile, then percentile rank, both within the **peer
  ladder** cross-section: GICS industry group when it has ≥ 8 standard-group names, else
  sector, else market (map source: doc 01; the output states which set was used).
  Ties → average rank.
- **SELF percentile**: rank of the current value within the stock's own trailing window —
  valid observations only; ≥ 3y of history required; ≥ 60% of the window valid; else null.
- **Fixed-scale mapping (ABS yields)**: `fcf_yield` → `clip(y, 0, 10%) / 10%` → 0–100.
  Absolute metrics are **never cross-sectionally ranked**: ranking a yield against peers
  reproduces the inverted multiple's ranking and cancels the anchor (the v0.2 bug).
- **QualityScore fixed bars (v0.5.9)**: ROIC clip(0, 20%)/20%; FCF margin clip(0, 25%)/25%;
  CAGR clip(0, 12%)/12%; `nd_ebitda` ≤ 0 → 100, 3.0× → 0; CoV 0 → 100, CoV at the
  sleeve zero (GM 25%, FCF 50%) → 0. Path stability weights: revenue-up-year share 50%,
  GM CoV 25%, FCF CoV 25%, renormalized over present slots. Floor also requires
  conservative ROIC in [10%, 100%] when ROIC is known (>100% is a tiny-IC artifact and
  fails the floor; missing still does not fail). Quality is **never** peer-percentile ranked.
  Profitability uses min(TTM, 5y median), not the 5y mean.
- **SELF z-score**: (current − 5y **median**) / 5y std — median-centered (v0.4): a
  bubble tail drags both the mean and the std, which was exactly the failure the
  median-ratio arm was added to fix. Valid observations only. **SELF median-ratio**:
  current / 5y median. Both are computed alongside the SELF percentile; the percentile
  scores, while z and median-ratio feed the `derated_quality` OR-arm (doc 05 §3).
- **Direction normalization**: every scored metric maps so 100 = best (cheap, profitable,
  consistent…), specified per-metric in docs 02–04.

## 10. Null policy

Any quantity whose inputs are missing, negative where nonsensical (e.g., EV/EBIT with
EBIT < 0), or failing a data-quality check (doc 01) is **null**: excluded from that
filter/score component with coverage reported — never coerced to zero, a neutral score,
or a silently clipped value.
