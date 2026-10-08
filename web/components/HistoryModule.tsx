"use client";

import { useEffect, useState } from "react";

import { PriceChart } from "@/components/PriceChart";
import { ErrorNote, Skeleton } from "@/components/ui";
import { api, errorMessage, type Forecast, type FxRate, type Game, type HistoryPoint, type Price, type SaleEvent } from "@/lib/api";
import { COUNTRIES, convertMinor, date, minor, toMinor } from "@/lib/format";

/** Region whose Steam history usually reaches back to release. */
const REFERENCE = "US";
/** A home history starting this long after release is worth flagging as short. */
const SHORT_HISTORY_DAYS = 200;

type Reference = { history: HistoryPoint[]; sales: SaleEvent[]; price: Price | null; fx: FxRate | null };

/** One whole unit of a currency in its minor units (100 for USD, 1 for JPY). */
const oneUnit = (code: string) => toMinor("1", code) ?? 100;

const countryName = (code: string) => COUNTRIES.find((c) => c.code === code)?.name ?? code;

/**
 * The price history chart, with the option to look at a longer-running region's full
 * history instead. That view shows the other region's own prices in its own currency,
 * says so plainly, and gives today's reference rate for a rough comparison.
 */
export function HistoryModule({
  game,
  country,
  currency,
  history,
  sales,
  forecast,
  price,
}: {
  game: Game;
  country: string;
  currency: string;
  history: HistoryPoint[];
  sales: SaleEvent[];
  forecast: Forecast;
  price: Price | null;
}) {
  const [view, setView] = useState<"home" | "reference">("home");
  const [ref, setRef] = useState<Reference | null>(null);
  const [error, setError] = useState<string | null>(null);
  const canCompare = country !== REFERENCE;

  const firstSeen = history[0]?.observed_at ?? null;
  const short =
    Boolean(firstSeen && game.release_date) &&
    (+new Date(firstSeen as string) - +new Date(game.release_date as string)) / 86_400_000 > SHORT_HISTORY_DAYS;

  useEffect(() => {
    if (view !== "reference" || ref) return;
    let live = true;
    setError(null);
    (async () => {
      try {
        const [h, s, p] = await Promise.all([
          api.history(game.id, REFERENCE),
          api.saleEvents(game.id, REFERENCE),
          api.price(game.id, REFERENCE).catch(() => null),
        ]);
        const refCurrency = p?.currency ?? h.items[0]?.price.currency ?? null;
        // The rate is a nice-to-have: the chart still shows without it.
        const fx = refCurrency && refCurrency !== currency ? await api.fx(refCurrency, currency).catch(() => null) : null;
        if (live) setRef({ history: h.items, sales: s, price: p, fx });
      } catch (err) {
        if (live) setError(errorMessage(err));
      }
    })();
    return () => {
      live = false;
    };
  }, [view, ref, game.id, currency]);

  const refCurrency = ref?.price?.currency ?? ref?.history[0]?.price.currency ?? "USD";
  const showingRef = view === "reference";

  return (
    <section className="module" aria-labelledby="history-title">
      <div className="module-head" style={{ flexWrap: "wrap" }}>
        <h2 id="history-title" className="h-sm">
          {showingRef ? `Full price history, ${countryName(REFERENCE)}` : "Price history and the estimated next sale"}
        </h2>
        {canCompare ? (
          <div className="seg" role="group" aria-label="Which region's price history to show">
            <button type="button" aria-pressed={!showingRef} onClick={() => setView("home")}>
              {country} · {currency}
            </button>
            <button type="button" aria-pressed={showingRef} onClick={() => setView("reference")}>
              Full history · {REFERENCE}
            </button>
          </div>
        ) : (
          <span className="data muted">{sales.length} sales on record</span>
        )}
      </div>
      <div className="module-body stack">
        {!showingRef ? (
          <>
            {short && canCompare && firstSeen ? (
              <p className="notice">
                Prices for {countryName(country)} have only been recorded since {date(firstSeen)}, so this chart is
                not the game&rsquo;s full history.{" "}
                <button type="button" className="link" style={{ background: "none", border: 0, padding: 0, cursor: "pointer", font: "inherit" }} onClick={() => setView("reference")}>
                  See the full history in {countryName(REFERENCE)} prices
                </button>
              </p>
            ) : null}
            <PriceChart
              key="home"
              history={history}
              sales={sales}
              forecast={forecast}
              currency={currency}
              lowMinor={price?.historical_low?.amount_minor ?? null}
            />
          </>
        ) : error ? (
          <ErrorNote>{error}</ErrorNote>
        ) : !ref ? (
          <Skeleton height="22rem" />
        ) : (
          <>
            <div className="notice" role="note">
              <p>
                <strong>These are {countryName(REFERENCE)} prices in {refCurrency}, not {countryName(country)} prices.</strong>{" "}
                Steam sets each country&rsquo;s price separately, so the amounts here are different from what you
                would pay, and discounts can differ too. Use this chart for the long-run pattern of sales, not for
                the price. Your forecast and recommendation still use {countryName(country)} prices only.
              </p>
              {ref.fx ? (
                <p style={{ marginTop: "0.5rem" }}>
                  Today&rsquo;s direct conversion: <strong>1 {refCurrency} = {minor(convertMinor(oneUnit(refCurrency), refCurrency, currency, ref.fx.rate), currency)}</strong>{" "}
                  ({ref.fx.source}, {date(ref.fx.as_of)}).
                  {ref.price && price
                    ? ` The ${countryName(REFERENCE)} price today, ${minor(ref.price.price.amount_minor, refCurrency)}, converts to about ${minor(convertMinor(ref.price.price.amount_minor, refCurrency, currency, ref.fx.rate), currency, { compact: true })}; the actual ${countryName(country)} price is ${minor(price.price.amount_minor, currency, { compact: true })}.`
                    : ""}
                </p>
              ) : (
                <p style={{ marginTop: "0.5rem" }}>A conversion rate to {currency} is not available right now.</p>
              )}
            </div>
            <PriceChart
              key="reference"
              history={ref.history}
              sales={ref.sales}
              forecast={null}
              currency={refCurrency}
              lowMinor={ref.price?.historical_low?.amount_minor ?? null}
              approx={ref.fx ? { rate: ref.fx.rate, currency } : undefined}
            />
            <p className="data muted">
              {ref.sales.length} sales on record in {countryName(REFERENCE)} · converted figures use today&rsquo;s rate
              for every past date and are a rough guide only
            </p>
          </>
        )}
      </div>
    </section>
  );
}
