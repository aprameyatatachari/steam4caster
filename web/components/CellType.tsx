"use client";

import { useMemo, useRef } from "react";

import { cellPath, layoutCells } from "@/lib/cells";
import { EASE, gsap, prefersReducedMotion, ScrollTrigger, useGSAP } from "@/lib/motion";

type Props = {
  /** One string per line. Rendered as cells; also exposed to assistive tech as text. */
  lines: string[];
  align?: "left" | "center" | "right";
  /** Per-line fill; defaults to the current text colour. */
  fills?: (string | undefined)[];
  /** `mount` builds on first render, `scroll` when it enters the viewport. */
  build?: "mount" | "scroll" | "none";
  delay?: number;
  className?: string;
  as?: "h1" | "h2" | "p" | "div";
};

/**
 * Display lettering constructed cell by cell on the page grid. When the text changes
 * the letterforms rebuild rather than cross-fade.
 */
export function CellType({ lines, align = "left", fills, build = "mount", delay = 0, className, as = "div" }: Props) {
  const ref = useRef<SVGSVGElement>(null);
  const key = lines.join("\n");
  const layout = useMemo(() => layoutCells(lines, { align }), [key, align]); // eslint-disable-line react-hooks/exhaustive-deps

  const rowsPerLine = 9; // 7 glyph rows + 2 gap rows
  const Tag = as;

  useGSAP(
    () => {
      const svg = ref.current;
      if (!svg || build === "none" || prefersReducedMotion()) return;
      const cells = svg.querySelectorAll<SVGPathElement>("path");
      const from = { scale: 0, opacity: 0, transformOrigin: "50% 50%" };
      const to = {
        scale: 1,
        opacity: 1,
        duration: 0.55,
        ease: EASE,
        delay,
        // The whole word lands in under a second however many cells it has.
        stagger: { amount: 0.65, from: "random" as const },
      };
      gsap.set(cells, from);
      if (build === "scroll") {
        const trigger = ScrollTrigger.create({
          trigger: svg,
          start: "top 88%",
          once: true,
          onEnter: () => gsap.to(cells, to),
        });
        return () => trigger.kill();
      }
      gsap.to(cells, to);
    },
    { dependencies: [key, build], scope: ref },
  );

  return (
    <Tag className={className} style={{ margin: 0 }}>
      <span className="sr-only">{lines.join(" ")}</span>
      <svg
        ref={ref}
        viewBox={`0 0 ${layout.cols} ${layout.rows}`}
        aria-hidden="true"
        focusable="false"
        style={{ width: "100%", height: "auto", overflow: "visible" }}
      >
        {layout.cells.map((cell) => (
          <path
            key={`${cell.x}-${cell.y}`}
            d={cellPath(cell)}
            fill={fills?.[Math.floor(cell.y / rowsPerLine)] ?? "currentColor"}
          />
        ))}
      </svg>
    </Tag>
  );
}
