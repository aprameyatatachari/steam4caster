"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { TierChart, Waffle } from "@/components/Charts";
import { CellType } from "@/components/CellType";
import { PriceChart } from "@/components/PriceChart";
import { AttributionNote, Button, ErrorNote, GameArt, Skeleton, steamUrl } from "@/components/ui";
import {
  api,
  ApiError,
  errorMessage,
  type Attribution,
  type Forecast,
  type Game,
  type HistoryPoint,
  type Price,
  type Recommendation,
  type SaleEvent,
  type WatchlistEntry,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { date, dayMonth, money, pct, probabilityWithin, relativeDays, TIER_LABEL, toMinor } from "@/lib/format";
import { EASE, gsap, prefersReducedMotion, useGSAP } from "@/lib/motion";

const QUALITY: Record<string, string> = {
  GOOD: "Plenty of sale history",
  LIMITED: "Limited sale history",
  INSUFFICIENT: "Too little history: leaning on similar games",
};

const REASON: Record<string, string> = {
  NEAR_HISTORICAL_LOW: "Near the lowest recorded price",
  ON_SALE_NOW: "On sale now",
  HIGH_SALE_PROBABILITY: "A sale is likely in your window",
  MEANINGFUL_EXPECTED_SAVINGS: "Expected saving is worth it",
  DEEPER_SALE_LIKELY: "A deeper sale is likely",
  SMALL_EXPECTED_SAVINGS: "Waiting would save little",
  LOW_SALE_PROBABILITY: "A sale is unlikely soon",
  LOW_CONFIDENCE: "Not enough history to call it",
  MARGINAL_BENEFIT: "Benefit of waiting is marginal",
  CONFLICTING_EVIDENCE: "Signals disagree",
  WAITING_COST_EXCEEDS_SAVINGS: "The wait outweighs the saving",
  NO_PRICE_DATA: "No price for this region",
  FREE: "Free right now",
};

type Loaded = {
  game: Game;
  price: Price | null;
  history: HistoryPoint[];
  sales: SaleEvent[];
  forecast: Forecast;
  attribution: Attribution | null;
};

export default function GamePage() {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuth();
  const country = user?.default_country;
  const [data, setData] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rec, setRec] = useState<Recommendation | null>(null);
  const [wait, setWait] = useState(30);
  const [entry, setEntry] = useState<WatchlistEntry | null | undefined>(undefined);
  const [past, setPast] = useState<Forecast[]>([]);
  const root = useRef<HTMLElement>(null);

  useEffect(() => {
    let live = true;
    setData(null);
    setError(null);
    (async () => {
      try {
        // The forecast call makes sure history and the current price are ingested first.
        const [game, forecast] = await Promise.all([api.game(id), api.forecast(id, country)]);
        const [price, history, sales] = await Promise.all([
          api.price(id, country).catch((err) => {
            if (err instanceof ApiError && err.status === 404) return null;
            throw err;
          }),
          api.history(id, country),
          api.saleEvents(id, country),
        ]);
        if (!live) return;
        setData({ game, forecast, price, history: history.items, sales, attribution: history.attribution });
        api.watchlist().then((all) => live && setEntry(all.find((e) => e.game.id === id) ?? null)).catch(() => live && setEntry(null));
        api.forecastHistory(id, country).then((page) => live && setPast(page.items)).catch(() => null);
      } catch (err) {
        if (live) setError(errorMessage(err));
      }
    })();
    return () => {
      live = false;
    };
  }, [id, country]);

  // The verdict follows the wait slider; the server applies the versioned policy.
  useEffect(() => {
    if (!data) return;
    let live = true;
    const timer = window.setTimeout(() => {
      api.recommendation(id, country, wait).then((r) => live && setRec(r)).catch(() => null);
    }, rec ? 280 : 0);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, wait, id, country]);

  useGSAP(
    () => {
      if (!data || prefersReducedMotion()) return;
      gsap.from("[data-stagger] > *", { opacity: 0, y: 18, duration: 0.7, ease: EASE, stagger: 0.05 });
      gsap.from(".factor-bar", { scaleX: 0, duration: 0.9, ease: "expo.inOut", stagger: 0.07, delay: 0.3 });
    },
    { dependencies: [Boolean(data)], scope: root },
  );

  if (error) {
    return (
      <main className="page wrap">
        <ErrorNote>{error}</ErrorNote>
        <Link className="link" href="/app">Back to search</Link>
      </main>
    );
  }
  if (!data) {
    return (
      <main className="page wrap" aria-busy="true">
        <p className="data muted">Reading price history and building the forecast…</p>
        <Skeleton height="9rem" />
        <Skeleton height="22rem" />
      </main>
    );
  }

  const { game, price, forecast, history, sales } = data;
  const currency = forecast.currency ?? price?.currency ?? user?.default_currency ?? "USD";
  const probs = forecast.currently_on_sale ? forecast.new_sale_probability : forecast.sale_probability;
  const liveChance = probabilityWithin(forecast.new_sale_probability, wait);
  const action = rec?.action ?? "NEUTRAL";
  const lowConfidence = forecast.data_quality === "INSUFFICIENT";

  // The provider's link goes to the same Steam page and is passed through untouched;
  // the plain Steam URL is the fallback when the provider gave none.
  const storeUrl = price?.url ?? steamUrl(game);

  // A free game has nothing to forecast, wait for or be alerted about.
  if (price && price.price.amount_minor === 0) {
    return (
      <main className="page wrap" ref={root}>
        <div className="page-head">
          <div>
            <h1 className="display h-lg" style={{ overflowWrap: "anywhere" }}>{game.title}</h1>
            <p className="data muted">
              {[game.publishers[0], game.release_date ? `Released ${date(game.release_date)}` : null, `Steam · ${price.country} · ${price.currency}`]
                .filter(Boolean)
                .join(" · ")}
            </p>
          </div>
          <span className="tag" data-tone="BUY">Free</span>
        </div>
        <section className="verdict" data-action="BUY" aria-label="This game is free">
          <div className="verdict-type">
            <CellType lines={["FREE"]} />
          </div>
          <p className="lede">it&rsquo;s free gng go claim it 🥀</p>
          {storeUrl ? (
            <div>
              <a className="btn" href={storeUrl} target="_blank" rel="noreferrer">
                <span>View on Steam</span>
              </a>
            </div>
          ) : null}
        </section>
        <div style={{ maxWidth: "34rem" }}>
          <GameArt game={game} />
        </div>
        <AttributionNote attribution={data.attribution ?? price.attribution} />
      </main>
    );
  }

  return (
    <main className="page wrap" ref={root}>
      <div className="page-head">
        <div>
          <h1 className="display h-lg" style={{ overflowWrap: "anywhere" }}>{game.title}</h1>
          <p className="data muted">
            {[game.publishers[0], game.release_date ? `Released ${date(game.release_date)}` : null, `Steam · ${forecast.country} · ${currency}`]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
        {storeUrl ? (
          <a className="btn btn-line btn-sm" href={storeUrl} target="_blank" rel="noreferrer">
            <span>View on Steam</span>
          </a>
        ) : null}
      </div>

      <div className="game-top">
        <section className="verdict" data-action={action} aria-labelledby="verdict-title">
          <h2 id="verdict-title" className="data">Our call if you can wait up to {wait} days</h2>
          <div className="verdict-type" aria-live="polite">
            {rec ? (
              <CellType lines={[action]} fills={[action === "WAIT" ? "var(--pen)" : undefined]} />
            ) : (
              <Skeleton height="5rem" />
            )}
          </div>
          <p className="lede" style={{ maxWidth: "36em" }}>{rec?.summary ?? "Working out the call…"}</p>
          {rec ? (
            <ul style={{ display: "flex", flexWrap: "wrap", gap: "0.375rem" }}>
              {rec.reason_codes.map((code) => (
                <li key={code} className="tag" style={{ background: "var(--paper)" }}>{REASON[code] ?? code.replace(/_/g, " ").toLowerCase()}</li>
              ))}
            </ul>
          ) : null}
          <label className="field">
            <span>How long can you wait? {wait} days</span>
            <input className="slider" type="range" min={1} max={120} value={wait} onChange={(e) => setWait(Number(e.target.value))} />
          </label>
          <dl className="kv">
            <div>
              <dt>Chance of a new sale</dt>
              <dd className="num">{pct(rec && rec.max_wait_days === wait ? rec.sale_probability : liveChance)}</dd>
            </div>
            <div>
              <dt>Expected saving</dt>
              <dd className="num">{rec?.expected_savings && rec.expected_savings.amount_minor > 0 ? money(rec.expected_savings, { compact: true }) : "None"}</dd>
            </div>
            <div>
              <dt>Expected price then</dt>
              <dd className="num">{money(rec?.expected_future_price, { compact: true })}</dd>
            </div>
          </dl>
        </section>

        <div className="stack" data-stagger>
          <GameArt game={game} />
          <dl className="kv">
            <div>
              <dt>Price now</dt>
              <dd className="num">{price ? money(price.price) : "—"}</dd>
            </div>
            <div>
              <dt>Regular</dt>
              <dd className="num">{price ? money(price.regular) : "—"}</dd>
            </div>
            <div>
              <dt>Discount now</dt>
              <dd className="num">{price ? (price.discount_pct ? <span className="mark">{price.discount_pct}% off</span> : "None") : "—"}</dd>
            </div>
            <div>
              <dt>Lowest recorded</dt>
              <dd className="num">{money(price?.historical_low)}</dd>
            </div>
          </dl>
          {!price ? <p className="notice">Steam has no price for this game in {forecast.country}. Change your country in Account to see another region.</p> : null}
          {price?.is_stale ? <p className="notice">This price was last checked {relativeDays(price.fetched_at)}. A refresh is failing right now.</p> : null}
          {price?.historical_low_at ? <p className="data muted">Lowest price first seen {date(price.historical_low_at)}</p> : null}
          <WatchForm game={game} currency={currency} country={forecast.country} entry={entry} onChange={setEntry} />
        </div>
      </div>

      <section className="module" aria-labelledby="history-title">
        <div className="module-head">
          <h2 id="history-title" className="h-sm">Price history and the estimated next sale</h2>
          <span className="data muted">{sales.length} sales on record</span>
        </div>
        <div className="module-body">
          <PriceChart
            history={history}
            sales={sales}
            forecast={forecast}
            currency={currency}
            lowMinor={price?.historical_low?.amount_minor ?? null}
          />
        </div>
      </section>

      <section className="split" aria-labelledby="forecast-title">
        <div className="stack">
          <h2 id="forecast-title" className="display h-md">
            {forecast.currently_on_sale ? "It is on sale now. Chance of another:" : "Chance a sale starts"}
          </h2>
          <div className="waffles">
            <Waffle value={probs.days_7} label="7 days" />
            <Waffle value={probs.days_30} label="30 days" />
            <Waffle value={probs.days_90} label="90 days" />
          </div>
          <p className="body">
            {forecast.likely_window
              ? `Most likely window: ${dayMonth(forecast.likely_window.start)} to ${dayMonth(forecast.likely_window.end)}.`
              : "The evidence is too spread out to name a likely window, so we do not."}{" "}
            Each filled cell is one percentage point of estimated chance.
          </p>
          <dl className="kv">
            <div>
              <dt>Confidence</dt>
              <dd className="num">{pct(forecast.confidence_score)}</dd>
            </div>
            <div>
              <dt>Made by</dt>
              <dd className="num" style={{ fontSize: "1rem" }}>{forecast.method === "ML" ? "Trained model" : "Rule baseline"}</dd>
            </div>
          </dl>
          <p className={lowConfidence ? "notice" : "data muted"}>
            {QUALITY[forecast.data_quality]} · forecast made {relativeDays(forecast.created_at)} · {forecast.model_version}
          </p>
        </div>
        <div className="stack">
          <h2 className="display h-md">If it goes on sale, how deep?</h2>
          <TierChart probabilities={forecast.discount_tier_probabilities} regularMinor={forecast.regular_price?.amount_minor} currency={currency} />
          {forecast.predicted_sale_price ? (
            <dl className="kv">
              <div>
                <dt>Likely sale price</dt>
                <dd className="num">
                  {money(forecast.predicted_sale_price.lower, { compact: true })} – {money(forecast.predicted_sale_price.upper, { compact: true })}
                </dd>
              </div>
              <div>
                <dt>Middle estimate</dt>
                <dd className="num">{money(forecast.predicted_sale_price.median, { compact: true })}</dd>
              </div>
              <div>
                <dt>Most likely cut</dt>
                <dd className="num">{TIER_LABEL[forecast.most_likely_discount_tier]}</dd>
              </div>
            </dl>
          ) : null}
          <p className="data muted">The range covers the middle 80% of estimated outcomes, if a sale happens.</p>
        </div>
      </section>

      <section aria-labelledby="why-title">
        <h2 id="why-title" className="display h-md" style={{ marginBottom: "0.75rem" }}>Why the forecast says this</h2>
        <ul className="factors rule-list">
          {forecast.explanation_factors.map((factor) => (
            <li key={factor.code}>
              <div>
                <span className="tag" data-tone={factor.direction}>{factor.direction === "NEUTRAL" ? "Context" : `Leans ${factor.direction.toLowerCase()}`}</span>
                <div className="factor-bar" style={{ width: `${Math.max(10, Math.round(factor.importance * 100))}%` }} title={`Weight ${pct(factor.importance)}`} />
              </div>
              <p>{factor.text}</p>
            </li>
          ))}
        </ul>
      </section>

      {past.length > 1 ? (
        <section className="module" aria-labelledby="past-title">
          <div className="module-head">
            <h2 id="past-title" className="h-sm">Earlier forecasts for this game</h2>
            <span className="data muted">Never edited after the fact</span>
          </div>
          <div className="module-body scroll-x">
            <table className="table">
              <thead>
                <tr><th>Made</th><th>7 days</th><th>30 days</th><th>90 days</th><th>Method</th><th>What happened</th></tr>
              </thead>
              <tbody>
                {past.map((f) => (
                  <tr key={f.id}>
                    <td>{date(f.created_at)}</td>
                    <td>{pct(f.sale_probability.days_7)}</td>
                    <td>{pct(f.sale_probability.days_30)}</td>
                    <td>{pct(f.sale_probability.days_90)}</td>
                    <td>{f.method === "ML" ? "Model" : "Baseline"}</td>
                    <td>
                      {f.outcome
                        ? [
                            f.outcome.sale_within_7d === null ? null : `7d ${f.outcome.sale_within_7d ? "sale" : "no sale"}`,
                            f.outcome.sale_within_30d === null ? null : `30d ${f.outcome.sale_within_30d ? "sale" : "no sale"}`,
                            f.outcome.sale_within_90d === null ? null : `90d ${f.outcome.sale_within_90d ? "sale" : "no sale"}`,
                          ]
                            .filter(Boolean)
                            .join(" · ")
                        : "Window still open"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      <AttributionNote attribution={data.attribution ?? price?.attribution} />
    </main>
  );
}

function WatchForm({
  game,
  currency,
  country,
  entry,
  onChange,
}: {
  game: Game;
  currency: string;
  country: string;
  entry: WatchlistEntry | null | undefined;
  onChange: (entry: WatchlistEntry | null) => void;
}) {
  const [target, setTarget] = useState("");
  const [discount, setDiscount] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (entry === undefined) return <Skeleton height="5rem" />;

  if (entry) {
    return (
      <div className="module">
        <div className="module-head">
          <h2 className="h-sm">On your watchlist</h2>
          <Link className="link" href="/app/watchlist">Edit alerts</Link>
        </div>
        <div className="module-body">
          <p className="body">
            {entry.target_price ? `Alert at ${money(entry.target_price)} or lower. ` : ""}
            {entry.min_discount_pct ? `Alert at ${entry.min_discount_pct}% off or more. ` : ""}
            {!entry.target_price && !entry.min_discount_pct ? "You will hear when the call changes to buy. " : ""}
          </p>
        </div>
      </div>
    );
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    const targetMinor = target ? toMinor(target, currency) : null;
    if (target && targetMinor === null) {
      setError(`Enter the target price as a number in ${currency}, for example 749.`);
      return;
    }
    const pctValue = discount ? Number(discount) : null;
    if (pctValue !== null && (!Number.isInteger(pctValue) || pctValue < 1 || pctValue > 100)) {
      setError("Minimum discount must be a whole number from 1 to 100.");
      return;
    }
    setBusy(true);
    try {
      onChange(await api.addToWatchlist({ game_id: game.id, country, target_price_minor: targetMinor, min_discount_pct: pctValue }));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="module" onSubmit={submit} noValidate>
      <div className="module-head">
        <h2 className="h-sm">Watch this game</h2>
        <span className="data muted">Both fields optional</span>
      </div>
      <div className="module-body stack">
        <div className="form-grid">
          <label className="field">
            <span>Alert at or below ({currency})</span>
            <input className="input" inputMode="decimal" placeholder="e.g. 749" value={target} onChange={(e) => setTarget(e.target.value)} />
          </label>
          <label className="field">
            <span>Or at least % off</span>
            <input className="input" inputMode="numeric" placeholder="e.g. 50" value={discount} onChange={(e) => setDiscount(e.target.value)} />
          </label>
        </div>
        {error ? <ErrorNote>{error}</ErrorNote> : null}
        <Button type="submit" variant="hi" disabled={busy}>{busy ? "Adding" : "Add to watchlist"}</Button>
      </div>
    </form>
  );
}
