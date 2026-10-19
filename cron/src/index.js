/**
 * Steam4Caster scheduler: a Cloudflare Worker on a cron trigger.
 *
 * The backend runs on Vercel, where nothing runs between requests and every request is
 * cut off after 60 seconds. This Worker is the clock: on each trigger it asks the
 * backend which periodic jobs are due, runs each with its own request, then drains the
 * queue of per-game work in short calls until it is empty. All the work happens on
 * the backend; the Worker only makes HTTP calls.
 *
 * Settings: API_URL (wrangler.toml) and the CRON_SECRET secret (`wrangler secret put`).
 */

/** Drain calls per trigger. Each starts at most 20 s of work on the backend. */
const MAX_DRAINS = 12;

async function call(env, path) {
  const response = await fetch(`${env.API_URL.replace(/\/$/, "")}/internal/jobs${path}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${env.CRON_SECRET}` },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`${path} -> HTTP ${response.status} ${JSON.stringify(body)}`);
  return body;
}

async function runOnce(env) {
  const log = [];
  const { due = [] } = await call(env, "/tick");
  // The outbox goes last (below) so alerts raised by this run are delivered in this run.
  for (const name of due.filter((n) => n !== "dispatch_outbox")) {
    try {
      log.push({ name, ...(await call(env, `/run/${name}`)) });
    } catch (error) {
      log.push({ name, error: String(error) });
    }
  }
  for (let i = 0; i < MAX_DRAINS; i += 1) {
    const result = await call(env, "/drain");
    log.push({ drain: i, ...result });
    if (!result.remaining) break;
  }
  if (due.includes("dispatch_outbox")) {
    log.push({ name: "dispatch_outbox", ...(await call(env, "/run/dispatch_outbox")) });
  }
  console.log(JSON.stringify({ due, log }));
}

export default {
  async scheduled(_event, env, ctx) {
    ctx.waitUntil(runOnce(env));
  },
  // Nothing to serve publicly.
  async fetch() {
    return new Response("Not found", { status: 404 });
  },
};
