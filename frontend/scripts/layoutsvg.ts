// Draw the live Flow Map layout of a flow snapshot as a plain SVG (offline visual check, same positions as the app).
// node scripts/run-ts.mjs scripts/layoutsvg.ts snaps.json <index|-1> out.svg [panelWidth]
import { readFileSync, writeFileSync } from "node:fs";
import { edgePath, jumpText } from "../core/flowLayout.ts";
import { liveLayout, problems } from "./layoutcheck.ts";

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;");
export function svgOf(flow: any, boxW: number): string {
  const { L, fits } = liveLayout(flow, boxW);
  const parts: string[] = [];
  for (const e of L.edges)
    parts.push(`<path d="${edgePath(e)}" fill="none" stroke="${e.kind === "gap" ? "#d97706" : e.kind === "fail" ? "#dc2626" : "#7c8597"}" stroke-width="1.8" ${e.kind === "gap" ? 'stroke-dasharray="6 5"' : ""} marker-end="url(#a)"/>`);
  for (const n of L.nodes as any[]) {
    if (n.type === "gap") {
      parts.push(`<rect x="${n.x}" y="${n.y}" width="${n.w}" height="${n.h}" rx="10" fill="#fffaf0" stroke="#d97706" stroke-dasharray="5 4"/><text x="${n.x + 10}" y="${n.y + 30}" font-size="13" fill="#92400e">${esc(n.gap.branch)}?</text>`);
    } else {
      const lane = n.lane ? ` [${esc(n.lane)}]` : "";
      parts.push(`<rect x="${n.x}" y="${n.y}" width="${n.w}" height="${n.h}" rx="11" fill="#fff" stroke="#9aa3b2"/><text x="${n.x + 10}" y="${n.y + 22}" font-size="12" font-weight="700" fill="#4f46e5">${n.num} ${n.step.kind.toUpperCase()}${n.jumps?.length ? "  " + esc(jumpText(n)) : ""}</text><text x="${n.x + 10}" y="${n.y + 52}" font-size="14" font-weight="600">${esc(n.step.label)}</text>`);
    }
  }
  for (const l of (L as any).lanes ?? [])
    parts.push(`<text x="${l.x}" y="${l.y}" font-size="13" font-weight="800" fill="#475467">${esc(l.label)}</text>`);
  const p = problems(L);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${L.width}" height="${L.height + 40}" viewBox="0 0 ${L.width} ${L.height + 40}" font-family="sans-serif"><defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#7c8597"/></marker></defs><rect width="100%" height="100%" fill="#f6f7f9"/>${parts.join("")}<text x="10" y="${L.height + 28}" font-size="12" fill="#b91c1c">fits=${fits} rows=${L.rows} problems: ${esc(p.join(" | ") || "none")}</text></svg>`;
}

if (process.argv[1]?.endsWith("layoutsvg.ts")) {
  const snaps = JSON.parse(readFileSync(process.argv[2], "utf8"));
  const i = Number(process.argv[3] ?? -1);
  const s = snaps[i < 0 ? snaps.length + i : i];
  writeFileSync(process.argv[4], svgOf(s.flow, Number(process.argv[5] ?? 1000)));
}
