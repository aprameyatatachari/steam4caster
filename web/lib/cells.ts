/**
 * The construction alphabet. Every display letterform in Steam4Caster is built from
 * unit cells on the same grid the page and the charts are drawn on.
 *
 * Glyph rows use: `#` full cell, `.` empty, and a/b/c/d for a cell whose
 * top-left / top-right / bottom-right / bottom-left corner is cut on the diagonal.
 */

export const GLYPH_ROWS = 7;

const G: Record<string, string[]> = {
  A: ["a###b", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
  B: ["####b", "#...#", "#...#", "####.", "#...#", "#...#", "####c"],
  C: ["a####", "#....", "#....", "#....", "#....", "#....", "d####"],
  D: ["####b", "#...#", "#...#", "#...#", "#...#", "#...#", "####c"],
  E: ["#####", "#....", "#....", "####.", "#....", "#....", "#####"],
  F: ["#####", "#....", "#....", "####.", "#....", "#....", "#...."],
  G: ["a####", "#....", "#....", "#.###", "#...#", "#...#", "d###c"],
  H: ["#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
  I: ["###", ".#.", ".#.", ".#.", ".#.", ".#.", "###"],
  J: ["....#", "....#", "....#", "....#", "#...#", "#...#", "d###c"],
  K: ["#..##", "#.##.", "###..", "##...", "###..", "#.##.", "#..##"],
  L: ["#....", "#....", "#....", "#....", "#....", "#....", "#####"],
  M: ["#...#", "##.##", "#####", "#.#.#", "#...#", "#...#", "#...#"],
  N: ["#...#", "##..#", "###.#", "#.###", "#..##", "#...#", "#...#"],
  O: ["a###b", "#...#", "#...#", "#...#", "#...#", "#...#", "d###c"],
  P: ["####b", "#...#", "#...#", "####c", "#....", "#....", "#...."],
  Q: ["a###b", "#...#", "#...#", "#...#", "#.###", "#..##", "d####"],
  R: ["####b", "#...#", "#...#", "####c", "#.##.", "#..##", "#...#"],
  S: ["a####", "#....", "#....", "d###b", "....#", "....#", "####c"],
  T: ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."],
  U: ["#...#", "#...#", "#...#", "#...#", "#...#", "#...#", "d###c"],
  V: ["#...#", "#...#", "#...#", "#...#", "##.##", ".###.", "..#.."],
  W: ["#...#", "#...#", "#...#", "#.#.#", "#####", "##.##", "#...#"],
  X: ["#...#", "##.##", ".###.", "..#..", ".###.", "##.##", "#...#"],
  Y: ["#...#", "##.##", ".###.", "..#..", "..#..", "..#..", "..#.."],
  Z: ["#####", "....#", "...##", "..##.", ".##..", "##...", "#####"],
  "0": ["a###b", "#...#", "#..##", "#.#.#", "##..#", "#...#", "d###c"],
  "1": ["##.", ".#.", ".#.", ".#.", ".#.", ".#.", "###"],
  "2": ["a###b", "....#", "....#", "a###c", "#....", "#....", "#####"],
  "3": ["####b", "....#", "....#", ".####", "....#", "....#", "####c"],
  "4": ["#...#", "#...#", "#...#", "#####", "....#", "....#", "....#"],
  "5": ["#####", "#....", "#....", "####b", "....#", "....#", "####c"],
  "6": ["a####", "#....", "#....", "####b", "#...#", "#...#", "d###c"],
  "7": ["#####", "....#", "....#", "...##", "..##.", "..#..", "..#.."],
  "8": ["a###b", "#...#", "#...#", "#####", "#...#", "#...#", "d###c"],
  "9": ["a###b", "#...#", "#...#", "d####", "....#", "....#", "####c"],
  "?": ["a###b", "....#", "....#", "..a#c", "..#..", ".....", "..#.."],
  "%": ["##..#", "##.##", "...#.", "..#..", ".#...", "##.##", "#..##"],
  ".": [".", ".", ".", ".", ".", ".", "#"],
  ":": [".", ".", "#", ".", "#", ".", "."],
  "-": ["...", "...", "...", "###", "...", "...", "..."],
  "+": ["...", "...", ".#.", "###", ".#.", "...", "..."],
  "/": ["..#", "..#", ".#.", ".#.", ".#.", "#..", "#.."],
  " ": ["..", "..", "..", "..", "..", "..", ".."],
};

export type Cell = {
  /** Column and row in cell units within the laid-out block. */
  x: number;
  y: number;
  /** Which corner is cut: 0 none, 1 TL, 2 TR, 3 BR, 4 BL. */
  cut: 0 | 1 | 2 | 3 | 4;
  /** Index of the glyph this cell belongs to, for staggering by letter. */
  glyph: number;
};

export type CellLayout = { cells: Cell[]; cols: number; rows: number };

const CUT: Record<string, Cell["cut"]> = { "#": 0, a: 1, b: 2, c: 3, d: 4 };

/** Width in cells of one line of text, including one-cell letter gaps. */
export function measureLine(line: string): number {
  let width = 0;
  for (const ch of line.toUpperCase()) {
    const glyph = G[ch] ?? G["?"];
    width += glyph[0].length + 1;
  }
  return Math.max(0, width - 1);
}

/**
 * Lay out lines of text as cells. Lines are stacked with `lineGap` empty rows and
 * aligned left, centre or right inside the widest line.
 */
export function layoutCells(
  lines: string[],
  { lineGap = 2, align = "left" }: { lineGap?: number; align?: "left" | "center" | "right" } = {},
): CellLayout {
  const widths = lines.map(measureLine);
  const cols = Math.max(1, ...widths);
  const cells: Cell[] = [];
  let glyphIndex = 0;
  lines.forEach((line, lineIndex) => {
    const offset =
      align === "left" ? 0 : align === "right" ? cols - widths[lineIndex] : Math.floor((cols - widths[lineIndex]) / 2);
    let cursor = offset;
    const top = lineIndex * (GLYPH_ROWS + lineGap);
    for (const ch of line.toUpperCase()) {
      const glyph = G[ch] ?? G["?"];
      glyph.forEach((row, r) => {
        for (let c = 0; c < row.length; c += 1) {
          const mark = row[c];
          if (mark !== ".") cells.push({ x: cursor + c, y: top + r, cut: CUT[mark] ?? 0, glyph: glyphIndex });
        }
      });
      cursor += glyph[0].length + 1;
      glyphIndex += 1;
    }
  });
  return { cells, cols, rows: lines.length * GLYPH_ROWS + (lines.length - 1) * lineGap };
}

/** SVG path for one cell at (x, y) with an inset so the grid shows between cells. */
export function cellPath(cell: Cell, inset = 0.06): string {
  const x0 = cell.x + inset;
  const y0 = cell.y + inset;
  const x1 = cell.x + 1 - inset;
  const y1 = cell.y + 1 - inset;
  const k = 0.62 * (1 - 2 * inset); // how far along the edge the diagonal cut reaches
  switch (cell.cut) {
    case 1:
      return `M${x0 + k} ${y0}H${x1}V${y1}H${x0}V${y0 + k}Z`;
    case 2:
      return `M${x0} ${y0}H${x1 - k}L${x1} ${y0 + k}V${y1}H${x0}Z`;
    case 3:
      return `M${x0} ${y0}H${x1}V${y1 - k}L${x1 - k} ${y1}H${x0}Z`;
    case 4:
      return `M${x0} ${y0}H${x1}V${y1}H${x0 + k}L${x0} ${y1 - k}Z`;
    default:
      return `M${x0} ${y0}H${x1}V${y1}H${x0}Z`;
  }
}
