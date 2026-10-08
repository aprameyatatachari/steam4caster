from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.forecasting.baseline import (
    baseline_new_sale_probs,
    baseline_tier_probs,
    cadence_probability,
    derive_window,
)
from app.forecasting.features.builder import FEATURE_NAMES, build_state
from app.forecasting.inference.engine import forecast_series
from app.forecasting.policy import (
    RULESET_VERSION,
    PolicyConfig,
    PolicyInput,
    decide,
    probability_within,
)
from app.forecasting.priors import GLOBAL_DEFAULT_PRIOR, choose_prior, prior_from_events
from app.forecasting.types import GameContext, PricePoint
from app.models.enums import DataQuality, ForecastMethod
from tests.helpers import NOW, price_points, regular_sales

CONTEXT = GameContext(release_date=date(2022, 1, 10), publisher="Script House", type="game")


def state_for(sales, now=NOW, **kwargs):  # type: ignore[no-untyped-def]
    return build_state(price_points(now, sales=sales), CONTEXT, now, GLOBAL_DEFAULT_PRIOR, **kwargs)


# --- features and leakage ---------------------------------------------------


def test_feature_vector_is_complete_and_named() -> None:
    state = state_for(regular_sales(6, 60, 30))
    assert set(state.features) == set(FEATURE_NAMES)
    assert state.features["n_sales_total"] == 6
    assert state.features["interval_median"] == pytest.approx(60)
    assert state.features["days_since_last_sale_start"] == pytest.approx(30)
    assert state.features["last_discount"] == 50
    assert state.features["price_to_low"] == pytest.approx(2.0)
    assert not state.on_sale


def test_future_observations_never_enter_features() -> None:
    """The defining leakage test: appending anything after the cutoff changes nothing."""
    cutoff = NOW - timedelta(days=100)
    past = price_points(NOW, sales=regular_sales(8, 60, 30))
    future_noise = [
        PricePoint(cutoff + timedelta(days=1), 200, 2000, 90),
        PricePoint(cutoff + timedelta(days=2), 2000, 2000, 0),
        PricePoint(cutoff + timedelta(days=40), 500, 1000, 50),  # even a regular-price change
    ]
    visible_only = [p for p in past if p.at <= cutoff]
    baseline = build_state(visible_only, CONTEXT, cutoff, GLOBAL_DEFAULT_PRIOR)
    with_future = build_state(past + future_noise, CONTEXT, cutoff, GLOBAL_DEFAULT_PRIOR)
    assert with_future.features == baseline.features
    assert with_future.events == baseline.events
    assert with_future.historical_low_minor == baseline.historical_low_minor
    assert forecast_series(with_future).new_sale_probs == forecast_series(baseline).new_sale_probs


def test_sale_in_progress_at_cutoff_is_seen_as_running() -> None:
    state = state_for([(200, 7, 50), (3, 0, 40)])
    assert state.on_sale
    assert state.features["days_since_last_sale_end"] == 0.0
    assert state.features["current_discount"] == 40


# --- baseline ---------------------------------------------------------------


def test_cadence_probability_rises_as_the_usual_gap_approaches() -> None:
    intervals = [58.0, 60.0, 62.0, 60.0, 59.0, 61.0]
    early = cadence_probability(intervals, 5, 30, prior_median=75)
    near = cadence_probability(intervals, 45, 30, prior_median=75)
    assert near > early
    assert near > 0.6
    assert early < 0.4


def test_baseline_probabilities_are_monotonic_and_bounded() -> None:
    for sales in ([], [(400, 7, 30)], regular_sales(6, 60, 30), regular_sales(10, 30, 2)):
        probs = baseline_new_sale_probs(state_for(sales))
        assert 0.01 <= probs[7] <= probs[30] <= probs[90] <= 0.97


def test_never_discounted_game_gets_a_lower_probability_than_the_prior() -> None:
    never = baseline_new_sale_probs(
        state_for(
            [],
        )
    )
    fresh = build_state(
        price_points(NOW, sales=[], first_days_ago=20), CONTEXT, NOW, GLOBAL_DEFAULT_PRIOR
    )
    assert never[90] < baseline_new_sale_probs(fresh)[90]


def test_tier_distribution_follows_recent_history_but_keeps_every_tier_possible() -> None:
    tiers = baseline_tier_probs(state_for(regular_sales(6, 60, 30, cut=50)))
    assert max(tiers, key=tiers.get) == "50_TO_59"  # type: ignore[arg-type]
    assert sum(tiers.values()) == pytest.approx(1.0)
    assert all(p > 0 for p in tiers.values())


def test_window_only_given_when_cadence_is_concentrated() -> None:
    regular = state_for(regular_sales(6, 60, 30))
    window = derive_window(regular, baseline_new_sale_probs(regular))
    assert window is not None
    start, end = window
    assert NOW.date() < start <= end
    assert (end - start).days <= 45

    erratic = state_for([(700, 7, 50), (690, 7, 50), (400, 7, 50), (390, 7, 50), (40, 7, 50)])
    assert erratic.features["interval_cv"] > 0.5
    assert derive_window(erratic, baseline_new_sale_probs(erratic)) is None

    on_sale = state_for([(200, 7, 50), (140, 7, 50), (80, 7, 50), (2, 0, 50)])
    assert derive_window(on_sale, baseline_new_sale_probs(on_sale)) is None


# --- cold start and confidence ---------------------------------------------


def test_cold_start_uses_prior_and_reports_low_confidence() -> None:
    state = build_state(
        price_points(NOW, sales=[], first_days_ago=10), CONTEXT, NOW, GLOBAL_DEFAULT_PRIOR
    )
    result = forecast_series(state)
    assert result.method == ForecastMethod.BASELINE
    assert result.data_quality == DataQuality.INSUFFICIENT
    assert result.confidence <= 0.30
    assert "INSUFFICIENT_GAME_HISTORY" in {f.code for f in result.factors}
    assert result.window is None  # no fabricated precision


def test_confidence_grows_with_history_and_is_not_the_max_class_probability() -> None:
    sparse = forecast_series(state_for([(120, 7, 50)]))
    rich = forecast_series(state_for(regular_sales(10, 60, 30)))
    assert rich.confidence > sparse.confidence
    assert rich.data_quality == DataQuality.GOOD
    assert sparse.data_quality == DataQuality.LIMITED
    assert rich.confidence != pytest.approx(max(rich.tier_probs.values()))


def test_stale_data_lowers_confidence() -> None:
    fresh = forecast_series(state_for(regular_sales(10, 60, 30), data_age_days=0))
    stale = forecast_series(state_for(regular_sales(10, 60, 30), data_age_days=120))
    assert stale.confidence < fresh.confidence


def test_prior_hierarchy_falls_back_in_order() -> None:
    def cohort(games: int, sales: int):  # type: ignore[no-untyped-def]
        return {
            g: [(NOW - timedelta(days=45 * i + g), 60) for i in range(sales)] for g in range(games)
        }

    assert choose_prior(cohort(3, 5), cohort(3, 5), None).level == "PUBLISHER"
    assert choose_prior(cohort(1, 2), cohort(3, 5), None).level == "TAG"  # publisher too thin
    assert choose_prior(None, None, cohort(4, 6)).level == "GLOBAL"
    assert choose_prior({}, None, None) is GLOBAL_DEFAULT_PRIOR
    prior = prior_from_events("PUBLISHER", cohort(3, 5))
    assert prior is not None
    assert prior.median_interval_days == pytest.approx(45)
    assert max(prior.tier_probs, key=prior.tier_probs.get) == "60_TO_74"  # type: ignore[arg-type]


# --- forecast output invariants --------------------------------------------


def test_forecast_prices_use_the_regional_regular_price_and_never_go_negative() -> None:
    for regular in (99, 2000, 249900):
        state = build_state(
            price_points(NOW, sales=regular_sales(6, 60, 30, cut=75), regular=regular),
            CONTEXT, NOW, GLOBAL_DEFAULT_PRIOR,
        )  # fmt: skip
        result = forecast_series(state)
        assert result.regular_price_minor == regular
        assert 0 <= result.price_lower_minor <= result.price_median_minor  # type: ignore[operator]
        assert result.price_median_minor <= result.price_upper_minor <= regular  # type: ignore[operator]


def test_reported_probability_is_certain_while_on_sale_but_new_sale_is_not() -> None:
    result = forecast_series(state_for([(200, 7, 50), (140, 7, 50), (80, 7, 50), (2, 0, 50)]))
    assert result.currently_on_sale
    assert result.sale_probs == {7: 1.0, 30: 1.0, 90: 1.0}
    assert result.new_sale_probs[7] < 1.0
    assert "CURRENTLY_ON_SALE" in {f.code for f in result.factors}


def test_explanations_are_templated_and_importances_sum_to_one() -> None:
    result = forecast_series(state_for(regular_sales(6, 60, 58)))
    codes = {f.code for f in result.factors}
    assert "RECENT_SALE_CADENCE" in codes
    assert sum(f.importance for f in result.factors) == pytest.approx(1.0)
    assert all(f.direction in {"BUY", "WAIT", "NEUTRAL"} for f in result.factors)
    cadence = next(f for f in result.factors if f.code == "RECENT_SALE_CADENCE")
    assert "60" in cadence.text


# --- recommendation policy --------------------------------------------------

TIERS_50 = {"50_TO_59": 1.0}


def policy_input(**overrides):  # type: ignore[no-untyped-def]
    base = {
        "current_price_minor": 2000,
        "regular_price_minor": 2000,
        "historical_low_minor": 1000,
        "current_discount_pct": 0,
        "currently_on_sale": False,
        "new_sale_probs": {7: 0.2, 30: 0.8, 90: 0.95},
        "tier_probs": TIERS_50,
        "confidence": 0.8,
        "currency": "USD",
        "max_wait_days": 30,
    }
    base.update(overrides)
    return PolicyInput(**base)


def test_expected_value_formula() -> None:
    decision = decide(policy_input())
    # E[price|sale] = 2000 * (1 - 0.545) = 910; future = 0.8*910 + 0.2*2000 = 1128
    assert decision.expected_future_price_minor == 1128
    assert decision.expected_savings_minor == 872
    assert decision.waiting_cost_minor == 100  # 5% of price per 30 days
    assert decision.action == "WAIT"
    assert decision.reason_codes[:2] == ["HIGH_SALE_PROBABILITY", "MEANINGFUL_EXPECTED_SAVINGS"]
    assert decision.thresholds["ruleset_version"] == RULESET_VERSION
    assert "estimate" in decision.summary and "guarantee" in decision.summary


def test_wait_probability_threshold_boundary() -> None:
    at = decide(policy_input(new_sale_probs={7: 0.1, 30: 0.60, 90: 0.9}))
    below = decide(policy_input(new_sale_probs={7: 0.1, 30: 0.59, 90: 0.9}))
    assert at.action == "WAIT"
    assert below.action == "NEUTRAL"
    assert "CONFLICTING_EVIDENCE" in below.reason_codes


def test_wait_savings_threshold_boundary() -> None:
    config = PolicyConfig(waiting_cost_fraction=0.0)
    # savings fraction = p * expected_discount; LT_20 midpoint is 12%.
    shallow = {"LT_20": 1.0}
    enough = decide(
        policy_input(new_sale_probs={7: 0.5, 30: 0.9, 90: 0.95}, tier_probs=shallow), config
    )
    too_little = decide(
        policy_input(new_sale_probs={7: 0.5, 30: 0.7, 90: 0.95}, tier_probs=shallow), config
    )
    assert enough.expected_savings_minor == 216 and enough.action == "WAIT"  # 10.8%
    assert too_little.expected_savings_minor == 168 and too_little.action == "NEUTRAL"  # 8.4%


def test_buy_when_near_regional_historical_low() -> None:
    within = decide(policy_input(current_price_minor=1050, current_discount_pct=47,
                                 currently_on_sale=True))  # fmt: skip
    outside = decide(policy_input(current_price_minor=1060, current_discount_pct=47,
                                  currently_on_sale=True))  # fmt: skip
    assert within.action == "BUY" and "NEAR_HISTORICAL_LOW" in within.reason_codes
    assert "NEAR_HISTORICAL_LOW" not in outside.reason_codes


def test_buy_when_expected_savings_are_small() -> None:
    decision = decide(policy_input(new_sale_probs={7: 0.01, 30: 0.04, 90: 0.1}))
    assert decision.action == "BUY"
    assert decision.reason_codes == ["SMALL_EXPECTED_SAVINGS", "LOW_SALE_PROBABILITY"]


def test_low_confidence_is_neutral_at_the_boundary() -> None:
    assert decide(policy_input(confidence=0.349)).action == "NEUTRAL"
    assert decide(policy_input(confidence=0.349)).reason_codes == ["LOW_CONFIDENCE"]
    assert decide(policy_input(confidence=0.35)).action == "WAIT"


def test_waiting_cost_can_flip_wait_to_neutral() -> None:
    patient = decide(policy_input(max_wait_days=30), PolicyConfig(waiting_cost_fraction=0.05))
    impatient = decide(policy_input(max_wait_days=30), PolicyConfig(waiting_cost_fraction=0.5))
    assert patient.action == "WAIT"
    assert impatient.action == "NEUTRAL"
    assert "WAITING_COST_EXCEEDS_SAVINGS" in impatient.reason_codes


def test_shallow_current_sale_can_still_be_wait_and_deep_one_is_buy() -> None:
    probs = {7: 0.3, 30: 0.85, 90: 0.97}
    shallow = decide(policy_input(current_price_minor=1800, current_discount_pct=10,
                                  currently_on_sale=True, new_sale_probs=probs,
                                  historical_low_minor=900))  # fmt: skip
    assert shallow.action == "WAIT" and "DEEPER_SALE_LIKELY" in shallow.reason_codes
    deep = decide(policy_input(current_price_minor=950, current_discount_pct=52,
                               currently_on_sale=True, new_sale_probs=probs,
                               historical_low_minor=600))  # fmt: skip
    assert deep.action == "BUY" and "ON_SALE_NOW" in deep.reason_codes


def test_no_price_and_free_edge_cases() -> None:
    assert decide(policy_input(current_price_minor=None)).reason_codes == ["NO_PRICE_DATA"]
    assert decide(policy_input(current_price_minor=0)).action == "BUY"


def test_probability_interpolation_is_monotonic_and_hits_anchors() -> None:
    probs = {7: 0.1, 30: 0.5, 90: 0.9}
    assert probability_within(probs, 7) == pytest.approx(0.1)
    assert probability_within(probs, 30) == pytest.approx(0.5)
    assert probability_within(probs, 90) == pytest.approx(0.9)
    series = [probability_within(probs, d) for d in range(0, 200, 5)]
    assert series == sorted(series)
    assert series[0] == 0.0 and series[-1] <= 0.97
