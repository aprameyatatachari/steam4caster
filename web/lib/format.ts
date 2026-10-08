import type { Money } from "./api";

const EXPONENT: Record<string, number> = { JPY: 0, KRW: 0, VND: 0, CLP: 0, KWD: 3, BHD: 3, OMR: 3 };

/** Format integer minor units in their own currency. Never converts. */
export function money(value: Money | null | undefined, { compact = false } = {}): string {
  if (!value) return "—";
  return minor(value.amount_minor, value.currency, { compact });
}

export function minor(amountMinor: number, currency: string, { compact = false } = {}): string {
  const digits = EXPONENT[currency] ?? 2;
  const amount = amountMinor / 10 ** digits;
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      minimumFractionDigits: compact || Number.isInteger(amount) ? 0 : digits,
      maximumFractionDigits: compact ? 0 : digits,
    }).format(amount);
  } catch {
    return `${currency} ${amount.toFixed(digits)}`;
  }
}

/**
 * Indicative conversion of minor units between currencies at a given rate (one unit of
 * `from` in `to`). For display beside another region's prices only.
 */
export function convertMinor(amountMinor: number, from: string, to: string, rate: number): number {
  const major = amountMinor / 10 ** (EXPONENT[from] ?? 2);
  return Math.round(major * rate * 10 ** (EXPONENT[to] ?? 2));
}

export const pct = (value: number, digits = 0) => `${(value * 100).toFixed(digits)}%`;

export function date(value: string | Date | null | undefined, style: "short" | "long" | "month" = "short"): string {
  if (!value) return "—";
  const d = typeof value === "string" ? new Date(value) : value;
  const options: Intl.DateTimeFormatOptions =
    style === "long"
      ? { day: "numeric", month: "long", year: "numeric" }
      : style === "month"
        ? { month: "short", year: "2-digit" }
        : { day: "numeric", month: "short", year: "numeric" };
  return new Intl.DateTimeFormat(undefined, options).format(d);
}

export function dayMonth(value: string | Date): string {
  const d = typeof value === "string" ? new Date(value) : value;
  return new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short" }).format(d);
}

export function daysBetween(a: Date, b: Date): number {
  return Math.round((b.getTime() - a.getTime()) / 86_400_000);
}

export function relativeDays(value: string): string {
  const days = daysBetween(new Date(value), new Date());
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 60) return `${days} days ago`;
  if (days < 730) return `${Math.round(days / 30)} months ago`;
  return `${Math.round(days / 365)} years ago`;
}

/**
 * P(sale starts within `days`), interpolated between the 7/30/90-day anchors with a
 * constant hazard per segment. Mirrors the backend policy so the wait slider can
 * respond instantly before the server confirms.
 */
export function probabilityWithin(h: { days_7: number; days_30: number; days_90: number }, days: number): number {
  const cap = 0.97;
  const anchors: [number, number][] = [
    [0, 0],
    [7, Math.min(cap, h.days_7)],
    [30, Math.min(cap, h.days_30)],
    [90, Math.min(cap, h.days_90)],
  ];
  if (days <= 0) return 0;
  for (let i = 1; i < anchors.length; i += 1) {
    const [d0, p0] = anchors[i - 1];
    const [d1, p1] = anchors[i];
    if (days <= d1 || i === anchors.length - 1) {
      const s0 = 1 - p0;
      const s1 = Math.max(1e-9, 1 - p1);
      const hazard = s1 < s0 ? Math.log(s0 / s1) / (d1 - d0) : 0;
      return Math.min(cap, 1 - s0 * Math.exp(-hazard * (days - d0)));
    }
  }
  return 0;
}

export const TIER_LABEL: Record<string, string> = {
  LT_20: "under 20%",
  "20_TO_29": "20–29%",
  "30_TO_39": "30–39%",
  "40_TO_49": "40–49%",
  "50_TO_59": "50–59%",
  "60_TO_74": "60–74%",
  "75_PLUS": "75%+",
};
export const TIER_ORDER = ["LT_20", "20_TO_29", "30_TO_39", "40_TO_49", "50_TO_59", "60_TO_74", "75_PLUS"];
export const TIER_RANGE: Record<string, [number, number]> = {
  LT_20: [5, 19],
  "20_TO_29": [20, 29],
  "30_TO_39": [30, 39],
  "40_TO_49": [40, 49],
  "50_TO_59": [50, 59],
  "60_TO_74": [60, 74],
  "75_PLUS": [75, 90],
};

export const COUNTRIES: { code: string; name: string }[] = [
  { code: "IN", name: "India" },
  { code: "US", name: "United States" },
  { code: "GB", name: "United Kingdom" },
  { code: "DE", name: "Germany" },
  { code: "FR", name: "France" },
  { code: "JP", name: "Japan" },
  { code: "BR", name: "Brazil" },
  { code: "CA", name: "Canada" },
  { code: "AU", name: "Australia" },
  { code: "PL", name: "Poland" },
  { code: "TR", name: "Türkiye" },
  { code: "KR", name: "South Korea" },
];

/** Parse a typed amount in major units ("749" or "749.50") into integer minor units. */
export function toMinor(input: string, currency: string): number | null {
  const value = Number(input.replace(/,/g, "").trim());
  if (!input.trim() || !Number.isFinite(value) || value < 0) return null;
  return Math.round(value * 10 ** (EXPONENT[currency] ?? 2));
}

export function fromMinor(amountMinor: number, currency: string): string {
  const digits = EXPONENT[currency] ?? 2;
  const value = amountMinor / 10 ** digits;
  return Number.isInteger(value) ? String(value) : value.toFixed(digits);
}
