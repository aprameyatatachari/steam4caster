/**
 * Typed client for the Steam4Caster backend (`/api/v1`).
 *
 * Tokens live in localStorage. Refresh tokens rotate on every use and replaying one
 * revokes the whole session, so refreshes are single-flight: concurrent 401s share one
 * refresh request.
 */

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const BASE = `${API_URL}/api/v1`;
const ACCESS_KEY = "s4c.access";
const REFRESH_KEY = "s4c.refresh";

export type Money = { amount_minor: number; currency: string; amount: string };

export type User = {
  id: string;
  email: string;
  default_country: string;
  default_currency: string;
  timezone: string;
  email_verified: boolean;
  created_at: string;
};

export type Game = {
  id: string;
  itad_id: string | null;
  steam_app_id: number | null;
  title: string;
  slug: string;
  type: string | null;
  mature: boolean;
  early_access: boolean | null;
  release_date: string | null;
  developers: string[];
  publishers: string[];
  tags: string[];
  assets: Record<string, string>;
  provider_url: string | null;
};

export type Attribution = { provider: string; text: string; url: string; affiliation: string };

export type Price = {
  game_id: string;
  shop: string;
  country: string;
  currency: string;
  price: Money;
  regular: Money;
  discount_pct: number;
  historical_low: Money | null;
  historical_low_at: string | null;
  url: string | null;
  observed_at: string;
  fetched_at: string;
  is_stale: boolean;
  attribution: Attribution;
};

export type HistoryPoint = { observed_at: string; price: Money; regular: Money; discount_pct: number };
export type HistoryPage = { items: HistoryPoint[]; next_cursor: string | null; attribution: Attribution };

export type SaleEvent = {
  started_at: string;
  ended_at: string | null;
  regular: Money;
  initial_price: Money;
  min_price: Money;
  max_discount_pct: number;
};

export type Horizons = { days_7: number; days_30: number; days_90: number };
export type Factor = { code: string; direction: "BUY" | "WAIT" | "NEUTRAL"; importance: number; text: string };

export type Forecast = {
  id: string;
  game_id: string;
  country: string;
  currency: string | null;
  created_at: string;
  cutoff_at: string;
  method: "BASELINE" | "ML";
  model_version: string;
  currently_on_sale: boolean;
  sale_probability: Horizons;
  new_sale_probability: Horizons;
  discount_tier_probabilities: Record<string, number>;
  most_likely_discount_tier: string;
  expected_discount_pct: number;
  current_price: Money | null;
  regular_price: Money | null;
  predicted_sale_price: { lower: Money; median: Money; upper: Money; coverage: number } | null;
  likely_window: { start: string; end: string } | null;
  confidence_score: number;
  data_quality: "GOOD" | "LIMITED" | "INSUFFICIENT";
  explanation_factors: Factor[];
  outcome: {
    sale_within_7d: boolean | null;
    sale_within_30d: boolean | null;
    sale_within_90d: boolean | null;
    max_discount_pct: number | null;
    min_price: Money | null;
    evaluated_at: string | null;
    complete: boolean;
  } | null;
  disclaimer: string;
};

export type Action = "BUY" | "WAIT" | "NEUTRAL";

export type Recommendation = {
  id: string;
  forecast_id: string;
  action: Action;
  score: number;
  sale_probability: number;
  max_wait_days: number;
  selected_horizon_days: number;
  expected_future_price: Money | null;
  expected_savings: Money | null;
  waiting_cost: Money | null;
  reason_codes: string[];
  summary: string;
  ruleset_version: string;
  created_at: string;
};

export type Channel = "WEB_PUSH" | "EMAIL" | "SMS";

export type WatchlistEntry = {
  id: string;
  game: Game;
  country: string;
  currency: string;
  target_price: Money | null;
  min_discount_pct: number | null;
  max_wait_days: number | null;
  historical_low_only: boolean;
  notify_on_buy: boolean;
  channels: Channel[];
  is_active: boolean;
  created_at: string;
  current: {
    price: Money;
    regular: Money;
    discount_pct: number;
    historical_low: Money | null;
    observed_at: string;
    url: string | null;
  } | null;
  forecast: {
    forecast_id: string;
    sale_probability_7d: number;
    sale_probability_30d: number;
    sale_probability_90d: number;
    expected_discount_pct: number;
    likely_window_start: string | null;
    likely_window_end: string | null;
    confidence_score: number;
    data_quality: string;
    method: string;
  } | null;
  recommendation: Recommendation | null;
};

export type WatchlistSummary = {
  entries: number;
  totals: {
    currency: string;
    games_priced: number;
    current_total: Money;
    historical_low_total: Money;
    games_with_forecast: number;
    expected: { horizon_days: number; expected_total: Money; expected_savings: Money }[];
  }[];
  recommendation_counts: Record<string, number>;
  likely_on_sale_within_30d: {
    entry_id: string;
    game_id: string;
    title: string;
    sale_probability_30d: number;
    window_start: string | null;
    window_end: string | null;
  }[];
  disclaimer: string;
};

export type WishlistImport = {
  steam_id: string;
  on_wishlist: number;
  added: number;
  added_titles: string[];
  already_watching: number;
  not_found: number;
  failed: number;
  skipped_over_limit: number;
};

export type Preference = {
  channel: Channel;
  enabled: boolean;
  available: boolean;
  quiet_hours_start: string | null;
  quiet_hours_end: string | null;
  timezone: string;
  delivery_mode: "IMMEDIATE" | "DIGEST";
  destination_masked: string | null;
  destination_verified: boolean;
};
export type Preferences = { preferences: Preference[]; vapid_public_key: string | null };

export type PushSub = { id: string; endpoint_host: string; device_label: string | null; created_at: string };

export type NotificationItem = {
  id: string;
  event_type: string;
  channel: Channel;
  state: "PENDING" | "SENDING" | "SENT" | "FAILED" | "SUPPRESSED";
  title: string;
  body: string;
  url: string | null;
  game_id: string | null;
  error_code: string | null;
  created_at: string;
  sent_at: string | null;
};

export type HorizonMetrics = {
  horizon_days: number;
  n: number;
  positive_rate?: number | null;
  brier?: number | null;
  log_loss?: number | null;
  ece?: number | null;
  roc_auc?: number | null;
};
export type CalibrationBin = {
  bin_lower: number;
  bin_upper: number;
  count: number;
  mean_predicted: number;
  observed_rate: number;
};
export type Calibration = { horizon_days: number; n: number; bins: CalibrationBin[] };
export type PerformanceSummary = {
  forecasts_total: number;
  forecasts_evaluated: number;
  sale_probability: Record<string, HorizonMetrics>;
  by_method: Record<string, Record<string, HorizonMetrics>>;
  discount_tier: Record<string, unknown>;
  recommendation_policy: {
    n?: number;
    overall?: {
      n: number;
      counts: Record<string, number>;
      avg_realized_savings_fraction: number | null;
      wait_followed_by_lower_price_rate: number | null;
      buy_regret_rate: number | null;
    };
  };
  disclaimer: string;
};
export type ActiveModel = {
  active: { version: string; name: string; training_cutoff: string; activated_at: string | null } | null;
  fallback_method: string;
  baseline_version: string;
  ruleset_version: string;
};

export type Meta = {
  app_name: string;
  attribution: Attribution;
  disclaimer: string;
  default_country: string;
  default_currency: string;
  forecast_horizons_days: number[];
  discount_tiers: { code: string; label: string; min_pct: number; max_pct: number }[];
  channels: Record<Channel, boolean>;
  vapid_public_key: string | null;
};

export class ApiError extends Error {
  status: number;
  code: string;
  details: unknown;
  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

/** A message a person can act on, for any thrown value. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "UPSTREAM_UNAVAILABLE") return "The price provider is not responding. Try again in a minute.";
    if (error.code === "RATE_LIMITED") return "Too many requests. Wait a moment and try again.";
    return error.message;
  }
  if (error instanceof TypeError) return `Cannot reach the Steam4Caster server at ${API_URL}. Is the backend running?`;
  return error instanceof Error ? error.message : "Something went wrong.";
}

const store = {
  get access() {
    return typeof window === "undefined" ? null : window.localStorage.getItem(ACCESS_KEY);
  },
  get refresh() {
    return typeof window === "undefined" ? null : window.localStorage.getItem(REFRESH_KEY);
  },
  set(tokens: { access_token: string; refresh_token: string }) {
    window.localStorage.setItem(ACCESS_KEY, tokens.access_token);
    window.localStorage.setItem(REFRESH_KEY, tokens.refresh_token);
  },
  clear() {
    window.localStorage.removeItem(ACCESS_KEY);
    window.localStorage.removeItem(REFRESH_KEY);
  },
};

export const hasSession = () => Boolean(store.refresh);

let refreshing: Promise<boolean> | null = null;
let onSessionLost: (() => void) | null = null;
export function setSessionLostHandler(handler: (() => void) | null) {
  onSessionLost = handler;
}

async function parse(response: Response) {
  if (response.status === 204) return null;
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function refreshSession(): Promise<boolean> {
  if (!refreshing) {
    refreshing = (async () => {
      const token = store.refresh;
      if (!token) return false;
      try {
        const response = await fetch(`${BASE}/auth/refresh`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: token }),
        });
        if (!response.ok) return false;
        store.set(await response.json());
        return true;
      } catch {
        return false;
      }
    })().finally(() => {
      refreshing = null;
    });
  }
  return refreshing;
}

type Options = { method?: string; body?: unknown; query?: Record<string, string | number | null | undefined>; auth?: boolean };

async function request<T>(path: string, { method = "GET", body, query, auth = true }: Options = {}, retry = true): Promise<T> {
  const url = new URL(`${BASE}${path}`);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== null && value !== undefined && value !== "") url.searchParams.set(key, String(value));
  }
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (auth && store.access) headers.Authorization = `Bearer ${store.access}`;
  const response = await fetch(url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (response.status === 401 && auth && retry) {
    if (await refreshSession()) return request<T>(path, { method, body, query, auth }, false);
    store.clear();
    onSessionLost?.();
  }
  const data = await parse(response);
  if (!response.ok) {
    const err = data?.error;
    throw new ApiError(response.status, err?.code ?? "HTTP_ERROR", err?.message ?? `Request failed (${response.status}).`, err?.details);
  }
  return data as T;
}

type AuthResponse = { user: User; tokens: { access_token: string; refresh_token: string } };

export const api = {
  meta: () => request<Meta>("/meta", { auth: false }),

  async register(input: { email: string; password: string; country?: string; timezone?: string }) {
    const result = await request<AuthResponse>("/auth/register", { method: "POST", body: input, auth: false });
    store.set(result.tokens);
    return result.user;
  },
  async login(input: { email: string; password: string }) {
    const result = await request<AuthResponse>("/auth/login", { method: "POST", body: input, auth: false });
    store.set(result.tokens);
    return result.user;
  },
  async logout() {
    const token = store.refresh;
    store.clear();
    if (token) {
      await request("/auth/logout", { method: "POST", body: { refresh_token: token }, auth: false }).catch(() => null);
    }
  },
  me: () => request<User>("/me"),
  updateMe: (body: Partial<Pick<User, "default_country" | "default_currency" | "timezone">>) =>
    request<User>("/me", { method: "PATCH", body }),
  resendVerification: () => request<{ sent: boolean }>("/auth/verify-email/request", { method: "POST" }),
  confirmEmail: (token: string) =>
    request<User>("/auth/verify-email/confirm", { method: "POST", body: { token }, auth: false }),

  search: (q: string) => request<Game[]>("/games/search", { query: { q } }),
  lookup: (steamAppId: number) => request<Game>("/games/lookup", { query: { steam_app_id: steamAppId } }),
  game: (id: string) => request<Game>(`/games/${id}`),
  price: (id: string, country?: string) => request<Price>(`/games/${id}/prices`, { query: { country } }),
  async history(id: string, country?: string) {
    const items: HistoryPoint[] = [];
    let cursor: string | null = null;
    let attribution: Attribution | null = null;
    do {
      const page: HistoryPage = await request<HistoryPage>(`/games/${id}/history`, {
        query: { country, cursor, limit: 1000 },
      });
      items.push(...page.items);
      attribution = page.attribution;
      cursor = page.next_cursor;
    } while (cursor);
    return { items, attribution };
  },
  saleEvents: (id: string, country?: string) => request<SaleEvent[]>(`/games/${id}/sale-events`, { query: { country } }),
  forecast: (id: string, country?: string) => request<Forecast>(`/games/${id}/forecast`, { query: { country } }),
  recommendation: (id: string, country?: string, maxWaitDays?: number) =>
    request<Recommendation>(`/games/${id}/recommendation`, { query: { country, max_wait_days: maxWaitDays } }),
  forecastHistory: (id: string, country?: string) =>
    request<{ items: Forecast[]; next_cursor: string | null }>(`/games/${id}/forecast-history`, {
      query: { country, limit: 20 },
    }),

  watchlist: () => request<WatchlistEntry[]>("/watchlist"),
  watchlistSummary: () => request<WatchlistSummary>("/watchlist/summary"),
  addToWatchlist: (body: {
    game_id: string;
    country?: string;
    target_price_minor?: number | null;
    min_discount_pct?: number | null;
    max_wait_days?: number | null;
  }) => request<WatchlistEntry>("/watchlist", { method: "POST", body }),
  updateEntry: (id: string, body: Record<string, unknown>) =>
    request<WatchlistEntry>(`/watchlist/${id}`, { method: "PATCH", body }),
  importSteamWishlist: (profile: string) =>
    request<WishlistImport>("/watchlist/import/steam", { method: "POST", body: { profile } }),
  removeEntry: (id: string) => request<null>(`/watchlist/${id}`, { method: "DELETE" }),

  preferences: () => request<Preferences>("/notification-preferences"),
  updatePreferences: (preferences: Record<string, unknown>[]) =>
    request<Preferences>("/notification-preferences", { method: "PATCH", body: { preferences } }),
  pushSubscriptions: () => request<PushSub[]>("/push-subscriptions"),
  addPushSubscription: (body: unknown) => request<PushSub>("/push-subscriptions", { method: "POST", body }),
  removePushSubscription: (id: string) => request<null>(`/push-subscriptions/${id}`, { method: "DELETE" }),
  sendTest: () =>
    request<{ results: { channel: Channel; state: string; error_code: string | null }[] }>("/notifications/test", {
      method: "POST",
    }),
  notifications: (cursor?: string | null) =>
    request<{ items: NotificationItem[]; next_cursor: string | null }>("/notifications", { query: { cursor, limit: 30 } }),

  performanceSummary: () => request<PerformanceSummary>("/model-performance/summary"),
  calibration: (horizonDays: number) =>
    request<Calibration>("/model-performance/calibration", { query: { horizon_days: horizonDays, bins: 10 } }),
  byHorizon: () => request<{ horizons: HorizonMetrics[] }>("/model-performance/by-horizon"),
  activeModel: () => request<ActiveModel>("/model-versions/active"),
};
