# Forecasting and recommendations

Steam prices are step functions driven by promotions, not continuously traded assets.
The system therefore models two separate questions and never extrapolates a price curve:

1. **When** — the probability that a sale starts within 7, 30 and 90 days.
2. **How deep** — a distribution over discount tiers, conditional on a sale happening.

Everything is an estimate. No output is an exact date or a guaranteed price.

## Inputs

**Sale events** are derived from the price change log (`app/forecasting/sale_events.py`):
a sale starts at the first discounted observation and ends at the next undiscounted one.
Depth changes inside a discounted period stay within one event. Events are rebuilt from
observations whenever history changes; the derivation is versioned (`se-1`).

**Features** (`app/forecasting/features/builder.py`, schema `fs-1`) are computed by one
function, `build_state`, used for both training rows and live inference. It only looks at
observations at or before the cutoff. Feature families:

- Cadence: sale counts (total, 90/180/365 days), days since the last sale began and
  ended, median/mean/spread/min/max of gaps between sale starts, elapsed-to-median ratio.
- Depth: last, median, mean, max and recent-three discounts, spread, mean sale length.
- Calendar: month, week, day-of-year encoding, days until the next estimated seasonal
  sale, whether one is running, and the share of past seasonal sales this title joined.
- Price position: current discount, price relative to regular and to the regional low,
  regular-price changes in the last year.
- Profile: game age, release month, DLC flag, history length.
- Cohort prior: the prior's median gap, size and seasonal participation.

Review scores and popularity are deliberately not used: they would need a documented,
permitted source. Cross-store prices are not used either.

**Seasonal windows** (`app/forecasting/calendar.py`) are date rules fitted to the
yearly pattern. From 2026 on they follow Steam's usual schedule: Spring (one week from
the third Thursday of March), Summer (two weeks from the last Thursday of June), Autumn
(one week from the first Thursday of October) and Winter (from the third Thursday of
December into early January). Earlier years keep the dates that actually happened
(Autumn was Thanksgiving week through 2024 and late September in 2025), since those are
matched against real price history. Valve publishes no schedule and has moved sales
before, so these are estimates used as features and hints only. Steam Next Fest weeks
are also estimated for display, but they are demo events, not sales, and never count as
a sale signal.

## Baseline (deterministic)

`app/forecasting/baseline.py`, version `baseline-1`. Used whenever no compatible model is
active, an artifact fails to load, inference raises, or the series has too little
history. Each forecast records the method that actually produced it.

- **Timing.** Empirical conditional survival over the game's own gaps: of the past gaps
  longer than the time already elapsed, the share that ended within the horizon. This is
  shrunk toward the cohort prior with a weight of `n / (n + 3)`. A game overdue relative
  to every past gap gets an elevated but uncertain hazard. A long history with no
  discount at all shrinks the prior instead of trusting it.
- **Seasonal.** For each estimated seasonal sale starting inside the horizon, the
  title's smoothed participation rate. Because cadence gaps already include past
  seasonal sales, this contributes only half of its independent effect.
- **Depth.** Recency-weighted counts of the game's own past tiers, smoothed by the
  cohort's tier distribution so no tier is ever impossible.
- **Window.** A likely window is only returned when the evidence is concentrated: a
  seasonal sale within 60 days that the title joined at least 70% of the time, or a
  regular cadence (coefficient of variation ≤ 0.5) with at least a 50% 90-day
  probability. Otherwise the window is null.

## Cold start

Priors fall back in order (`app/forecasting/priors.py`):

1. The game's own history.
2. Other games from the same publisher.
3. Games sharing the primary tag.
4. All games for the shop and country.
5. A documented default (a sale about every 75 days, mid-depth, 60% seasonal uptake)
   when the database holds too little to estimate a cohort.

A cohort needs at least 8 sales and 4 gaps to be used. Cold-start forecasts carry
`data_quality = INSUFFICIENT`, a capped confidence, the factor
`INSUFFICIENT_GAME_HISTORY`, and no window.

## ML models

LightGBM (`app/forecasting/training/trainer.py`). Chosen because it handles missing
values natively (cold-start rows are full of them), trains quickly on small tabular
data, ships self-contained wheels on every platform, and exposes per-feature
contributions for explanations.

- **Model A:** one binary classifier per horizon, calibrated with isotonic regression.
- **Model B:** a multiclass classifier over the tiers present in training, blended 80/20
  with the baseline tier distribution so unseen tiers keep non-zero probability.

Tiers: `LT_20`, `20_TO_29`, `30_TO_39`, `40_TO_49`, `50_TO_59`, `60_TO_74`, `75_PLUS`.
The predicted sale-price interval is the central 80% of the tier distribution applied to
the **regional regular price**, clamped to `[0, regular]`, in the region's own currency.

### Dataset and leakage control

`app/forecasting/datasets/builder.py` builds one row per (series, cutoff), stepping
through each series every 14 days.

- Features use only observations at or before the cutoff.
- Labels use only what happened after it. A label is left missing when its horizon runs
  past the dataset's as-of date.
- Rows are not sampled while a sale is running.
- Cohort priors for a row use only sales that started before the cutoff's month.

Tests check this directly: appending arbitrary future observations must not change any
feature, and rows rebuilt from truncated history must equal the dataset rows.

### Validation

Forward-chaining only, never a random split: train through an earlier period, validate
on the next, four times. Training rows whose label window would overlap the validation
period are purged. Calibration is honest: each fold is scored with a calibrator fitted
only on earlier folds, and only those folds count toward reported metrics.

Reported for candidate and baseline on the same held-out rows:

- Sale probability: Brier score, log loss, ROC-AUC, PR-AUC, precision and recall at the
  decision threshold, calibration bins, expected calibration error; Brier by history
  cohort (fewer than 4 sales, 4 or more).
- Discount tier: macro and per-class F1, within-one-tier accuracy, discount MAE, price
  MAE as a fraction of regular price, quantile loss, interval coverage.
- Policy: realised savings versus buying immediately, share of `WAIT` followed by a
  lower price, regret when `BUY` preceded a material drop, by confidence band and
  history cohort.

### Promotion gates

`app/forecasting/training/gates.py`. A candidate must satisfy all of:

- Brier score no worse than the baseline at every horizon.
- 30-day log loss no worse than the baseline.
- Expected calibration error ≤ max(0.05, baseline + 0.01) at every horizon.
- No history cohort (with at least 50 rows) more than 5% worse than the baseline.
- Within-one-tier accuracy at most 0.02 lower, and discount MAE at most 5% higher.

`activate` refuses a candidate that failed unless `--force` is given. Training never
activates anything. `rollback` restores the previously active model, or the baseline.

On the synthetic demo catalogue (12 games) the candidate does **not** beat the baseline
and is refused. That is the expected result on so little data, and it is why the
baseline is the default.

### Artifacts

A joblib file per version under `MODEL_ARTIFACT_PATH`, with its SHA-256, metrics,
hyperparameters, training cutoff and feature-schema version in `model_versions`. An
artifact is deserialised only after its checksum matches the registered one, and only if
its feature schema and feature names match the running code. The active model is loaded
once per process and reloaded only when the active version changes.

## Confidence

`app/forecasting/confidence.py`. Not the largest class probability. A weighted blend of:

| Component | Weight | Meaning |
| --- | --- | --- |
| History | 0.35 | Number of past sales and length of history |
| Recency | 0.15 | How recently the data was refreshed |
| Completeness | 0.10 | Share of features that are present |
| Sharpness | 0.15 | One minus the entropy of the tier distribution |
| Calibration | 0.15 | Validation calibration of the producing method |
| In-distribution | 0.10 | Share of features inside the model's training range |

Capped at 0.30 for `INSUFFICIENT` data and 0.45 with fewer than two sales. The baseline's
calibration component is a fixed assumption (0.6) until measured outcomes replace it.

## Explanations

`app/forecasting/explanations.py`. Factors are produced by rules over the series state
and filled into fixed templates. With an ML forecast, LightGBM contribution values are
grouped by feature family to weight the factors and set their direction. No language
model is involved. Codes include `RECENT_SALE_CADENCE`, `OVERDUE_FOR_SALE`,
`RECENTLY_ON_SALE`, `UPCOMING_SEASONAL_SALE`, `NEAR_HISTORICAL_LOW`, `CURRENTLY_ON_SALE`,
`TYPICAL_DISCOUNT_DEPTH`, `NEVER_DISCOUNTED`, `INSUFFICIENT_GAME_HISTORY`,
`LIMITED_GAME_HISTORY` and `REGULAR_PRICE_CHANGED`.

## Recommendation policy

`app/forecasting/policy.py`, ruleset `rules-1`. A pure function of the current price, the
forecast, its confidence and the user's maximum wait.

```text
expected_future_price = P(sale in horizon) * E(price | sale)
                      + (1 - P(sale in horizon)) * price_if_no_sale
expected_savings      = current_price - expected_future_price
waiting_cost          = current_price * waiting_cost_fraction * (max_wait_days / 30)
wait_utility          = expected_savings - waiting_cost
```

`P(sale in horizon)` is interpolated between the 7/30/90-day anchors assuming a constant
hazard between them. `price_if_no_sale` is the current price, or the regular price when
a sale is running now (it is assumed to end).

**Waiting cost is an assumption, not a fact.** By default waiting costs 5% of the price
per 30 days (`REC_WAITING_COST_FRACTION`). It expresses impatience and is configurable.

Rules, in order:

1. No regional price → `NEUTRAL` (`NO_PRICE_DATA`). Free → `BUY`.
2. Discounted and within 5% of the regional historical low → `BUY`
   (`NEAR_HISTORICAL_LOW`).
3. Confidence below 0.35 → `NEUTRAL` (`LOW_CONFIDENCE`).
4. Probability ≥ 0.60, expected savings ≥ 10% of price, and positive utility → `WAIT`.
5. Expected savings ≤ 3% of price → `BUY` (`SMALL_EXPECTED_SAVINGS`).
6. Otherwise `NEUTRAL` (`MARGINAL_BENEFIT`, plus `CONFLICTING_EVIDENCE` or
   `WAITING_COST_EXCEEDS_SAVINGS` where they apply).

Every decision stores its reason codes, the thresholds in force and the ruleset version,
so it can be reproduced. Thresholds are settings (`REC_*`); changing rule logic should
bump `RULESET_VERSION`.

## Accountability

Forecast rows are never updated with a new prediction. A daily job fills outcome columns
once horizons expire, using only stored observations after the forecast's cutoff and in
the forecast's currency. "Sale within N days" means the game was discounted at any point
in the window, so a sale already running at the cutoff counts. The performance endpoints
report Brier score, log loss, calibration and policy regret over those outcomes, by
method and by model version.

## Caveats

- Publishers change behaviour; a regular cadence can stop without warning.
- Seasonal dates are estimates and can shift by days or move entirely.
- Provider history can be incomplete, especially for older games and smaller regions.
- A regular-price change makes older sale prices less comparable.
- Offline policy savings assume a `WAIT` buyer bought at the lowest price in the
  window, which is an upper bound on what a real buyer captures.
