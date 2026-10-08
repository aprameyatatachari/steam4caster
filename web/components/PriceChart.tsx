"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import type { Forecast, HistoryPoint, SaleEvent } from "@/lib/api";
import { date, dayMonth, daysBetween, minor } from "@/lib/format";
import { EASE, gsap, prefersReducedMotion, useGSAP } from "@/lib/motion";

const DAY = 86_400_000;
const M = { top: 18, right: 14, bottom: 30, left: 58 };
const RANGES: { key: string; label: string; days: number | null }[] = [
  { key: "1y", label: "1Y", days: 365 },
  { key: "2y", label: "2Y", days: 730 },
  { key: "all", label: "All", days: null },
];

type Step = { t0: number; t1: number; price: number; regular: number; cut: number };

type Props = {
  history: HistoryPoint[];
  sales: SaleEvent[];
  forecast?: Forecast | null;
  currency: string;
  lowMinor?: number | null;
  height?: number;
  /** Draw-on progress 0..1 controlled from outside (scroll scenes). Omit to self-animate. */
  revealRef?: React.RefObject<{ value: number } | null>;
  initialRange?: string;
};

function niceTicks(max: number, count = 4): number[] {
  const raw = max / count;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= raw) ?? raw;
  const ticks: number[] = [];
  for (let v = 0; v <= max + 1; v += step) ticks.push(v);
  return ticks;
}

/**
 * Interactive step chart of a regional price history with the forecast drawn as an
 * estimate to the right of today. Highlighted planes are the money a sale took off.
 * Hover or arrow-key through steps, drag to zoom, double-click to reset.
 */
export function PriceChart({
  history,
  sales,
  forecast,
  currency,
  lowMinor,
  height = 380,
  revealRef,
  initialRange = "all",
}: Props) {
  const uid = useId().replace(/:/g, "");
  const boxRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [width, setWidth] = useState(900);
  // The clock is read after mount so server and client markup agree.
  const [clock, setClock] = useState<number | null>(null);
  useEffect(() => setClock(Date.now()), []);
  const now = clock ?? 0;
  const [range, setRange] = useState(initialRange);
  const [hover, setHover] = useState<number | null>(null);
  const [brush, setBrush] = useState<{ a: number; b: number } | null>(null);
  const dragRef = useRef<{ x: number; moved: boolean } | null>(null);

  const steps: Step[] = useMemo(() => {
    const sorted = [...history].sort((a, b) => +new Date(a.observed_at) - +new Date(b.observed_at));
    return sorted.map((p, i) => ({
      t0: +new Date(p.observed_at),
      t1: i + 1 < sorted.length ? +new Date(sorted[i + 1].observed_at) : now,
      price: p.price.amount_minor,
      regular: p.regular.amount_minor,
      cut: p.discount_pct,
    }));
  }, [history, now]);

  const first = steps[0]?.t0 ?? now - 365 * DAY;
  const future = now + 100 * DAY;
  const fullDomain = useMemo<[number, number]>(() => [first, future], [first, future]);
  const [domain, setDomain] = useState<[number, number]>(fullDomain);
  const domainRef = useRef(domain);
  domainRef.current = domain;

  const tweenDomain = useCallback((target: [number, number]) => {
    if (prefersReducedMotion()) {
      setDomain(target);
      return;
    }
    const proxy = { a: domainRef.current[0], b: domainRef.current[1] };
    gsap.to(proxy, {
      a: target[0],
      b: target[1],
      duration: 0.9,
      ease: "expo.inOut",
      onUpdate: () => setDomain([proxy.a, proxy.b]),
    });
  }, []);

  const settled = useRef(false);
  useEffect(() => {
    const preset = RANGES.find((r) => r.key === range);
    if (!preset || clock === null) return;
    const target: [number, number] = [preset.days ? Math.max(first, now - preset.days * DAY) : first, future];
    if (settled.current) tweenDomain(target);
    else setDomain(target); // first paint: no tween from an unset clock
    settled.current = true;
  }, [range, first, future, now, clock, tweenDomain]);

  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(280, entry.contentRect.width)));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const narrow = width < 520;
  const h = narrow ? Math.min(height, 300) : height;
  const left = narrow ? 46 : M.left;
  const innerW = width - left - M.right;
  const innerH = h - M.top - M.bottom;
  const maxRegular = Math.max(1, ...steps.map((s) => s.regular));
  const yMax = maxRegular * 1.1;
  const x = (t: number) => left + ((t - domain[0]) / (domain[1] - domain[0])) * innerW;
  const y = (v: number) => M.top + innerH - (v / yMax) * innerH;
  const invX = (px: number) => domain[0] + ((px - left) / innerW) * (domain[1] - domain[0]);

  const linePath = useMemo(() => {
    if (!steps.length) return "";
    let d = `M${x(steps[0].t0)} ${y(steps[0].price)}`;
    for (const s of steps) d += `V${y(s.price)}H${x(s.t1)}`;
    return d;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [steps, domain, width, h]);

  // Year and quarter gridlines keep the chart on the same rhythm as the page grid.
  const timeTicks = useMemo(() => {
    const spanDays = (domain[1] - domain[0]) / DAY;
    const monthsStep = spanDays > 1500 ? 12 : spanDays > 700 ? 6 : spanDays > 300 ? 3 : 1;
    const ticks: { t: number; label: string; major: boolean }[] = [];
    const start = new Date(domain[0]);
    const cursor = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 1));
    while (+cursor < domain[1]) {
      const month = cursor.getUTCMonth();
      if (month % monthsStep === 0) {
        ticks.push({
          t: +cursor,
          major: month === 0,
          label:
            month === 0 || monthsStep < 12
              ? new Intl.DateTimeFormat(undefined, monthsStep >= 12 || month === 0 ? { year: "numeric" } : { month: "short" }).format(cursor)
              : "",
        });
      }
      cursor.setUTCMonth(month + 1);
    }
    const maxLabels = Math.max(3, Math.floor(innerW / 64));
    const every = Math.ceil(ticks.length / maxLabels);
    return ticks.map((tick, i) => ({ ...tick, label: i % every === 0 ? tick.label : "" }));
  }, [domain, innerW]);

  const priceTicks = useMemo(() => niceTicks(yMax, narrow ? 3 : 4).filter((v) => v <= yMax), [yMax, narrow]);

  // --- forecast geometry ---------------------------------------------------
  const fc = useMemo(() => {
    if (!forecast?.predicted_sale_price) return null;
    const interval = forecast.predicted_sale_price;
    const t0 = forecast.likely_window ? +new Date(forecast.likely_window.start) : now + DAY;
    const t1 = forecast.likely_window ? +new Date(forecast.likely_window.end) + DAY : now + 90 * DAY;
    return {
      t0,
      t1,
      windowed: Boolean(forecast.likely_window),
      lower: interval.lower.amount_minor,
      median: interval.median.amount_minor,
      upper: interval.upper.amount_minor,
    };
  }, [forecast, now]);

  // --- draw-on animation ---------------------------------------------------
  const clipRef = useRef<SVGRectElement>(null);
  const applyReveal = useCallback(
    (value: number) => {
      const rect = clipRef.current;
      if (!rect) return;
      rect.setAttribute("width", String(Math.max(0, (innerW + M.right + 4) * value)));
      const fx = boxRef.current?.querySelector<SVGGElement>("[data-forecast]");
      if (fx) fx.style.opacity = String(Math.max(0, Math.min(1, (value - 0.82) / 0.18)));
    },
    [innerW],
  );

  useGSAP(
    () => {
      if (revealRef) {
        const tick = () => applyReveal(revealRef.current?.value ?? 1);
        gsap.ticker.add(tick);
        return () => gsap.ticker.remove(tick);
      }
      if (prefersReducedMotion() || !steps.length) {
        applyReveal(1);
        return;
      }
      const proxy = { value: 0 };
      gsap.to(proxy, { value: 1, duration: 1.8, ease: EASE, delay: 0.15, onUpdate: () => applyReveal(proxy.value) });
    },
    { dependencies: [steps.length > 0, applyReveal, revealRef, clock !== null], scope: boxRef },
  );

  // --- interaction ---------------------------------------------------------
  const pick = (clientX: number) => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return null;
    const t = invX(clientX - rect.left);
    if (t > now) return fc ? -1 : steps.length - 1;
    let lo = 0;
    let hi = steps.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (steps[mid].t0 <= t) lo = mid;
      else hi = mid - 1;
    }
    return lo;
  };

  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (e.pointerType !== "mouse" || e.button !== 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    dragRef.current = { x: e.clientX - rect.left, moved: false };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const drag = dragRef.current;
    if (drag) {
      if (Math.abs(px - drag.x) > 6) drag.moved = true;
      if (drag.moved) {
        setBrush({ a: Math.max(left, Math.min(drag.x, px)), b: Math.min(left + innerW, Math.max(drag.x, px)) });
        setHover(null);
        return;
      }
    }
    setHover(pick(e.clientX));
  };
  const onPointerUp = () => {
    const drag = dragRef.current;
    dragRef.current = null;
    if (drag?.moved && brush && brush.b - brush.a > 12) {
      setRange("custom");
      tweenDomain([invX(brush.a), invX(brush.b)]);
    }
    setBrush(null);
  };
  const onKeyDown = (e: React.KeyboardEvent<SVGSVGElement>) => {
    if (!steps.length) return;
    const order = fc ? [...steps.keys(), -1] : [...steps.keys()];
    const at = hover === null ? order.length - 1 : order.indexOf(hover);
    if (e.key === "ArrowLeft") setHover(order[Math.max(0, at - 1)]);
    else if (e.key === "ArrowRight") setHover(order[Math.min(order.length - 1, at + 1)]);
    else if (e.key === "Home") setHover(order[0]);
    else if (e.key === "End") setHover(order[order.length - 1]);
    else if (e.key === "Escape") setHover(null);
    else return;
    e.preventDefault();
  };

  const active = hover !== null && hover >= 0 ? steps[hover] : null;
  const onForecast = hover === -1 && fc;
  const focusX = active ? Math.min(left + innerW, Math.max(left, x((Math.max(active.t0, domain[0]) + Math.min(active.t1, domain[1])) / 2))) : onForecast ? x((fc.t0 + fc.t1) / 2) : 0;
  const readoutLeft = Math.min(Math.max(8, focusX + 14), Math.max(8, width - 200));

  if (clock === null) return <div className="chart skeleton" ref={boxRef} style={{ minHeight: height }} aria-hidden="true" />;

  if (!steps.length) {
    return (
      <div className="empty">
        <p className="h-sm">No price history yet</p>
        <p className="body">History for this region has not been recorded. It appears here once the first price is stored.</p>
      </div>
    );
  }

  return (
    <div className="chart" ref={boxRef}>
      <div className="chart-bar">
        <div className="legend data">
          <span><i style={{ background: "var(--hi)" }} />Discount taken off</span>
          <span><i style={{ backgroundImage: "repeating-linear-gradient(-45deg, transparent 0 3px, var(--pen) 3px 4px)" }} />Estimated next sale</span>
          {lowMinor ? <span><i style={{ border: 0, borderTop: "1px dashed var(--pen)", height: 0 }} />Lowest recorded</span> : null}
        </div>
        <div className="seg" role="group" aria-label="Time range">
          {RANGES.map((r) => (
            <button key={r.key} type="button" aria-pressed={range === r.key} onClick={() => setRange(r.key)}>
              {r.label}
            </button>
          ))}
        </div>
      </div>

      <svg
        ref={svgRef}
        width={width}
        height={h}
        viewBox={`0 0 ${width} ${h}`}
        role="application"
        tabIndex={0}
        data-plot={`${left},${M.top},${innerW},${innerH}`}
        data-domain={`${domain[0]},${domain[1]}`}
        data-ymax={yMax}
        data-now={now}
        aria-label={`Price history step chart in ${currency}. Use the left and right arrow keys to move between price changes.`}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={() => !dragRef.current && setHover(null)}
        onDoubleClick={() => setRange("all")}
        onKeyDown={onKeyDown}
        onBlur={() => setHover(null)}
      >
        <defs>
          <clipPath id={`plot-${uid}`}>
            <rect x={left} y={0} width={innerW + M.right} height={h} />
          </clipPath>
          <clipPath id={`reveal-${uid}`}>
            <rect ref={clipRef} x={left - 2} y={0} width={innerW + M.right + 4} height={h} />
          </clipPath>
          <pattern id={`hatch-${uid}`} width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)">
            <line x1="0" y1="0" x2="0" y2="7" stroke="var(--pen)" strokeWidth="1.4" />
          </pattern>
        </defs>

        <g className="axis">
          {priceTicks.map((v) => (
            <g key={v}>
              <line x1={left} x2={left + innerW} y1={y(v)} y2={y(v)} stroke="var(--grid-major)" />
              <text x={left - 8} y={y(v) + 3} textAnchor="end">
                {minor(v, currency, { compact: true })}
              </text>
            </g>
          ))}
          {timeTicks.map((tick) => (
            <g key={tick.t}>
              <line x1={x(tick.t)} x2={x(tick.t)} y1={M.top} y2={M.top + innerH} stroke={tick.major ? "var(--grid-major)" : "var(--grid)"} />
              {tick.label ? (
                <text x={x(tick.t) + 4} y={h - 10}>
                  {tick.label}
                </text>
              ) : null}
            </g>
          ))}
          <line x1={left} x2={left + innerW} y1={M.top + innerH} y2={M.top + innerH} stroke="var(--ink)" />
          <line x1={left} x2={left} y1={M.top} y2={M.top + innerH} stroke="var(--ink)" />
        </g>

        <g clipPath={`url(#plot-${uid})`}>
          <g clipPath={`url(#reveal-${uid})`}>
            {/* Highlighter planes: the gap between regular and sale price is the saving. */}
            {steps.map((s, i) =>
              s.cut > 0 ? (
                <rect
                  key={s.t0}
                  x={x(s.t0)}
                  y={y(s.regular)}
                  width={Math.max(2, x(s.t1) - x(s.t0))}
                  height={Math.max(0, y(s.price) - y(s.regular))}
                  fill="var(--hi)"
                  stroke={hover === i ? "var(--ink)" : "none"}
                />
              ) : null,
            )}
            {lowMinor ? (
              <line x1={left} x2={left + innerW} y1={y(lowMinor)} y2={y(lowMinor)} stroke="var(--pen)" strokeDasharray="5 4" />
            ) : null}
            <path d={linePath} fill="none" stroke="var(--ink)" strokeWidth={2} strokeLinejoin="miter" />
          </g>

          <g data-forecast style={{ opacity: revealRef ? 0 : 1 }}>
            <line x1={x(now)} x2={x(now)} y1={M.top - 6} y2={M.top + innerH} stroke="var(--ink)" strokeDasharray="2 3" />
            <text x={x(now) + 5} y={M.top + 4} className="data" style={{ fontSize: 10, fill: "var(--ink)", fontFamily: "var(--font-data)" }}>
              TODAY
            </text>
            {fc ? (
              <g>
                <rect
                  x={x(fc.t0)}
                  y={y(fc.upper)}
                  width={Math.max(4, x(fc.t1) - x(fc.t0))}
                  height={Math.max(2, y(fc.lower) - y(fc.upper))}
                  fill={`url(#hatch-${uid})`}
                  stroke="var(--pen)"
                  strokeWidth={hover === -1 ? 2 : 1}
                  opacity={fc.windowed ? 1 : 0.55}
                />
                <line x1={x(fc.t0)} x2={x(fc.t1)} y1={y(fc.median)} y2={y(fc.median)} stroke="var(--pen)" strokeWidth={2} />
              </g>
            ) : null}
          </g>

          {active ? (
            <g pointerEvents="none">
              <line x1={focusX} x2={focusX} y1={M.top} y2={M.top + innerH} stroke="var(--ink)" />
              <rect x={focusX - 5} y={y(active.price) - 5} width={10} height={10} fill="var(--paper)" stroke="var(--ink)" strokeWidth={2} />
            </g>
          ) : null}
          {brush ? (
            <rect x={brush.a} y={M.top} width={brush.b - brush.a} height={innerH} fill="var(--pen)" opacity={0.12} stroke="var(--pen)" />
          ) : null}
        </g>
      </svg>

      <div aria-live="polite">
        {active ? (
          <div className="readout" style={{ transform: `translate(${readoutLeft}px, ${M.top + 6}px)` }}>
            <span className="data">
              {dayMonth(new Date(active.t0))} {new Date(active.t0).getFullYear()} · {Math.max(1, daysBetween(new Date(active.t0), new Date(active.t1)))} days
            </span>
            <strong className="num">{minor(active.price, currency)}</strong>
            <span className="data">
              {active.cut > 0 ? `${active.cut}% off ${minor(active.regular, currency)}` : "Regular price"}
            </span>
          </div>
        ) : null}
        {onForecast ? (
          <div className="readout" style={{ transform: `translate(${readoutLeft}px, ${M.top + 6}px)` }}>
            <span className="data">
              Estimate · {fc.windowed ? `${dayMonth(new Date(fc.t0))} – ${dayMonth(new Date(fc.t1 - DAY))}` : "if a sale starts in 90 days"}
            </span>
            <strong className="num">
              {minor(fc.lower, currency, { compact: true })} – {minor(fc.upper, currency, { compact: true })}
            </strong>
            <span className="data">Likely sale price, middle {minor(fc.median, currency, { compact: true })}</span>
          </div>
        ) : null}
      </div>
      <p className="data muted" style={{ marginTop: "0.5rem" }}>
        Hover or use arrow keys to read a step · drag to zoom · double-click to reset · showing {date(new Date(domain[0]), "month")} to {date(new Date(Math.min(domain[1], future)), "month")}
      </p>
    </div>
  );
}
