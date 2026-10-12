"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { CellType } from "@/components/CellType";
import { Terrain, type TerrainControl } from "@/components/Terrain";
import { EASE, EASE_INOUT, gsap, prefersReducedMotion } from "@/lib/motion";

const SEEN_KEY = "s4c-intro-seen";

/** Runs before first paint so a returning visitor never sees the intro flash. */
const SKIP_SCRIPT = `try{if(sessionStorage.getItem("${SEEN_KEY}")||matchMedia("(prefers-reduced-motion: reduce)").matches)document.documentElement.dataset.intro="skip"}catch(e){}`;

type Phase = "loading" | "ready" | "leaving" | "gone";

/** Forget that the intro was seen, so it plays again. */
export function resetIntro() {
  try {
    sessionStorage.removeItem(SEEN_KEY);
  } catch {
    /* storage unavailable: the intro simply shows */
  }
  delete document.documentElement.dataset.intro;
}

function seen(): boolean {
  try {
    return sessionStorage.getItem(SEEN_KEY) !== null;
  } catch {
    return false;
  }
}

/**
 * The front sheet. The grid builds itself into a skyline while the page loads, then
 * the whole sheet is dragged up and away to uncover the landing page beneath it.
 * Shown once per browser session; skipped entirely under reduced motion.
 *
 * `onOpen` fires when the sheet is up, `onEnter` as it starts to leave (the page
 * underneath should come alive then) and `onDone` once it is gone.
 */
export function Intro({ onOpen, onEnter, onDone }: { onOpen: () => void; onEnter: () => void; onDone: () => void }) {
  const root = useRef<HTMLElement>(null);
  const gate = useRef<HTMLButtonElement>(null);
  const control = useRef<TerrainControl>({ build: 0, lift: 0 });
  const [phase, setPhase] = useState<Phase>("loading");
  const [pct, setPct] = useState(0);
  const phaseRef = useRef<Phase>("loading");
  phaseRef.current = phase;
  const callbacks = useRef({ onOpen, onEnter, onDone });
  callbacks.current = { onOpen, onEnter, onDone };

  const leave = useCallback((fast = false) => {
    const el = root.current;
    if (!el || phaseRef.current === "leaving" || phaseRef.current === "gone") return;
    phaseRef.current = "leaving";
    setPhase("leaving");
    try {
      sessionStorage.setItem(SEEN_KEY, "1");
    } catch {
      /* storage unavailable: it will show again next load */
    }
    gsap.killTweensOf([el, control.current]);
    const duration = fast ? 0.7 : 1;
    gsap.to(control.current, { lift: 1, build: 1, duration: duration * 0.55, ease: "power2.out" });
    gsap.delayedCall(duration * 0.3, () => callbacks.current.onEnter());
    gsap.to(el, {
      yPercent: -100,
      y: 0,
      duration,
      ease: EASE_INOUT,
      onComplete: () => {
        setPhase("gone");
        callbacks.current.onDone();
      },
    });
  }, []);

  // Decide before paint whether this visit gets the intro at all.
  useLayoutEffect(() => {
    if (seen() || prefersReducedMotion()) {
      phaseRef.current = "gone";
      setPhase("gone");
      callbacks.current.onEnter();
      callbacks.current.onDone();
      return;
    }
    callbacks.current.onOpen();
  }, []);

  // Loading: the skyline is the progress bar.
  useEffect(() => {
    if (phaseRef.current !== "loading") return;
    const progress = { value: 0 };
    let cancelled = false;
    const apply = () => {
      control.current.build = progress.value;
      setPct(Math.round(progress.value * 100));
    };
    const first = gsap.to(progress, { value: 0.88, duration: 2, ease: "power2.out", onUpdate: apply });
    const loaded = new Promise<void>((resolve) => {
      if (document.readyState === "complete") resolve();
      else window.addEventListener("load", () => resolve(), { once: true });
    });
    const patience = new Promise<void>((resolve) => setTimeout(resolve, 5000));
    Promise.all([first.then(), Promise.race([Promise.all([document.fonts.ready, loaded]), patience])]).then(() => {
      if (cancelled) return;
      gsap.to(progress, {
        value: 1,
        duration: 0.55,
        ease: "power2.inOut",
        onUpdate: apply,
        onComplete: () => {
          if (cancelled || phaseRef.current !== "loading") return;
          setPhase("ready");
          const el = root.current;
          if (el) control.current.pulse?.(el.clientWidth / 2, el.clientHeight * 0.55, 1.6);
        },
      });
    });
    return () => {
      cancelled = true;
      gsap.killTweensOf(progress);
    };
  }, []);

  // Ready: bring up the handle and listen for the swipe.
  useEffect(() => {
    const el = root.current;
    if (phase !== "ready" || !el) return;
    const ctx = gsap.context(() => {
      gsap.fromTo(".intro-gate", { yPercent: 100 }, { yPercent: 0, duration: 0.9, ease: EASE });
      gsap.fromTo(".intro-cue rect", { opacity: 0.18 }, { opacity: 1, duration: 0.45, ease: "none", stagger: { each: 0.14, from: "end", repeat: -1, yoyo: true } });
    }, el);
    gate.current?.focus({ preventScroll: true });

    let drag: { id: number; startY: number; lastY: number; lastT: number; velocity: number } | null = null;
    let wheel = 0;

    const onDown = (e: PointerEvent) => {
      if (phaseRef.current !== "ready" || (e.target as HTMLElement).closest(".intro-skip")) return;
      drag = { id: e.pointerId, startY: e.clientY, lastY: e.clientY, lastT: e.timeStamp, velocity: 0 };
      gsap.killTweensOf([el, control.current]);
    };
    const onMove = (e: PointerEvent) => {
      if (!drag || e.pointerId !== drag.id) return;
      const dt = Math.max(1, e.timeStamp - drag.lastT);
      drag.velocity = 0.7 * drag.velocity + 0.3 * ((e.clientY - drag.lastY) / dt);
      drag.lastY = e.clientY;
      drag.lastT = e.timeStamp;
      const dy = e.clientY - drag.startY;
      // Up follows the finger; down resists like a sheet pinned at the top.
      const y = dy < 0 ? dy : dy * 0.12;
      gsap.set(el, { y });
      control.current.lift = Math.min(1, Math.max(0, -dy / (el.clientHeight * 0.55)));
    };
    const onUp = (e: PointerEvent) => {
      if (!drag || e.pointerId !== drag.id) return;
      const dy = e.clientY - drag.startY;
      const flung = drag.velocity < -0.45;
      drag = null;
      if (dy < -el.clientHeight * 0.18 || (flung && dy < -24)) return leave(true);
      gsap.to(el, { y: 0, duration: 0.9, ease: "elastic.out(1, 0.55)" });
      gsap.to(control.current, { lift: 0, duration: 0.6, ease: "power2.out" });
    };
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      if (e.deltaY <= 0) return;
      wheel += e.deltaY;
      if (wheel > 50) leave();
    };
    const onKey = (e: KeyboardEvent) => {
      if (["ArrowDown", "ArrowUp", "PageDown"].includes(e.key)) {
        e.preventDefault();
        leave();
      }
    };
    el.addEventListener("pointerdown", onDown);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    el.addEventListener("wheel", onWheel, { passive: false });
    window.addEventListener("keydown", onKey);
    return () => {
      ctx.revert();
      el.removeEventListener("pointerdown", onDown);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      el.removeEventListener("wheel", onWheel);
      window.removeEventListener("keydown", onKey);
    };
  }, [phase, leave]);

  if (phase === "gone") return null;

  const ready = phase !== "loading";
  return (
    <section ref={root} className="intro gridded" data-phase={phase} aria-label="Steam4Caster intro">
      <script dangerouslySetInnerHTML={{ __html: SKIP_SCRIPT }} />
      <noscript>
        <style>{".intro{display:none}"}</style>
      </noscript>
      <Terrain control={control} />

      <div className="intro-bar">
        <span className="wordmark">
          <svg viewBox="0 0 5 5" width="18" height="18" aria-hidden="true">
            <path d="M0 4h1V3h1V2h1V1h1V0h1v5H0z" fill="currentColor" />
          </svg>
          <span>
            Steam<i>4</i>Caster
          </span>
        </span>
        <button type="button" className="chip intro-skip" onClick={() => leave(true)}>
          Skip intro
        </button>
      </div>

      <div className="intro-stage">
        <div className="intro-type" data-terrain-clear>
          <CellType lines={["STEAM4", "CASTER"]} fills={[undefined, "var(--pen)"]} delay={0.15} />
        </div>
        <p className="intro-line display h-md" data-terrain-clear>
          Steam discounts, forecast.
        </p>
      </div>

      <div className="intro-foot">
        <div className="hud intro-load" role="status" aria-live="polite">
          <div className="hud-row">
            <span className="h-sm">{ready ? "Grid ready" : "Laying out the grid"}</span>
            <span className="num hud-big">{pct}%</span>
          </div>
          <div className="intro-waffle" aria-hidden="true">
            {Array.from({ length: 100 }, (_, i) => (
              <span key={i} data-on={i < pct ? "" : undefined} />
            ))}
          </div>
          <div className="hud-row">
            <span className="data muted">The skyline is decoration, not price data</span>
          </div>
        </div>
      </div>

      <button
        ref={gate}
        type="button"
        className="intro-gate"
        onClick={() => leave()}
        disabled={!ready}
        aria-label="Enter Steam4Caster"
      >
        <svg className="intro-cue" viewBox="0 0 5 8" aria-hidden="true">
          {[0, 1, 2].flatMap((row) =>
            [
              [2, 0],
              [1, 1],
              [3, 1],
              [0, 2],
              [4, 2],
            ].map(([x, y]) => <rect key={`${row}-${x}-${y}`} x={x + 0.08} y={y + row * 2.5 + 0.08} width={0.84} height={0.84} data-row={row} />),
          )}
        </svg>
        <span className="display intro-gate-word">
          <span className="touch-only">Swipe up</span>
          <span className="mouse-only">Drag up or scroll</span>
        </span>
        <span className="data intro-gate-note">to the forecasts</span>
      </button>
    </section>
  );
}
