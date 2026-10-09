"use client";

import { useState, type FormEvent } from "react";

import { Button, ErrorNote } from "@/components/ui";
import { api, errorMessage, type WishlistImport as Result } from "@/lib/api";

function summarise(r: Result): string {
  const parts = [
    r.added === 0
      ? "Nothing new to add."
      : `Added ${r.added} ${r.added === 1 ? "game" : "games"} from your Steam wishlist.`,
  ];
  if (r.already_watching) parts.push(`${r.already_watching} already on your watchlist.`);
  if (r.not_found) parts.push(`${r.not_found} could not be matched to a priced game.`);
  if (r.failed) parts.push(`${r.failed} failed to look up; import again to retry them.`);
  if (r.skipped_over_limit) parts.push(`${r.skipped_over_limit} more were over the per-import limit; import again to continue.`);
  if (r.added) parts.push("Prices and forecasts fill in over the next minute or two.");
  return parts.join(" ");
}

/** Paste a Steam profile link to add its public wishlist to the watchlist. */
export function WishlistImport({ onImported }: { onImported: () => void }) {
  const [profile, setProfile] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const result = await api.importSteamWishlist(profile.trim());
      setNote(summarise(result));
      if (result.added) onImported();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="module" onSubmit={submit}>
      <div className="module-head">
        <h2 className="h-sm">Import your Steam wishlist</h2>
        <span className="data muted">Your wishlist must be public</span>
      </div>
      <div className="module-body stack">
        <div className="searchbar">
          <label className="sr-only" htmlFor="steam-profile">Steam profile link, custom URL name or Steam ID</label>
          <input
            id="steam-profile"
            className="input"
            style={{ minHeight: "3rem", fontSize: "1rem", fontWeight: 400 }}
            placeholder="https://steamcommunity.com/id/yourname"
            value={profile}
            onChange={(e) => setProfile(e.target.value)}
            autoComplete="off"
            spellCheck={false}
          />
          <Button type="submit" variant="hi" style={{ minHeight: "3rem" }} disabled={busy || profile.trim().length < 2}>
            {busy ? "Importing" : "Import"}
          </Button>
        </div>
        <p className="data muted">
          {busy
            ? "Looking up each game. A long wishlist can take up to a minute."
            : "We only read the public list of games. Nothing is signed in and nothing on Steam changes."}
        </p>
        {error ? <ErrorNote>{error}</ErrorNote> : null}
        {note ? <p className="notice" role="status">{note}</p> : null}
      </div>
    </form>
  );
}
