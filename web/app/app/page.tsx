"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { SalesPanel } from "@/components/SalesPanel";
import { Button, ErrorNote, GameArt, Skeleton, Slash } from "@/components/ui";
import { api, errorMessage, type Game, type WatchlistSummary } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { date, pct } from "@/lib/format";
import { EASE, gsap, prefersReducedMotion, useGSAP } from "@/lib/motion";

/** Pause in typing before a search is sent; keeps well inside the search rate limit. */
const SEARCH_DELAY_MS = 350;

export default function SearchPage() {
  const { user } = useAuth();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Game[] | null>(null);
  const [searched, setSearched] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<WatchlistSummary | null>(null);
  const list = useRef<HTMLUListElement>(null);

  useEffect(() => {
    api.watchlistSummary().then(setSummary).catch(() => null);
  }, []);

  // Only the newest request may update the list, however the responses arrive.
  const latest = useRef(0);
  const run = async (q: string) => {
    const ticket = ++latest.current;
    setBusy(true);
    setError(null);
    try {
      const found = await api.search(q);
      if (ticket !== latest.current) return;
      setResults(found);
      setSearched(q);
    } catch (err) {
      if (ticket === latest.current) setError(errorMessage(err));
    } finally {
      if (ticket === latest.current) setBusy(false);
    }
  };

  // Search as you type, once typing pauses. Enter or the button searches at once.
  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) {
      latest.current += 1; // drop any request still in flight
      setResults(null);
      setError(null);
      setBusy(false);
      return;
    }
    if (q === searched) return;
    const timer = window.setTimeout(() => run(q), SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  const search = (event?: FormEvent) => {
    event?.preventDefault();
    const q = query.trim();
    if (q.length >= 2) run(q);
  };

  useGSAP(
    () => {
      if (!list.current || prefersReducedMotion()) return;
      gsap.from(list.current.children, { opacity: 0, x: -18, duration: 0.6, ease: EASE, stagger: 0.035 });
    },
    { dependencies: [searched], scope: list },
  );

  const likely = summary?.likely_on_sale_within_30d ?? [];

  return (
    <main className="page wrap">
      <div className="page-head">
        <h1 className="display h-lg">Which game are you eyeing?</h1>
      </div>

      <form className="searchbar" onSubmit={search} role="search">
        <label className="sr-only" htmlFor="q">Search Steam games by title</label>
        <input
          id="q"
          className="input"
          type="search"
          placeholder="Start typing a game title"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          autoComplete="off"
          aria-describedby="search-status"
          autoFocus
        />
        <Button type="submit" variant="hi" disabled={busy || query.trim().length < 2}>
          {busy ? "Searching" : "Search"}
        </Button>
      </form>

      <p id="search-status" className="data muted" role="status" style={{ marginTop: "-0.75rem", minHeight: "1.3em" }}>
        {busy
          ? "Searching…"
          : query.trim().length === 1
            ? "Keep typing: two letters or more"
            : results && query.trim() === searched
              ? `${results.length} ${results.length === 1 ? "match" : "matches"} for “${searched}” · prices and forecasts for ${user?.default_country}`
              : ""}
      </p>

      {error ? <ErrorNote>{error}</ErrorNote> : null}
      {busy && !results ? <Skeleton height="12rem" /> : null}

      {results && results.length === 0 ? (
        <div className="empty">
          <p className="h-sm">Nothing matched &ldquo;{searched}&rdquo;</p>
          <p className="body">Try a shorter part of the title. Only games sold on Steam can be forecast.</p>
        </div>
      ) : null}

      {results && results.length > 0 ? (
        <section aria-label={`Results for ${searched}`}>
          <ul className="rows" ref={list}>
            {results.map((game) => (
              <li key={game.id}>
                <Link className="row row-art" href={`/app/game/${game.id}`}>
                  <GameArt game={game} size="thumb" />
                  <span>
                    <span className="row-title">{game.title}</span>
                    <span className="data muted" style={{ display: "block", marginTop: "0.25rem" }}>
                      {[game.type, game.publishers[0], game.release_date ? `Released ${date(game.release_date)}` : null]
                        .filter(Boolean)
                        .join(" · ") || "Open for price history and forecast"}
                    </span>
                  </span>
                  <Slash />
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {!results ? <SalesPanel /> : null}

      {!results && likely.length > 0 ? (
        <section aria-labelledby="likely-title">
          <h2 id="likely-title" className="h-sm" style={{ marginBottom: "0.75rem" }}>
            On your watchlist, likely to go on sale within 30 days
          </h2>
          <ul className="rows">
            {likely.map((item) => (
              <li key={item.entry_id}>
                <Link className="row" href={`/app/game/${item.game_id}`}>
                  <span>
                    <span className="row-title">{item.title}</span>
                    <span className="data muted" style={{ display: "block", marginTop: "0.25rem" }}>
                      {item.window_start ? `Estimated window from ${date(item.window_start)}` : "No clear window yet"}
                    </span>
                  </span>
                  <span className="num" style={{ fontSize: "1.375rem" }}>{pct(item.sale_probability_30d)}</span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {!results && summary && summary.entries === 0 ? (
        <p className="body">
          Search for a game to see its price history, the estimated chance of a sale, and whether to buy now or wait.
          Add it to your watchlist to be alerted when the price hits your target.
        </p>
      ) : null}
    </main>
  );
}
