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
  /** big folded maps: links drawn as a connector chip ("↪ 7") instead of a long line across rows */
  jumps?: { id: string; num: number; label: string }[];
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
const MAX_BAND_LINES = 2;     // lines allowed to run through the band between two rows
const MARGIN = 16;

/**
 * Long left-to-right flows get very wide, so text becomes tiny when fitted. This folds the columns
 * into rows ("snake") and keeps the fold that gives the biggest readable scale for the box.
 * Edges that cross into the next row are routed down through the gap between rows.
 */
export function layoutFlow(
  flow: FlowData,
  box?: { w: number; h: number; enough?: number; widthOnly?: boolean },
): FlowLayout {
  let base = layoutOnce(flow);
  if (!box || !base.nodes.length) return base;
  // widthOnly (big flows on the live map): fold so the rows fit the panel width at full size; the height may
  // grow past the panel and the view scrolls instead of shrinking the text
  const scaleOf = box.widthOnly
    ? (w: number) => Math.min(1, box.w / Math.max(1, w))
    : (w: number, h: number) => Math.min(box.w / Math.max(1, w), box.h / Math.max(1, h));
  if (box.enough && scaleOf(base.width, base.height) >= box.enough) return base;
  // a big map: loops ("review -> schedule again") and long jumps (a line over 3+ columns of boxes) are shown as
  // connector chips ("↪ 7"), and the map is laid out without them: dagre bends every other line around those
  // long lines, which made busy hub-shaped meetings unreadable. A small map keeps its loop lines ("wrong OTP ->
  // back to Enter OTP") when it isn't folded: they are easy to follow there.
  const big = flow.steps.length >= BIG_MAP_STEPS;
  const without = (drop: { id: string }[]) => {
    const ids = new Set(drop.map((l) => l.id));
    return { ...flow, links: flow.links.filter((l) => !ids.has(l.id)) };
  };
  const loops = loopLinks(flow);
  let chipLinks = loops;
  if (big) chipLinks = [...loops, ...longLinks(flow, layoutOnce(without(loops)), loops)];
  if (chipLinks.length) {
    const lean = layoutOnce(without(chipLinks));
    const unfolded = big ? withChips(lean, chipLinks) : base;
    if (box.enough && scaleOf(unfolded.width, unfolded.height) >= box.enough) return unfolded;
    base = big ? unfolded : lean;
  }

  // columns = distinct node centre x values (dagre puts every rank on one x)
  const centres = [...new Set(base.nodes.map((n) => Math.round(n.x + n.w / 2)))].sort((a, b) => a - b);
  if (centres.length < 3) return base;
  // bigger text wins, but every arrow that has to jump to the next row costs a little
  const score = (L: FlowLayout) => scaleOf(L.width, L.height) - (box.widthOnly ? 0.004 : 0.03) * L.wrapped;
  // try 2, 3, … rows with the columns shared out evenly (7 columns -> 4+3, never 6+1, which leaves a lonely box)
  let best = base;
  const tried = new Set<number>();
  for (let r = 2; r <= Math.ceil(centres.length / 2); r++) {
    const k = Math.ceil(centres.length / r);
    if (k < 2 || tried.has(k)) continue;
    tried.add(k);
    const cand = fold(base, centres, k, flow, chipLinks);
    if (score(cand) > score(best) + 0.02) best = cand;
  }
  return best;
}

type Rect = { x: number; y: number; w: number; h: number };
const overlaps = (a: Rect, b: Rect, pad = 8) =>
  a.x < b.x + b.w + pad && b.x < a.x + a.w + pad && a.y < b.y + b.h + pad && b.y < a.y + a.h + pad;
function segCrossesRect(a: Pt, b: Pt, r: Rect, pad = 4): boolean {
  for (let t = 0; t <= 1; t += 0.05) {
    const x = a.x + (b.x - a.x) * t;
    const y = a.y + (b.y - a.y) * t;
    if (x > r.x - pad && x < r.x + r.w + pad && y > r.y - pad && y < r.y + r.h + pad) return true;
  }
  return false;
}

/** Where a gap box goes: right next to its own step, on free space (no box, no arrow), so its short dashed
 *  arrow never crosses the flow. Tried in order: right, above-right, below-right, above, below. */
function placeGap(st: Rect, others: Rect[], lines: Pt[][]): Rect {
  const right = st.x + st.w + 34;
  const cands: Rect[] = [
    { x: right, y: st.y + (st.h - GAP_H) / 2, w: GAP_W, h: GAP_H },
    { x: right, y: st.y - GAP_H - 14, w: GAP_W, h: GAP_H },
    { x: right, y: st.y + st.h + 14, w: GAP_W, h: GAP_H },
    { x: st.x + (st.w - GAP_W) / 2, y: st.y - GAP_H - 22, w: GAP_W, h: GAP_H },
    { x: st.x + (st.w - GAP_W) / 2, y: st.y + st.h + 22, w: GAP_W, h: GAP_H },
  ];
  const free = (c: Rect) =>
    !others.some((o) => overlaps(c, o)) && !lines.some((pts) => pts.some((p, i) => i > 0 && segCrossesRect(pts[i - 1], p, c)));
  return cands.find(free) ?? cands.find((c) => !others.some((o) => overlaps(c, o))) ?? cands[0];
}

/** Dashed arrow from a step to its gap box (side or top/bottom, whichever faces the gap). */
function gapRoute(s: Rect, t: Rect): Pt[] {
  if (t.x >= s.x + s.w) {
    const sx = s.x + s.w;
    const sy = s.y + s.h / 2;
    const ty = t.y + t.h / 2;
    const mx = (sx + t.x) / 2;
    return [{ x: sx, y: sy }, { x: mx, y: sy }, { x: mx, y: ty }, { x: t.x, y: ty }];
  }
  const cx = s.x + s.w / 2;
  return t.y < s.y ? [{ x: cx, y: s.y }, { x: cx, y: t.y + t.h }] : [{ x: cx, y: s.y + s.h }, { x: cx, y: t.y }];
}

/** Links that close a loop (go back to a step already on the way here), found by walking from the start steps
 *  in the order things were said. */
function loopLinks(flow: FlowData): FlowData["links"] {
  const ids = new Set(flow.steps.map((s) => s.id));
  const out = new Map<string, FlowData["links"]>();
  flow.links
    .filter((l) => ids.has(l.src) && ids.has(l.dst) && l.src !== l.dst)
    .sort((a, b) => a.t - b.t)
    .forEach((l) => out.set(l.src, [...(out.get(l.src) ?? []), l]));
  const state = new Map<string, 1 | 2>();      // 1 = on the current path, 2 = done
  const loops: FlowData["links"] = [];
  const visit = (id: string) => {
    state.set(id, 1);
    for (const l of out.get(id) ?? []) {
      const st = state.get(l.dst);
      if (st === 1) loops.push(l);
      else if (!st) visit(l.dst);
    }
    state.set(id, 2);
  };
  const order = [...flow.steps].sort((a, b) => (a.kind === "start" ? 0 : 1) - (b.kind === "start" ? 0 : 1) || a.t - b.t);
  order.forEach((s) => !state.has(s.id) && visit(s.id));
  return loops;
}

function fold(base: FlowLayout, centres: number[], k: number, flow: FlowData, loops: FlowData["links"] = []): FlowLayout {
  const colOf = (n: LaidNode) => centres.indexOf(Math.round(n.x + n.w / 2));
  const colX = (c: number) => centres[c] - centres[c - (c % k)] + centres[0];   // x of column inside its row
  const rows = Math.ceil(centres.length / k);

  // 1. steps: row and x inside the row (y still dagre's, made compact per row below)
  const rowOf = new Map<string, number>();
  const dxOf = new Map<string, number>();
  const steps: LaidStep[] = base.nodes
    .filter((n): n is LaidStep => n.type === "step")
    .map((n) => {
      const c = colOf(n);
      rowOf.set(n.id, Math.floor(c / k));
      dxOf.set(n.id, colX(c) - centres[c]);
      return { ...n, x: n.x + dxOf.get(n.id)! };
    });
  const stepById = new Map(steps.map((n) => [n.id, n]));
  const sameRow = base.edges
    .filter((e) => e.kind !== "gap" && rowOf.get(e.src) === rowOf.get(e.dst))
    .map((e) => {
      const dx = dxOf.get(e.src)!;
      return {
        ...e,
        points: e.points.map((p) => ({ x: p.x + dx, y: p.y })),
        labelPos: e.labelPos ? { x: e.labelPos.x + dx, y: e.labelPos.y } : null,
      };
    });

  // 2. gap boxes next to their step, on free space of that row
  const gapNodes: LaidGap[] = [];
  base.nodes
    .filter((n): n is LaidGap => n.type === "gap")
    .forEach((n) => {
      const st = stepById.get(n.gap.step);
      if (!st) return;
      const r = rowOf.get(st.id)!;
      const others: Rect[] = [...steps.filter((x) => rowOf.get(x.id) === r && x.id !== st.id), ...gapNodes.filter((g) => rowOf.get(g.id) === r)];
      const lines = sameRow.filter((e) => rowOf.get(e.src) === r).map((e) => e.points);
      const at = placeGap(st, others, lines);
      rowOf.set(n.id, r);
      gapNodes.push({ ...n, x: at.x, y: at.y });
    });

  // 3. compact rows: each row is as tall as what is in it (not as tall as the whole unfolded graph)
  const top: number[] = [];
  const bottom: number[] = [];
  for (let r = 0; r < rows; r++) {
    const ys = [
      ...[...steps, ...gapNodes].filter((n) => rowOf.get(n.id) === r).flatMap((n) => [n.y, n.y + n.h]),
      ...sameRow.filter((e) => rowOf.get(e.src) === r).flatMap((e) => e.points.map((p) => p.y)),
    ];
    top.push(ys.length ? Math.min(...ys) : 0);
    bottom.push(ys.length ? Math.max(...ys) : 0);
  }
  const dy: number[] = [];
  let y = MARGIN;
  for (let r = 0; r < rows; r++) {
    dy.push(y - top[r]);
    y += bottom[r] - top[r] + ROW_GAP;
  }
  const rowTop = (r: number) => top[r] + dy[r];
  const rowBottom = (r: number) => bottom[r] + dy[r];
  const shiftY = <T extends { y: number }>(p: T, r: number): T => ({ ...p, y: p.y + dy[r] });

  let nodes: LaidNode[] = [...steps, ...gapNodes].map((n) => shiftY(n, rowOf.get(n.id)!));
  const byId = new Map(nodes.map((n) => [n.id, n]));

  // 4. which links are drawn as lines. A line that goes back (a loop), skips a row, or is one of many lines into
  //    the next row turns the map into spaghetti; those become a connector chip on the source box ("↪ 7").
  const col = new Map(base.nodes.map((n) => [n.id, colOf(n)]));
  const jumpIds = new Set<string>();
  const perBand = new Map<number, number>();
  base.edges
    .filter((e) => e.kind !== "gap")
    .map((e) => ({ e, ra: rowOf.get(e.src)!, rb: rowOf.get(e.dst)!, back: col.get(e.dst)! <= col.get(e.src)! }))
    .sort((a, b) => col.get(b.e.src)! - col.get(a.e.src)! || col.get(a.e.dst)! - col.get(b.e.dst)!)   // snake first
    .forEach(({ e, ra, rb, back }) => {
      if (ra === rb) {
        if (back) jumpIds.add(e.id);
        return;
      }
      const used = perBand.get(ra) ?? 0;
      if (back || rb !== ra + 1 || used >= MAX_BAND_LINES) jumpIds.add(e.id);
      else perBand.set(ra, used + 1);
    });
  const minX = Math.min(...nodes.map((n) => n.x));
  let lane = 0;
  let side = 0;
  let edges: LaidEdge[] = base.edges.filter((e) => !jumpIds.has(e.id)).map((e) => {
    const ra = rowOf.get(e.src)!;
    const rb = rowOf.get(e.dst)!;
    const s = byId.get(e.src)!;
    const t = byId.get(e.dst)!;
    if (e.kind === "gap") return { ...e, points: gapRoute(s, t) };
    if (ra === rb) {
      const same = sameRow.find((x) => x.id === e.id)!;
      return { ...same, points: same.points.map((p) => shiftY(p, ra)), labelPos: same.labelPos ? shiftY(same.labelPos, ra) : null };
    }
    const tx = t.x;
    const ty = t.y + t.h / 2;
    const off = ((lane++ % 4) - 1.5) * 7;
    const inX = tx - 20 - Math.abs(off);
    // leave from the bottom, unless another box sits under the source in its row (stacked branch):
    // then leave from the right side and go down beside the column
    const blocked = nodes.some(
      (n) => n.id !== s.id && rowOf.get(n.id) === ra && n.y > s.y && n.x < s.x + s.w / 2 && n.x + n.w > s.x + s.w / 2,
    );
    const exit: Pt[] = blocked
      ? [{ x: s.x + s.w, y: s.y + s.h / 2 }, { x: s.x + s.w + 18 + Math.abs(off), y: s.y + s.h / 2 }]
      : [{ x: s.x + s.w / 2, y: s.y + s.h }];
    const sx = exit[exit.length - 1].x;
    if (rb === ra + 1) {
      // next row: down into the free band between the two rows, along it, into the target's left side
      const gy = rowBottom(ra) + ROW_GAP / 2 + off;
      const points = [...exit, { x: sx, y: gy }, { x: inX, y: gy }, { x: inX, y: ty }, { x: tx, y: ty }];
      return { ...e, points, wrapped: true, labelPos: e.labelPos ? { x: (sx + inX) / 2, y: gy } : null };
    }
    // jumps over a row, or goes back up (retry loops): leave through the band under the source row,
    // run up/down a lane left of every box, then in through the band above the target row
    const laneX = minX - 26 - (side++ % 3) * 9;
    const y1 = rowBottom(ra) + ROW_GAP / 2 + off;
    const y2 = rowTop(rb) - ROW_GAP / 2 - off;
    const points = [...exit, { x: sx, y: y1 }, { x: laneX, y: y1 }, { x: laneX, y: y2 }, { x: inX, y: y2 }, { x: inX, y: ty }, { x: tx, y: ty }];
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
  nodes = numbered(nodes, (n) => rowOf.get(n.id) ?? 0, flowGroups(flow), flow);
  const chips = [...base.edges.filter((e) => jumpIds.has(e.id)), ...loops];
  if (chips.length) {
    const numOf = new Map(nodes.flatMap((n) => (n.type === "step" ? [[n.id, n]] : [])) as [string, LaidStep][]);
    nodes = nodes.map((n) => {
      if (n.type !== "step") return n;
      const jumps = chips
        .filter((e) => e.src === n.id && numOf.has(e.dst))
        .map((e) => ({ id: e.dst, num: numOf.get(e.dst)!.num, label: numOf.get(e.dst)!.step.label }))
        .sort((a, b) => a.num - b.num);
      return jumps.length ? { ...n, jumps } : n;
    });
  }
  const width = Math.max(...nodes.map((n) => n.x + n.w), ...edges.flatMap((e) => e.points.map((p) => p.x))) + MARGIN;
  const height = Math.max(...nodes.map((n) => n.y + n.h), ...edges.flatMap((e) => e.points.map((p) => p.y))) + MARGIN;
  return { nodes, edges, width, height, openGaps: base.openGaps, rows, wrapped: edges.filter((e) => e.wrapped).length + jumpIds.size + loops.length };
}

const BIG_MAP_STEPS = 12;
const LONG_LINK_COLS = 3;     // a line that spans this many columns or more becomes a chip on big maps

/** Links that jump over 2+ columns of boxes in a left-to-right layout (drawn as chips on big maps). */
function longLinks(flow: FlowData, L: FlowLayout, skip: { id: string }[]): FlowData["links"] {
  const centres = [...new Set(L.nodes.map((n) => Math.round(n.x + n.w / 2)))].sort((a, b) => a - b);
  const col = new Map(L.nodes.map((n) => [n.id, centres.indexOf(Math.round(n.x + n.w / 2))]));
  const skipIds = new Set(skip.map((l) => l.id));
  return flow.links.filter(
    (l) => !skipIds.has(l.id) && col.has(l.src) && col.has(l.dst) && col.get(l.dst)! - col.get(l.src)! >= LONG_LINK_COLS,
  );
}

/** Put connector chips ("↪ 7") on the boxes the given links start from (links not drawn as lines). */
function withChips(L: FlowLayout, links: { src: string; dst: string }[]): FlowLayout {
  const byId = new Map(L.nodes.flatMap((n) => (n.type === "step" ? [[n.id, n]] : [])) as [string, LaidStep][]);
  const nodes = L.nodes.map((n) => {
    if (n.type !== "step") return n;
    const jumps = links
      .filter((l) => l.src === n.id && byId.has(l.dst))
      .map((l) => ({ id: l.dst, num: byId.get(l.dst)!.num, label: byId.get(l.dst)!.step.label }))
      .sort((a, b) => a.num - b.num);
    return jumps.length ? { ...n, jumps } : n;
  });
  return { ...L, nodes, wrapped: L.wrapped + links.length };
}

/** "↪ 7, 12" — the steps a box continues to through connector chips. */
export function jumpText(n: LaidStep, max = 3): string {
  const j = n.jumps ?? [];
  return "↪ " + j.slice(0, max).map((x) => x.num).join(", ") + (j.length > max ? " …" : "");
}
