"use client";
import { useEffect, useState } from "react";
import { Bug, CircleCheckBig, CircleHelp, Sparkles, TriangleAlert } from "lucide-react";
import type { Activity, CardActivity } from "@/core/store";
import type { Card, FlowEvent } from "@/core/types";
import { COLUMN_META, STATUS_META } from "@/core/types";

const VERB: Record<CardActivity["kind"], string> = {
  resolved: "resolved a question",
  done: "marked an action done",
  updated: "updated a card",
};

function changes(a: Card, b: Card): string[] {
  const out: string[] = [];
  if (b.column === "updates" && b.status && a.status !== b.status)
    out.push(`${a.status ? STATUS_META[a.status].label : "No status"} → ${STATUS_META[b.status].label}`);
  if (b.owner && a.owner !== b.owner) out.push(`Owner: ${b.owner}`);
  if (b.due && a.due !== b.due) out.push(`Due: ${b.due}`);
  if (a.title !== b.title) out.push(`Was: “${a.title}”`);
  else if (a.detail !== b.detail && b.detail) out.push(b.detail);
  return out;
}

const FLOW_HEAD: Partial<Record<FlowEvent["kind"], { text: string; color: string; Icon: typeof Sparkles }>> = {
  gap: { text: "Flow Map found a gap", color: "#d97706", Icon: CircleHelp },
  gap_resolved: { text: "Gap answered", color: "#16a34a", Icon: CircleCheckBig },
  contradiction: { text: "Conflicting statements", color: "#dc2626", Icon: TriangleAlert },
  contradiction_resolved: { text: "Conflict settled", color: "#16a34a", Icon: CircleCheckBig },
  bug: { text: "Bug pinned on the flow", color: "#dc2626", Icon: Bug },
  bug_fixed: { text: "Bug marked fixed", color: "#16a34a", Icon: CircleCheckBig },
};

const FRESH_MS = 8000;

/** Sidebar list of what Muse Spark did on its own (Flow Map finds, card updates, resolved questions), newest first. */
export function SparkFeed({
  items,
  onOpen,
  onOpenFlow,
}: {
  items: Activity[];
  onOpen: (cardId: string) => void;
  onOpenFlow: (e: FlowEvent) => void;
}) {
  const [now, setNow] = useState(() => Date.now());
  const newest = items.length ? items[items.length - 1].at : 0;
  useEffect(() => {
    if (!newest || Date.now() - newest > FRESH_MS) return;
    setNow(Date.now());
    const id = setTimeout(() => setNow(Date.now()), FRESH_MS + 100);
    return () => clearTimeout(id);
  }, [newest]);

  if (!items.length)
    return <p className="muted small spark-empty">Gaps, conflicts and answered questions show up here as Muse Spark finds them.</p>;
  return (
    <div className="spark-feed" aria-live="polite">
      {[...items].reverse().map((a) => {
        const fresh = now - a.at < FRESH_MS ? "is-fresh" : "";
        if (a.kind === "flow") {
          const h = FLOW_HEAD[a.event.kind] ?? { text: "Flow Map", color: "#4f46e5", Icon: Sparkles };
          return (
            <div key={a.id} className={`spark-item kind-flow fk-${a.event.kind} ${fresh}`} style={{ "--col": h.color } as React.CSSProperties}>
              <button className="spark-item-body" onClick={() => onOpenFlow(a.event)}>
                <span className="spark-item-head" style={{ color: h.color }}>
                  <h.Icon size={13} aria-hidden /> {h.text}
                </span>
                <b>{a.event.text}</b>
              </button>
            </div>
          );
        }
        const m = COLUMN_META[a.card.column];
        const lines = a.kind === "resolved" ? (a.card.answer ? [`→ ${a.card.answer}`] : []) : changes(a.before, a.card);
        return (
          <div key={a.id} className={`spark-item kind-${a.kind} ${fresh}`} style={{ "--col": m.color } as React.CSSProperties}>
            <button className="spark-item-body" onClick={() => onOpen(a.cardId)}>
              <span className="spark-item-head">
                <Sparkles size={13} aria-hidden /> Muse Spark {VERB[a.kind]}
              </span>
              <b>{a.card.title}</b>
              {lines.slice(0, 2).map((l, k) => (
                <span key={k} className="spark-item-line">
                  {l}
                </span>
              ))}
            </button>
          </div>
        );
      })}
    </div>
  );
}
