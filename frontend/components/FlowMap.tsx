"use client";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import { edgePath, layoutFlow, type LaidEdge } from "@/core/flowLayout";
import { conflictTag, issueTag, stepIcon, stepNote, type StepNote } from "@/core/flowLook";
import {
  Bug,
  ChevronDown,
  ChevronUp,
  CircleCheckBig,
  CircleHelp,
  Diamond,
  Flag,
  GitBranch,
  LoaderCircle,
  Play,
  Quote as QuoteIcon,
  ScanSearch,
  ShieldCheck,
  Square,
  TriangleAlert,
  X,
} from "lucide-react";
import type { Evidence, FlowContradiction, FlowData, FlowGap, FlowStats, FlowStep, StepKind } from "@/core/types";
import type { FlowStatus } from "@/core/store";
import { fmtTime, speakerName } from "@/core/store";

export type FlowFocus = { kind: "step" | "gap" | "conflict"; id: string; n: number };
type Selection = { kind: "step" | "gap" | "conflicts"; id: string } | null;

const FLASH_MS = 9000;
const PULSE_MS = 3200;
const BODY_MIN = 260;
const BODY_MAX = 500;

const KIND_LABEL: Record<StepKind, string> = { start: "Start", step: "Step", decision: "Decision", end: "End" };

function KindIcon({ kind }: { kind: StepKind }) {
  const p = { size: 13, strokeWidth: 2.4, "aria-hidden": true } as const;
  if (kind === "start") return <Play {...p} />;
  if (kind === "decision") return <Diamond {...p} />;
  if (kind === "end") return <Flag {...p} />;
  return <Square {...p} />;
}

// ---------------- nodes ----------------
type StepData = {
  step: FlowStep;
  num: number;
  bugs: number;
  conflicts: number;
  gaps: number;
  note: StepNote | null;
  fresh: boolean;
  selected: boolean;
  focused: boolean;
};
type GapData = { gap: FlowGap; fresh: boolean; selected: boolean; focused: boolean };

const StepNode = memo(function StepNode({ data }: NodeProps<Node<StepData>>) {
  const { step, num, bugs, conflicts, note, fresh, selected, focused } = data;
  const Icon = stepIcon(step);
  const state = bugs ? "is-bug" : conflicts ? "is-conflict" : "";
  return (
    <div
      className={`fm-node kind-${step.kind} ${state} ${fresh ? "is-fresh" : ""} ${selected ? "is-selected" : ""} ${focused ? "is-focus" : ""}`}
      title="Click to see who said it"
    >
      <Handle type="target" position={Position.Left} className="fm-handle" isConnectable={false} />
      <span className="fm-top">
        <span className="fm-num">{num}</span>
        <span className="fm-kind">{KIND_LABEL[step.kind]}</span>
        {fresh && <b className="fm-fresh">{step.updated_t != null ? "Updated" : "New"}</b>}
      </span>
      <span className="fm-main">
        <span className="fm-icon">
          <Icon size={16} strokeWidth={2.1} aria-hidden />
        </span>
        <span className="fm-label" title={step.label}>
          {step.label}
        </span>
      </span>
      {note && (
        <span className={`fm-note-line ${note.tone}`} title={note.tone === "quote" ? `“${note.text}”` : note.text}>
          {note.tone === "quote" ? `“${note.text}”` : note.text}
        </span>
      )}
      {(bugs > 0 || conflicts > 0) && (
        <span className="fm-tags">
          {bugs > 0 && (
            <span className="fm-tag bug">
              <Bug size={11} aria-hidden /> {issueTag(bugs)}
            </span>
          )}
          {conflicts > 0 && (
            <span className="fm-tag conflict">
              <TriangleAlert size={11} aria-hidden /> {conflictTag(conflicts)}
            </span>
          )}
        </span>
      )}
      <Handle type="source" id="r" position={Position.Right} className="fm-handle" isConnectable={false} />
      <Handle type="source" id="b" position={Position.Bottom} className="fm-handle" isConnectable={false} />
    </div>
  );
});

const GapNode = memo(function GapNode({ data }: NodeProps<Node<GapData>>) {
  const { gap, fresh, selected, focused } = data;
  return (
    <div className={`fm-gap ${fresh ? "is-fresh" : ""} ${selected ? "is-selected" : ""} ${focused ? "is-focus" : ""}`} title={gap.question}>
      <Handle type="target" position={Position.Left} className="fm-handle" isConnectable={false} />
      <CircleHelp size={15} aria-hidden />
      <span>
        <b>{gap.branch}?</b>
        <small>Nobody said</small>
      </span>
    </div>
  );
});

// ---------------- edges ----------------
// Draws the route computed in flowLayout (same as the report), so folded rows never cut through a box.
type RoutedData = { laid: LaidEdge; tone: "normal" | "fail" | "gap" };
const RoutedEdge = memo(function RoutedEdge({ id, data, markerEnd, style, sourceX, sourceY, targetX, targetY }: EdgeProps<Edge<RoutedData>>) {
  const laid = data!.laid;
  const d =
    laid.points.length >= 2 ? edgePath(laid) : `M${sourceX},${sourceY} L${targetX},${targetY}`;
  return (
    <>
      <BaseEdge id={id} path={d} markerEnd={markerEnd} style={style} />
      {laid.label && laid.labelPos && (
        <EdgeLabelRenderer>
          <div
            className={`fm-elabel ${data!.tone === "fail" ? "fail" : ""}`}
            style={{ transform: `translate(-50%, -50%) translate(${laid.labelPos.x}px, ${laid.labelPos.y}px)` }}
          >
            {laid.label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
});

const NODE_TYPES = { step: StepNode, gap: GapNode };
const EDGE_TYPES = { routed: RoutedEdge };

// ---------------- layout ----------------
function buildGraph(
  flow: FlowData,
  flash: Record<string, number>,
  sel: Selection,
  now: number,
  box: { w: number; h: number },
  pulse: { id: string; at: number } | null,
) {
  // fold long flows into rows when fitting the whole line would make the text too small
  const L = layoutFlow(flow, { ...box, enough: 0.8 });
  const fresh = (id: string) => !!flash[id] && now - flash[id] < FLASH_MS;
  const focused = (nodeId: string) => !!pulse && pulse.id === nodeId && now - pulse.at < PULSE_MS;
  const nodes: Node[] = L.nodes.map((n) =>
    n.type === "step"
      ? {
          id: n.id,
          type: "step",
          position: { x: n.x, y: n.y },
          data: {
            step: n.step,
            num: n.num,
            bugs: n.bugs,
            conflicts: n.conflicts,
            gaps: L.openGaps.filter((x) => x.step === n.id).length,
            note: stepNote(flow, n.step),
            fresh: fresh(n.id),
            selected: sel?.kind === "step" && sel.id === n.id,
            focused: focused(n.id),
          } satisfies StepData,
          draggable: false,
        }
      : {
          id: n.id,
          type: "gap",
          position: { x: n.x, y: n.y },
          data: {
            gap: n.gap,
            fresh: fresh(n.gap.id),
            selected: sel?.kind === "gap" && sel.id === n.gap.id,
            focused: focused(n.id),
          } satisfies GapData,
          draggable: false,
        },
  );
  const edges: Edge[] = L.edges.map((e) => {
    const color = e.kind === "fail" ? "#dc2626" : e.kind === "gap" ? "#d97706" : "#7c8597";
    return {
      id: e.id,
      source: e.src,
      target: e.dst,
      type: "routed",
      data: { laid: e, tone: e.kind } satisfies RoutedData,
      animated: e.kind === "gap",
      className: `fm-edge ${e.kind} ${fresh(e.id) ? "is-fresh" : ""}`,
      style: {
        stroke: color,
        strokeWidth: e.kind === "fail" ? 2.2 : 1.8,
        strokeDasharray: e.kind === "gap" ? "6 5" : undefined,
      },
      markerEnd: { type: MarkerType.ArrowClosed, color, width: 18, height: 18 },
    };
  });
  return { nodes, edges, openGaps: L.openGaps, width: L.width, height: L.height };
}

// ---------------- panel ----------------
export interface FlowMapProps {
  flow: FlowData;
  stats: FlowStats | null;
  status: FlowStatus | null;
  flash: Record<string, number>;
  speakers: Record<string, string>;
  live: boolean;
  focus: FlowFocus | null;
  onCheckGaps: () => void;
  onOpenCard: (cardId: string) => void;
}

export function FlowMap(props: FlowMapProps) {
  return (
    <ReactFlowProvider>
      <FlowMapInner {...props} />
    </ReactFlowProvider>
  );
}

function FlowMapInner({ flow, stats, status, flash, speakers, live, focus, onCheckGaps, onOpenCard }: FlowMapProps) {
  const [sel, setSel] = useState<Selection>(null);
  const [open, setOpen] = useState(true);
  const [now, setNow] = useState(() => Date.now());
  const [pulse, setPulse] = useState<{ id: string; at: number } | null>(null);
  const rf = useReactFlow();
  const gapCursor = useRef(0);

  // re-render while highlights are fading
  useEffect(() => {
    const newest = Math.max(0, ...Object.values(flash), pulse ? pulse.at - FLASH_MS + PULSE_MS : 0);
    if (Date.now() - newest > FLASH_MS) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 1000);
    const stop = setTimeout(() => clearInterval(id), FLASH_MS + 500);
    return () => {
      clearInterval(id);
      clearTimeout(stop);
    };
  }, [flash, pulse]);

  const bodyRef = useRef<HTMLDivElement>(null);
  const [boxW, setBoxW] = useState(1000);
  const { nodes, edges, openGaps, width, height } = useMemo(
    () => buildGraph(flow, flash, sel, now, { w: boxW - 40, h: BODY_MAX - 70 }, pulse),
    [flow, flash, sel, now, boxW, pulse],
  );
  useEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    // refit whenever the panel itself changes size (sidebar toggle, window resize, new height)
    const ro = new ResizeObserver(() => {
      setBoxW(el.clientWidth || 1000);
      requestAnimationFrame(() => rf.fitView({ padding: 0.06, maxZoom: 1.15 }));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [open, rf]);
  // grow with the graph so text stays readable (scale is limited by width), within limits
  const scale = Math.min(1.1, Math.max(0.45, (boxW - 40) / Math.max(1, width)));
  const bodyH = Math.round(Math.min(BODY_MAX, Math.max(BODY_MIN, height * scale + 70)));
  const openConflicts = flow.contradictions.filter((c) => !c.resolved);

  // keep the whole map in view as it grows
  const shape = `${nodes.length}:${edges.length}:${bodyH}`;
  useEffect(() => {
    if (!open) return;
    const id = setTimeout(() => rf.fitView({ padding: 0.06, duration: 450, maxZoom: 1.15 }), 60);
    return () => clearTimeout(id);
  }, [shape, open, rf]);

  const centerOn = useCallback(
    (nodeId: string, highlight = false) => {
      if (highlight) setPulse({ id: nodeId, at: Date.now() });
      setTimeout(() => rf.fitView({ nodes: [{ id: nodeId }], padding: 0.9, duration: 500, maxZoom: 1.2 }), 80);
    },
    [rf],
  );

  // focus requests from callouts / question cards
  useEffect(() => {
    if (!focus) return;
    setOpen(true);
    // jump to the box and pulse it, so it's clear which part of the flow the Spark find is about
    if (focus.kind === "gap") {
      setSel({ kind: "gap", id: focus.id });
      const g = flow.gaps.find((x) => x.id === focus.id);
      if (g) centerOn(g.resolved || g.dismissed ? g.step : `gap:${g.id}`, true);
    } else if (focus.kind === "step") {
      setSel({ kind: "step", id: focus.id });
      centerOn(focus.id, true);
    } else {
      setSel({ kind: "conflicts", id: focus.id });
      const c = flow.contradictions.find((x) => x.id === focus.id);
      if (c?.step && flow.steps.some((x) => x.id === c.step)) centerOn(c.step, true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus?.n]);

  function nextGap() {
    if (!openGaps.length) return;
    const g = openGaps[gapCursor.current % openGaps.length];
    gapCursor.current += 1;
    setSel({ kind: "gap", id: g.id });
    centerOn(`gap:${g.id}`, true);
  }

  const reviewing = status?.state === "thinking" && status.mode === "gap_review";
  const mapping = status?.state === "thinking" && !reviewing;

  return (
    <section className={`fm ${open ? "" : "is-collapsed"}`} aria-label="Flow Map">
      <div className="fm-head">
        <button className="fm-title" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          <GitBranch size={16} aria-hidden />
          <span className="section-label">Flow Map</span>
          <span className="fm-count">
            {flow.steps.length} step{flow.steps.length === 1 ? "" : "s"}
          </span>
          {open ? <ChevronUp size={15} aria-hidden /> : <ChevronDown size={15} aria-hidden />}
        </button>
        {mapping && (
          <span className="fm-busy">
            <LoaderCircle size={13} className="spin" aria-hidden /> Muse Spark is mapping…
          </span>
        )}
        <div className="fm-head-right">
          {open && (
            <span className="fm-legend" aria-label="Legend">
              <i className="lg-solid" /> discussed
              <i className="lg-dash" /> gap
              <i className="lg-fail" /> fail path
            </span>
          )}
          {openConflicts.length > 0 && (
            <button
              className="fm-pill conflict"
              onClick={() => {
                setSel({ kind: "conflicts", id: openConflicts[0].id });
                const st = openConflicts[0].step;
                if (st && flow.steps.some((x) => x.id === st)) centerOn(st, true);
              }}
            >
              <TriangleAlert size={13} aria-hidden /> {openConflicts.length} conflict{openConflicts.length > 1 ? "s" : ""}
            </button>
          )}
          {openGaps.length > 0 && (
            <button className="fm-pill gap" onClick={nextGap} title="Jump to the next gap">
              <CircleHelp size={13} aria-hidden /> {openGaps.length} gap{openGaps.length > 1 ? "s" : ""}
            </button>
          )}
          {stats && stats.accepted > 0 && (
            <span
              className="fm-checked"
              title={`Every box and arrow is backed by a quote checked against the transcript. ${stats.rejected} AI suggestion${
                stats.rejected === 1 ? " was" : "s were"
              } rejected because the quote wasn’t really said.`}
            >
              <ShieldCheck size={13} aria-hidden /> {stats.accepted} quote-checked
              {stats.rejected > 0 && <em> · {stats.rejected} rejected</em>}
            </span>
          )}
          {live && (
            <button className="btn fm-check" onClick={onCheckGaps} disabled={reviewing || status?.state === "thinking"}>
              {reviewing ? <LoaderCircle size={14} className="spin" aria-hidden /> : <ScanSearch size={14} aria-hidden />}
              {reviewing ? "Checking…" : "Check gaps"}
            </button>
          )}
        </div>
      </div>

      {open && (
        <div className="fm-body" ref={bodyRef} style={{ height: bodyH }}>
          <div className="fm-canvas">
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={NODE_TYPES}
              edgeTypes={EDGE_TYPES}
              nodesDraggable={false}
              nodesConnectable={false}
              elementsSelectable={false}
              fitView
              fitViewOptions={{ padding: 0.06, maxZoom: 1.15 }}
              minZoom={0.25}
              maxZoom={1.6}
              attributionPosition="top-right"
              onNodeClick={(_, n) => {
                if (n.type === "gap") setSel({ kind: "gap", id: n.id.slice(4) });
                else setSel({ kind: "step", id: n.id });
              }}
              onPaneClick={() => setSel(null)}
            >
              <Background gap={18} size={1} color="#dfe3ea" />
              <Controls showInteractive={false} position="bottom-right" />
            </ReactFlow>
          </div>
          {sel && (
            <Drawer
              flow={flow}
              sel={sel}
              speakers={speakers}
              onClose={() => setSel(null)}
              onSelect={(s) => {
                setSel(s);
                if (s?.kind === "step") centerOn(s.id);
              }}
              onOpenCard={onOpenCard}
            />
          )}
        </div>
      )}
    </section>
  );
}

// ---------------- evidence drawer ----------------
function QuoteRow({ ev, speakers, tone }: { ev: Evidence; speakers: Record<string, string>; tone?: string }) {
  return (
    <blockquote className={`fm-quote ${tone ?? ""}`}>
      <p>“{ev.quote}”</p>
      <footer>
        {speakerName(speakers, ev.speaker)} · {fmtTime(ev.t)}
      </footer>
    </blockquote>
  );
}

function Drawer({
  flow,
  sel,
  speakers,
  onClose,
  onSelect,
  onOpenCard,
}: {
  flow: FlowData;
  sel: NonNullable<Selection>;
  speakers: Record<string, string>;
  onClose: () => void;
  onSelect: (s: Selection) => void;
  onOpenCard: (cardId: string) => void;
}) {
  const stepById = (id: string | null) => flow.steps.find((s) => s.id === id);
  let body: React.ReactNode = null;
  let title = "";

  if (sel.kind === "step") {
    const s = stepById(sel.id);
    if (!s) return null;
    title = s.label;
    const bugs = flow.bugs.filter((b) => b.step === s.id);
    const gaps = flow.gaps.filter((g) => g.step === s.id);
    const conflicts = flow.contradictions.filter((c) => c.step === s.id);
    const into = flow.links.filter((l) => l.dst === s.id);
    const out = flow.links.filter((l) => l.src === s.id);
    body = (
      <>
        <span className={`fm-kind-chip kind-${s.kind}`}>
          <KindIcon kind={s.kind} /> {KIND_LABEL[s.kind]}
        </span>
        <h4 className="fm-sec">
          <QuoteIcon size={13} aria-hidden /> Said in the meeting
        </h4>
        {s.evidence.map((ev) => (
          <QuoteRow key={ev.line} ev={ev} speakers={speakers} />
        ))}
        {(into.length > 0 || out.length > 0) && (
          <div className="fm-links">
            {into.map((l) => (
              <button key={l.id} onClick={() => onSelect({ kind: "step", id: l.src })}>
                ← {stepById(l.src)?.label}
                {l.label && <em> ({l.label})</em>}
              </button>
            ))}
            {out.map((l) => (
              <button key={l.id} className={l.type === "fail" ? "fail" : ""} onClick={() => onSelect({ kind: "step", id: l.dst })}>
                → {stepById(l.dst)?.label}
                {l.label && <em> ({l.label})</em>}
              </button>
            ))}
          </div>
        )}
        {bugs.length > 0 && (
          <>
            <h4 className="fm-sec bug">
              <Bug size={13} aria-hidden /> Bugs
            </h4>
            {bugs.map((b) => (
              <div key={b.id} className={`fm-item ${b.fixed ? "done" : "bug"}`}>
                <b>
                  {b.fixed && <CircleCheckBig size={13} aria-hidden />} {b.text}
                  {b.fixed && <em> · fixed</em>}
                </b>
                <QuoteRow ev={b.evidence[0]} speakers={speakers} tone="bug" />
                {b.fix && <QuoteRow ev={b.fix} speakers={speakers} tone="ok" />}
              </div>
            ))}
          </>
        )}
        {gaps.filter((g) => !g.dismissed).length > 0 && (
          <>
            <h4 className="fm-sec gap">
              <CircleHelp size={13} aria-hidden /> Gaps
            </h4>
            {gaps
              .filter((g) => !g.dismissed)
              .map((g) => (
                <button key={g.id} className={`fm-item fm-item-btn ${g.resolved ? "done" : "gap"}`} onClick={() => onSelect({ kind: "gap", id: g.id })}>
                  <b>{g.question}</b>
                  {g.resolved && g.answer && <span className="fm-answer">→ {g.answer}</span>}
                </button>
              ))}
          </>
        )}
        {conflicts.length > 0 && (
          <>
            <h4 className="fm-sec conflict">
              <TriangleAlert size={13} aria-hidden /> Conflicts
            </h4>
            {conflicts.map((c) => (
              <ConflictItem key={c.id} c={c} speakers={speakers} />
            ))}
          </>
        )}
      </>
    );
  } else if (sel.kind === "gap") {
    const g = flow.gaps.find((x) => x.id === sel.id);
    if (!g) return null;
    const s = stepById(g.step);
    title = g.resolved ? "Gap answered" : "Gap — nobody covered this";
    body = (
      <>
        <p className="fm-gap-q">{g.question}</p>
        {s && (
          <button className="fm-step-link" onClick={() => onSelect({ kind: "step", id: s.id })}>
            On step: <b>{s.label}</b>
          </button>
        )}
        <h4 className="fm-sec">
          <QuoteIcon size={13} aria-hidden /> Where this step came up
        </h4>
        {g.evidence.map((ev) => (
          <QuoteRow key={ev.line} ev={ev} speakers={speakers} />
        ))}
        {g.resolved ? (
          <>
            <h4 className="fm-sec ok">
              <CircleCheckBig size={13} aria-hidden /> Answer
            </h4>
            {g.answer && <p className="fm-answer">{g.answer}</p>}
            {g.resolved_by && <QuoteRow ev={g.resolved_by} speakers={speakers} tone="ok" />}
          </>
        ) : (
          <p className="fm-note">Muse Spark found that this branch was never discussed. It’s also in Open questions.</p>
        )}
        {g.card_id && (
          <button className="btn" onClick={() => onOpenCard(g.card_id!)}>
            <CircleHelp size={14} aria-hidden /> Open question card
          </button>
        )}
      </>
    );
  } else {
    title = "Conflicting statements";
    body = (
      <>
        {flow.contradictions.map((c) => (
          <ConflictItem key={c.id} c={c} speakers={speakers} step={stepById(c.step)?.label} />
        ))}
        <p className="fm-note">MinuteCanvas shows both quotes. It never decides who is right.</p>
      </>
    );
  }

  return (
    <aside className="fm-drawer" aria-label={title}>
      <div className="fm-drawer-head">
        <h3>{title}</h3>
        <button className="icon-btn" onClick={onClose} aria-label="Close">
          <X size={16} aria-hidden />
        </button>
      </div>
      <div className="fm-drawer-body">{body}</div>
    </aside>
  );
}

function ConflictItem({ c, speakers, step }: { c: FlowContradiction; speakers: Record<string, string>; step?: string }) {
  return (
    <div className={`fm-item ${c.resolved ? "done" : "conflict"}`}>
      <b>
        {c.note}
        {step && <em> · {step}</em>}
      </b>
      <QuoteRow ev={c.a} speakers={speakers} tone="conflict" />
      <span className="fm-vs">vs</span>
      <QuoteRow ev={c.b} speakers={speakers} tone="conflict" />
      {c.resolved && (
        <>
          {c.answer && <span className="fm-answer">Settled: {c.answer}</span>}
          {c.resolved_by && <QuoteRow ev={c.resolved_by} speakers={speakers} tone="ok" />}
        </>
      )}
    </div>
  );
}
