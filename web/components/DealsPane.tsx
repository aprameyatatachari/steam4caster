"use client";

import { useRef } from "react";

import { AttributionNote, GameArt, Skeleton, Slash } from "@/components/ui";
import type { Attribution, Deal, Game } from "@/lib/api";
import { DEMO_COUNTRIES, endsIn, money } from "@/lib/format";
import { EASE, gsap, prefersReducedMotion, useGSAP } from "@/lib/motion";

export type DealsState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; items: Deal[]; minDiscount: number; attribution: Attribution };

/**
 * The pane at the top of the landing page: popular Steam games on a deep sale right
 * now, in the visitor's region. Picking one loads it into the demo further down.
 */
export function DealsPane({
  state,
  country,
  onCountry,
  selectedId,
  onPick,
}: {
  state: DealsState;
  country: string;
  onCountry: (code: string) => void;
  selectedId: string | null;
  onPick: (game: Game) => void;
}) {
  const root = useRef<HTMLElement>(null);
  const track = useRef<HTMLUListElement>(null);
  const count = state.status === "ready" ? state.items.length : 0;
  const region = DEMO_COUNTRIES.find((c) => c.code === country)?.name ?? country;

  useGSAP(
    () => {
      if (!count || prefersReducedMotion()) return;
      gsap.from(".deal", { opacity: 0, y: 20, duration: 0.7, ease: EASE, stagger: 0.05 });
      gsap.from(".deal-cut", { scale: 0, transformOrigin: "0% 0%", duration: 0.5, ease: EASE, stagger: 0.05, delay: 0.25 });
    },
    { scope: root, dependencies: [count, country] },
  );

  const page = (direction: 1 | -1) => {
    const el = track.current;
    if (el) el.scrollBy({ left: direction * el.clientWidth * 0.8, behavior: "smooth" });
  };

  return (
    <section ref={root} className="deals" aria-labelledby="deals-title">
      <div className="deals-head wrap">
        <div>
          <h2 id="deals-title" className="display h-md">Best deals of the week</h2>
          <p className="data muted">
            {state.status === "ready"
              ? `Popular Steam games at ${state.minDiscount}% off or more right now · ${region} prices`
              : `Steam sales running right now · ${region} prices`}
          </p>
        </div>
        <div className="deals-tools">
          <label className="deals-region">
            <span className="sr-only">Region for prices</span>
            <select className="select" value={country} onChange={(e) => onCountry(e.target.value)}>
              {DEMO_COUNTRIES.map((c) => (
                <option key={c.code} value={c.code}>{c.name}</option>
              ))}
            </select>
          </label>
          <button type="button" className="chip deals-step" onClick={() => page(-1)} aria-label="Earlier deals" disabled={count === 0}>
            <span className="deals-back"><Slash /></span>
          </button>
          <button type="button" className="chip deals-step" onClick={() => page(1)} aria-label="More deals" disabled={count === 0}>
            <Slash />
          </button>
        </div>
      </div>

      {state.status === "loading" ? (
        <div className="wrap" aria-busy="true">
          <Skeleton height="13rem" />
        </div>
      ) : null}

      {state.status === "error" ? (
        <p className="notice deals-note">Live deals could not be loaded. {state.message}</p>
      ) : null}

      {state.status === "ready" && count === 0 ? (
        <p className="notice deals-note">
          No popular Steam game is {state.minDiscount}% off or more in {region} right now. Try another region, or create an account to forecast any game.
        </p>
      ) : null}

      {state.status === "ready" && count > 0 ? (
        <>
          <ul ref={track} className="deals-track" tabIndex={-1}>
            {state.items.map((deal) => {
              const free = deal.price.amount_minor === 0;
              return (
                <li key={deal.game.id}>
                  <button
                    type="button"
                    className="deal"
                    aria-pressed={deal.game.id === selectedId}
                    onClick={() => onPick(deal.game)}
                  >
                    <span className="deal-art">
                      <GameArt game={deal.game} />
                      <span className="deal-cut num">−{deal.discount_pct}%</span>
                    </span>
                    <span className="row-title deal-title">{deal.game.title}</span>
                    <span className="deal-prices">
                      <span className="sr-only">Regular price </span>
                      <s className="deal-was">{money(deal.regular, { compact: true })}</s>
                      <span className="sr-only">, sale price </span>
                      <strong className="num deal-now">{free ? "Free" : money(deal.price, { compact: true })}</strong>
                    </span>
                    <span className="data muted">{endsIn(deal.expires_at) ?? "End date not given"}</span>
                    <span className="data deal-go">
                      See its forecast <Slash />
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
          <div className="wrap deals-foot">
            <AttributionNote attribution={state.attribution} />
          </div>
        </>
      ) : null}
    </section>
  );
}
