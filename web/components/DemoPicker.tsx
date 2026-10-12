"use client";

import { ButtonLink, GameArt, Skeleton, steamUrl } from "@/components/ui";
import type { Deal, Game, Snapshot } from "@/lib/api";
import { date, DEMO_COUNTRIES, money, relativeDays } from "@/lib/format";

/**
 * The demo's controls. Only this week's listed deals can be picked: the landing page
 * shows what the app does, and looking up any other game needs an account. The rest
 * of the page redraws from whichever deal is picked.
 */
export function DemoPicker({
  deals,
  snapshot,
  picked,
  loading,
  error,
  country,
  onCountry,
  onPick,
  startHref,
}: {
  deals: Deal[];
  snapshot: Snapshot | null;
  picked: Game | null;
  loading: boolean;
  error: string | null;
  country: string;
  onCountry: (code: string) => void;
  onPick: (game: Game) => void;
  startHref: string;
}) {
  const shown = snapshot && (!picked || snapshot.game.id === picked.id) ? snapshot : null;
  const game = shown?.game ?? picked;
  const price = shown?.price ?? null;
  const storeUrl = price?.url ?? (game ? steamUrl(game) : null);
  const region = DEMO_COUNTRIES.find((c) => c.code === country)?.name ?? country;

  return (
    <div className="try-grid">
      <div className="stack">
        <label className="field" style={{ maxWidth: "16rem" }}>
          <span>Region</span>
          <select className="select" value={country} onChange={(e) => onCountry(e.target.value)}>
            {DEMO_COUNTRIES.map((c) => (
              <option key={c.code} value={c.code}>{c.name}</option>
            ))}
          </select>
        </label>
        {deals.length > 0 ? (
          <ul className="rows" aria-label="This week's deals">
            {deals.map((deal) => (
              <li key={deal.game.id}>
                <button
                  type="button"
                  className="row row-art try-result"
                  aria-pressed={deal.game.id === picked?.id}
                  onClick={() => onPick(deal.game)}
                >
                  <GameArt game={deal.game} size="thumb" />
                  <span className="row-title">{deal.game.title}</span>
                  <span className="num">−{deal.discount_pct}%</span>
                </button>
              </li>
            ))}
          </ul>
        ) : loading ? (
          <Skeleton height="16rem" />
        ) : (
          <div className="empty">
            <p className="h-sm">No deals to try in {region} right now</p>
            <p className="body">Pick another region, or create an account to forecast any Steam game.</p>
          </div>
        )}
        <div className="try-more">
          <p className="body">Want a game that is not on this list? An account lets you search all of Steam and keep a watchlist.</p>
          <ButtonLink href={startHref}>Look up any game</ButtonLink>
        </div>
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
        {loading && !shown && game ? (
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
      </div>
    </div>
  );
}
