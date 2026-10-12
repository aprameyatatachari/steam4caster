"use client";

import { useEffect, useMemo, useState } from "react";

import { CellType } from "@/components/CellType";
import { ButtonLink, Skeleton } from "@/components/ui";
import type { Snapshot } from "@/lib/api";
import { date, DEMO_WAITS, minor, money, pct } from "@/lib/format";

const REASON_TEXT = (code: string) => code.replace(/_/g, " ").toLowerCase();

/** One control for "how long can you wait?", shared by the forecast and the call. */
export function WaitPicker({ wait, onWait, id }: { wait: number; onWait: (days: number) => void; id: string }) {
  return (
    <div className="field">
      <span id={id}>How long can you wait?</span>
      <div className="seg" role="radiogroup" aria-labelledby={id}>
        {DEMO_WAITS.map((option) => (
          <button
            key={option.days}
            type="button"
            role="radio"
            aria-checked={wait === option.days}
            aria-label={option.name}
            onClick={() => onWait(option.days)}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}

/** The live BUY / WAIT / NEUTRAL call for the picked game, from the real policy. */
export function DemoVerdict({
  snapshot,
  wait,
  onWait,
  loading,
}: {
  snapshot: Snapshot;
  wait: number;
  onWait: (days: number) => void;
  loading: boolean;
}) {
  const { recommendation: rec, price, game } = snapshot;
  const current = rec.max_wait_days === wait && !loading;

  if (price && price.price.amount_minor === 0) {
    return (
      <section className="verdict" data-action="BUY" aria-label={`${game.title} is free`}>
        <div className="verdict-type">
          <CellType lines={["FREE"]} />
        </div>
        <p className="lede">{game.title} costs nothing right now, so there is no sale to wait for.</p>
      </section>
    );
  }

  return (
    <section className="verdict" data-action={rec.action} aria-labelledby="demo-verdict-title" aria-busy={!current}>
      <h3 id="demo-verdict-title" className="data">
        Our call on {game.title} if you can wait up to {wait} days
      </h3>
      <div className="verdict-type" aria-live="polite">
        {current ? (
          <CellType lines={[rec.action]} fills={[rec.action === "WAIT" ? "var(--pen)" : undefined]} />
        ) : (
          <Skeleton height="5rem" />
        )}
      </div>
      <p className="lede" style={{ maxWidth: "36em" }}>{current ? rec.summary : "Working out the call…"}</p>
      {current ? (
        <ul style={{ display: "flex", flexWrap: "wrap", gap: "0.375rem" }}>
          {rec.reason_codes.map((code) => (
            <li key={code} className="tag" style={{ background: "var(--paper)" }}>{REASON_TEXT(code)}</li>
          ))}
        </ul>
      ) : null}
      <WaitPicker wait={wait} onWait={onWait} id="demo-verdict-wait" />
      <dl className="kv">
        <div>
          <dt>Chance of a new sale</dt>
          <dd className="num">{current ? pct(rec.sale_probability) : "…"}</dd>
        </div>
        <div>
          <dt>Expected saving</dt>
          <dd className="num">
            {current ? (rec.expected_savings && rec.expected_savings.amount_minor > 0 ? money(rec.expected_savings, { compact: true }) : "None") : "…"}
          </dd>
        </div>
        <div>
          <dt>Expected price then</dt>
          <dd className="num">{current ? money(rec.expected_future_price, { compact: true }) : "…"}</dd>
        </div>
      </dl>
    </section>
  );
}

/**
 * Try a price alert against the game's real sale history: how often would this target
 * have been reached, and would it fire today? Nothing is saved without an account.
 */
export function AlertTester({ snapshot, startHref }: { snapshot: Snapshot; startHref: string }) {
  const { price, sale_events: sales, game } = snapshot;
  const regular = price?.regular.amount_minor ?? 0;
  const currency = price?.currency ?? "USD";
  const floor = useMemo(() => {
    const lows = sales.map((s) => s.min_price.amount_minor).filter((v) => v > 0);
    const lowest = Math.min(regular, price?.historical_low?.amount_minor ?? regular, ...lows);
    return Math.max(0, Math.floor(lowest * 0.8));
  }, [sales, regular, price]);
  const step = Math.max(1, Math.round((regular - floor) / 100));
  const [target, setTarget] = useState(0);
  useEffect(() => setTarget(Math.round((regular * 0.5) / step) * step), [game.id, regular, step]);

  if (!price || regular === 0 || price.price.amount_minor === 0) return null;

  const value = Math.min(regular, Math.max(floor, target));
  const hits = sales.filter((s) => s.min_price.amount_minor <= value);
  const last = hits.length ? hits.reduce((a, b) => (a.started_at > b.started_at ? a : b)) : null;
  const firesNow = price.price.amount_minor <= value;
  const off = Math.round((1 - value / regular) * 100);

  return (
    <section className="alert-test" aria-labelledby="alert-test-title">
      <h3 id="alert-test-title" className="display h-md">Now test an alert.</h3>
      <p className="body">Drag to a target price. We check it against every sale {game.title} has actually had.</p>
      <label className="field">
        <span>
          Alert me at {minor(value, currency, { compact: true })} or lower{off > 0 ? ` (${off}% off)` : ""}
        </span>
        <input
          className="slider"
          type="range"
          min={floor}
          max={regular}
          step={step}
          value={value}
          onChange={(e) => setTarget(Number(e.target.value))}
        />
      </label>
      <dl className="kv" aria-live="polite">
        <div>
          <dt>Past sales that reached it</dt>
          <dd className="num">
            {hits.length} of {sales.length}
          </dd>
        </div>
        <div>
          <dt>Most recent time</dt>
          <dd className="num">{last ? date(last.started_at) : "Never"}</dd>
        </div>
        <div>
          <dt>Today, at {money(price.price, { compact: true })}</dt>
          <dd className="num">{firesNow ? <span className="mark">It would alert you</span> : "It would stay quiet"}</dd>
        </div>
      </dl>
      <p className="body">
        {sales.length === 0
          ? "There are no sales on record for this game in this region yet, so there is nothing to compare against."
          : hits.length === 0
            ? "No sale on record has gone this low. An alert here might never fire; try a higher target."
            : "With an account, this alert reaches you by browser notification or email, once, when the price gets there."}
      </p>
      <div>
        <ButtonLink href={startHref} variant="hi">Set this alert for real</ButtonLink>
      </div>
    </section>
  );
}
