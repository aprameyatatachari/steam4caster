"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { CalibrationChart, TierChart, Waffle } from "@/components/Charts";
import { CellType } from "@/components/CellType";
import { HeroMorph } from "@/components/HeroMorph";
import { PriceChart } from "@/components/PriceChart";
import { AttributionNote, ButtonLink, Wordmark } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { DEMO_CALIBRATION, demoForecast, demoSeries } from "@/lib/demo";
import { dayMonth, probabilityWithin } from "@/lib/format";
import { EASE, gsap, prefersReducedMotion, ScrollTrigger, useGSAP } from "@/lib/motion";
import { nextSeasonalSale } from "@/lib/seasons";

/** The pointer is a highlighter: cells it passes over are shaded, then fade. */
function HighlighterField() {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    const host = canvas?.parentElement;
    if (!canvas || !host || prefersReducedMotion() || !window.matchMedia("(hover: hover)").matches) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const sheet = host.closest<HTMLElement>(".sheet-inner");
    const lit = new Map<string, number>();
    let cell = 30;
    let dpr = 1;

    const resize = () => {
      const cols = Number(getComputedStyle(sheet ?? host).getPropertyValue("--cols")) || 48;
      cell = host.clientWidth / cols;
      dpr = Math.min(2, window.devicePixelRatio || 1);
      canvas.width = Math.round(host.clientWidth * dpr);
      canvas.height = Math.round(host.clientHeight * dpr);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(host);
    resize();

    const onMove = (e: PointerEvent) => {
      const rect = host.getBoundingClientRect();
      const col = Math.floor((e.clientX - rect.left) / cell);
      const row = Math.floor((e.clientY - rect.top) / cell);
      lit.set(`${col},${row}`, 1);
    };
    host.addEventListener("pointermove", onMove);

    const draw = () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      if (!lit.size) return;
      ctx.fillStyle = "#ffe600";
      for (const [key, alpha] of lit) {
        const [col, row] = key.split(",").map(Number);
        ctx.globalAlpha = alpha;
        ctx.fillRect((col * cell + 1) * dpr, (row * cell + 1) * dpr, (cell - 1) * dpr, (cell - 1) * dpr);
        const next = alpha - 0.018;
        if (next <= 0) lit.delete(key);
        else lit.set(key, next);
      }
      ctx.globalAlpha = 1;
    };
    gsap.ticker.add(draw);
    return () => {
      gsap.ticker.remove(draw);
      host.removeEventListener("pointermove", onMove);
      observer.disconnect();
    };
  }, []);

  return <canvas ref={ref} className="hero-ink" aria-hidden="true" />;
}

function SeasonReadout() {
  const [info, setInfo] = useState<ReturnType<typeof nextSeasonalSale> | null>(null);
  useEffect(() => setInfo(nextSeasonalSale()), []);
  return (
    <aside className="hud" aria-label="Estimated next Steam seasonal sale">
      <div className="hud-row">
        <span className="data">Next seasonal sale</span>
        <span className="data muted">Estimate</span>
      </div>
      <div className="hud-row">
        <span className="h-sm">{info ? `${info.window.kind} sale` : "…"}</span>
        <span className="num hud-big">{info ? (info.running ? "Now" : info.days) : "–"}</span>
      </div>
      <div className="hud-row">
        <span className="data muted">{info ? `From about ${dayMonth(info.window.start)}` : " "}</span>
        <span className="data muted">{info && !info.running ? "Days" : ""}</span>
      </div>
    </aside>
  );
}

export function Landing() {
  const { user } = useAuth();
  const root = useRef<HTMLDivElement>(null);
  const reveal = useRef({ value: 0 });
  const { history, sales } = useMemo(() => demoSeries(), []);
  const forecast = useMemo(() => demoForecast(), []);
  const [wait, setWait] = useState(30);
  const chance = probabilityWithin(forecast.new_sale_probability, wait);
  const startHref = user ? "/app" : "/register";

  useGSAP(
    () => {
      if (prefersReducedMotion()) {
        reveal.current.value = 1;
        return;
      }
      gsap.from("[data-rise]", { y: 28, opacity: 0, duration: 1.1, ease: EASE, stagger: 0.09, delay: 0.75 });

      // Scene: the price log draws itself as you scroll, one beat of copy per third.
      const beats = gsap.utils.toArray<HTMLElement>(".beat");
      gsap.set(beats.slice(1), { opacity: 0, y: 24 });
      const tl = gsap.timeline({
        scrollTrigger: { trigger: ".scene-steps", start: "top top", end: "+=240%", pin: true, scrub: 0.6, anticipatePin: 1 },
      });
      tl.to(reveal.current, { value: 0.5, duration: 1, ease: "none" }, 0)
        .to(beats[0], { opacity: 0, y: -24, duration: 0.18 }, 0.82)
        .to(beats[1], { opacity: 1, y: 0, duration: 0.18 }, 1)
        .to(reveal.current, { value: 0.82, duration: 1, ease: "none" }, 1)
        .to(beats[1], { opacity: 0, y: -24, duration: 0.18 }, 1.82)
        .to(beats[2], { opacity: 1, y: 0, duration: 0.18 }, 2)
        .to(reveal.current, { value: 1, duration: 0.8, ease: "none" }, 2)
        .to({}, { duration: 0.3 });

      gsap.utils.toArray<HTMLElement>("[data-in]").forEach((el) => {
        gsap.fromTo(
          el,
          { clipPath: "inset(0% 100% 0% 0%)" },
          { clipPath: "inset(0% 0% 0% 0%)", duration: 1.2, ease: "expo.inOut", scrollTrigger: { trigger: el, start: "top 86%", once: true } },
        );
      });
      ScrollTrigger.refresh();
    },
    { scope: root },
  );

  return (
    <div ref={root}>
      <HeroMorph history={history} reveal={reveal} />
      <header className="nav">
        <Wordmark />
        <nav className="nav-links" aria-label="Sections">
          <a href="#steps">How it reads prices</a>
          <a href="#forecast">The forecast</a>
          <a href="#call">The call</a>
          <a href="#score">The score</a>
        </nav>
        <div className="nav-end">
          {user ? (
            <ButtonLink href="/app" size="sm" variant="hi">Open app</ButtonLink>
          ) : (
            <>
              <Link className="chip" href="/login">Sign in</Link>
              <ButtonLink href="/register" size="sm" variant="hi">Start</ButtonLink>
            </>
          )}
        </div>
      </header>

      <main>
        <section className="hero wrap" aria-labelledby="hero-title">
          <HighlighterField />
          <CellType as="h1" className="hero-type" lines={["BUY NOW", "OR WAIT?"]} fills={[undefined, "var(--pen)"]} />
          <CellType as="div" className="hero-type-narrow" lines={["BUY", "NOW", "OR", "WAIT?"]} fills={[undefined, undefined, undefined, "var(--pen)"]} />
          <span id="hero-title" className="sr-only">Buy now or wait?</span>
          <div className="hero-foot">
            <div>
              <p className="lede" data-rise>
                Steam4Caster estimates when a Steam game will next go on sale and how deep the cut will be, then
                gives you a straight call: <span className="mark">buy today or hold</span>. Every call comes with its reasons.
              </p>
              <div className="hero-actions" data-rise>
                <ButtonLink href={startHref} variant="hi">Start a watchlist</ButtonLink>
                <a className="btn btn-line" href="#steps"><span>See how it works</span></a>
              </div>
            </div>
            <div data-rise>
              <SeasonReadout />
            </div>
          </div>
        </section>

        <section id="steps" className="scene-steps wrap" aria-label="How Steam4Caster reads a price history">
          <div className="beats">
            <div className="beat">
              <h2 className="display h-lg">A price does not drift. It steps.</h2>
              <p className="body">Steam prices sit flat, drop for a sale, and snap back. So we draw them as steps and keep every one, in your country&rsquo;s own currency.</p>
            </div>
            <div className="beat">
              <h2 className="display h-lg">Every highlight is money off.</h2>
              <p className="body">The shaded block between the regular price and the sale price is what that sale saved. How often they come and how deep they go is the pattern we measure.</p>
            </div>
            <div className="beat">
              <h2 className="display h-lg">Then we sketch the next one.</h2>
              <p className="body">The hatched box right of today is an estimate: when the next sale is likely and the price range it should land in. A range, never a promise.</p>
            </div>
          </div>
          <div>
            <PriceChart history={history} sales={sales} forecast={forecast} currency="INR" lowMinor={50966} revealRef={reveal} height={400} initialRange="2y" />
            <p className="data muted" style={{ marginTop: "0.25rem" }}>Illustrative data for a made-up game, not a real price history.</p>
          </div>
        </section>

        <section id="forecast" className="scene blueprint gridded wrap">
          <div className="split">
            <div className="stack sticky-col">
              <h2 className="display h-lg" data-in>One hundred cells per horizon.</h2>
              <p className="lede">Each grid is a hundred cells. Filled cells are the estimated chance a sale starts inside that window. No dates pulled from thin air.</p>
              <label className="field" style={{ marginTop: "2rem" }}>
                <span>How long can you wait? {wait} days</span>
                <input className="slider" type="range" min={1} max={120} value={wait} onChange={(e) => setWait(Number(e.target.value))} />
              </label>
              <div style={{ width: "min(100%, 15rem)" }}>
                <CellType lines={[`${Math.round(chance * 100)}%`]} fills={["var(--hi)"]} build="mount" />
              </div>
              <p className="body">Estimated chance of a new sale within {wait} days, for the example game.</p>
            </div>
            <div className="stack">
              <div className="waffles">
                <Waffle value={forecast.sale_probability.days_7} label="7 days" />
                <Waffle value={forecast.sale_probability.days_30} label="30 days" />
                <Waffle value={forecast.sale_probability.days_90} label="90 days" />
              </div>
              <div style={{ marginTop: "calc(var(--cell) * 2)" }}>
                <h3 className="h-sm" style={{ marginBottom: "1rem" }}>And if it does go on sale, how deep?</h3>
                <TierChart probabilities={forecast.discount_tier_probabilities} regularMinor={149900} currency="INR" />
              </div>
              <p className="data">Illustrative forecast for a made-up game.</p>
            </div>
          </div>
        </section>

        <section id="call" className="scene wrap">
          <h2 className="display h-xl" data-in style={{ maxWidth: "16ch" }}>Three calls. Each one shows its working.</h2>
          <ul className="verdicts rule-list" style={{ marginTop: "calc(var(--cell) * 2)" }}>
            <li>
              <div className="verdict-word"><CellType lines={["BUY"]} build="scroll" /></div>
              <div className="stack">
                <p className="lede">The price is at or near the lowest we have recorded for your region, or waiting is unlikely to save you much.</p>
                <p className="body">Example reason: &ldquo;The current price is within 5% of the lowest recorded price in this region.&rdquo;</p>
              </div>
            </li>
            <li>
              <div className="verdict-word"><CellType lines={["WAIT"]} fills={["var(--pen)"]} build="scroll" /></div>
              <div className="stack">
                <p className="lede">A sale is likely inside the time you are willing to wait, and the expected saving is worth the wait.</p>
                <p className="body">Example reason: &ldquo;{forecast.explanation_factors[0].text}&rdquo;</p>
              </div>
            </li>
            <li>
              <div className="verdict-word"><CellType lines={["NEUTRAL"]} build="scroll" /></div>
              <div className="stack">
                <p className="lede">The evidence conflicts, there is too little history, or the benefit is marginal. We say so instead of guessing.</p>
                <p className="body">Example reason: &ldquo;There is too little sale history for this game, so the estimate relies on similar games.&rdquo;</p>
              </div>
            </li>
          </ul>
        </section>

        <section id="score" className="scene wrap" style={{ borderTop: "1px solid var(--ink)" }}>
          <div className="split flip">
            <div className="stack">
              <h2 className="display h-xl" data-in>We keep the score.</h2>
              <p className="lede">No forecast is ever edited after the fact. When its window closes it is marked against what actually happened, and the tally is yours to read.</p>
              <p className="body">A forecaster that says 70% should be right about seven times in ten. On this plot that is the blue diagonal. Squares off the line are where the estimates ran hot or cold.</p>
              <div className="hero-actions">
                <ButtonLink href={startHref}>Start a watchlist</ButtonLink>
              </div>
            </div>
            <figure style={{ maxWidth: "34rem" }}>
              <CalibrationChart bins={DEMO_CALIBRATION} />
              <figcaption className="data muted">Illustrative. The live scorecard in the app uses real, scored forecasts.</figcaption>
            </figure>
          </div>
        </section>

        <section className="closer gridded wrap">
          <div className="closer-type"><CellType as="h2" lines={["WATCH", "THE STEPS"]} build="scroll" /></div>
          <p className="lede" style={{ marginTop: "2rem" }}>
            Add the games you are eyeing, set a target price or a minimum discount, and get one alert when it matters.
          </p>
          <div className="hero-actions">
            <ButtonLink href={startHref}>Create your watchlist</ButtonLink>
          </div>
        </section>
      </main>

      <footer className="footer wrap">
        <Wordmark />
        <p>
          Forecasts and recommendations are statistical estimates based on past prices. They are not guarantees and not
          financial advice. Steam seasonal sale dates shown here are estimated from past years.
        </p>
        <AttributionNote />
        <nav aria-label="Footer">
          <Link className="link" href="/login">Sign in</Link>
          <Link className="link" href="/register">Create account</Link>
          <a className="link" href="#steps">How it works</a>
        </nav>
      </footer>
    </div>
  );
}
