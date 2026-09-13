# 03 — Quality Filters

Version 0.5 (2026-09-11). Search methodology frozen (v0.3/v0.4 content). v0.5 hygiene: `fcf_margin` default wording → peers; input availability per doc 08 §5.

Business-quality filters. These answer: *"is the company actually good?"* — **price is
never an input to anything in this doc.** That separation is deliberate: quality is
measured blind, then compared against price in doc 05.

Scoring basis: 5-year lookback (10y where noted). **QualityScore (v0.5.9) is on fixed
economic bars**, not peer percentiles — see doc 05 §2.1. Cheapness still uses the
**peer ladder** for multiples: GICS industry group when it has ≥ 8 standard-group names,
else sector, else market. FCF basis is split by use (Q8, v0.3): margins and multiples on
`fcf_adj` (valuation honesty); positivity counts and CAGR gates on plain `fcf`.
Ideas **owner-earnings** mode (v0.5.11) remaps the FCF-CAGR gate and QualityScore FCF
sleeves onto `fcf_owner` = CFO − D&A − SBC; positivity counts stay on plain `fcf`.
Formulas: doc 06 §7 / §9.

## 3.1 Profitability

| ID | Metric | Definition | Default gate | Priority |
|---|---|---|---|---|
| `roic` | ROIC | NOPAT / invested capital, 5y avg | ≥ peers 60th pct | MVP |
| `roic_exgoodwill` | ROIC ex-goodwill | NOPAT / (IC − goodwill − acquired intangibles), 5y avg | scores when goodwill+intangibles > 40% of IC; else display | P1 |
| `roic_ttm` | ROIC (current) | same, TTM | ≥ 0.90 × 5y avg (flag brake) | MVP |
| `gm_ttm` | Gross margin (current) | same, TTM | ≥ 0.97 × 5y avg (flag brake) | MVP |
| `fcf_margin_ttm` | FCF margin (current) | `fcf_adj`-basis, TTM | ≥ 0.95 × 5y avg (flag brake) | MVP |
| `fcf_margin` | FCF margin | `fcf_adj` / revenue, 5y avg | ≥ peers (ladder) 60th pct | MVP |
| `gm` | Gross margin | gross profit / revenue, 5y avg | context | MVP |
| `gm_stability` | Gross margin stability | std(gm) / \|mean(gm)\| over 5y FY, lower better | QualityScore stability sleeve | MVP |
| `op_margin` | Operating margin | EBIT / revenue, 5y avg | context | P1 |
| `roe` / `roa` | ROE / ROA | standard | restricted-group use | P1 |

- **NOPAT** = EBIT × (1 − effective tax rate); `t_eff` winsorized to [0%, 40%] with a 3y
  average fallback, EBIT as reported (never non-GAAP) — full rule in doc 06 §6.
- **Invested capital**: canonical formula in doc 06 §5 — identical non-equity stack as EV,
  plus equity, kept identical by design.
- Fallback if invested-capital build is unreliable for a ticker: ROIC-alt = EBIT×(1−t) / (total assets − current liabilities). Score both when possible; note which one produced a value.
- The `*_ttm` vs 5y-avg checks exist as **slope brakes** for the `derated_quality` flag
  (doc 05 §3): margins erode before 5y-average levels roll over, so they catch a slow
  bleed (PayPal/Intel-early) that level tests pass.

## 3.2 Consistency (the compounder check)

| ID | Metric | Definition | Default gate | Priority |
|---|---|---|---|---|
| `roic_years` | ROIC consistency | years of last 10 with ROIC > 10% | ≥ 7 | MVP |
| `fcf_pos_years` | FCF positivity | years of last 10 with plain `fcf` > 0 | ≥ 9 | MVP |
| `rev_pos_years` | Revenue path | years of last 10 FY with YoY revenue **not down** (`rev_yoy_n` = pair count; flat counts) | QualityScore stability sleeve | MVP |
| `eps_vol` | Earnings stability | std of TTM EPS year-over-year changes over 5y, lower better | context | P1 |
| `fcf_cov` | FCF path | std(plain `fcf`) / mean(`fcf`) over 5y FY, lower better; null if mean ≤ 0 | QualityScore stability sleeve | MVP |

Consistency is what separates "good company" from "good year". Adobe/Oracle-type names
pass `roic_years` = 10/10 even in years their multiple collapses.

## 3.3 Growth

| ID | Metric | Definition | Default gate | Priority |
|---|---|---|---|---|
| `rev_cagr5` | Revenue CAGR 5y | standard | ≥ 0 | MVP |
| `fcf_cagr5` | FCF CAGR 5y | standard, plain `fcf` | ≥ −0.05 (deceleration-tolerant) | MVP |
| `eps_cagr5` | EPS CAGR 5y | standard | context | P1 |
| `rev_cagr10` | Revenue CAGR 10y | standard | context | P2 |

Growth gates are floors, not maximizers: the scanner hunts underpricing, not maximal growth
(maximal growth is usually already priced). Negative-FCF-growth names get flagged as
"fundamentals deteriorating" and are blocked from the divergence presets (doc 05).
Since v0.5.8, growth is a **capped sleeve** of QualityScore (12% CAGR = 100) **and**
still a gate (§1 of doc 05). Percentile-maximizing growth is still rejected: the cap
stops a 40% cyclical spike from outranking an 8% compounder. The path (stability
multiplier) is what separates a smooth 11% CAGR from a boom/bust 11% CAGR.

Deliberate tolerance for *deceleration*: the flagship pattern (doc 05, `derated_quality`)
often presents as growth slowing from 20% to 5% while ROIC and FCF **levels** stay intact.
These gates test levels, not acceleration, so decelerating-but-healthy names stay
eligible; genuine deterioration is caught by `roic_ttm` (≥ 0.9× 5y avg) and the FCF
non-deterioration condition in the flag rule itself.

## 3.4 Accounting quality

| ID | Metric | Definition | Default gate | Priority |
|---|---|---|---|---|
| `fcf_ni` | FCF / net income | TTM and 5y avg | ≥ 0.8 | MVP |
| `accruals` | Accruals ratio | (net income − CFO) / total assets, lower better | ≤ sector 60th pct | P1 |
| `sbc_rev` | Stock comp intensity | SBC / revenue | flag > sector 90th pct | P1 |

- `fcf_ni` is the cheap detector of paper earnings: sustained < 0.8 means profit that
  never shows up as cash.
- `sbc_rev` doesn't disqualify (normal in software) but high SBC + heavy buybacks =
  buybacks just offsetting dilution — `buyback_yield` should then be read net of SBC.

## 3.5 Balance sheet

| ID | Metric | Definition | Default gate | Priority |
|---|---|---|---|---|
| `nd_ebitda` | Net debt / EBITDA | (debt − cash) / EBITDA, TTM | ≤ 2.5 | MVP |
| `int_cov` | Interest coverage | EBIT / interest expense | ≥ 5 | P1 |
| `current_ratio` | Current ratio | current assets / current liabilities | ≥ 1 | P1 |
| `altman_z` | Altman Z-score | standard manufacturing formula | context | P2 |

## 3.6 Composite

| ID | Metric | Definition | Priority |
|---|---|---|---|
| `f_score` | Piotroski F-score | 9 binary checks below, on TTM vs. prior year | MVP |

| # | Check |
|---|---|
| 1 | ROA > 0 |
| 2 | CFO > 0 |
| 3 | ΔROA > 0 |
| 4 | CFO > ROA (accrual check) |
| 5 | leverage (LTD/TA) did not increase |
| 6 | current ratio did not decrease |
| 7 | shares outstanding did not increase |
| 8 | gross margin did not decrease |
| 9 | asset turnover did not decrease |

**Context only (v0.3) — never a gate.** Three of the nine checks (#3 ΔROA, #8 ΔGM,
#9 Δturnover) are year-over-year *improvements*, which contradicts
levels-not-acceleration: a decelerating-but-healthy Adobe can print a 6 and fail a
"≥ 7" gate. It remains a cheap deterioration **display** next to the percentile scores.

## Caveats

- **Quality is absolute; cheapness still uses the industry-group ladder (v0.5.9 / v0.3)**:
  QualityScore is fixed economic bars × path stability, never a peer percentile. Multiples
  still score at GICS industry group when it has ≥ 8 standard-group names, else sector,
  else market — the output states which peer set produced each cheapness percentile.
  Rationale for the ladder: broad IT percentiles are Mag7-relative, while Energy/Materials
  at ~20 names make a "30th percentile" a handful of tickers.
- **Write-downs and one-offs** wreck 1-year metrics; that's why most gates are 5y averages
  with a TTM non-deterioration check rather than TTM levels alone.
- **Restricted group** (financials/utilities/RE): `roe`, `pb`, `div_yield` replace ROIC/EV
  metrics. Not scored in MVP (excluded per doc 01).
