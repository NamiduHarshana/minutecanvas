// Shared Flow Map layout (dagre, left → right). Used by the live map, report slide and board export.
import { Graph, layout as dagreLayout } from "@dagrejs/dagre";
import type { FlowData, FlowGap, FlowStep } from "./types";

export const STEP_W = 216;
export const STEP_H = 92;
export const GAP_W = 186;
export const GAP_H = 54;
export const LINK_LABEL_CHARS = 22;

export interface LaidStep {
  type: "step"; id: string; x: number; y: number; w: number; h: number; step: FlowStep; bugs: number; conflicts: number;
  num: number;   // reading order (row by row, left to right) — what the presenter says out loud: "step 4"
}
export interface LaidGap { type: "gap"; id: string; x: number; y: number; w: number; h: number; gap: FlowGap }
export type LaidNode = LaidStep | LaidGap;
export interface LaidEdge {
  id: string;
  src: string;
  dst: string;
  kind: "normal" | "fail" | "gap";
  label: string;
  points: { x: number; y: number }[];
  labelPos: { x: number; y: number } | null;
  wrapped?: boolean;   // goes on into the next row (folded layout)
}
export interface FlowLayout { nodes: LaidNode[]; edges: LaidEdge[]; width: number; height: number; openGaps: FlowGap[]; rows: number; wrapped: number }

export function shortLabel(s: string, n = LINK_LABEL_CHARS): string {
  return s.length > n ? s.slice(0, n - 1).trimEnd() + "…" : s;
}

/** Positions are node top-left corners; points are dagre's edge routes. */
function layoutOnce(flow: FlowData): FlowLayout {
  const openGaps = flow.gaps.filter((g) => !g.resolved && !g.dismissed);
  const ids = new Set(flow.steps.map((s) => s.id));
  const g = new Graph();
  g.setGraph({ rankdir: "LR", nodesep: 26, ranksep: 58, edgesep: 14, marginx: 16, marginy: 16 });
  g.setDefaultEdgeLabel(() => ({}));
  flow.steps.forEach((s) => g.setNode(s.id, { width: STEP_W, height: STEP_H }));
  const gaps = openGaps.filter((x) => ids.has(x.step));
  gaps.forEach((x) => g.setNode(`gap:${x.id}`, { width: GAP_W, height: GAP_H }));
  const links = flow.links.filter((l) => ids.has(l.src) && ids.has(l.dst) && l.src !== l.dst);
  // a label takes room between ranks, so arrows and labels don't sit on top of the boxes
  links.forEach((l) => {
    const text = shortLabel(l.label || "");
    g.setEdge(l.src, l.dst, text ? { width: text.length * 6.6 + 14, height: 20, labelpos: "c" } : {});
  });
  gaps.forEach((x) => g.setEdge(x.step, `gap:${x.id}`, {}));
  dagreLayout(g);

  const nodes: LaidNode[] = [];
  flow.steps.forEach((s) => {
    const p = g.node(s.id);
    nodes.push({
      type: "step",
      id: s.id,
      x: p.x - STEP_W / 2,
      y: p.y - STEP_H / 2,
      w: STEP_W,
      h: STEP_H,
      step: s,
      bugs: flow.bugs.filter((b) => b.step === s.id && !b.fixed).length,
      conflicts: flow.contradictions.filter((c) => c.step === s.id && !c.resolved).length,
      num: 0,
    });
  });
  gaps.forEach((x) => {
    const p = g.node(`gap:${x.id}`);
    nodes.push({ type: "gap", id: `gap:${x.id}`, x: p.x - GAP_W / 2, y: p.y - GAP_H / 2, w: GAP_W, h: GAP_H, gap: x });
  });

  const edges: LaidEdge[] = [];
  links.forEach((l) => {
    const e = g.edge(l.src, l.dst) as { points?: { x: number; y: number }[]; x?: number; y?: number } | undefined;
    edges.push({
      id: l.id,
      src: l.src,
      dst: l.dst,
      kind: l.type === "fail" ? "fail" : "normal",
      label: shortLabel(l.label || ""),
      points: e?.points ?? [],
      labelPos: l.label && e?.x != null && e?.y != null ? { x: e.x, y: e.y } : null,
    });
  });
  gaps.forEach((x) => {
    const e = g.edge(x.step, `gap:${x.id}`) as { points?: { x: number; y: number }[] } | undefined;
    edges.push({ id: `gapedge:${x.id}`, src: x.step, dst: `gap:${x.id}`, kind: "gap", label: "", points: e?.points ?? [], labelPos: null });
  });
  const gl = g.graph() as { width?: number; height?: number };
  return { nodes: numbered(nodes, () => 0, flowGroups(flow), flow), edges, width: gl.width ?? 0, height: gl.height ?? 0, openGaps, rows: 1, wrapped: 0 };
}

/**
 * Separate flows in one meeting (e.g. "coaching" and "system design") are numbered one after the other,
 * in the order they were first discussed, so a second flow never jumps in between the first flow's numbers.
 */
function flowGroups(flow: FlowData): Map<string, number> {
  const parent = new Map(flow.steps.map((s) => [s.id, s.id]));
  const find = (x: string): string => {
    let r = x;
    while (parent.get(r) !== r) r = parent.get(r)!;
    parent.set(x, r);
    return r;
  };
  flow.links.forEach((l) => {
    if (parent.has(l.src) && parent.has(l.dst)) parent.set(find(l.src), find(l.dst));
  });
  const first = new Map<string, number>();     // group root -> earliest time a step of it was said
  flow.steps.forEach((s) => {
    const r = find(s.id);
    first.set(r, Math.min(first.get(r) ?? Infinity, s.t));
  });
  const roots = [...first.entries()].sort((a, b) => a[1] - b[1]).map((e) => e[0]);
  const rank = new Map(roots.map((r, i) => [r, i]));
  return new Map(flow.steps.map((s) => [s.id, rank.get(find(s.id)) ?? 0]));
}

/**
 * Number the steps by following the arrows, one branch at a time (1 → 2 → 3 is always an arrow when the flow
 * allows it). Each flow starts at its entry step; leftovers (loops) follow in reading order.
 */
function numbered(nodes: LaidNode[], rowOf: (n: LaidNode) => number, group: Map<string, number>, flow: FlowData): LaidNode[] {
  const steps = nodes.filter((n): n is LaidStep => n.type === "step");
  const byId = new Map(steps.map((n) => [n.id, n]));
  const g = (n: LaidNode) => group.get(n.id) ?? 0;
  const reading = (a: LaidStep, b: LaidStep) =>
    g(a) - g(b) || rowOf(a) - rowOf(b) || Math.round(a.x) - Math.round(b.x) || a.y - b.y;
  const out = new Map<string, { dst: string; t: number }[]>();
  flow.links.forEach((l) => {
    if (!byId.has(l.src) || !byId.has(l.dst) || l.src === l.dst) return;
    out.set(l.src, [...(out.get(l.src) ?? []), { dst: l.dst, t: l.t }]);
  });
  // topological walk: the next number goes to a child of the step just numbered (first-said link first),
  // a step where branches merge waits until all its inputs are numbered; loops fall back to reading order
  const indeg = new Map(steps.map((n) => [n.id, 0]));
  flow.links.forEach((l) => {
    if (byId.has(l.src) && byId.has(l.dst) && l.src !== l.dst) indeg.set(l.dst, indeg.get(l.dst)! + 1);
  });
  const remaining = new Set(steps.map((n) => n.id));
  const order: string[] = [];
  let last: string | null = null as string | null;
  while (remaining.size) {
    const grp = Math.min(...[...remaining].map((id) => group.get(id) ?? 0));
    const inGrp = [...remaining].map((id) => byId.get(id)!).filter((n) => g(n) === grp);
    const ready = inGrp.filter((n) => indeg.get(n.id)! <= 0);
    const kids: { dst: string; t: number }[] = last
      ? (out.get(last) ?? [])
          .filter((e) => remaining.has(e.dst) && indeg.get(e.dst)! <= 0)
          .sort((a, b) => a.t - b.t || byId.get(a.dst)!.y - byId.get(b.dst)!.y)
      : [];
    const pick: LaidStep = kids.length ? byId.get(kids[0].dst)! : (ready.length ? ready : inGrp).sort(reading)[0];
    remaining.delete(pick.id);
    order.push(pick.id);
    (out.get(pick.id) ?? []).forEach((e) => indeg.set(e.dst, indeg.get(e.dst)! - 1));
    last = pick.id;
  }
  const num = new Map(order.map((id, i) => [id, i + 1]));
  return nodes.map((n) => (n.type === "step" ? { ...n, num: num.get(n.id) ?? 0 } : n));
}

type Pt = { x: number; y: number };

/** Orthogonal route with rounded corners (rows/lanes). */
export function orthoPath(points: Pt[], r = 10): string {
  if (points.length < 2) return "";
  let d = `M${points[0].x},${points[0].y}`;
  for (let i = 1; i < points.length - 1; i++) {
    const p = points[i - 1];
    const c = points[i];
    const n = points[i + 1];
    const r1 = Math.min(r, Math.hypot(c.x - p.x, c.y - p.y) / 2, Math.hypot(n.x - c.x, n.y - c.y) / 2);
    const ux = Math.sign(c.x - p.x);
    const uy = Math.sign(c.y - p.y);
    const vx = Math.sign(n.x - c.x);
    const vy = Math.sign(n.y - c.y);
    d += ` L${c.x - ux * r1},${c.y - uy * r1} Q${c.x},${c.y} ${c.x + vx * r1},${c.y + vy * r1}`;
  }
  const last = points[points.length - 1];
  return d + ` L${last.x},${last.y}`;
}

/** Smooth curve through dagre's route (same-row edges). */
export function smoothPath(points: Pt[]): string {
  if (points.length < 2) return "";
  let d = `M${points[0].x},${points[0].y}`;
  for (let i = 1; i < points.length - 1; i++) {
    const p = points[i];
    const n = points[i + 1];
    d += ` Q${p.x},${p.y} ${(p.x + n.x) / 2},${(p.y + n.y) / 2}`;
  }
  const last = points[points.length - 1];
  return d + ` L${last.x},${last.y}`;
}

export function edgePath(e: LaidEdge): string {
  return e.wrapped || e.kind === "gap" ? orthoPath(e.points) : smoothPath(e.points);
}

/** Word-wrap for SVG text (no layout engine there). */
export function wrapText(s: string, perLine: number, maxLines: number): string[] {
  const words = s.split(/\s+/).filter(Boolean);
  const lines: string[] = [];
  let cur = "";
  for (const w of words) {
    const next = cur ? `${cur} ${w}` : w;
    if (next.length <= perLine) cur = next;
    else {
      if (cur) lines.push(cur);
      cur = w.length > perLine ? w.slice(0, perLine - 1) + "…" : w;
    }
    if (lines.length === maxLines) break;
  }
  if (lines.length < maxLines && cur) lines.push(cur);
  if (lines.length === maxLines && words.join(" ").length > lines.join(" ").length) {
    const last = lines[maxLines - 1];
    lines[maxLines - 1] = (last.length >= perLine ? last.slice(0, perLine - 1) : last).trimEnd() + "…";
  }
  return lines;
}

const ROW_GAP = 44;
const MARGIN = 16;

/**
 * Long left-to-right flows get very wide, so text becomes tiny when fitted. This folds the columns
 * into rows ("snake") and keeps the fold that gives the biggest readable scale for the box.
 * Edges that cross into the next row are routed down through the gap between rows.
 */
export function layoutFlow(flow: FlowData, box?: { w: number; h: number; enough?: number }): FlowLayout {
  const base = layoutOnce(flow);
  if (!box || !base.nodes.length) return base;
  const scaleOf = (w: number, h: number) => Math.min(box.w / Math.max(1, w), box.h / Math.max(1, h));
  if (box.enough && scaleOf(base.width, base.height) >= box.enough) return base;

  // columns = distinct node centre x values (dagre puts every rank on one x)
  const centres = [...new Set(base.nodes.map((n) => Math.round(n.x + n.w / 2)))].sort((a, b) => a - b);
  if (centres.length < 3) return base;
  // bigger text wins, but every arrow that has to jump to the next row costs a little
  const score = (L: FlowLayout) => scaleOf(L.width, L.height) - 0.03 * L.wrapped;
  // try 2, 3, … rows with the columns shared out evenly (7 columns -> 4+3, never 6+1, which leaves a lonely box)
  let best = base;
  const tried = new Set<number>();
  for (let r = 2; r <= Math.ceil(centres.length / 2); r++) {
    const k = Math.ceil(centres.length / r);
    if (k < 2 || tried.has(k)) continue;
    tried.add(k);
    const cand = fold(base, centres, k, flow);
    if (score(cand) > score(best) + 0.02) best = cand;
  }
  return best;
}

function fold(base: FlowLayout, centres: number[], k: number, flow: FlowData): FlowLayout {
  const colOf = (n: LaidNode) => centres.indexOf(Math.round(n.x + n.w / 2));
  const rowH = base.height - MARGIN * 2 + ROW_GAP;
  const colX = (c: number) => centres[c] - centres[c - (c % k)] + centres[0];   // x of column inside its row
  const pos = new Map<string, { row: number; dx: number; dy: number }>();
  const place = (n: LaidNode) => {
    const c = colOf(n);
    const row = Math.floor(c / k);
    const dx = colX(c) - centres[c];
    const dy = row * rowH;
    pos.set(n.id, { row, dx, dy });
    return { ...n, x: n.x + dx, y: n.y + dy };
  };
  const steps = base.nodes.filter((n) => n.type === "step").map(place);
  const stepById = new Map(steps.map((n) => [n.id, n]));
  // a gap stays in its step's row (right next to it) so its dashed arrow never jumps rows
  const gapNodes = base.nodes
    .filter((n): n is LaidGap => n.type === "gap")
    .map((n) => {
      const st = stepById.get(n.gap.step);
      const placed = place(n);
      const sp = st ? pos.get(st.id)! : null;
      if (!st || !sp || pos.get(n.id)!.row === sp.row) return placed;
      const x = st.x + st.w + 54;
      const y = n.y + sp.dy;
      pos.set(n.id, { row: sp.row, dx: x - n.x, dy: sp.dy });
      return { ...n, x, y };
    });
  let nodes: LaidNode[] = [...steps, ...gapNodes];
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const rows = Math.ceil(centres.length / k);
  const rowTop = (r: number) => r * rowH + MARGIN;                 // top of row r
  const gapAbove = (r: number) => rowTop(r) - ROW_GAP / 2;         // middle of the free band above row r
  const minX = Math.min(...nodes.map((n) => n.x));
  let lane = 0;
  let side = 0;
  let edges: LaidEdge[] = base.edges.map((e) => {
    const a = pos.get(e.src)!;
    const b = pos.get(e.dst)!;
    const s = byId.get(e.src)!;
    const t = byId.get(e.dst)!;
    if (e.kind === "gap") {
      const sx = s.x + s.w;
      const sy = s.y + s.h / 2;
      const tx = t.x;
      const ty = t.y + t.h / 2;
      const mx = (sx + tx) / 2;
      return { ...e, points: [{ x: sx, y: sy }, { x: mx, y: sy }, { x: mx, y: ty }, { x: tx, y: ty }] };
    }
    if (a.row === b.row) {
      return {
        ...e,
        points: e.points.map((p) => ({ x: p.x + a.dx, y: p.y + a.dy })),
        labelPos: e.labelPos ? { x: e.labelPos.x + a.dx, y: e.labelPos.y + a.dy } : null,
      };
    }
    const tx = t.x;
    const ty = t.y + t.h / 2;
    const off = ((lane++ % 4) - 1.5) * 7;
    const inX = tx - 20 - Math.abs(off);
    // leave from the bottom, unless another box sits under the source in its row (stacked branch):
    // then leave from the right side and go down beside the column
    const blocked = nodes.some(
      (n) => n.id !== s.id && pos.get(n.id)?.row === a.row && n.y > s.y && n.x < s.x + s.w / 2 && n.x + n.w > s.x + s.w / 2,
    );
    const exit: Pt[] = blocked
      ? [{ x: s.x + s.w, y: s.y + s.h / 2 }, { x: s.x + s.w + 18 + Math.abs(off), y: s.y + s.h / 2 }]
      : [{ x: s.x + s.w / 2, y: s.y + s.h }];
    const sx = exit[exit.length - 1].x;
    if (b.row === a.row + 1) {
      // next row: down into the free band between the two rows, along it, into the target's left side
      const gy = gapAbove(b.row) + off;
      const points = [...exit, { x: sx, y: gy }, { x: inX, y: gy }, { x: inX, y: ty }, { x: tx, y: ty }];
      return { ...e, points, wrapped: true, labelPos: e.labelPos ? { x: (sx + inX) / 2, y: gy } : null };
    }
    // jumps over a row, or goes back up (retry loops): leave through the band under the source row,
    // run up/down a lane left of every box, then in through the band above the target row
    const laneX = minX - 26 - (side++ % 3) * 9;
    const y1 = rowTop(a.row) + (rowH - ROW_GAP) + ROW_GAP / 2 + off;
    const y2 = gapAbove(b.row) - off;
    const points = [
      ...exit,
      { x: sx, y: y1 },
      { x: laneX, y: y1 },
      { x: laneX, y: y2 },
      { x: inX, y: y2 },
      { x: inX, y: ty },
      { x: tx, y: ty },
    ];
    return { ...e, points, wrapped: true, labelPos: e.labelPos ? { x: (sx + laneX) / 2, y: y1 } : null };
  });
  // lanes may sit left of the first column: shift everything so nothing starts before the margin
  const allX = [...nodes.map((n) => n.x), ...edges.flatMap((e) => e.points.map((p) => p.x))];
  const shift = Math.max(0, MARGIN - Math.min(...allX));
  if (shift) {
    nodes = nodes.map((n) => ({ ...n, x: n.x + shift }));
    edges = edges.map((e) => ({
      ...e,
      points: e.points.map((p) => ({ x: p.x + shift, y: p.y })),
      labelPos: e.labelPos ? { x: e.labelPos.x + shift, y: e.labelPos.y } : null,
    }));
  }
  nodes = numbered(nodes, (n) => pos.get(n.id)?.row ?? 0, flowGroups(flow), flow);
  const width = Math.max(...nodes.map((n) => n.x + n.w), ...edges.flatMap((e) => e.points.map((p) => p.x))) + MARGIN;
  const height = Math.max(...nodes.map((n) => n.y + n.h), ...edges.flatMap((e) => e.points.map((p) => p.y))) + MARGIN;
  return { nodes, edges, width, height, openGaps: base.openGaps, rows, wrapped: edges.filter((e) => e.wrapped).length };
}
