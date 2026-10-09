"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { GameArt } from "@/components/ui";
import { api, type WatchlistEntry } from "@/lib/api";
import { dayMonth, money } from "@/lib/format";
import { saleSchedule, type Schedule } from "@/lib/seasons";

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/**
 * What is on sale right now and what is likely next: Steam's seasonal sale schedule
 * (estimated dates) plus any watched games that are discounted today.
 */
export function SalesPanel() {
  const [schedule, setSchedule] = useState<Schedule | null>(null);
  const [discounted, setDiscounted] = useState<WatchlistEntry[] | null>(null);

  useEffect(() => {
    setSchedule(saleSchedule());
    api
      .watchlist()
      .then((entries) =>
        setDiscounted(
          entries
            .filter((e) => (e.current?.discount_pct ?? 0) > 0 && (e.current?.price.amount_minor ?? 0) > 0)
            .sort((a, b) => (b.current?.discount_pct ?? 0) - (a.current?.discount_pct ?? 0)),
        ),
      )
      .catch(() => setDiscounted([]));
  }, []);

  if (!schedule) return null;
  const { ongoing, next } = schedule;

  return (
    <section className="module" aria-labelledby="sales-title">
      <div className="module-head">
        <h2 id="sales-title" className="h-sm">Steam sales</h2>
        <span className="data muted">Dates are estimates from Steam&rsquo;s usual schedule</span>
      </div>
      <dl className="kv" style={{ border: 0 }}>
        <div style={ongoing ? { background: "var(--hi)" } : undefined}>
          <dt style={ongoing ? { color: "var(--ink)" } : undefined}>Right now</dt>
          <dd className="num">{ongoing ? `${ongoing.kind} Sale is on` : "No sale ongoing"}</dd>
          <p className="data" style={{ marginTop: "0.375rem" }}>
            {ongoing
              ? `Expected to end about ${dayMonth(ongoing.end)} · ${plural(schedule.daysLeft, "day")} left`
              : "No seasonal sale is expected to be running today"}
          </p>
        </div>
        <div>
          <dt>Possible next sale</dt>
          <dd className="num">{next.kind} Sale</dd>
          <p className="data" style={{ marginTop: "0.375rem" }}>
            About {dayMonth(next.start)} to {dayMonth(next.end)} · in {plural(schedule.daysToNext, "day")}
          </p>
        </div>
        <div>
          <dt>Next Fest (free demos, not a sale)</dt>
          <dd className="num">{schedule.festOngoing ? "On now" : `In ${plural(schedule.daysToFest, "day")}`}</dd>
          <p className="data" style={{ marginTop: "0.375rem" }}>
            {schedule.festOngoing
              ? `Expected to end about ${dayMonth(schedule.festOngoing.end)}`
              : `Around ${dayMonth(schedule.festNext.start)}`}
          </p>
        </div>
      </dl>
      {discounted && discounted.length > 0 ? (
        <div className="module-body" style={{ borderTop: "1px solid var(--ink)" }}>
          <h3 className="data" style={{ marginBottom: "0.25rem" }}>On your watchlist, discounted today</h3>
          <ul className="rows">
            {discounted.slice(0, 6).map((entry) => (
              <li key={entry.id}>
                <Link className="row row-art" href={`/app/game/${entry.game.id}`}>
                  <GameArt game={entry.game} size="thumb" />
                  <span>
                    <span className="row-title">{entry.game.title}</span>
                    <span className="data muted" style={{ display: "block", marginTop: "0.25rem" }}>
                      {money(entry.current?.price)} · was {money(entry.current?.regular, { compact: true })}
                    </span>
                  </span>
                  <span className="tag" data-tone="BUY">{entry.current?.discount_pct}% off</span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
