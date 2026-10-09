/**
 * Steam's usual yearly sale schedule, as estimated dates. Mirrors the backend rules in
 * `app/forecasting/calendar.py` (keep the two in step). Valve publishes no schedule, so
 * everything here is an estimate and is labelled as one in the interface.
 */

export type SeasonalWindow = { kind: string; start: Date; end: Date };

const DAY = 86_400_000;
const THU = 4;
const MON = 1;
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

const span = (kind: string, start: Date, days: number): SeasonalWindow => ({ kind, start, end: new Date(+start + days * DAY) });

/** The four seasonal sales: Spring, Summer, Autumn, Winter. */
function salesFor(year: number): SeasonalWindow[] {
  return [
    span("Spring", nthWeekday(year, 2, THU, 3), 7), // one week, mid-to-late March
    span("Summer", lastWeekday(year, 5, THU), 14), // two weeks from late June
    span("Autumn", nthWeekday(year, 9, THU, 1), 7), // one week, early October
    span("Winter", nthWeekday(year, 11, THU, 3), 18), // mid-December into early January
  ];
}

/** Steam Next Fest: free demos three times a year. An event, not a sale. */
function festsFor(year: number): SeasonalWindow[] {
  return [nthWeekday(year, 1, MON, 4), nthWeekday(year, 5, MON, 2), nthWeekday(year, 9, MON, 2)].map((start) =>
    span("Next Fest", start, 7),
  );
}

export type Schedule = {
  /** Seasonal sale estimated to be running today, if any. */
  ongoing: SeasonalWindow | null;
  daysLeft: number;
  /** Next seasonal sale estimated to start after today. */
  next: SeasonalWindow;
  daysToNext: number;
  festOngoing: SeasonalWindow | null;
  festNext: SeasonalWindow;
  daysToFest: number;
};

function pick(windows: SeasonalWindow[], now: Date) {
  const sorted = [...windows].sort((a, b) => +a.start - +b.start);
  const ongoing = sorted.find((w) => +w.start <= +now && +now < +w.end) ?? null;
  const next = sorted.find((w) => +w.start > +now)!;
  return { ongoing, next };
}

export function saleSchedule(now = new Date()): Schedule {
  const year = now.getUTCFullYear();
  // The previous year matters in early January, while the Winter Sale is still running.
  const years = [year - 1, year, year + 1];
  const sales = pick(years.flatMap(salesFor), now);
  const fests = pick(years.flatMap(festsFor), now);
  const days = (to: Date) => Math.max(0, Math.ceil((+to - +now) / DAY));
  return {
    ongoing: sales.ongoing,
    daysLeft: sales.ongoing ? days(sales.ongoing.end) : 0,
    next: sales.next,
    daysToNext: days(sales.next.start),
    festOngoing: fests.ongoing,
    festNext: fests.next,
    daysToFest: days(fests.next.start),
  };
}

export function nextSeasonalSale(now = new Date()): { window: SeasonalWindow; days: number; running: boolean } {
  const schedule = saleSchedule(now);
  return schedule.ongoing
    ? { window: schedule.ongoing, days: 0, running: true }
    : { window: schedule.next, days: schedule.daysToNext, running: false };
}
