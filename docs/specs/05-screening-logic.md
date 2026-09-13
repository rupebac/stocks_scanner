# 05 — Screening Logic: Gates → Scores → Flags → Screens

Version 0.5.11 (2026-09-14). QualityScore absolute bars × path stability (v0.5.8),
path weights + ROIC band on the floor (v0.5.9/v0.5.10). Named owner-earnings cash
setting (v0.5.11). CheapnessScore / residual / flags otherwise as v0.4. v0.5:
input-availability pointer added (doc 08 §5).

How the filter catalog becomes a search. Four layers:

1. **Hard gates** (pass/fail filters)
2. **Scores** (quality gate + cheapness rank)
3. **Divergence flags** (the "market is wrong" detectors)
4. **Preset screens** (named searches combining 1–3) + evaluation harness

Division of labor (v0.3): **quality is the gate, cheapness is the rank, sentiment is the
flag.** No layer does another layer's job.

## 1. Hard gates

Boolean pass/fail, evaluated first; a stock failing any gate is out of the scan regardless
of scores. Default gates (docs 02–04):

- universe membership (standard sector group, doc 01)
- `adv_usd` ≥ $20M · `mktcap` ≥ $10B · `index_tenure` ≥ 1y
- growth floors (levels, plain `fcf` by default): `rev_cagr5` ≥ 0 · `fcf_cagr5` ≥ −0.05 —
  tolerant of Adobe-style deceleration. Ideas **owner-earnings** mode remaps `fcf_cagr5`
  onto `CFO − D&A − SBC` (doc 06 §7) and re-evaluates this gate; revenue CAGR is unchanged.
- data coverage: stock must have ≥ 80% of MVP score inputs, else unscored (listed separately)

Gates exist to remove noise, never to express the thesis. The thesis lives in scores and
flags. Deliberately **not** gates: `f_score` (doc 03 §3.6 — three of its nine checks are
year-over-year *improvements*, contradicting levels-not-acceleration) and `nd_ebitda`
(stays inside the QualityScore sleeve at 10%; Oracle-style leverage must not hard-block
the flagship).

## 2. Scores

Two scoring scales, kept strictly separate (mechanics: doc 06 §9):

- **Relative metrics (multiples)**: winsorized at the 1st/99th percentile, then
  percentile-ranked, within the **peer ladder** — GICS industry group when it has ≥ 8
  standard-group names, else sector, else market; the output always states which peer set
  was used. SELF-mode inputs are first converted to own-history percentiles (validity
  rules: doc 02), then ranked against the market cross-section.
- **Absolute metrics (yields)**: scored on a **fixed economic scale** (e.g. `fcf_yield` →
  clip(0, 10%)/10%), never cross-sectionally ranked — sector-ranking a yield just
  reproduces the inverted multiple's ranking and cancels the anchor (the v0.2 bug, kept
  here as the lesson).

Direction normalized so 100 = best.

### 2.1 QualityScore — "is the business good?" (no price inputs)

v0.5.8/v0.5.9: **absolute bars, not peer percentiles.** A 20% ROIC is good whether peers
are software or nitrogen. Peer-ladder percentiles remain for CheapnessScore (`ev_ebit`)
and the `beaten_but_delivering` momentum arm only.

```
QualityScore = core × (stability / 100)
core        = weighted blend of profitability / growth / balance (renormalize if a
              sleeve is missing)
stability   = 0–100 path smoothness; missing path data does **not** haircut (×1)
```

A jumpy path cannot average its way to a high score. `fcf_ni` / `gm` *level* /
`roic_years` / `fcf_pos_years` stay as display; they are not in the score.

| Sleeve | Weight | Mapping (100 = best) |
|---|---|---|
| Profitability | 40% | Conservative level = min(TTM, 5y **median**; 5y mean only if both missing). ROIC clip(0, 20%)/20%. FCF margin clip(0, 25%)/25%. Equal mix. No gross-margin *level* (sector-structural). |
| Growth | 30% | `rev_cagr5` and `fcf_cagr5` each clip(0, 12%)/12% — **12% CAGR is full marks**; 40% is not better. Equal mix; one missing → the other. The §1 gates (`rev ≥ 0`, `FCF CAGR ≥ −5%`) still apply separately. |
| Balance | 30% | `nd_ebitda`: net cash (≤ 0) = 100; 3.0× = 0; linear between. |
| Stability (multiplier) | — | Weighted path (v0.5.9): revenue-up-year share 50% (`rev_pos_years` / `rev_yoy_n`; flat counts as stable); GM CoV (`gm_stability`) 25%, with 25% CoV → 0; FCF CoV (`fcf_cov`) 25%, with 50% CoV → 0 (cash is lumpier). Missing slots renormalize — a missing GM tag cannot promote FCF CoV to half the multiplier. |

Growth stays a **gate and a capped score**, never a maximizer: that is what keeps EQT-style
volume spikes from outranking a steady 8% compounder. Stability is what drops CF-style
boom/bust names whose 5y CAGR still looks fine.

### 2.2 CheapnessScore — "how cheap is the price?" (renamed from MispricingScore, v0.3)

| Component | Weight (v0.3, tunable) | Inputs (doc 02) |
|---|---|---|
| Self-history cheapness | 40% | `ev_fcf` SELF |
| Cross-sectional cheapness | 30% | `ev_ebit` SECT (industry-group peers, §2) |
| Absolute anchor | 30% | `fcf_yield` on the fixed scale |

Deliberately **removed from the score** (v0.3 de-collinearization):

- `pe_ttm` (both modes) → display: same earnings power as `ev_ebit` (capital structure
  aside) and a second SELF multiple riding one price path
- `ey_spread` → display: with GS10 constant on a scan date, its sector rank is
  algebraically identical to inverted `ev_ebit` SECT; its job is done by the fixed-scale
  `fcf_yield` and the flag's yield gate
- `sh_yield` → context: capital-return **policy**, not cheapness
- inverted `mom_12_1` → flags only: "price falling" double-counts with `ev_fcf` SELF
  (both fire on price down + stale TTM FCF) and rewards falling knives; momentum lives in
  `beaten_but_delivering` and nowhere else
- `rs_12m` → context (collinear with `mom_12_1`, v0.2)

With momentum out, this score measures cheapness, nothing else — hence the rename. The
*mispricing judgment* (cheap **and** quality intact **and** sentiment bad) is the flag
layer's job.

### 2.3 Quality floor, ranking key, composite

- **Quality floor — a gate, not a ranker.** `QualityScore ≥ 60` on the absolute scale
  (v0.5.8) **and** conservative ROIC in **[10%, 100%]** when ROIC is computable
  (`min(TTM, 5y median)`; v0.5.9/v0.5.10). Missing ROIC does not fail. A collapsed
  current ROIC cannot average past the floor via growth + cash + FCF margin. A
  *computed* ROIC > 100% (NOPAT > invested capital) is a tiny-IC artifact and **fails**
  — that is not the same as “we could not compute ROIC.” 80–85% with a real denominator
  (buybacks) still passes. Replaces the v0.4 top-20% cut: a fixed numeral so a name's
  candidacy does not flicker with tonight's cohort, and so Materials peers cannot lift a
  cyclical to “high quality.” Threshold is stored on the scan artifact as
  `quality_floor_threshold`.
- **Flagship rank key: the valuation residual.** Per scan date, fit
  `log(EV/FCF) ~ log(ROIC) + industry-group dummies` — **on the full standard-group
  cross-section** with valid EV/FCF and ROIC (ROIC floored at 1% for the log; ROIC ≤ 0
  excluded from the fit; v0.4 — fitting on the ~20% of floor-passers plus dummies would
  overfit). The fit universe is everybody; the **ranking** universe is floor-passers.
  The residual is the multiple discount the market does **not** justify by quality —
  "cheap *for its quality*" in one number. Display fitted vs. actual alongside, and
  display the **industry dummies next to the residual**: the dummy is the sector-wide
  (regime) discount, the residual is the idiosyncratic one — a 2022-style crush shows up
  in dummies, an Adobe-vs-software-peers de-rating in the residual. Robust/winsorized
  fit; tiny dummy groups fall back down the §2 ladder. CheapnessScore ranks presets
  whose inputs can't support the residual.
- **Composite** = 0.5 × QualityScore + 0.5 × CheapnessScore — **display only** (v0.3).
  Flagship lists are never ranked by it: quality is the gate, cheapness (residual) is the
  rank, sentiment is the flag.

**Cash definition (v0.5.11, Ideas setting).** Default FCF for QualityScore sleeves,
the residual, the FCF-CAGR gate, and the Ideas yield bar is **reported** `fcf_adj`
(CFO − capex − SBC). A named **owner earnings** switch remaps those slots onto
`CFO − D&A − SBC` and rescores — D&A stands in for maintenance capex so growth
buildouts do not zero the operating cash engine. Overlay strike yields stay on
reported FCF. The two series are never averaged. Scan artifacts keep both columns;
old scans without owner columns stay reported.

**Ideas highlights holdability ROIC (v0.5.11).** Membership still requires the quality
floor, residual < 0, ROIC ≥ 10%, and FCF yield ≥ max(4%, GS10). The ROIC used is the
5y average when present, else conservative `min(TTM, 5y median)`. Missing both fails.
Options never rank, hide, or change membership except via the quality-floor /
membership rules above.

Named, inspectable detections. Each flag stores its evidence (triggering metrics + values)
so the UI can always answer *"why is this name on the list?"*.

### `derated_quality` — the flagship detector (rewritten v0.3)

**Rule — ALL of:**

1. Quality floor passed (§2.3)
2. Depressed multiple vs own history — **any** of:
   - `ev_fcf` SELF ≤ 20th pct (new lows), **or**
   - z(`ev_fcf`, 5y) ≤ −1.0, **or**
   - EV/FCF ≤ 0.8 × 5y median (depressed-but-not-new-low)
3. Cheap in absolute terms **or** unjustified by quality — **either** of:
   - `fcf_yield` ≥ max(4%, GS10) — bond-competitive cash yield, or
   - valuation residual ≤ 20th pct among quality-floor passers — idiosyncratic discount
     vs. the ROIC-justified multiple
4. Fundamentals not eroding — **all** of (TTM vs 5y avg, the non-price brake):
   - `fcf_margin_ttm` ≥ 0.95 × · `gm_ttm` ≥ 0.97 × · `roic_ttm` ≥ 0.90 ×

Rationale, per arm:

- **OR-group (2):** once the 5y window contains a prior crash, SELF ≤ 20th fires only on
  *new* lows and misses the canonical second derating that stays depressed vs. the median
  (Adobe 2024–26 — the highest-cost false negative in v0.2). The median-ratio arm catches
  that case scale-free; the z arm adds distance-from-center. Which arm carries the load is
  an empirical question for the harness, not a spec choice.
- **Yield/residual OR (3, v0.4):** a hard 4% yield AND blocks exactly the names we hunt
  — a high-ROIC compounder at 28× FCF yields 3.6% and fails `max(4%, GS10)` while being
  cheap vs. its own history and vs. its ROIC. The OR keeps the guard without the block:
  in a 2022-style *regime* de-rating, the industry dummies absorb the sector-wide crush,
  residuals stay quiet, and the cohort still needs the yield arm (which most fail at 4%+
  rates) — regime false positives stay out. An *idiosyncratic* de-rating (hated vs.
  peers, quality intact) fires the residual arm at any yield level. Margin brakes stay
  hard AND either way.
- **Slope brake (4):** the old FCF-TTM ≥ 0.9 × 5y check was a level test — a slow bleed
  (100→95→90→88→86 passes at 0.94) never trips it. Margins erode before 5y-average ROIC
  rolls over; these tests discriminate Adobe (margins flat) from PayPal/Intel-early
  (margins bleeding).

### `beaten_but_delivering`

`mom_12_1` ≤ sector 25th pct **and** `eps_rev_breadth_3m` > 0 **and** quality floor
passed. Price punished while estimates and delivery improve. Positive revisions belong
**only** here — the Adobe hunt window often has *down* revisions, so the flagship must
tolerate them.

### `crowd_exiting` (P2)

`dd_52w` ≤ −25% **and** `short_chg_3m` < 0 **and** quality floor passed. Capitulation in
a quality name (slow signal).

Flags may overlap; presets select on flags + scores.

## 4. Preset screens (v0.3)

| Preset | Definition | Purpose |
|---|---|---|
| **Quality at a Discount** (flagship) | `derated_quality` flag, ranked by valuation residual (§2.3) | the Adobe/Oracle hunt |
| **Beaten but Delivering** | `beaten_but_delivering` flag, ranked by CheapnessScore | sentiment-divergence entries, earlier in the recovery |
| **Compounders on Sale** | stability component ≥ 80 + `ev_fcf` SELF ≤ 30th pct **+ the same brakes as the flagship** (quality floor, margin brakes, yield/residual OR) | long-horizon: smooth-path names at self-history discounts |
| **Custom** | arbitrary {metric, mode, operator, value} filter stack on any catalog ID | the "like any other scanner" part — full filter-builder UI |

A preset's definition is data (JSON: gates + flag rules + ranking key), not code, so
presets are diffable, versioned, and shareable.

## 4b. Scan artifacts & reproducibility

Every scan persists (contract mirrored in doc 01): scan id + date, the exact preset/filter
config (JSON + hash), universe version, and the full per-stock output — raw metric values,
peer-set percentiles, SELF percentiles (incl. z and median-ratio), component scores,
residual, flags and their evidence. Any past scan must be re-runnable bit-for-bit from its
stored config and snapshot. The evaluation harness — and every methodology argument we
ever have — depends on this.

## 5. Evaluation harness (Phase 4, spec'd now)

Every screen must be measurable, or we're decorating noise:

- **Vintaged snapshots only**: run each screen on historical scan snapshots persisted by
  the nightly job (doc 01, scan artifacts). We make **no point-in-time claims for dates
  before snapshot persistence starts** — no backfilled 10y study on today's constituents
  with restated `companyfacts`: survivorship, restatements and today's GICS classes would
  be silently embedded in every number. Every pre-tracking table must say so explicitly.
- **Forward returns**: 6m and 12m, per screen, vs. three baselines: S&P 500 equal-weight,
  the screen's own sector, and a **matched-cheapness basket** — same sector, same SELF
  percentile band, quality floor OFF. The third baseline is the thesis test: it isolates
  what the quality floor and divergence logic add beyond "buy what fell".
- **No PIT estimates, no revisions backtest**: `eps_rev_breadth_3m` cannot be made
  point-in-time on free sources; `beaten_but_delivering` gets forward tracking from launch
  instead of a fake history.
- **Report**: hit rate, median/mean excess return, max drawdown, and per-regime results
  (rate-up, rate-down, 2022-style drawdown, recovery). Which SELF arm (percentile / z /
  median-ratio) actually fires the flag gets reported, and the OR-group simplified
  accordingly.
- **Known limitations, stated up front**: survivorship and latest-restated data for
  pre-tracking dates (doc 01); ~monthly rebalance granularity vs. intraday screens; no
  transaction costs in v1.

## Anti-goals (explicit)

- Not a backtest playground for arbitrary strategies — screens only, ranked lists out.
- No intraday data, no auto-trading anything. Options exist only as the **nightly display
  overlay** of doc 07 (strike-implied yields, DTE badge, IV/HV display) — no options
  metric ever enters a score, a flag, or a ranking; full surfaces/skews are paid-data
  territory and out of scope.
- No black boxes: every ranked stock must be traceable to its triggering metrics.
