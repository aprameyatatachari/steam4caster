"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { Button, ErrorNote } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { COUNTRIES, date } from "@/lib/format";

export default function AccountPage() {
  const { user, setUser, signOut } = useAuth();
  const router = useRouter();
  const [country, setCountry] = useState(user?.default_country ?? "IN");
  const [timezone, setTimezone] = useState(user?.timezone ?? "UTC");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  if (!user) return null;
  const known = COUNTRIES.some((c) => c.code === country);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      setUser(await api.updateMe({ default_country: country, timezone }));
      setNote("Saved. Prices and forecasts now use this country.");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="page wrap" style={{ maxWidth: "54rem" }}>
      <h1 className="display h-lg">Account</h1>

      <dl className="kv">
        <div>
          <dt>Signed in as</dt>
          <dd className="num" style={{ fontSize: "1.0625rem", overflowWrap: "anywhere" }}>{user.email}</dd>
        </div>
        <div>
          <dt>Email</dt>
          <dd className="num" style={{ fontSize: "1.0625rem" }}>{user.email_verified ? "Confirmed" : "Not confirmed yet"}</dd>
        </div>
        <div>
          <dt>Member since</dt>
          <dd className="num" style={{ fontSize: "1.0625rem" }}>{date(user.created_at)}</dd>
        </div>
      </dl>

      <form className="module" onSubmit={save}>
        <div className="module-head">
          <h2 className="h-sm">Region</h2>
          <span className="data muted">Prices are never converted</span>
        </div>
        <div className="module-body stack">
          <div className="form-grid">
            <label className="field">
              <span>Country for prices</span>
              <select className="select" value={country} onChange={(e) => setCountry(e.target.value)}>
                {!known ? <option value={country}>{country}</option> : null}
                {COUNTRIES.map((c) => (
                  <option key={c.code} value={c.code}>{c.name}</option>
                ))}
              </select>
              <small>You see Steam&rsquo;s own price for this country, in its currency.</small>
            </label>
            <label className="field">
              <span>Time zone</span>
              <input className="input" value={timezone} onChange={(e) => setTimezone(e.target.value)} placeholder="Asia/Kolkata" />
              <small>Used for quiet hours and digests.</small>
            </label>
          </div>
          {error ? <ErrorNote>{error}</ErrorNote> : null}
          {note ? <p className="notice" role="status">{note}</p> : null}
          <div>
            <Button type="submit" variant="hi" disabled={busy}>{busy ? "Saving" : "Save region"}</Button>
          </div>
        </div>
      </form>

      <div>
        <Button variant="line" onClick={async () => { await signOut(); router.replace("/"); }}>Sign out</Button>
      </div>
    </main>
  );
}
