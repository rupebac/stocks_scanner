# Open Questions & Decision Log

Version 0.4 (2026-09-11). All questions Q1–Q9 are **DECIDED** (external methodology
review + owner sign-off, v0.3; unchanged by the v0.4 pass — see the log for what v0.4
changed). The standing decisions are below; to change one, add a new decision-log row —
never silently edit a decision.

## Q1 — MVP filter set — DECIDED (v0.3)

Keep the v0.1 MVP set with two amendments: `eps_rev_breadth_3m` is "MVP\*" — it feeds only
`beaten_but_delivering` and drops to P1 without blocking anything if the free estimates
source proves patchy. The flagship preset (`derated_quality`) depends on **no** estimates
data at all.

## Q2 — Restricted sectors — DECIDED (v0.3)

Exclude Financials, Utilities, Real Estate in MVP. A hybrid ROIC/P-B score would poison
QualityScore before the thesis is tested. The restricted filter set remains P1.

## Q3 — Fundamentals data source — DECIDED (v0.3)

SEC EDGAR XBRL `companyfacts` computes **every** ratio — single source; dual-sourced ROIC
is un-debuggable. FMP free tier only for analyst estimates (and short interest if
yfinance's is unusable). yfinance for prices.

## Q4 — Short side — DECIDED (v0.3)

No inverted/short preset in MVP. Different product, doubles the harness, and steals review
cycles the long thesis still needs. Revisit after the evaluation harness proves the long
side.

## Q5 — Self-history window — DECIDED (v0.3)

SELF percentile **scores** on the 5y window. A 10y SELF percentile ships as a display
column ("decade low"), never in the score — pre-2020 rates make everything look cheap
against a 10y window. The flag uses the 5y OR-arm group (percentile / z / median-ratio,
doc 05 §3).

## Q6 — Restatement policy — DECIDED (v0.3)

Live scans: latest available figures. Evaluation harness: **vintaged snapshots only** —
never backfilled-restated history presented as point-in-time. Honesty starts the night
scan-artifact persistence starts (doc 01).

## Q7 — Scan cadence — DECIDED (v0.3)

Nightly EOD full scan. The thesis is multi-month; intraday is an anti-goal (doc 05).

## Q8 — FCF basis — DECIDED (v0.3)

Split by use:

- `fcf_adj` (CFO − capex − SBC) for **multiples and margins** — valuation honesty.
- plain `fcf` for **positivity counts and CAGR gates** (`fcf_pos_years`, `fcf_cagr5`) and
  as the display line — applying the SBC haircut to a 9-of-10 positivity count can eject
  the software compounders we hunt.
- `buyback_yield` on **basic** shares outstanding change, not diluted weighted-average —
  diluted counts grow with SBC and understate executed buybacks in high-SBC names.

## Q9 — SELF-mode statistic — DECIDED (v0.3)

Percentile **scores** (time spent cheap); z-score **displays** (distance from center); the
flag's OR-arm group adds the **median-ratio** (EV/FCF ≤ 0.8 × 5y median) — more robust
than z when a bubble tail inflates the window's standard deviation (owner refinement).

## Decision log

| Date | Question | Decision |
|---|---|---|
| 2026-09-11 | Q8 | `fcf_adj` set as default basis for FCF multiples/margins (editorial default, v0.2) |
| 2026-09-11 | v0.2 review pass | `rs_12m` demoted to context; report-date as-of rule; share-class dedupe; SELF validity rules; data-quality checks; scan artifacts; canonical-definitions appendix (doc 06) |
| 2026-09-11 | Q1–Q9 | **All closed** per external methodology review, with two owner refinements: median-ratio arm added to `derated_quality`; residual `log(EV/FCF) ~ log(ROIC) + industry group` adopted as flagship rank key (with robust-fit caveats) |
| 2026-09-11 | v0.3 pass | MispricingScore → CheapnessScore (momentum, `pe_ttm`, `ey_spread`, `sh_yield` out of the score; fixed-scale absolute anchor); `derated_quality` rewritten (yield gate, margin-slope brake, SELF OR-arm); quality floor → pass-rate calibration (~15–25%); growth → gates only, 0% weight; `f_score` → context; industry-group peer ladder; harness → vintaged snapshots only, no pre-tracking PIT claims |
| 2026-09-11 | v0.4 pass | Flag yield gate softened to OR with the valuation residual; residual fit on the full standard-group universe (ROIC floored at 1%, industry dummies reported); quality floor = fixed top-20% cut; Compounders on Sale given the flagship brakes; z-score recentered on the 5y median; static GICS industry-group map added; put overlay (doc 07) specced as display-only. Q1–Q9 unchanged |
| 2026-09-11 | v0.5 pass | Search methodology (02–07) frozen at v0.4. Data-layer contract added (doc 08): XBRL tag map, YTD→TTM assembly, DGS10, FX, earnings, options ATM rule, EBITDA. MVP input availability + weight renormalization, overlay gate numbers. GICS map file |
| 2026-09-11 | review round (7 agents) | Implementation reviewed by 7 parallel agents; ~60 issues fixed. Interpretation calls recorded: (1) CheapnessScore SELF input is market-cross-section ranked per doc 05 §2 letter, not the raw own-history percentile; (2) `residual_pct` uses midrank (rank−0.5)/n with n≥2 — the old rank/n + n≥5 guard silently disabled the residual arm in small scans (the v0.4 false-negative trap); (3) CheapnessScore requires all three components (doc 08 §5 "(complete)" reading — no renormalization contract, doc 06 §10 forbids neutral fills); (4) `fcf_cagr5` nulls for hypergrowth bases (NVDA: base FCF < 5% of current revenue per doc 06 §8) are accepted as spec-letter behavior, excluding extreme growers from the flagship; (5) ladder rung sizing counts standard-group NAMES (doc 05 §2), not valid-metric rows; (6) `nd_ebitda` = (debt + leases − cash − STI)/EBITDA — preferred and MI excluded per doc 03 §3.5; (7) the scoring cross-section (percentiles, floor, residual fit) is every coverage-pass name — gates filter presets afterwards, they do not shrink the fit universe (doc 05 §2.3 "the fit universe is everybody") |
| 2026-09-12 | v0.5.2 coverage fixes (owner-approved) | (1) **GM derived from cost-of-revenue** when GrossProfit is unreported (143 standard-group filers present no gross-profit line): gm = (revenue − cost of revenue)/revenue via the `CostOfGoodsAndServicesSold → CostOfRevenue → CostOfGoodsSold → CostOfServices` chain; applies to the FY average and the gm_ttm brake; dq flag `gm_derived`. Reported GrossProfit always wins. (2) **SBC=0 fallback** for the 26 filers that never tag ShareBasedCompensation (trivial stock comp): `fcf_adj` = plain `fcf`, dq flag `sbc_unreported` — nulling every FCF metric for non-taggers was worse than the approximation; Q8's SBC-adjusted basis is unchanged wherever the tag exists. Motivation: the ≥80% input-coverage rule left 128/360 names unscored, roughly half from unreported line items rather than market verdicts |
| 2026-09-12 | owner decision (free-data constraint) | v0.5.3: the `beaten_but_delivering` "delivering" input switched from the FMP vintaged consensus-EPS proxy (`sign(eps_today − eps_≥90d_ago)`) to yfinance analyst-action breadth: net of (rating upgrades + price-target raises) − (downgrades + lowers) over the trailing 90 days, point-in-time by event date, fetched for quality-floor passers. Owner bars paid plans; the FMP free proxy also needed ~90 days of accumulated snapshots before it could fire. Semantics: rating/target revisions instead of consensus-EPS drift — same sign convention (positive breadth = delivering); `eps_rev_proxy` keeps its name and `> 0` flag arm (doc 04 §4.2 updated). `scanner/fmp.py`, the `--no-fmp` flag and `CONSULT_FILE` removed; no API keys remain in the stack |
| 2026-09-13 | owner decision (product reshape, external review) | v0.5.4: the product is a cash-secured-put idea hopper, not a flagship prover. Default UI list = "this morning's list": gated quality-floor names that are also cheap (SELF pct ≤ 50 OR FCF yield ≥ max(4%, DGS10)), ranked by CheapnessScore (21 of 38 today) + live toggles (sector, min yield, max SELF pct, put-liquidity). derated_quality / beaten_but_delivering / compounders demoted from homepage presets to TAGS; margin brakes surface as ⚠ warnings on the hopper instead of hiding names. Overlay widened from flagship-only to the gated quality-floor list (OVERLAY_TOP_N=40, cheapest first) — still display-only, never ranks (doc 07 scope note). Scoring, flags, thresholds: UNCHANGED — thesis presets remain as saved views. Reviewer option A + thin B; default list size (b) ~20–40 |
| 2026-09-13 | owner decision (equity cheapness in the UI) | v0.5.5: the Ideas page ranks equity cheapness by the valuation residual, not by SELF history. Zone = gated quality-floor names with residual < 0, sorted residual ascending; hunt-map x-axis is −residual (cheaper to the right); cards lead with FCF yield + residual; the CheapnessScore bar/column/headline are gone from the UI and the `hunt` (derated_quality/SELF) chip is dropped — SELF remains a chart only (sparkline + company EV/FCF history) and never ranks. New quiet chip `⚠ ROIC < 10%` (absolute holdability, EQT-like). CheapnessScore itself is unchanged in the scanner and remains in metrics.csv unused by the UI. Scanner math untouched; TDG-style names (residual > 0 at a 5y-low multiple) correctly fall out of the zone |
| 2026-09-13 | owner decision (highlights membership) | v0.5.6: residual stays the rank but is no longer sufficient for the Ideas highlights. Membership = quality floor AND residual < 0 AND 5y-avg ROIC ≥ 10% AND FCF yield ≥ max(4%, DGS10); missing ROIC/yield fails. Kills EQT-style cyclicals that only look high-quality vs a weak peer set and QCOM-style rich-sector names cheap only vs a fat industry dummy (6 of 19 pass today: ADBE, CF, TPR, ADSK, BF-B, PAYX). New `⚠ cycle` chip on Energy/Materials (no exclusion); ROIC/margins stay chips, not gates. UI-only — scanner math, residual fit and CheapnessScore untouched |
| 2026-09-13 | owner decision (live put panel v1) | Live put quotes on the Ideas company detail only: `listed_expiries` 0–60 DTE (0 allowed; OPTIONS_MIN_DTE stays a nightly-overlay floor), one chain per requested expiry, `put_quote` picks the listed put nearest a DTE-dependent target (DTE<7 ATM · 7–20 ≈0.97 · ≥21 ≈0.95), premium = bid>0 else mid else last with `weak_quote` flag (bid=0 ≠ premium 0). Display: cash % of strike always, 365-annualization only from 21 DTE with a `low premium` note vs DGS10, earnings 0–5d adds a second ATM event quote (expiry on/before the print, never annualized, `earnings in window` chip). Quotes are live (~90s cache), scan fundamentals stay artifacts. Options never change who is highlighted, never hide and never rank — `_highlighted` untouched |
| 2026-09-13 | owner decision (company detail: two sections + chain) | Company detail splits into two tabs: **Financials** (scores, assignment FCF yields, history charts, margins) and **Options** (live expiry 0–60d, suggested standing/event puts, full put/call chain, click-a-contract: breakeven, cash %, IV, intrinsic/extrinsic, long BS greeks from listed IV). Highlights / `_highlighted` still untouched |
| 2026-09-13 | owner decision (FCF CAGR 5y-base fallback) | `fcf_cagr5` still prefers the closest year in 4.6–5.5y. If that base is unusable (negative / <5% of current revenue — the COVID hole: EXPE), fall back to the nearest *valid* FCF year in 3.5–5.5y and flag `fcf_cagr5_base_fallback` (Ideas chip `⚠ 5y FCF base`). Computed CAGR still must be ≥ −5% or the gate fails. Revenue CAGR unchanged. Nulls that remain uncomputable still fail the gate (honesty rule). Methodology change to a frozen gate; highlight membership rules otherwise untouched |
| 2026-09-13 | owner decision (QualityScore = business, not peers) | v0.5.8: QualityScore is absolute, not industry-group percentiles. `Quality = core × (stability/100)` with core = profitability 40% (min(TTM, 5y median) ROIC & FCF margin on 20%/25% caps) + growth 30% (rev & FCF CAGR capped at 12%) + balance 30% (`nd_ebitda` 0×=100, 3×=0). Stability = mean of GM CoV, FCF CoV, revenue up-year share; a jumpy path caps the score (CF-style boom in the 5y window). Floor = **60**, not top 20%. Gross-margin *level*, `fcf_ni`, `roic_years`, `fcf_pos_years` are display. Peer ladder stays for CheapnessScore / momentum only. Residual, FCF-yield gates, `_highlighted` membership rules otherwise unchanged. |
| 2026-09-13 | owner decision (path weights + current-ROIC floor) | v0.5.9: two class holes, not ticker patches. (1) Path stability is **revenue 50% / GM CoV 25% / FCF CoV 25%**, renormalized over present slots — so a missing GrossProfit tag cannot promote lumpy FCF to half the multiplier. Operating-business smoothness (revenue path + margins) is the path; FCF CoV stays a minority check so CF-style boom/bust still fails. (2) Quality floor is Quality ≥ 60 **and** conservative ROIC ≥ 10% when ROIC is computable (`min(TTM, 5y median)`; same holdability bar as Ideas / `roic_years`). Missing ROIC does not fail. A collapsed current ROIC cannot average past the floor via growth + net cash + still-fat FCF margin. Residual / FCF-yield / `_highlighted` membership otherwise unchanged. |
| 2026-09-14 | owner decision (unreliable ROIC fails the floor) | v0.5.10: a *computed* conservative ROIC **> 100%** (NOPAT > invested capital) is a tiny-IC artifact, not holdability — it **fails** the quality floor. Missing ROIC still does not fail (FTNT-style negative IC / tax-tag holes). Distinguishes “we could not compute ROIC” from “we printed 250% and called it 10%.” Apple/Lam 80%+ stay: those denominators are real. Residual / `_highlighted` otherwise unchanged. |
| 2026-09-14 | owner decision (named owner-earnings cash setting) | v0.5.11: Q8 default stays **reported FCF** = CFO − capex − SBC. Ideas gains a named setting **owner earnings** = CFO − D&A − SBC (D&A as maintenance-capex proxy). Owner mode remaps the FCF-CAGR gate, QualityScore FCF sleeves (margin / CAGR / CoV), residual EV/FCF, and the Ideas yield bar onto that series, then rescores. Overlay strike yields stay reported. One series at a time — never a blend of reported and owner FCF (the averaging-away already rejected for CF). Old scans without owner columns stay reported. Highlights holdability ROIC: 5y average when present, else conservative `min(TTM, 5y median)`; missing both still fails. Floor 60 and ROIC band [10%, 100%] unchanged. |
| 2026-09-14 | owner decision (session-configurable zone view) | The Ideas hunt-zone thresholds are adjustable in the UI (sidebar: quality floor, max residual, min holdability ROIC, max EV/FCF), defaulting to the scan's saved rule; overrides recompute the orange zone, cards, table and map rectangle live and are session-only — the scanner, its artifacts and the frozen gates are unchanged. The inspector now also lists unscored names (marked `· unscored`, e.g. ORCL); unscored selections have no map position and show their coverage/gate reasons as a badge instead. Scored non-zone selections keep the amber star on the map |
