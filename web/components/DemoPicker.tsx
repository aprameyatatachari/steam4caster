"use client";

import { useEffect, useRef, useState } from "react";

import { GameArt, Skeleton, steamUrl } from "@/components/ui";
import { api, errorMessage, type Game, type Snapshot } from "@/lib/api";
import { date, DEMO_COUNTRIES, money, relativeDays } from "@/lib/format";

/** Pause in typing before a search is sent. */
const SEARCH_DELAY = 350;

/**
 * The demo's controls: search any Steam game (no account needed) and see its current
 * price facts. The rest of the landing page redraws from whichever game is picked.
 */
export function DemoPicker({
  snapshot,
  picked,
  loading,
  error,
  country,
  onCountry,
  onPick,
}: {
  snapshot: Snapshot | null;
  picked: Game | null;
  loading: boolean;
  error: string | null;
  country: string;
  onCountry: (code: string) => void;
  onPick: (game: Game) => void;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Game[] | null>(null);
  const [status, setStatus] = useState<string>("");
  const latest = useRef(0);

  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) {
      setResults(null);
      setStatus("");
      return;
    }
    const ticket = ++latest.current;
    const timer = window.setTimeout(async () => {
      setStatus("Searching…");
      try {
        const found = await api.publicSearch(q);
        if (ticket !== latest.current) return;
        setResults(found);
        setStatus(found.length ? `${found.length} ${found.length === 1 ? "match" : "matches"}` : `Nothing matched “${q}”. Check the spelling or try a shorter title.`);
      } catch (err) {
        if (ticket !== latest.current) return;
        setResults(null);
        setStatus(errorMessage(err));
      }
    }, SEARCH_DELAY);
    return () => window.clearTimeout(timer);
  }, [query]);

  const choose = (game: Game) => {
    setQuery("");
    setResults(null);
    setStatus("");
    onPick(game);
  };

  const shown = snapshot && (!picked || snapshot.game.id === picked.id) ? snapshot : null;
  const game = shown?.game ?? picked;
  const price = shown?.price ?? null;
  const storeUrl = price?.url ?? (game ? steamUrl(game) : null);
  const region = DEMO_COUNTRIES.find((c) => c.code === country)?.name ?? country;

  return (
    <div className="try-grid">
      <div className="stack">
        <form className="try-form" role="search" onSubmit={(e) => e.preventDefault()}>
          <label className="field try-search">
            <span>Game</span>
            <input
              className="input"
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Type a Steam game, e.g. Hades"
              autoComplete="off"
              aria-describedby="try-status"
            />
          </label>
          <label className="field">
            <span>Region</span>
            <select className="select" value={country} onChange={(e) => onCountry(e.target.value)}>
              {DEMO_COUNTRIES.map((c) => (
                <option key={c.code} value={c.code}>{c.name}</option>
              ))}
            </select>
          </label>
        </form>
        <p id="try-status" className="data muted" role="status" style={{ minHeight: "1.3em" }}>{status}</p>
        {results && results.length > 0 ? (
          <ul className="rows" aria-label="Search results">
            {results.map((g) => (
              <li key={g.id}>
                <button type="button" className="row row-art try-result" onClick={() => choose(g)}>
                  <GameArt game={g} size="thumb" />
                  <span className="row-title">{g.title}</span>
                  <span className="data muted">{g.type ?? "game"}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      <div className="stack try-card" aria-busy={loading} data-loading={loading ? "" : undefined} aria-live="polite">
        {game ? (
          <>
            <GameArt game={game} />
            <h3 className="display h-md" style={{ overflowWrap: "anywhere" }}>{game.title}</h3>
            <p className="data muted">
              {[game.publishers[0], game.release_date ? `Released ${date(game.release_date)}` : null, `Steam · ${region}`].filter(Boolean).join(" · ")}
            </p>
          </>
        ) : null}
        {error ? <p className="error" role="alert">{error}</p> : null}
        {loading && !shown ? (
          <>
            <p className="data muted">Reading the price history and building the forecast…</p>
            <Skeleton height="6rem" />
          </>
        ) : null}
        {shown ? (
          <>
            <dl className="kv">
              <div>
                <dt>Price now</dt>
                <dd className="num">{price ? (price.price.amount_minor === 0 ? "Free" : money(price.price)) : "—"}</dd>
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
            {!price ? <p className="notice">Steam has no price for this game in {region}. Pick another region.</p> : null}
            {price?.is_stale ? <p className="notice">This price was last checked {relativeDays(price.fetched_at)}.</p> : null}
            <p className="data muted">
              {shown.history.length} price changes and {shown.sale_events.length} sales on record
              {shown.history.length ? ` since ${date(shown.history[0].observed_at)}` : ""}
            </p>
            {storeUrl ? (
              <div>
                <a className="btn btn-line btn-sm" href={storeUrl} target="_blank" rel="noreferrer">
                  <span>View on Steam</span>
                </a>
              </div>
            ) : null}
          </>
        ) : null}
        {!game && !loading && !error ? (
          <div className="empty">
            <p className="h-sm">No game picked yet</p>
            <p className="body">Search for a Steam game to load its real price history and forecast.</p>
          </div>
        ) : null}
      </div>
    </div>
  );
}
