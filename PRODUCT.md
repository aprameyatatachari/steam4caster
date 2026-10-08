# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Next.js (App Router) + TypeScript, chosen by the project owner. GSAP for animation (owner requirement). The frontend lives in `web/` and talks to the existing FastAPI backend over the versioned `/api/v1` HTTP API. Intended deploy target: Vercel for the frontend, a container host for the backend.

## Users

PC gamers who buy on Steam and do not want to overpay. They have a specific game in mind, or a wishlist of several, and are deciding whether to buy now or hold out for a sale. They check in briefly: when a game catches their eye, before a seasonal sale, or when an alert arrives. Prices are regional; the first audience is in India (INR), and every user sees their own country's prices.

## Product Purpose

Steam4Caster forecasts Steam discounts. For a game and region it estimates the probability of a sale within 7, 30 and 90 days, the likely discount tier and sale-price range, and gives an explainable BUY, WAIT or NEUTRAL recommendation. Users keep a watchlist with alert preferences and get notified by Web Push or email when a condition they care about is met. Success is a user making a buy-or-wait decision they understand and trust, and being told at the right moment.

## Positioning

It predicts when a game will next be discounted and how deep, with stated confidence, and then publishes how those past predictions turned out. Deal sites list current and historical prices; Steam4Caster forecasts the next one and holds itself accountable. It is decision support for a personal watchlist, not a deal feed.

## Operating Context

- Flows: search or look up a game, read its price history and forecast, add it to the watchlist with a target price, minimum discount and maximum wait, receive alerts, review forecast accuracy.
- Price history is a step series of price changes, not regular samples.
- The app runs in a desktop or mobile browser. Web Push needs a service worker and browser permission.
- The backend may run on a synthetic demo catalogue (`PRICE_PROVIDER=fake`) during development; that data is not real.

## Capabilities and Constraints

- Backend API contract: `docs/api.md` and `docs/openapi.json`. Auth is bearer access tokens with rotating refresh tokens; only one refresh may be in flight at a time.
- Money is always integer minor units plus an ISO currency. Amounts in different currencies are never added or converted, with one owner-requested exception: the game page can show another region's full price history in that region's own currency, with a plain disclaimer and today's reference exchange rate as an indicative comparison. Forecasts, recommendations, alerts and totals never use converted prices.
- Only the Steam shop is supported.
- Forecast fields can be absent: `likely_window` and `predicted_sale_price` are often null; a new watchlist entry may have no forecast for a few seconds.
- Forecasts come from a deterministic baseline unless a trained model has been activated. The UI must show the method actually used.
- Model-performance endpoints return empty metrics until forecasts are at least 7 days old.
- Steam seasonal sale dates are estimates.
- Not built in the backend: password reset, social login, account deletion.

## Brand Commitments

- Name: **Steam4Caster**.
- Every probability, price range and recommendation is presented as an estimate, never a promise, and never as financial advice.
- The IsThereAnyDeal attribution returned by `GET /api/v1/meta` is displayed wherever prices are shown. No affiliation with IsThereAnyDeal or Valve is implied. Provider URLs are passed through unmodified.
- Visual reference made binding by the owner: landonorris.com as inspiration, "same energy, own identity". Borrow its moves (large uppercase kinetic type, one acid accent, contour or telemetry lines, corner HUD widgets, pinned scroll scenes); the palette, typefaces and imagery are Steam4Caster's own.
- Animation is built with GSAP. Charts are interactive.

## Evidence on Hand

- A working backend with a deterministic demo catalogue of 12 synthetic games across five regions, with years of generated price history. Usable for demonstrations when labelled as demo data.
- Real forecast output, explanation factors and recommendation summaries from that backend.
- Absent, and not to be fabricated: user counts, testimonials, press, accuracy claims on real data, pricing or plans, partner logos, game cover art rights.

## Product Principles

1. Honest uncertainty. Show ranges, probabilities and confidence; say plainly when there is too little history.
2. Explain the call. Every recommendation carries its reasons in plain language.
3. Accountable. Past forecasts and their outcomes are part of the product, not hidden.
4. The user's own region. Prices and currency are the user's, never converted.
5. Respect attention. Alerts fire when something changes, not because a condition is still true.

## Accessibility & Inclusion

No formal standard has been set by the owner. Working assumption (inferred, not confirmed): WCAG 2.1 AA contrast and keyboard access, reduced-motion support for all GSAP animation, and charts that remain readable without hover.
