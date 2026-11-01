// Offline Flow Map layout check (any Node 18+): node scripts/run-ts.mjs scripts/layoutcheck.ts snaps.json [panelWidth]
// For every flow snapshot (backend/scripts/replay.py --snapshots) it runs the same layout as the live map and
// reports: boxes that overlap, lines that run through a box, and step numbers that changed since the last snapshot.
import { readFileSync } from "node:fs";
import { layoutFlow, type FlowLayout, type LaidNode } from "../core/flowLayout.ts";

const READ_ZOOM = 0.8;
const BODY_MAX = 500;

export function liveLayout(flow: any, boxW: number): { L: FlowLayout; fits: boolean } {
  const box = { w: boxW - 40, h: BODY_MAX - 70 };
  let L = layoutFlow(flow, { ...box, enough: READ_ZOOM });
  const fitScale = Math.min(box.w / Math.max(1, L.width), box.h / Math.max(1, L.height));
  const fits = fitScale >= READ_ZOOM * 0.97;
  if (!fits) L = layoutFlow(flow, { w: box.w / 0.88, h: box.h, widthOnly: true });
  return { L, fits };
}

type R = { x: number; y: number; w: number; h: number };
const inter = (a: R, b: R) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
function segHits(p: { x: number; y: number }, q: { x: number; y: number }, r: R) {
  for (let t = 0.02; t <= 0.98; t += 0.02) {
    const x = p.x + (q.x - p.x) * t, y = p.y + (q.y - p.y) * t;
    if (x > r.x + 2 && x < r.x + r.w - 2 && y > r.y + 2 && y < r.y + r.h - 2) return true;
  }
  return false;
}

export function problems(L: FlowLayout) {
  const out: string[] = [];
  const nodes = L.nodes;
  const name = (n: LaidNode) => (n.type === "step" ? `${n.num}:${n.step.label}` : `GAP(${n.gap.branch})`);
  for (let i = 0; i < nodes.length; i++)
    for (let j = i + 1; j < nodes.length; j++)
      if (inter(nodes[i], nodes[j])) out.push(`overlap ${name(nodes[i])} x ${name(nodes[j])}`);
  for (const e of L.edges) {
    for (const n of nodes) {
      if (n.id === e.src || n.id === e.dst) continue;
      if (e.points.some((p, k) => k > 0 && segHits(e.points[k - 1], p, n))) {
        out.push(`line ${e.src}->${e.dst} through ${name(n)}`);
        break;
      }
    }
  }
  return out;
}

if (process.argv[1]?.endsWith("layoutcheck.ts")) {
  const snaps = JSON.parse(readFileSync(process.argv[2], "utf8"));
  const boxW = Number(process.argv[3] ?? 1000);
  let prev = new Map<string, number>();
  let renumbered = 0, overlapSnaps = 0, lineSnaps = 0;
  for (const s of snaps) {
    if (!s.flow.steps.length) continue;
    const { L, fits } = liveLayout(s.flow, boxW);
    const nums = new Map(L.nodes.flatMap((n) => (n.type === "step" ? [[n.id, n.num] as [string, number]] : [])));
    const changed = [...nums].filter(([id, n]) => prev.has(id) && prev.get(id) !== n).map(([id, n]) => `${id}:${prev.get(id)}→${n}`);
    const p = problems(L);
    if (p.some((x) => x.startsWith("overlap"))) overlapSnaps++;
    if (p.some((x) => x.startsWith("line"))) lineSnaps++;
    if (changed.length) renumbered++;
    const mm = `${String(Math.floor(s.meeting_t / 60)).padStart(2, "0")}:${String(Math.floor(s.meeting_t % 60)).padStart(2, "0")}`;
    if (changed.length || p.length)
      console.log(`[${mm} ${s.mode}] steps=${s.flow.steps.length} rows=${L.rows} fits=${fits} ${changed.length ? "RENUMBERED " + changed.join(" ") : ""}${p.length ? "\n    " + p.join("\n    ") : ""}`);
    prev = nums;
  }
  console.log(`summary: snapshots with overlaps=${overlapSnaps}, with lines through a box=${lineSnaps}, ` +
    `that renumbered existing steps=${renumbered}`);
}
