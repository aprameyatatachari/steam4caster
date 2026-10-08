/**
 * Illustrative data for the public landing page. It is authored, not real: the page
 * labels it as such wherever it appears. The signed-in app only ever shows data from
 * the backend.
 */
import type { Forecast, HistoryPoint, SaleEvent } from "./api";

const CURRENCY = "INR";
const REGULAR = 149900;
const DAY = 86_400_000;

const m = (amount_minor: number) => ({
  amount_minor,
  currency: CURRENCY,
  amount: (amount_minor / 100).toFixed(2),
});

/** A plausible four-year price log: seasonal sales plus the publisher's own promotions. */
export function demoSeries(now = new Date()): { history: HistoryPoint[]; sales: SaleEvent[] } {
  const end = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  // [days before today the sale started, length in days, discount %]
  const pattern: [number, number, number][] = [
    [1380, 14, 20], [1290, 7, 25], [1205, 14, 33], [1120, 7, 33], [1040, 14, 40],
    [955, 7, 40], [860, 14, 50], [770, 7, 40], [690, 14, 50], [600, 7, 50],
    [520, 14, 60], [432, 7, 50], [350, 14, 60], [262, 7, 50], [176, 14, 66],
    [92, 7, 50], [41, 7, 60],
  ];
  const history: HistoryPoint[] = [
    { observed_at: new Date(end - 1460 * DAY).toISOString(), price: m(REGULAR), regular: m(REGULAR), discount_pct: 0 },
  ];
  const sales: SaleEvent[] = [];
  for (const [ago, length, cut] of pattern) {
    const start = end - ago * DAY;
    const price = Math.round((REGULAR * (100 - cut)) / 100);
    history.push({ observed_at: new Date(start).toISOString(), price: m(price), regular: m(REGULAR), discount_pct: cut });
    history.push({
      observed_at: new Date(start + length * DAY).toISOString(),
      price: m(REGULAR),
      regular: m(REGULAR),
      discount_pct: 0,
    });
    sales.push({
      started_at: new Date(start).toISOString(),
      ended_at: new Date(start + length * DAY).toISOString(),
      regular: m(REGULAR),
      initial_price: m(price),
      min_price: m(price),
      max_discount_pct: cut,
    });
  }
  return { history, sales };
}

export function demoForecast(now = new Date()): Forecast {
  const end = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const iso = (offset: number) => new Date(end + offset * DAY).toISOString().slice(0, 10);
  return {
    id: "demo",
    game_id: "demo",
    country: "IN",
    currency: CURRENCY,
    created_at: new Date(end).toISOString(),
    cutoff_at: new Date(end).toISOString(),
    method: "BASELINE",
    model_version: "baseline-1",
    currently_on_sale: false,
    sale_probability: { days_7: 0.07, days_30: 0.46, days_90: 0.94 },
    new_sale_probability: { days_7: 0.07, days_30: 0.46, days_90: 0.94 },
    discount_tier_probabilities: {
      LT_20: 0.02, "20_TO_29": 0.03, "30_TO_39": 0.05, "40_TO_49": 0.11,
      "50_TO_59": 0.44, "60_TO_74": 0.31, "75_PLUS": 0.04,
    },
    most_likely_discount_tier: "50_TO_59",
    expected_discount_pct: 56.1,
    current_price: m(REGULAR),
    regular_price: m(REGULAR),
    predicted_sale_price: { lower: m(50900), median: m(67400), upper: m(88400), coverage: 0.8 },
    likely_window: { start: iso(38), end: iso(52) },
    confidence_score: 0.81,
    data_quality: "GOOD",
    explanation_factors: [
      { code: "RECENT_SALE_CADENCE", direction: "WAIT", importance: 0.34, text: "Sales have historically started every 82–90 days." },
      { code: "UPCOMING_SEASONAL_SALE", direction: "WAIT", importance: 0.27, text: "A seasonal sale is estimated to begin in about 38 days, and this game joined 9 of the last 10." },
      { code: "TYPICAL_DISCOUNT_DEPTH", direction: "NEUTRAL", importance: 0.21, text: "Past sales most often fell in the 50–59% discount range (most recent: 60%)." },
      { code: "ABOVE_HISTORICAL_LOW", direction: "WAIT", importance: 0.18, text: "The current price is well above the lowest recorded price in this region." },
    ],
    outcome: null,
    disclaimer: "",
  };
}

/** Illustrative reliability bins: predicted probability against what then happened. */
export const DEMO_CALIBRATION = [
  { bin_lower: 0.0, bin_upper: 0.1, count: 212, mean_predicted: 0.05, observed_rate: 0.04 },
  { bin_lower: 0.1, bin_upper: 0.2, count: 148, mean_predicted: 0.15, observed_rate: 0.17 },
  { bin_lower: 0.2, bin_upper: 0.3, count: 96, mean_predicted: 0.25, observed_rate: 0.22 },
  { bin_lower: 0.3, bin_upper: 0.4, count: 81, mean_predicted: 0.35, observed_rate: 0.38 },
  { bin_lower: 0.4, bin_upper: 0.5, count: 74, mean_predicted: 0.45, observed_rate: 0.41 },
  { bin_lower: 0.5, bin_upper: 0.6, count: 69, mean_predicted: 0.55, observed_rate: 0.58 },
  { bin_lower: 0.6, bin_upper: 0.7, count: 88, mean_predicted: 0.65, observed_rate: 0.62 },
  { bin_lower: 0.7, bin_upper: 0.8, count: 103, mean_predicted: 0.75, observed_rate: 0.79 },
  { bin_lower: 0.8, bin_upper: 0.9, count: 131, mean_predicted: 0.85, observed_rate: 0.83 },
  { bin_lower: 0.9, bin_upper: 1.0, count: 157, mean_predicted: 0.95, observed_rate: 0.96 },
];
