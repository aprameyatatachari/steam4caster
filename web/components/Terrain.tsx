"use client";

import { useEffect, useRef } from "react";

import { gsap } from "@/lib/motion";

export type TerrainControl = {
  /** 0..1: how much of the skyline has been built, left to right. */
  build: number;
  /** 0..1: how far the sheet has been lifted; the columns sink flat as it rises. */
  lift: number;
  /** Set by the terrain: send a shockwave out from a point (CSS pixels, host-relative). */
  pulse?: (x: number, y: number, strength?: number) => void;
};

/** Page cells are split in two so the skyline has enough columns to read as terrain. */
const SUB = 2;
/** Extra columns to the left and rows below: tall columns there lean into view. */
const PAD = 10;
const MAX_LEVEL = 12;
/** A band is one made-up price history: BAND_DEPTH rows of columns, then open paper. */
const BAND_PERIOD = 4;
const BAND_DEPTH = 2;

const VERT = `#version 300 es
in vec3 aPos;
in float aFace;
in vec2 aCell;
in vec2 aState;
uniform vec2 uRes;
uniform float uSub;
uniform float uExt;
out vec2 vGround;
out float vY;
out float vFace;
out float vHeat;
out vec2 vEdge;
out float vRise;
void main() {
  float y = aPos.y * aState.x;
  vec2 ground = aCell + aPos.xz;
  // Oblique projection: tops stay square on the page grid, height runs up the diagonal.
  vec2 px = ground * uSub + vec2(y, -y) * uExt;
  vec2 clip = px / uRes * 2.0 - 1.0;
  gl_Position = vec4(clip.x, -clip.y, 1.0 - 2.0 * y / ${MAX_LEVEL.toFixed(1)}, 1.0);
  vGround = ground;
  vY = y;
  vFace = aFace;
  vHeat = aState.y;
  vEdge = aPos.xz;
  vRise = aState.x;
}`;

const FRAG = `#version 300 es
precision highp float;
in vec2 vGround;
in float vY;
in float vFace;
in float vHeat;
in vec2 vEdge;
in float vRise;
uniform vec3 uPaper;
uniform vec3 uInk;
uniform vec3 uPen;
uniform vec3 uHi;
out vec4 outColor;
float hair(float v) {
  float d = abs(fract(v - 0.5) - 0.5) / max(fwidth(v), 1e-5);
  return 1.0 - clamp(d - 0.5, 0.0, 1.0);
}
void main() {
  vec3 col;
  if (vFace < 0.5) {
    vec2 g = vGround / ${SUB.toFixed(1)};
    float minor = max(hair(g.x), hair(g.y));
    float major = max(hair(g.x / 8.0), hair(g.y / 8.0));
    col = mix(uPaper, uHi, clamp(vHeat, 0.0, 1.0));
    col = mix(col, uPen, 0.085 * minor);
    col = mix(col, uPen, 0.2 * major);
    // A raised top is ruled in ink, like a block in a technical drawing.
    vec2 e = min(vEdge, 1.0 - vEdge) / max(fwidth(vEdge), vec2(1e-5));
    float rim = 1.0 - clamp(min(e.x, e.y) - 0.6, 0.0, 1.0);
    col = mix(col, uInk, rim * smoothstep(0.15, 0.6, vRise));
  } else if (vFace < 1.5) {
    float seams = max(hair(vY), hair(vGround.x));
    col = mix(uPen, uPaper, 0.34 * seams);
  } else {
    float seams = max(hair(vY), hair(vGround.y));
    col = mix(uInk, uPaper, 0.2 * seams);
  }
  outColor = vec4(col, 1.0);
}`;

/** Fast integer hash to 0..1; the skyline must be identical frame to frame. */
function hash(a: number, b: number): number {
  let h = Math.imul(a | 0, 0x27d4eb2d) ^ Math.imul(b | 0, 0x165667b1);
  h = Math.imul(h ^ (h >>> 15), 0x85ebca6b);
  h ^= h >>> 13;
  return (h >>> 0) / 4294967296;
}

/** Height, in levels, of one band's made-up price history at step `i`. */
function priceLevel(band: number, i: number): number {
  const regular = 3 + Math.floor(hash(band, Math.floor(i / 22) + 9001) * 5);
  const roll = hash(band * 7 + 3, Math.floor(i / 3));
  const cut = roll < 0.07 ? 0.75 : roll < 0.15 ? 0.5 : roll < 0.22 ? 0.25 : 0;
  return Math.max(1, Math.round(regular * (1 - cut)));
}

const rgb = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);

function compile(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader | null {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  return gl.getShaderParameter(shader, gl.COMPILE_STATUS) ? shader : null;
}

type Renderer = { resize: (w: number, h: number, dpr: number, cells: Float32Array) => void; draw: (state: Float32Array, count: number, sub: number) => void; dispose: () => void };

function webglRenderer(canvas: HTMLCanvasElement): Renderer | null {
  const gl = canvas.getContext("webgl2", { antialias: true, alpha: false, powerPreference: "high-performance" });
  if (!gl) return null;
  const vs = compile(gl, gl.VERTEX_SHADER, VERT);
  const fs = compile(gl, gl.FRAGMENT_SHADER, FRAG);
  const program = gl.createProgram();
  if (!vs || !fs || !program) return null;
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return null;
  gl.useProgram(program);

  // One column: its top, the face toward the reader and the face on the left.
  const quad = (face: number, p: number[][]) => [0, 1, 2, 0, 2, 3].flatMap((k) => [...p[k], face]);
  const mesh = new Float32Array([
    ...quad(0, [[0, 1, 0], [1, 1, 0], [1, 1, 1], [0, 1, 1]]),
    ...quad(1, [[0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]]),
    ...quad(2, [[0, 0, 0], [0, 0, 1], [0, 1, 1], [0, 1, 0]]),
  ]);
  const vao = gl.createVertexArray();
  gl.bindVertexArray(vao);
  const loc = (name: string) => gl.getAttribLocation(program, name);
  const meshBuffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, meshBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, mesh, gl.STATIC_DRAW);
  gl.enableVertexAttribArray(loc("aPos"));
  gl.vertexAttribPointer(loc("aPos"), 3, gl.FLOAT, false, 16, 0);
  gl.enableVertexAttribArray(loc("aFace"));
  gl.vertexAttribPointer(loc("aFace"), 1, gl.FLOAT, false, 16, 12);
  const cellBuffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, cellBuffer);
  gl.enableVertexAttribArray(loc("aCell"));
  gl.vertexAttribPointer(loc("aCell"), 2, gl.FLOAT, false, 0, 0);
  gl.vertexAttribDivisor(loc("aCell"), 1);
  const stateBuffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, stateBuffer);
  gl.enableVertexAttribArray(loc("aState"));
  gl.vertexAttribPointer(loc("aState"), 2, gl.FLOAT, false, 0, 0);
  gl.vertexAttribDivisor(loc("aState"), 1);

  const uniform = (name: string) => gl.getUniformLocation(program, name);
  gl.uniform3fv(uniform("uPaper"), rgb("#fbfcfe"));
  gl.uniform3fv(uniform("uInk"), rgb("#0c0e14"));
  gl.uniform3fv(uniform("uPen"), rgb("#1b3cff"));
  gl.uniform3fv(uniform("uHi"), rgb("#ffe600"));
  const uRes = uniform("uRes");
  const uSub = uniform("uSub");
  const uExt = uniform("uExt");
  const paper = rgb("#fbfcfe");
  gl.clearColor(paper[0], paper[1], paper[2], 1);
  gl.enable(gl.DEPTH_TEST);
  gl.depthFunc(gl.LEQUAL);

  return {
    resize(w, h, dpr, cells) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.uniform2f(uRes, w, h);
      gl.bindBuffer(gl.ARRAY_BUFFER, cellBuffer);
      gl.bufferData(gl.ARRAY_BUFFER, cells, gl.STATIC_DRAW);
      gl.bindBuffer(gl.ARRAY_BUFFER, stateBuffer);
      gl.bufferData(gl.ARRAY_BUFFER, cells.length * 4, gl.DYNAMIC_DRAW);
    },
    draw(state, count, sub) {
      gl.uniform1f(uSub, sub);
      gl.uniform1f(uExt, sub * 0.55);
      gl.bindBuffer(gl.ARRAY_BUFFER, stateBuffer);
      gl.bufferSubData(gl.ARRAY_BUFFER, 0, state);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.drawArraysInstanced(gl.TRIANGLES, 0, 18, count);
    },
    // The context is left alive: a remount on the same canvas gets it back.
    dispose() {
      gl.deleteProgram(program);
    },
  };
}

/** Without WebGL the columns stay flat: only the highlighter marks are drawn. */
function flatRenderer(canvas: HTMLCanvasElement): Renderer | null {
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  let cells: Float32Array = new Float32Array(0);
  let scale = 1;
  return {
    resize(w, h, dpr, next) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      cells = next;
      scale = dpr;
    },
    draw(state, count, sub) {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = "#ffe600";
      for (let i = 0; i < count; i += 1) {
        const heat = state[i * 2 + 1];
        if (heat < 0.02) continue;
        ctx.globalAlpha = Math.min(1, heat);
        ctx.fillRect(cells[i * 2] * sub * scale, cells[i * 2 + 1] * sub * scale, sub * scale, sub * scale);
      }
      ctx.globalAlpha = 1;
    },
    dispose() {},
  };
}

/**
 * The intro's ground: the page grid itself, extruded. Bands of columns are made-up
 * price histories sliding past; the pointer presses a sale into them (the columns drop
 * and their tops are highlighted) and a press sends a ripple across the sheet.
 * Decoration only: none of it is price data.
 */
export function Terrain({ control }: { control: React.RefObject<TerrainControl> }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const host = canvas?.parentElement;
    if (!canvas || !host) return;
    const renderer = webglRenderer(canvas) ?? flatRenderer(canvas);
    if (!renderer) return;
    const sheet = host.closest<HTMLElement>(".sheet-inner");

    let sub = 15;
    let gw = 0;
    let gh = 0;
    let count = 0;
    let height = new Float32Array(0);
    let heat = new Float32Array(0);
    let waveA = new Float32Array(0);
    let waveB = new Float32Array(0);
    let clear = new Float32Array(0);
    let state = new Float32Array(0);
    let lost = false;

    const measureClear = () => {
      clear.fill(0);
      const origin = host.getBoundingClientRect();
      host.querySelectorAll<HTMLElement>("[data-terrain-clear]").forEach((el) => {
        const r = el.getBoundingClientRect();
        if (r.width === 0) return;
        // Wider to the left and below: columns there would lean over the text.
        const c0 = Math.floor((r.left - origin.left) / sub) + PAD - 7;
        const c1 = Math.ceil((r.right - origin.left) / sub) + PAD + 2;
        const r0 = Math.floor((r.top - origin.top) / sub) - 2;
        const r1 = Math.ceil((r.bottom - origin.top) / sub) + 7;
        for (let row = Math.max(0, r0); row < Math.min(gh, r1); row += 1) {
          for (let col = Math.max(0, c0); col < Math.min(gw, c1); col += 1) clear[row * gw + col] = 1;
        }
      });
    };

    const resize = () => {
      const w = host.clientWidth;
      const h = host.clientHeight;
      if (!w || !h) return;
      const cols = Number(getComputedStyle(sheet ?? host).getPropertyValue("--cols")) || 48;
      sub = w / cols / SUB;
      gw = Math.ceil(w / sub) + PAD + 1;
      gh = Math.ceil(h / sub) + PAD + 1;
      count = gw * gh;
      height = new Float32Array(count);
      heat = new Float32Array(count);
      waveA = new Float32Array(count);
      waveB = new Float32Array(count);
      clear = new Float32Array(count);
      state = new Float32Array(count * 2);
      const cells = new Float32Array(count * 2);
      for (let r = 0; r < gh; r += 1) {
        for (let c = 0; c < gw; c += 1) {
          cells[(r * gw + c) * 2] = c - PAD;
          cells[(r * gw + c) * 2 + 1] = r;
        }
      }
      renderer.resize(w, h, Math.min(2, window.devicePixelRatio || 1), cells);
      measureClear();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(host);
    resize();

    /** Mark cells around a point as "on sale" and push the sheet there. */
    const stamp = (x: number, y: number, radius: number, push: number, mark = true) => {
      const cc = x / sub + PAD;
      const cr = y / sub;
      const reach = Math.ceil(radius);
      for (let r = Math.max(1, Math.floor(cr) - reach); r <= Math.min(gh - 2, Math.floor(cr) + reach); r += 1) {
        for (let c = Math.max(1, Math.floor(cc) - reach); c <= Math.min(gw - 2, Math.floor(cc) + reach); c += 1) {
          const d = Math.hypot(c + 0.5 - cc, r + 0.5 - cr) / radius;
          if (d >= 1) continue;
          const i = r * gw + c;
          if (mark) heat[i] = Math.max(heat[i], 1 - d * d);
          waveA[i] += push * (1 - d);
        }
      }
    };
    control.current.pulse = (x, y, strength = 1) => stamp(x, y, 4.5 * strength, 2.6 * strength, false);

    let last: { x: number; y: number } | null = null;
    const local = (e: PointerEvent) => {
      const rect = host.getBoundingClientRect();
      return { x: e.clientX - rect.left, y: e.clientY - rect.top };
    };
    const onMove = (e: PointerEvent) => {
      const p = local(e);
      // Fill in fast strokes so the mark is a line, not dots.
      const from = last ?? p;
      const steps = Math.max(1, Math.min(24, Math.ceil(Math.hypot(p.x - from.x, p.y - from.y) / sub)));
      for (let s = 1; s <= steps; s += 1) {
        stamp(from.x + ((p.x - from.x) * s) / steps, from.y + ((p.y - from.y) * s) / steps, 2.4, -0.16);
      }
      last = p;
    };
    const onDown = (e: PointerEvent) => {
      const p = local(e);
      last = p;
      stamp(p.x, p.y, 4.5, 2.6);
    };
    const onLeave = () => {
      last = null;
    };
    host.addEventListener("pointermove", onMove);
    host.addEventListener("pointerdown", onDown);
    host.addEventListener("pointerleave", onLeave);
    const onLost = (e: Event) => {
      e.preventDefault();
      lost = true;
    };
    canvas.addEventListener("webglcontextlost", onLost);

    let time = 0;
    let frames = 0;
    let nextSale = 1.2;
    const speeds = new Map<number, number>();
    const speedOf = (band: number) => {
      let v = speeds.get(band);
      if (v === undefined) {
        v = 1.2 + hash(band, 77) * 2.6;
        speeds.set(band, v);
      }
      return v;
    };

    const frame = (_now: number, delta: number) => {
      if (lost || !count) return;
      const { build, lift } = control.current;
      // Real elapsed time, so the skyline moves at one speed on any refresh rate.
      time += Math.min(delta || 16.7, 50) / 1000;
      frames += 1;
      if (frames % 20 === 0) measureClear();

      // With no pointer about (phones), sales still land on their own.
      if (time > nextSale && build >= 1) {
        nextSale = time + 0.5 + Math.random() * 0.9;
        const band = Math.floor((Math.random() * gh) / BAND_PERIOD);
        stamp((Math.random() * (gw - PAD) + 0.5) * sub, (band * BAND_PERIOD + 1) * sub, 2.2, 0.5);
      }

      // Ripples: a damped wave across the sheet.
      for (let r = 1; r < gh - 1; r += 1) {
        for (let c = 1; c < gw - 1; c += 1) {
          const i = r * gw + c;
          waveB[i] = ((waveA[i - 1] + waveA[i + 1] + waveA[i - gw] + waveA[i + gw]) * 0.5 - waveB[i]) * 0.965;
        }
      }
      [waveA, waveB] = [waveB, waveA];

      const standing = 1 - lift;
      for (let r = 0; r < gh; r += 1) {
        const band = Math.floor(r / BAND_PERIOD);
        const inBand = r % BAND_PERIOD < BAND_DEPTH;
        const shift = Math.floor(time * speedOf(band));
        const jitter = hash(band, 5) * 0.18;
        for (let c = 0; c < gw; c += 1) {
          const i = r * gw + c;
          let target = 0;
          if (inBand && clear[i] === 0 && build * 1.2 - c / gw - jitter > 0) {
            target = priceLevel(band, c + shift) * (1 - 0.72 * heat[i]) * standing;
          }
          height[i] += (target - height[i]) * 0.16;
          heat[i] *= 0.982;
          const lifted = height[i] + (clear[i] === 0 ? waveA[i] * standing : 0);
          state[i * 2] = lifted < 0 ? 0 : lifted > MAX_LEVEL ? MAX_LEVEL : lifted;
          state[i * 2 + 1] = heat[i];
        }
      }
      renderer.draw(state, count, sub);
    };
    gsap.ticker.add(frame);

    const handle = control.current;
    return () => {
      gsap.ticker.remove(frame);
      observer.disconnect();
      host.removeEventListener("pointermove", onMove);
      host.removeEventListener("pointerdown", onDown);
      host.removeEventListener("pointerleave", onLeave);
      canvas.removeEventListener("webglcontextlost", onLost);
      handle.pulse = undefined;
      renderer.dispose();
    };
  }, [control]);

  return <canvas ref={canvasRef} className="terrain" aria-hidden="true" />;
}
