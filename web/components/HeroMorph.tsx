"use client";

import { useEffect, useRef } from "react";

import type { HistoryPoint } from "@/lib/api";
import { layoutCells, type Cell } from "@/lib/cells";
import { gsap, prefersReducedMotion } from "@/lib/motion";

type Variant = { selector: string; lines: string[]; penLine: number };

const VARIANTS: Variant[] = [
  { selector: ".hero-type svg", lines: ["BUY NOW", "OR WAIT?"], penLine: 1 },
  { selector: ".hero-type-narrow svg", lines: ["BUY", "NOW", "OR", "WAIT?"], penLine: 3 },
];
const ROWS_PER_LINE = 9;
const INK = [12, 14, 20];
const PEN = [27, 60, 255];
const INSET = 0.06;
const CUT = 0.62 * (1 - 2 * INSET);
/** Scroll progress before the letters let go, so the hero is intact at rest. */
const HOLD = 0.05;

type Target = { x: number; y: number; w: number; h: number };
type Plan = { key: string; targets: Target[] };

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));
const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

/** Deterministic per-cell randomness so the flight is identical on every scrub. */
function rand(i: number, salt: number): number {
  const v = Math.sin(i * 127.1 + salt * 311.7) * 43758.5453;
  return v - Math.floor(v);
}

/**
 * The hero's signature moment. As the page scrolls from the headline to the price
 * scene, every cell of BUY NOW / OR WAIT? breaks loose and re-stacks as the price
 * history itself: columns of cells whose tops trace the same steps the chart then
 * draws over them. Scroll-scrubbed and fully reversible.
 */
export function HeroMorph({ history, reveal }: { history: HistoryPoint[]; reveal: React.RefObject<{ value: number }> }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || prefersReducedMotion()) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const layouts = VARIANTS.map((v) => {
      const layout = layoutCells(v.lines);
      // Left-to-right order so letters on the left become the oldest prices.
      const cells = [...layout.cells].sort((a, b) => a.x - b.x || a.y - b.y);
      return { ...v, cols: layout.cols, cells };
    });
    const steps = [...history]
      .map((p) => ({ t: +new Date(p.observed_at), price: p.price.amount_minor }))
      .sort((a, b) => a.t - b.t);

    let dpr = 1;
    let plan: Plan | null = null;
    let hidden: SVGSVGElement | null = null;
    let dirty = false;

    const resize = () => {
      dpr = Math.min(2, window.devicePixelRatio || 1);
      canvas.width = Math.round(window.innerWidth * dpr);
      canvas.height = Math.round(window.innerHeight * dpr);
    };
    resize();
    window.addEventListener("resize", resize);

    /** Time-weighted mean price inside a bucket, so each sale notches the skyline in
     *  proportion to how long and how deep it was. */
    const meanIn = (t0: number, t1: number) => {
      let sum = 0;
      let covered = 0;
      for (let i = 0; i < steps.length; i += 1) {
        const end = i + 1 < steps.length ? steps[i + 1].t : Infinity;
        const overlap = Math.min(end, t1) - Math.max(steps[i].t, t0);
        if (overlap > 0) {
          sum += steps[i].price * overlap;
          covered += overlap;
        }
      }
      return covered > 0 ? sum / covered : 0;
    };

    /** Lay the same number of cells out as columns under the price line. */
    const buildPlan = (chart: SVGSVGElement, count: number): Plan | null => {
      const { plot, domain, ymax, now } = chart.dataset;
      if (!plot || !domain || !ymax || !now) return null;
      const key = `${plot}|${domain}|${ymax}|${count}`;
      if (plan?.key === key) return plan;
      const [left, top, innerW, innerH] = plot.split(",").map(Number);
      const [d0, d1] = domain.split(",").map(Number);
      const yMax = Number(ymax);
      const histW = ((Math.min(Number(now), d1) - d0) / (d1 - d0)) * innerW;
      if (!(histW > 40) || !(innerH > 40)) return null;

      const columns = Math.max(8, Math.round(Math.sqrt((histW * count) / (0.87 * innerH))));
      const colW = histW / columns;
      const levels: number[] = [];
      for (let c = 0; c < columns; c += 1) {
        const t0 = d0 + ((c * colW) / innerW) * (d1 - d0);
        const t1 = d0 + (((c + 1) * colW) / innerW) * (d1 - d0);
        levels.push(meanIn(t0, t1) / yMax);
      }
      const total = levels.reduce((sum, v) => sum + v, 0) || 1;
      const rows = count / total; // cells that would reach the top of the plot
      const heights = levels.map((v) => Math.floor(v * rows));
      // Largest-remainder rounding: the cell count matches exactly and no column
      // gains more than one cell over its true level.
      const byRemainder = levels
        .map((v, c) => ({ c, rest: v * rows - Math.floor(v * rows) }))
        .sort((a, b) => b.rest - a.rest);
      let missing = count - heights.reduce((sum, v) => sum + v, 0);
      for (let i = 0; missing > 0; i += 1, missing -= 1) heights[byRemainder[i % columns].c] += 1;
      const rowH = innerH / rows;
      const targets: Target[] = [];
      heights.forEach((height, c) => {
        for (let r = 0; r < height; r += 1) {
          targets.push({ x: left + c * colW, y: top + innerH - (r + 1) * rowH, w: colW, h: rowH });
        }
      });
      plan = { key, targets: targets.slice(0, count) };
      return plan;
    };

    const drawCell = (x: number, y: number, w: number, h: number, cut: Cell["cut"], chamfer: number, angle: number) => {
      const kx = cut ? CUT * w * chamfer : 0;
      const ky = cut ? CUT * h * chamfer : 0;
      ctx.save();
      ctx.translate(x + w / 2, y + h / 2);
      if (angle) ctx.rotate(angle);
      const x0 = -w / 2;
      const y0 = -h / 2;
      const x1 = w / 2;
      const y1 = h / 2;
      ctx.beginPath();
      ctx.moveTo(x0 + (cut === 1 ? kx : 0), y0);
      ctx.lineTo(x1 - (cut === 2 ? kx : 0), y0);
      if (cut === 2) ctx.lineTo(x1, y0 + ky);
      ctx.lineTo(x1, y1 - (cut === 3 ? ky : 0));
      if (cut === 3) ctx.lineTo(x1 - kx, y1);
      ctx.lineTo(x0 + (cut === 4 ? kx : 0), y1);
      if (cut === 4) ctx.lineTo(x0, y1 - ky);
      ctx.lineTo(x0, y0 + (cut === 1 ? ky : 0));
      ctx.closePath();
      ctx.fill();
      ctx.restore();
    };

    const release = () => {
      if (hidden) hidden.style.opacity = "";
      hidden = null;
      if (dirty) ctx.clearRect(0, 0, canvas.width, canvas.height);
      dirty = false;
    };

    const frame = () => {
      const scene = document.querySelector<HTMLElement>(".scene-steps");
      const chart = scene?.querySelector<SVGSVGElement>(".chart svg[data-plot]");
      const variant = layouts.find((v) => {
        const el = document.querySelector<SVGSVGElement>(v.selector);
        return el && el.getBoundingClientRect().width > 0;
      });
      const source = variant ? document.querySelector<SVGSVGElement>(variant.selector) : null;
      if (!scene || !chart || !variant || !source) return release();

      const sceneRect = scene.getBoundingClientRect();
      const sceneTop = sceneRect.top + window.scrollY;
      // The pin holds the scene at the top, so progress simply saturates at 1 there.
      const raw = sceneTop > 0 ? window.scrollY / sceneTop : 0;
      const progress = clamp01((raw - HOLD) / (1 - HOLD));
      const done = reveal.current?.value ?? 0;
      if (progress <= 0 || sceneRect.bottom < 0) return release();
      // Fully handed over: keep the headline hidden but draw nothing.
      if (progress > 0.999 && done >= 0.999) {
        if (dirty) ctx.clearRect(0, 0, canvas.width, canvas.height);
        dirty = false;
        return;
      }

      const built = buildPlan(chart, variant.cells.length);
      if (!built) return release();

      if (hidden !== source) {
        release();
        hidden = source;
        source.style.opacity = "0";
      }

      const from = source.getBoundingClientRect();
      const to = chart.getBoundingClientRect();
      const unit = from.width / variant.cols;
      const [left, , innerW] = (chart.dataset.plot ?? "0,0,0,0").split(",").map(Number);
      // Where the real chart's draw-on sweep currently is, in viewport pixels.
      const sweepX = to.left + left - 2 + (innerW + 18) * done;

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
      dirty = true;

      const n = variant.cells.length;
      for (let i = 0; i < n; i += 1) {
        const cell = variant.cells[i];
        const target = built.targets[i];
        if (!target) continue;
        // Letters let go left to right, each cell with its own small hesitation.
        const delay = 0.42 * (0.65 * (i / n) + 0.35 * rand(i, 1));
        const p = ease(clamp01((progress - delay) / (1 - 0.42)));
        const arc = Math.sin(p * Math.PI);

        const sx = from.left + (cell.x + INSET) * unit;
        const sy = from.top + (cell.y + INSET) * unit;
        const sSize = unit * (1 - 2 * INSET);
        const gap = Math.min(2, target.w * 0.07);
        const tx = to.left + target.x + gap / 2;
        const ty = to.top + target.y + gap / 2;

        const x = lerp(sx, tx, p) + arc * (rand(i, 2) - 0.5) * 140;
        const y = lerp(sy, ty, p) + arc * (rand(i, 3) - 0.75) * 110;
        const w = lerp(sSize, target.w - gap, p);
        const h = lerp(sSize, target.h - gap, p);
        if (y > window.innerHeight + 60 || y + h < -60) continue;

        // Once landed, each cell hands over to the real chart as the sweep passes it.
        const handover = progress > 0.999 ? clamp01((sweepX - (tx + target.w / 2)) / 36 + 0.5) : 0;
        if (handover >= 1) continue;

        const pen = Math.floor(cell.y / ROWS_PER_LINE) === variant.penLine;
        const base = pen ? PEN : INK;
        const mix = pen ? p : 0; // the blue line settles into ink as it lands
        ctx.fillStyle = `rgb(${Math.round(lerp(base[0], INK[0], mix))}, ${Math.round(lerp(base[1], INK[1], mix))}, ${Math.round(lerp(base[2], INK[2], mix))})`;
        ctx.globalAlpha = 1 - handover;
        const shrink = 1 - handover * 0.6;
        drawCell(
          x + (w * (1 - shrink)) / 2,
          y + (h * (1 - shrink)) / 2,
          w * shrink,
          h * shrink,
          cell.cut,
          1 - p,
          arc * (rand(i, 4) - 0.5) * 1.8,
        );
      }
      ctx.globalAlpha = 1;
    };

    gsap.ticker.add(frame);
    return () => {
      gsap.ticker.remove(frame);
      window.removeEventListener("resize", resize);
      release();
    };
  }, [history, reveal]);

  return <canvas ref={canvasRef} className="hero-morph" aria-hidden="true" />;
}
