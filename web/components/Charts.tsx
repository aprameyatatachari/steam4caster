"use client";

import { useRef, useState } from "react";

import type { CalibrationBin } from "@/lib/api";
import { minor, pct, TIER_LABEL, TIER_ORDER, TIER_RANGE } from "@/lib/format";
import { EASE, gsap, prefersReducedMotion, ScrollTrigger, useGSAP } from "@/lib/motion";

/* -------------------------------------------------------------------------
   Waffle: one hundred cells, filled cells are the estimated chance.
   ------------------------------------------------------------------------- */

export function Waffle({ value, label, sublabel }: { value: number; label: string; sublabel?: string }) {
  const ref = useRef<SVGSVGElement>(null);
  const filled = Math.round(Math.max(0, Math.min(1, value)) * 100);
  const previous = useRef(0);
  const entered = useRef(false);

  useGSAP(
    () => {
      const svg = ref.current;
      if (!svg) return;
      const cells = Array.from(svg.querySelectorAll<SVGRectElement>("[data-i]"));
      const paint = (from: number, to: number, animate: boolean) => {
        const lo = Math.min(from, to);
        const hi = Math.max(from, to);
        const changed = cells.slice(lo, hi);
        const on = to > from;
        if (!animate || prefersReducedMotion()) {
          cells.forEach((c, i) => gsap.set(c, { scale: i < to ? 1 : 0, opacity: i < to ? 1 : 0 }));
          return;
        }
        gsap.to(on ? changed : changed.reverse(), {
          scale: on ? 1 : 0,
          opacity: on ? 1 : 0,
          transformOrigin: "50% 50%",
          duration: 0.5,
          ease: EASE,
          stagger: Math.min(0.012, 0.6 / Math.max(1, changed.length)),
          overwrite: true,
        });
      };
      if (!entered.current) {
        gsap.set(cells, { scale: 0, opacity: 0, transformOrigin: "50% 50%" });
        const trigger = ScrollTrigger.create({
          trigger: svg,
          start: "top 90%",
          once: true,
          onEnter: () => {
            entered.current = true;
            paint(0, filled, true);
            previous.current = filled;
          },
        });
        return () => trigger.kill();
      }
      paint(previous.current, filled, true);
      previous.current = filled;
    },
    { dependencies: [filled], scope: ref },
  );

  return (
    <figure>
      <svg ref={ref} viewBox="0 0 10 10" role="img" aria-label={`${label}: estimated ${filled}% chance`} style={{ width: "100%", height: "auto" }}>
        {Array.from({ length: 100 }, (_, i) => {
          // Fill from the bottom-left corner, row by row, like shading graph paper.
          const col = i % 10;
          const row = 9 - Math.floor(i / 10);
          return (
            <g key={i}>
              <rect x={col + 0.06} y={row + 0.06} width={0.88} height={0.88} fill="none" stroke="var(--ink)" strokeWidth={0.03} opacity={0.35} />
              <rect data-i={i} x={col + 0.06} y={row + 0.06} width={0.88} height={0.88} fill="var(--hi)" />
            </g>
          );
        })}
      </svg>
      <figcaption className="waffle-cap">
        <span className="num">{filled}%</span>
        <span className="data" style={{ textAlign: "right" }}>
          {label}
          {sublabel ? <><br />{sublabel}</> : null}
        </span>
      </figcaption>
    </figure>
  );
}

/* -------------------------------------------------------------------------
   Tier chart: discount depth as stacked cells. Hover or focus a column.
   ------------------------------------------------------------------------- */

const TIER_ROWS = 10;
const ROW_H = 0.55;
const COL_W = 3;

export function TierChart({
  probabilities,
  regularMinor,
  currency,
}: {
  probabilities: Record<string, number>;
  regularMinor?: number | null;
  currency?: string | null;
}) {
  const ref = useRef<SVGSVGElement>(null);
  const top = TIER_ORDER.reduce((best, code) => ((probabilities[code] ?? 0) > (probabilities[best] ?? 0) ? code : best), TIER_ORDER[0]);
  const [active, setActive] = useState<string>(top);
  const peak = Math.max(0.01, ...TIER_ORDER.map((code) => probabilities[code] ?? 0));
  // Each cell is an equal slice of probability, so the tallest column fills the plot.
  const perCell = peak / TIER_ROWS;
  const height = TIER_ROWS * ROW_H;

  useGSAP(
    () => {
      if (!ref.current || prefersReducedMotion()) return;
      gsap.from(ref.current.querySelectorAll("[data-cell]"), {
        scaleY: 0,
        opacity: 0,
        transformOrigin: "50% 100%",
        duration: 0.5,
        ease: EASE,
        stagger: { amount: 0.6, from: "start" },
        scrollTrigger: { trigger: ref.current, start: "top 90%", once: true },
      });
    },
    { dependencies: [JSON.stringify(probabilities)], scope: ref },
  );

  const range = TIER_RANGE[active];
  const p = probabilities[active] ?? 0;
  return (
    <div>
      <svg ref={ref} viewBox={`0 0 ${TIER_ORDER.length * COL_W} ${height}`} style={{ width: "100%", height: "auto", display: "block" }} aria-hidden="true">
        {TIER_ORDER.map((code, c) => {
          const count = Math.round((probabilities[code] ?? 0) / perCell);
          const isActive = code === active;
          return (
            <g key={code} onMouseEnter={() => setActive(code)} onClick={() => setActive(code)} style={{ cursor: "pointer" }}>
              <rect x={c * COL_W} y={0} width={COL_W} height={height} fill="transparent" />
              {Array.from({ length: TIER_ROWS }, (_, r) => {
                const y = height - (r + 1) * ROW_H + 0.05;
                return (
                  <g key={r}>
                    <rect x={c * COL_W + 0.2} y={y} width={COL_W - 0.4} height={ROW_H - 0.1} fill="none" stroke="var(--ink)" strokeWidth={0.02} opacity={0.3} />
                    {r < count ? (
                      <rect data-cell x={c * COL_W + 0.2} y={y} width={COL_W - 0.4} height={ROW_H - 0.1} fill={isActive ? "var(--hi)" : "var(--ink)"} style={{ transition: "fill 0.25s" }} />
                    ) : null}
                  </g>
                );
              })}
            </g>
          );
        })}
      </svg>
      <div className="tier-keys" role="group" aria-label="Estimated discount depth if a sale happens">
        {TIER_ORDER.map((code) => (
          <button
            key={code}
            type="button"
            aria-pressed={code === active}
            aria-label={`${TIER_LABEL[code]} off: ${pct(probabilities[code] ?? 0)} of estimated sales`}
            onMouseEnter={() => setActive(code)}
            onFocus={() => setActive(code)}
            onClick={() => setActive(code)}
          >
            {TIER_LABEL[code].replace("under ", "<").replace("%", "")}
          </button>
        ))}
      </div>
      <p style={{ marginTop: "0.75rem" }} aria-live="polite">
        <span className="num" style={{ fontSize: "1.375rem" }}>{pct(p)}</span>{" "}
        <span className="body">
          of estimated sales land at <strong>{TIER_LABEL[active]} off</strong>
          {regularMinor && currency
            ? `, about ${minor(Math.round((regularMinor * (100 - range[1])) / 100), currency, { compact: true })} to ${minor(Math.round((regularMinor * (100 - range[0])) / 100), currency, { compact: true })}.`
            : "."}
        </span>
      </p>
    </div>
  );
}

/* -------------------------------------------------------------------------
   Calibration: predicted probability against what happened. The diagonal is perfect.
   ------------------------------------------------------------------------- */

export function CalibrationChart({ bins }: { bins: CalibrationBin[] }) {
  const ref = useRef<SVGSVGElement>(null);
  const [active, setActive] = useState<number | null>(null);
  const maxCount = Math.max(1, ...bins.map((b) => b.count));
  const S = 100;
  const pad = { l: 12, b: 10, t: 3, r: 3 };
  const w = S - pad.l - pad.r;
  const h = S - pad.t - pad.b;
  const px = (v: number) => pad.l + v * w;
  const py = (v: number) => pad.t + (1 - v) * h;

  useGSAP(
    () => {
      if (!ref.current || prefersReducedMotion()) return;
      const tl = gsap.timeline({ scrollTrigger: { trigger: ref.current, start: "top 85%", once: true } });
      tl.from(ref.current.querySelector("[data-diagonal]"), { attr: { x2: px(0), y2: py(0) }, duration: 1.2, ease: "expo.inOut" }).from(
        ref.current.querySelectorAll("[data-bin]"),
        { scale: 0, transformOrigin: "50% 50%", duration: 0.6, ease: EASE, stagger: 0.05 },
        0.45,
      );
    },
    { dependencies: [bins.length], scope: ref },
  );

  const bin = active !== null ? bins[active] : null;
  return (
    <div>
      <svg ref={ref} viewBox={`0 0 ${S} ${S}`} style={{ width: "100%", height: "auto" }} role="group" aria-label="Calibration: predicted probability against observed sale rate">
        {Array.from({ length: 11 }, (_, i) => (
          <g key={i}>
            <line x1={px(i / 10)} x2={px(i / 10)} y1={py(0)} y2={py(1)} stroke={i % 5 === 0 ? "var(--grid-major)" : "var(--grid)"} strokeWidth={0.25} />
            <line x1={px(0)} x2={px(1)} y1={py(i / 10)} y2={py(i / 10)} stroke={i % 5 === 0 ? "var(--grid-major)" : "var(--grid)"} strokeWidth={0.25} />
          </g>
        ))}
        <line x1={px(0)} x2={px(1)} y1={py(0)} y2={py(0)} stroke="var(--ink)" strokeWidth={0.3} />
        <line x1={px(0)} x2={px(0)} y1={py(0)} y2={py(1)} stroke="var(--ink)" strokeWidth={0.3} />
        <line data-diagonal x1={px(0)} y1={py(0)} x2={px(1)} y2={py(1)} stroke="var(--pen)" strokeWidth={0.5} />
        {[0, 0.5, 1].map((v) => (
          <g key={v} style={{ fontFamily: "var(--font-data)", fontSize: 2.6, fill: "var(--ink-3)" }}>
            <text x={px(v)} y={S - 3.5} textAnchor={v === 0 ? "start" : v === 1 ? "end" : "middle"}>{pct(v)}</text>
            <text x={pad.l - 1.5} y={py(v) + (v === 1 ? 2.4 : v === 0 ? 0 : 1)} textAnchor="end">{pct(v)}</text>
          </g>
        ))}
        {bins.map((b, i) => {
          const size = 1.6 + 4.4 * Math.sqrt(b.count / maxCount);
          return (
            <g
              key={i}
              tabIndex={0}
              role="button"
              aria-label={`Forecasts near ${pct(b.mean_predicted)}: a sale happened ${pct(b.observed_rate)} of the time, ${b.count} forecasts`}
              onMouseEnter={() => setActive(i)}
              onMouseLeave={() => setActive(null)}
              onFocus={() => setActive(i)}
              onBlur={() => setActive(null)}
              style={{ outline: "none", cursor: "pointer" }}
            >
              <line x1={px(b.mean_predicted)} x2={px(b.mean_predicted)} y1={py(b.mean_predicted)} y2={py(b.observed_rate)} stroke="var(--ink)" strokeWidth={0.3} />
              <rect x={px(b.mean_predicted) - 4} y={py(b.observed_rate) - 4} width={8} height={8} fill="transparent" />
              <rect
                data-bin
                x={px(b.mean_predicted) - size / 2}
                y={py(b.observed_rate) - size / 2}
                width={size}
                height={size}
                fill={active === i ? "var(--hi)" : "var(--ink)"}
                stroke="var(--ink)"
                strokeWidth={0.4}
               
              />
            </g>
          );
        })}
      </svg>
      <p className="data" style={{ marginTop: "0.5rem", minHeight: "2.6em" }} aria-live="polite">
        {bin
          ? `Forecast ≈ ${pct(bin.mean_predicted)} · sale happened ${pct(bin.observed_rate)} · ${bin.count} forecasts`
          : "Across: what was forecast · Up: what happened · Blue diagonal: perfect calibration · Hover a square"}
      </p>
    </div>
  );
}
