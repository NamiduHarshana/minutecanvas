"use client";
import { useEffect } from "react";
import { Sparkles, X } from "lucide-react";
import type { Activity } from "@/core/store";
import type { Card } from "@/core/types";
import { COLUMN_META, STATUS_META } from "@/core/types";

const VERB: Record<Activity["kind"], string> = {
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

/** Callouts for what Muse Spark changed on its own (updates, resolved questions, done actions). */
export function SparkFeed({
  items,
  onOpen,
  onDismiss,
}: {
  items: Activity[];
  onOpen: (cardId: string) => void;
  onDismiss: (id: number) => void;
}) {
  useEffect(() => {
    if (!items.length) return;
    const first = items[0];
    const id = setTimeout(() => onDismiss(first.id), Math.max(1000, 10000 - (Date.now() - first.at)));
    return () => clearTimeout(id);
  }, [items, onDismiss]);

  if (!items.length) return null;
  return (
    <div className="spark-feed" aria-live="polite">
      {items.map((a) => {
        const m = COLUMN_META[a.card.column];
        const lines = a.kind === "resolved" ? (a.card.answer ? [`→ ${a.card.answer}`] : []) : changes(a.before, a.card);
        return (
          <div key={a.id} className={`spark-item kind-${a.kind}`} style={{ "--col": m.color } as React.CSSProperties}>
            <button className="spark-item-body" onClick={() => onOpen(a.cardId)}>
              <span className="spark-item-head">
                <Sparkles size={14} aria-hidden /> Muse Spark {VERB[a.kind]}
              </span>
              <b>{a.card.title}</b>
              {lines.slice(0, 2).map((l, k) => (
                <span key={k} className="spark-item-line">
                  {l}
                </span>
              ))}
            </button>
            <button className="icon-btn" aria-label="Dismiss" onClick={() => onDismiss(a.id)}>
              <X size={14} aria-hidden />
            </button>
          </div>
        );
      })}
    </div>
  );
}
