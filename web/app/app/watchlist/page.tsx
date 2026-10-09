"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import { Button, ButtonLink, ErrorNote, GameArt, Skeleton, steamUrl } from "@/components/ui";
import { WishlistImport } from "@/components/WishlistImport";
import { api, errorMessage, type WatchlistEntry, type WatchlistSummary } from "@/lib/api";
import { dayMonth, fromMinor, money, pct, toMinor } from "@/lib/format";
import { EASE, gsap, prefersReducedMotion, useGSAP } from "@/lib/motion";

export default function WatchlistPage() {
  const [entries, setEntries] = useState<WatchlistEntry[] | null>(null);
  const [summary, setSummary] = useState<WatchlistSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const root = useRef<HTMLElement>(null);

  const load = useCallback(async () => {
    try {
      const [list, totals] = await Promise.all([api.watchlist(), api.watchlistSummary()]);
      setEntries(list);
      setSummary(totals);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // A freshly added game has no forecast for a few seconds while the job runs.
  useEffect(() => {
    if (!entries?.some((e) => !e.forecast)) return;
    const timer = window.setTimeout(load, 5000);
    return () => window.clearTimeout(timer);
  }, [entries, load]);

  useGSAP(
    () => {
      if (!entries || prefersReducedMotion()) return;
      gsap.from("[data-step]", { scaleY: 0, transformOrigin: "bottom", duration: 0.9, ease: "expo.inOut", stagger: 0.08 });
      gsap.from(".rows > li", { opacity: 0, x: -16, duration: 0.55, ease: EASE, stagger: 0.04 });
    },
    { dependencies: [Boolean(entries)], scope: root },
  );

  if (error) return <main className="page wrap"><ErrorNote>{error}</ErrorNote></main>;
  if (!entries) return <main className="page wrap" aria-busy="true"><Skeleton height="8rem" /><Skeleton height="16rem" /></main>;

  if (entries.length === 0) {
    return (
      <main className="page wrap">
        <h1 className="display h-lg">Your watchlist</h1>
        <div className="empty">
          <p className="h-sm">Nothing on the sheet yet</p>
          <p className="body">Search for a game you are thinking of buying and add it. You will get a forecast, a call, and an alert when the price hits your target.</p>
          <ButtonLink href="/app" variant="hi">Find a game</ButtonLink>
        </div>
        <WishlistImport onImported={load} />
      </main>
    );
  }

  const counts = summary?.recommendation_counts ?? {};

  return (
    <main className="page wrap" ref={root}>
      <div className="page-head">
        <div>
          <h1 className="display h-lg">Your watchlist</h1>
          <p className="data muted">
            {entries.length} {entries.length === 1 ? "game" : "games"} · {counts.BUY ?? 0} buy · {counts.WAIT ?? 0} wait · {counts.NEUTRAL ?? 0} neutral
          </p>
        </div>
        <ButtonLink href="/app" variant="line" size="sm">Add a game</ButtonLink>
      </div>

      {summary?.totals.map((t) => {
        const points = [
          { label: "Buy all today", value: t.current_total },
          ...t.expected.map((e) => ({ label: `Expected in ${e.horizon_days}d`, value: e.expected_total })),
          { label: "All at recorded lows", value: t.historical_low_total },
        ];
        const max = Math.max(1, ...points.map((p) => p.value.amount_minor));
        return (
          <section key={t.currency} className="module" aria-label={`Totals in ${t.currency}`}>
            <div className="module-head">
              <h2 className="h-sm">What the list costs in {t.currency}</h2>
              <span className="data muted">Expected figures are estimates · {t.games_priced} priced</span>
            </div>
            <div className="module-body" style={{ display: "grid", gridTemplateColumns: `repeat(${points.length}, minmax(0, 1fr))`, gap: "0.5rem", alignItems: "end" }}>
              {points.map((p, i) => (
                <div key={p.label}>
                  <div style={{ height: "7rem", display: "flex", alignItems: "flex-end" }}>
                    <div
                      data-step
                      style={{
                        width: "100%",
                        height: `${Math.max(4, (p.value.amount_minor / max) * 100)}%`,
                        background: i === 0 ? "var(--ink)" : i === points.length - 1 ? "transparent" : "var(--hi)",
                        border: "1px solid var(--ink)",
                        backgroundImage: i === points.length - 1 ? "repeating-linear-gradient(-45deg, transparent 0 6px, var(--pen) 6px 7px)" : undefined,
                      }}
                    />
                  </div>
                  <p className="num" style={{ fontSize: "clamp(0.9rem, 2.4cqw, 1.375rem)", marginTop: "0.5rem", overflowWrap: "anywhere" }}>{money(p.value, { compact: true })}</p>
                  <p className="data muted">{p.label}</p>
                </div>
              ))}
            </div>
          </section>
        );
      })}

      <ul className="rows">
        {entries.map((entry) =>
          entry.current?.price.amount_minor === 0 ? (
            <li key={entry.id}>
              <div className="row row-art">
                <GameArt game={entry.game} size="thumb" />
                <div>
                  <Link className="row-title link" style={{ textDecorationColor: "transparent" }} href={`/app/game/${entry.game.id}`}>
                    {entry.game.title}
                  </Link>
                  <p className="body" style={{ marginTop: "0.3rem" }}>it&rsquo;s free gng go claim it 🥀</p>
                </div>
                <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap", justifyContent: "flex-end" }}>
                  <SteamLink entry={entry} />
                  <span className="tag" data-tone="BUY">Free</span>
                  <Button size="sm" variant="line" onClick={() => api.removeEntry(entry.id).then(load).catch((err) => setError(errorMessage(err)))}>
                    Remove
                  </Button>
                </div>
              </div>
            </li>
          ) : (
          <li key={entry.id}>
            <div className="row row-art">
                <GameArt game={entry.game} size="thumb" />
              <div>
                <Link className="row-title link" style={{ textDecorationColor: "transparent" }} href={`/app/game/${entry.game.id}`}>
                  {entry.game.title}
                </Link>
                <p className="data muted" style={{ marginTop: "0.3rem" }}>
                  {entry.current ? `${money(entry.current.price)}${entry.current.discount_pct ? ` · ${entry.current.discount_pct}% off` : ""}` : "No price yet"}
                  {entry.current?.historical_low ? ` · low ${money(entry.current.historical_low, { compact: true })}` : ""}
                  {entry.forecast ? ` · ${pct(entry.forecast.sale_probability_30d)} chance in 30d` : " · forecast on its way"}
                  {entry.forecast?.likely_window_start ? ` · window from ${dayMonth(entry.forecast.likely_window_start)}` : ""}
                </p>
                <p className="data" style={{ marginTop: "0.3rem" }}>
                  {entry.target_price ? `Alert ≤ ${money(entry.target_price, { compact: true })}` : "No target price"}
                  {entry.min_discount_pct ? ` · ≥ ${entry.min_discount_pct}% off` : ""}
                  {entry.max_wait_days ? ` · wait up to ${entry.max_wait_days}d` : ""}
                  {!entry.is_active ? " · paused" : ""}
                </p>
              </div>
              <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap", justifyContent: "flex-end" }}>
                <SteamLink entry={entry} />
                <span className="tag" data-tone={entry.recommendation?.action}>{entry.recommendation?.action ?? "Pending"}</span>
                <Button size="sm" variant="line" aria-expanded={open === entry.id} onClick={() => setOpen(open === entry.id ? null : entry.id)}>
                  {open === entry.id ? "Close" : "Edit"}
                </Button>
              </div>
            </div>
            {open === entry.id ? <EntryEditor entry={entry} onSaved={() => { setOpen(null); load(); }} /> : null}
          </li>
          ),
        )}
      </ul>

      <WishlistImport onImported={load} />
    </main>
  );
}

function SteamLink({ entry }: { entry: WatchlistEntry }) {
  const href = entry.current?.url ?? steamUrl(entry.game);
  if (!href) return null;
  return (
    <a className="link data" href={href} target="_blank" rel="noreferrer">
      Steam page
    </a>
  );
}

function EntryEditor({ entry, onSaved }: { entry: WatchlistEntry; onSaved: () => void }) {
  const [target, setTarget] = useState(entry.target_price ? fromMinor(entry.target_price.amount_minor, entry.currency) : "");
  const [discount, setDiscount] = useState(entry.min_discount_pct ? String(entry.min_discount_pct) : "");
  const [wait, setWait] = useState(entry.max_wait_days ? String(entry.max_wait_days) : "");
  const [lowOnly, setLowOnly] = useState(entry.historical_low_only);
  const [onBuy, setOnBuy] = useState(entry.notify_on_buy);
  const [active, setActive] = useState(entry.is_active);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    const targetMinor = target ? toMinor(target, entry.currency) : null;
    if (target && targetMinor === null) return setError(`Enter the target price as a number in ${entry.currency}.`);
    const d = discount ? Number(discount) : null;
    if (d !== null && (!Number.isInteger(d) || d < 1 || d > 100)) return setError("Minimum discount must be a whole number from 1 to 100.");
    const w = wait ? Number(wait) : null;
    if (w !== null && (!Number.isInteger(w) || w < 1 || w > 365)) return setError("Maximum wait must be a whole number of days from 1 to 365.");
    setBusy(true);
    try {
      await api.updateEntry(entry.id, {
        target_price_minor: targetMinor,
        min_discount_pct: d,
        max_wait_days: w,
        historical_low_only: lowOnly,
        notify_on_buy: onBuy,
        is_active: active,
      });
      onSaved();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await api.removeEntry(entry.id);
      onSaved();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  };

  return (
    <form className="stack" onSubmit={save} noValidate style={{ padding: "0 0.25rem 1.25rem" }}>
      <div className="form-grid">
        <label className="field">
          <span>Alert at or below ({entry.currency})</span>
          <input className="input" inputMode="decimal" value={target} onChange={(e) => setTarget(e.target.value)} placeholder="No target" />
        </label>
        <label className="field">
          <span>At least % off</span>
          <input className="input" inputMode="numeric" value={discount} onChange={(e) => setDiscount(e.target.value)} placeholder="Any" />
        </label>
        <label className="field">
          <span>Willing to wait (days)</span>
          <input className="input" inputMode="numeric" value={wait} onChange={(e) => setWait(e.target.value)} placeholder="30" />
        </label>
      </div>
      <label className="check">
        <input type="checkbox" checked={onBuy} onChange={(e) => setOnBuy(e.target.checked)} />
        <span>Tell me when the call changes to buy</span>
      </label>
      <label className="check">
        <input type="checkbox" checked={lowOnly} onChange={(e) => setLowOnly(e.target.checked)} />
        <span>Only alert me on a new lowest recorded price</span>
      </label>
      <label className="check">
        <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
        <span>Alerts on for this game</span>
      </label>
      {error ? <ErrorNote>{error}</ErrorNote> : null}
      <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap", alignItems: "center" }}>
        <Button type="submit" variant="hi" disabled={busy}>{busy ? "Saving" : "Save alerts"}</Button>
        {confirming ? (
          <>
            <Button variant="line" onClick={remove} disabled={busy}>Yes, remove {entry.game.title}</Button>
            <button type="button" className="link" style={{ background: "none", border: 0, cursor: "pointer" }} onClick={() => setConfirming(false)}>Keep it</button>
          </>
        ) : (
          <button type="button" className="link" style={{ background: "none", border: 0, cursor: "pointer" }} onClick={() => setConfirming(true)}>
            Remove from watchlist
          </button>
        )}
      </div>
    </form>
  );
}
