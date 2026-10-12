"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { CalibrationChart, TierChart, Waffle } from "@/components/Charts";
import { CellType } from "@/components/CellType";
import { DealsPane, type DealsState } from "@/components/DealsPane";
import { AlertTester, DemoVerdict, WaitPicker } from "@/components/DemoCall";
import { DemoPicker } from "@/components/DemoPicker";
import { HeroMorph } from "@/components/HeroMorph";
import { Intro, resetIntro } from "@/components/Intro";
import { PriceChart } from "@/components/PriceChart";
import { AttributionNote, ButtonLink, Wordmark } from "@/components/ui";
import { api, errorMessage, type Calibration, type Game, type Snapshot } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { DEMO_CALIBRATION, demoForecast, demoSeries } from "@/lib/demo";
import { dayMonth, DEMO_COUNTRIES, pct, probabilityWithin } from "@/lib/format";
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

const REGION_KEY = "s4c.region";
const isDemoRegion = (code: string | null | undefined): code is string => DEMO_COUNTRIES.some((c) => c.code === code);

const QUALITY: Record<string, string> = {
  GOOD: "Plenty of price history behind this forecast",
  LIMITED: "Limited price history, so the forecast leans on similar games",
  INSUFFICIENT: "Too little price history for this game: treat this as a rough guide",
};

export function Landing() {
  const { user } = useAuth();
  const root = useRef<HTMLDivElement>(null);
  const reveal = useRef({ value: 0 });
  // Authored stand-in, used only while real data is loading or the server is unreachable.
  const sample = useMemo(() => ({ ...demoSeries(), forecast: demoForecast() }), []);
  const [country, setCountry] = useState("IN");
  const [deals, setDeals] = useState<DealsState>({ status: "loading" });
  const [picked, setPicked] = useState<Game | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [snapBusy, setSnapBusy] = useState(false);
  const [snapError, setSnapError] = useState<string | null>(null);
  const [calibration, setCalibration] = useState<Calibration | "unavailable" | null>(null);
  const [wait, setWait] = useState(30);
  const pickedRef = useRef<Game | null>(null);
  pickedRef.current = picked;
  const regionChosen = useRef(false);
  const startHref = user ? "/app" : "/register";

  const chooseCountry = (code: string) => {
    regionChosen.current = true;
    setCountry(code);
    try {
      window.localStorage.setItem(REGION_KEY, code);
    } catch {
      /* storage unavailable: the choice lasts for this visit */
    }
  };

  // Region: the visitor's last choice, else their account's country, else India.
  useEffect(() => {
    let stored: string | null = null;
    try {
      stored = window.localStorage.getItem(REGION_KEY);
    } catch {
      stored = null;
    }
    if (isDemoRegion(stored)) {
      regionChosen.current = true;
      setCountry(stored);
    }
  }, []);
  useEffect(() => {
    if (!regionChosen.current && isDemoRegion(user?.default_country)) setCountry(user.default_country);
  }, [user]);

  useEffect(() => {
    let live = true;
    setDeals({ status: "loading" });
    api
      .publicDeals(country)
      .then((result) => {
        if (!live) return;
        setDeals({ status: "ready", items: result.items, minDiscount: result.min_discount_pct, attribution: result.attribution });
        // The demo only covers this week's deals: open on the first, and move there
        // if the game being shown is not on this region's list.
        const current = pickedRef.current;
        if (!current || !result.items.some((d) => d.game.id === current.id)) {
            setPicked(result.items[0]?.game ?? null);
            setSnapshot(null);
        }
      })
      .catch((err) => live && setDeals({ status: "error", message: errorMessage(err) }));
    return () => {
      live = false;
    };
  }, [country]);

  const pickedId = picked?.id;
  useEffect(() => {
    if (!pickedId) return;
    let live = true;
    setSnapBusy(true);
    setSnapError(null);
    api
      .snapshot(pickedId, country, wait)
      .then((result) => live && setSnapshot(result))
      .catch((err) => live && setSnapError(errorMessage(err)))
      .finally(() => live && setSnapBusy(false));
    return () => {
      live = false;
    };
  }, [pickedId, country, wait]);

  useEffect(() => {
    api.publicCalibration().then(setCalibration).catch(() => setCalibration("unavailable"));
  }, []);

  const pick = (game: Game) => {
    setPicked(game);
    document.getElementById("try")?.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "start" });
  };

  const real = snapshot !== null;
  const history = snapshot ? snapshot.history : sample.history;
  const sales = snapshot ? snapshot.sale_events : sample.sales;
  const forecast = snapshot ? snapshot.forecast : sample.forecast;
  const currency = snapshot ? (snapshot.forecast.currency ?? snapshot.price?.currency ?? "USD") : "INR";
  const lowMinor = snapshot ? (snapshot.price?.historical_low?.amount_minor ?? null) : 50966;
  const subject = snapshot ? snapshot.game.title : "a made-up game";
  const region = DEMO_COUNTRIES.find((c) => c.code === country)?.name ?? country;
  const probs = forecast.currently_on_sale ? forecast.new_sale_probability : forecast.sale_probability;
  const chance = probabilityWithin(forecast.new_sale_probability, wait);

  // Loaded content changes the page height above and below the pinned scene.
  const dealsStatus = deals.status;
  const snapshotId = snapshot?.game.id;
  useEffect(() => {
    ScrollTrigger.refresh();
  }, [dealsStatus, snapshotId]);
  // The intro sheet covers the page first: "open" while it is up, "entered" once it lifts.
  const [gate, setGate] = useState<"pending" | "open" | "entered">("pending");
  const [introRun, setIntroRun] = useState(0);
  const entered = gate === "entered";

  const replayIntro = () => {
    resetIntro();
    window.scrollTo({ top: 0, behavior: "instant" });
    setGate("pending");
    setIntroRun((n) => n + 1);
  };

  // The hero copy rises as the sheet leaves, not underneath it.
  useGSAP(
    () => {
      if (prefersReducedMotion()) return;
      if (!entered) {
        gsap.set("[data-rise]", { y: 28, opacity: 0 });
        return;
      }
      gsap.to("[data-rise]", { y: 0, opacity: 1, duration: 1.1, ease: EASE, stagger: 0.09, delay: 0.55 });
    },
    { scope: root, dependencies: [entered] },
  );

  useGSAP(
    () => {
      if (prefersReducedMotion()) {
        reveal.current.value = 1;
        return;
      }

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
    <>
    <Intro
      key={introRun}
      onOpen={() => setGate("open")}
      onEnter={() => setGate("entered")}
      onDone={() => ScrollTrigger.refresh()}
    />
    <div ref={root} inert={gate === "open"}>
      <HeroMorph history={history} reveal={reveal} />
      <header className="nav">
        <Wordmark />
        <nav className="nav-links" aria-label="Sections">
          <a href="#steps">How it reads prices</a>
          <a href="#try">Try it</a>
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
        <DealsPane state={deals} country={country} onCountry={chooseCountry} selectedId={picked?.id ?? null} onPick={pick} />

        <section className="hero wrap" aria-labelledby="hero-title">
          <HighlighterField />
          <CellType as="h1" className="hero-type" lines={["BUY NOW", "OR WAIT?"]} fills={[undefined, "var(--pen)"]} build={entered ? "mount" : "none"} />
          <CellType as="div" className="hero-type-narrow" lines={["BUY", "NOW", "OR", "WAIT?"]} fills={[undefined, undefined, undefined, "var(--pen)"]} build={entered ? "mount" : "none"} />
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
            {history.length > 1 ? (
              <PriceChart history={history} sales={sales} forecast={forecast} currency={currency} lowMinor={lowMinor} revealRef={reveal} height={400} initialRange="2y" />
            ) : (
              <p className="notice">Steam has no price history for {subject} in {region}. Pick another deal or region below.</p>
            )}
            <p className="data muted" style={{ marginTop: "0.25rem" }}>
              {real ? (
                <>
                  Real Steam price history for {subject} in {region}. <a className="link" href="#try">Pick another deal</a>
                </>
              ) : (
                "Illustrative data for a made-up game while real prices load."
              )}
            </p>
          </div>
        </section>

        <section id="try" className="scene wrap" style={{ borderTop: "1px solid var(--ink)" }}>
          <h2 className="display h-xl" data-in style={{ maxWidth: "16ch" }}>Try it on this week&rsquo;s deals.</h2>
          <p className="lede" style={{ marginBlock: "1rem calc(var(--cell) * 1.5)" }}>
            Pick one of the ten. The price chart above and everything below redraw from its real history in your region. An account lets you look up any Steam game.
          </p>
          <DemoPicker
            deals={deals.status === "ready" ? deals.items : []}
            snapshot={snapshot}
            picked={picked}
            loading={snapBusy || deals.status === "loading"}
            error={snapError}
            country={country}
            onCountry={chooseCountry}
            onPick={setPicked}
            startHref={startHref}
          />
        </section>

        <section id="forecast" className="scene blueprint gridded wrap">
          <div className="split">
            <div className="stack sticky-col">
              <h2 className="display h-lg" data-in>One hundred cells per horizon.</h2>
              <p className="lede">
                Each grid is a hundred cells. Filled cells are the estimated chance {forecast.currently_on_sale ? "another sale" : "a sale"} starts inside that window for {subject}. No dates pulled from thin air.
              </p>
              <div style={{ marginTop: "2rem" }}>
                <WaitPicker wait={wait} onWait={setWait} id="forecast-wait" />
              </div>
              <div style={{ width: "min(100%, 15rem)" }}>
                <CellType lines={[`${Math.round(chance * 100)}%`]} fills={["var(--hi)"]} build="mount" />
              </div>
              <p className="body">Estimated chance of a new sale within {wait} days, for {subject}.</p>
            </div>
            <div className="stack">
              <div className="waffles">
                <Waffle value={probs.days_7} label="7 days" />
                <Waffle value={probs.days_30} label="30 days" />
                <Waffle value={probs.days_90} label="90 days" />
              </div>
              <div style={{ marginTop: "calc(var(--cell) * 2)" }}>
                <h3 className="h-sm" style={{ marginBottom: "1rem" }}>And if it does go on sale, how deep?</h3>
                <TierChart probabilities={forecast.discount_tier_probabilities} regularMinor={forecast.regular_price?.amount_minor ?? null} currency={currency} />
              </div>
              <p className="data">
                {real
                  ? `${QUALITY[forecast.data_quality] ?? ""} · made by ${forecast.method === "ML" ? "the trained model" : "the rule baseline"} · confidence ${pct(forecast.confidence_score)} · an estimate, not a promise`
                  : "Illustrative forecast for a made-up game."}
              </p>
            </div>
          </div>
        </section>

        <section id="call" className="scene wrap">
          <h2 className="display h-xl" data-in style={{ maxWidth: "16ch" }}>Three calls. Each one shows its working.</h2>
          {snapshot ? (
            <div className="call-live">
              <div className="stack">
                <DemoVerdict snapshot={snapshot} wait={wait} onWait={setWait} loading={snapBusy} />
                {snapshot.forecast.explanation_factors.length ? (
                  <div>
                    <h3 className="h-sm">The working</h3>
                    <ul className="rule-list call-working">
                      {snapshot.forecast.explanation_factors.map((factor) => (
                        <li key={factor.code}>
                          <span className="tag" data-tone={factor.direction}>{factor.direction}</span>
                          <span className="body">{factor.text}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </div>
              <AlertTester snapshot={snapshot} startHref={startHref} />
            </div>
          ) : null}
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
                <p className="body">Example reason: &ldquo;{sample.forecast.explanation_factors[0].text}&rdquo;</p>
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
            {calibration && calibration !== "unavailable" && calibration.n === 0 ? (
              <div className="empty" style={{ maxWidth: "34rem" }}>
                <p className="h-sm">No forecasts have been scored yet</p>
                <p className="body">
                  A forecast is scored once its window closes, so the first results appear a week after it was made. Nothing on this plot is filled in by hand.
                </p>
              </div>
            ) : (
              <figure style={{ maxWidth: "34rem" }}>
                <CalibrationChart bins={calibration && calibration !== "unavailable" ? calibration.bins : DEMO_CALIBRATION} />
                <figcaption className="data muted">
                  {calibration && calibration !== "unavailable"
                    ? `${calibration.n} real forecasts scored against what happened, 30-day window.`
                    : "Illustrative while the real scorecard loads."}
                </figcaption>
              </figure>
            )}
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
          <button type="button" className="link link-button" onClick={replayIntro}>Replay intro</button>
        </nav>
      </footer>
    </div>
    </>
  );
}
