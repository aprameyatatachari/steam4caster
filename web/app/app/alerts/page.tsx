"use client";

import { useCallback, useEffect, useState } from "react";

import { Button, ErrorNote, Skeleton } from "@/components/ui";
import { api, errorMessage, type Channel, type NotificationItem, type Preference, type Preferences, type PushSub } from "@/lib/api";
import { date } from "@/lib/format";

const CHANNEL: Record<Channel, { name: string; note: string }> = {
  WEB_PUSH: { name: "Browser push", note: "Free. Arrives on this device even when the tab is closed." },
  EMAIL: { name: "Email", note: "Sent to your account address." },
  SMS: { name: "Text message", note: "Optional and carrier-charged." },
};

const EVENT: Record<string, string> = {
  TARGET_PRICE: "Target price hit",
  MIN_DISCOUNT: "Discount reached",
  HISTORICAL_LOW: "New lowest price",
  BUY_TRANSITION: "Call changed to buy",
  SALE_WINDOW_APPROACHING: "Sale window near",
  TEST: "Test",
};

const STATE: Record<string, string> = {
  SENT: "Sent",
  PENDING: "Queued",
  SENDING: "Sending",
  FAILED: "Failed",
  SUPPRESSED: "Held back",
};

function keyToBytes(base64url: string): Uint8Array<ArrayBuffer> {
  const padded = base64url.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(base64url.length / 4) * 4, "=");
  const raw = atob(padded);
  const bytes = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

export default function AlertsPage() {
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [subs, setSubs] = useState<PushSub[]>([]);
  const [items, setItems] = useState<NotificationItem[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [p, s, n] = await Promise.all([api.preferences(), api.pushSubscriptions(), api.notifications()]);
      setPrefs(p);
      setSubs(s);
      setItems(n.items);
      setCursor(n.next_cursor);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const run = async (key: string, work: () => Promise<void>) => {
    setBusy(key);
    setError(null);
    setNote(null);
    try {
      await work();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  const patch = (pref: Preference, change: Record<string, unknown>) =>
    run(pref.channel, async () => setPrefs(await api.updatePreferences([{ channel: pref.channel, ...change }])));

  const enablePush = () =>
    run("push", async () => {
      if (!prefs?.vapid_public_key) throw new Error("Browser push is not set up on this server yet.");
      if (!("serviceWorker" in navigator) || !("PushManager" in window)) throw new Error("This browser does not support push notifications.");
      const permission = await Notification.requestPermission();
      if (permission !== "granted") throw new Error("Notifications are blocked for this site. Allow them in your browser's site settings, then try again.");
      const registration = await navigator.serviceWorker.register("/sw.js");
      await navigator.serviceWorker.ready;
      const subscription =
        (await registration.pushManager.getSubscription()) ??
        (await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyToBytes(prefs.vapid_public_key) }));
      await api.addPushSubscription({ ...subscription.toJSON(), device_label: navigator.userAgent.includes("Mobile") ? "Phone browser" : "Desktop browser" });
      setSubs(await api.pushSubscriptions());
      setNote("This browser will now receive alerts.");
    });

  const sendTest = () =>
    run("test", async () => {
      const result = await api.sendTest();
      setNote(result.results.map((r) => `${CHANNEL[r.channel].name}: ${STATE[r.state] ?? r.state}`).join(" · "));
      const n = await api.notifications();
      setItems(n.items);
      setCursor(n.next_cursor);
    });

  if (!prefs && !error) return <main className="page wrap" aria-busy="true"><Skeleton height="14rem" /></main>;

  return (
    <main className="page wrap">
      <div className="page-head">
        <div>
          <h1 className="display h-lg">Alerts</h1>
          <p className="body">One alert when something changes, not a reminder every day it stays true.</p>
        </div>
        <Button variant="line" size="sm" onClick={sendTest} disabled={busy === "test"}>{busy === "test" ? "Sending" : "Send a test"}</Button>
      </div>

      {error ? <ErrorNote>{error}</ErrorNote> : null}
      {note ? <p className="notice" role="status">{note}</p> : null}

      <section aria-labelledby="channels-title">
        <h2 id="channels-title" className="h-sm" style={{ marginBottom: "0.75rem" }}>Where alerts go</h2>
        <ul className="rows">
          {prefs?.preferences.map((pref) => {
            const info = CHANNEL[pref.channel];
            const needsDevice = pref.channel === "WEB_PUSH" && pref.available && subs.length === 0;
            return (
              <li key={pref.channel} style={{ padding: "1rem 0.25rem", display: "grid", gap: "0.875rem" }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: "1rem", flexWrap: "wrap", alignItems: "start" }}>
                  <label className="check" style={{ opacity: pref.available ? 1 : 0.6 }}>
                    <input
                      type="checkbox"
                      checked={pref.enabled && pref.available}
                      disabled={!pref.available || busy === pref.channel}
                      onChange={(e) => patch(pref, { enabled: e.target.checked })}
                    />
                    <span>
                      <span className="row-title">{info.name}</span>
                      <span className="body" style={{ display: "block" }}>
                        {pref.available ? info.note : "Not switched on for this server."}
                      </span>
                    </span>
                  </label>
                  {pref.channel === "WEB_PUSH" && pref.available ? (
                    <Button size="sm" variant={needsDevice ? "hi" : "line"} onClick={enablePush} disabled={busy === "push"}>
                      {busy === "push" ? "Asking" : subs.length ? "Add this browser" : "Turn on for this browser"}
                    </Button>
                  ) : null}
                </div>
                {pref.channel === "WEB_PUSH" && subs.length > 0 ? (
                  <ul style={{ display: "grid", gap: "0.375rem" }}>
                    {subs.map((sub) => (
                      <li key={sub.id} className="data" style={{ display: "flex", gap: "1rem", alignItems: "center", flexWrap: "wrap" }}>
                        <span>{sub.device_label ?? "Browser"} · {sub.endpoint_host} · added {date(sub.created_at)}</span>
                        <button
                          type="button"
                          className="link"
                          style={{ background: "none", border: 0, cursor: "pointer", font: "inherit", textTransform: "inherit" }}
                          onClick={() => run("push", async () => { await api.removePushSubscription(sub.id); setSubs(await api.pushSubscriptions()); })}
                        >
                          Remove
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : null}
                {pref.available && pref.enabled ? (
                  <div className="form-grid">
                    <label className="field">
                      <span>Delivery</span>
                      <select className="select" value={pref.delivery_mode} onChange={(e) => patch(pref, { delivery_mode: e.target.value })}>
                        <option value="IMMEDIATE">As it happens</option>
                        <option value="DIGEST">One daily digest</option>
                      </select>
                    </label>
                    <label className="field">
                      <span>Quiet from</span>
                      <input className="input" type="time" defaultValue={pref.quiet_hours_start?.slice(0, 5) ?? ""} onBlur={(e) => patch(pref, { quiet_hours_start: e.target.value ? `${e.target.value}:00` : null })} />
                    </label>
                    <label className="field">
                      <span>Quiet until</span>
                      <input className="input" type="time" defaultValue={pref.quiet_hours_end?.slice(0, 5) ?? ""} onBlur={(e) => patch(pref, { quiet_hours_end: e.target.value ? `${e.target.value}:00` : null })} />
                      <small>{pref.timezone} time. Alerts raised in quiet hours arrive after.</small>
                    </label>
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      </section>

      <section aria-labelledby="history-title">
        <h2 id="history-title" className="h-sm" style={{ marginBottom: "0.75rem" }}>What has been sent</h2>
        {items && items.length === 0 ? (
          <div className="empty">
            <p className="h-sm">No alerts yet</p>
            <p className="body">When a watched game hits your target, reaches your discount, or the call changes to buy, it shows up here.</p>
          </div>
        ) : (
          <ul className="rows">
            {items?.map((item) => (
              <li key={item.id}>
                <div className="row">
                  <div>
                    <p className="row-title">{item.title}</p>
                    <p className="body" style={{ marginTop: "0.25rem" }}>{item.body}</p>
                    <p className="data muted" style={{ marginTop: "0.375rem" }}>
                      {EVENT[item.event_type] ?? item.event_type} · {CHANNEL[item.channel].name} · {date(item.created_at)}
                      {item.state === "SUPPRESSED" ? ` · ${item.error_code === "COOLDOWN" ? "too soon after a similar alert" : "daily limit reached"}` : ""}
                    </p>
                  </div>
                  <span className="tag" data-tone={item.state}>{STATE[item.state] ?? item.state}</span>
                </div>
              </li>
            ))}
          </ul>
        )}
        {cursor ? (
          <Button variant="line" size="sm" style={{ marginTop: "1rem" }} onClick={() => run("more", async () => { const n = await api.notifications(cursor); setItems((prev) => [...(prev ?? []), ...n.items]); setCursor(n.next_cursor); })}>
            Show older
          </Button>
        ) : null}
      </section>
    </main>
  );
}
