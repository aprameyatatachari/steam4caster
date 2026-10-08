/**
 * Estimated recurring Steam seasonal-sale windows. Mirrors the backend's date rules.
 * Valve publishes no schedule, so these are estimates and are labelled as such.
 */

export type SeasonalWindow = { kind: string; start: Date; end: Date };

const DAY = 86_400_000;
const utc = (y: number, m: number, d: number) => new Date(Date.UTC(y, m, d));

function nthWeekday(year: number, month: number, weekday: number, n: number): Date {
  const first = utc(year, month, 1);
  const offset = (weekday - first.getUTCDay() + 7) % 7;
  return new Date(+first + (offset + 7 * (n - 1)) * DAY);
}

function lastWeekday(year: number, month: number, weekday: number): Date {
  const last = new Date(+utc(year, month + 1, 1) - DAY);
  return new Date(+last - ((last.getUTCDay() - weekday + 7) % 7) * DAY);
}

function windowsFor(year: number): SeasonalWindow[] {
  const THU = 4;
  const MON = 1;
  const span = (kind: string, start: Date, days: number) => ({ kind, start, end: new Date(+start + days * DAY) });
  return [
    span("Spring", nthWeekday(year, 2, THU, 2), 7),
    span("Summer", lastWeekday(year, 5, THU), 14),
    span("Autumn", lastWeekday(year, 8, MON), 7),
    span("Winter", nthWeekday(year, 11, THU, 3), 14),
  ];
}

export function nextSeasonalSale(now = new Date()): { window: SeasonalWindow; days: number; running: boolean } {
  const all = [...windowsFor(now.getUTCFullYear()), ...windowsFor(now.getUTCFullYear() + 1)];
  const running = all.find((w) => +w.start <= +now && +now <= +w.end);
  if (running) return { window: running, days: 0, running: true };
  const next = all.find((w) => +w.start > +now)!;
  return { window: next, days: Math.ceil((+next.start - +now) / DAY), running: false };
}
