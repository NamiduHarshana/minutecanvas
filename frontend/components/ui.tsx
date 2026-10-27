"use client";
import { Activity, CalendarDays, CircleCheckBig, CircleHelp, ImageOff, ListTodo, Sparkles, Target, User } from "lucide-react";
import type { Card, Column, UpdateStatus } from "@/core/types";
import { COLUMN_META, STATUS_META } from "@/core/types";
import { useImage } from "@/core/images";

export function ColumnIcon({ column, size = 16 }: { column: Column; size?: number }) {
  const p = { size, strokeWidth: 2.2, "aria-hidden": true } as const;
  switch (column) {
    case "goals":
      return <Target {...p} />;
    case "decisions":
      return <CircleCheckBig {...p} />;
    case "actions":
      return <ListTodo {...p} />;
    case "updates":
      return <Activity {...p} />;
    case "questions":
      return <CircleHelp {...p} />;
  }
}

export function typeLabel(c: Card): string {
  if (c.column === "questions" && c.resolved) return "Resolved question";
  return COLUMN_META[c.column].single;
}

export function TypeChip({ card }: { card: Card }) {
  const m = COLUMN_META[card.column];
  return (
    <span className="type-chip" style={{ color: m.color, background: m.soft }}>
      <ColumnIcon column={card.column} size={14} />
      {typeLabel(card)}
    </span>
  );
}

export function StatusBadge({ status }: { status: UpdateStatus }) {
  const m = STATUS_META[status];
  return (
    <span className="status-badge" style={{ color: m.color, background: m.soft }}>
      <span className="dot" style={{ background: m.color }} />
      {m.label}
    </span>
  );
}

/** Card illustration: Muse Image result, a "drawing…" shimmer while pending, or the column icon. */
export function Illus({ card, className = "", eager = false }: { card: Card; className?: string; eager?: boolean }) {
  const m = COLUMN_META[card.column];
  const { url, failed } = useImage(card.image_status === "ready" ? card.image_url : null);
  if (url) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img className={`illus ${className}`} src={url} alt="" loading={eager ? "eager" : "lazy"} draggable={false} />;
  }
  if (card.image_status === "pending" || (card.image_status === "ready" && !failed)) {
    return (
      <div className={`illus illus-pending ${className}`} style={{ background: m.soft, color: m.color }} title="Muse Image is drawing…">
        <Sparkles size={18} aria-hidden />
      </div>
    );
  }
  return (
    <div className={`illus illus-empty ${className}`} style={{ background: m.soft, color: m.color }}>
      {card.image_status === "failed" || failed ? <ImageOff size={18} aria-hidden /> : <ColumnIcon column={card.column} size={20} />}
    </div>
  );
}

export function CardMeta({ card }: { card: Card }) {
  const bits: React.ReactNode[] = [];
  if (card.column === "updates" && card.status) bits.push(<StatusBadge key="s" status={card.status} />);
  if (card.column === "questions" && card.resolved) {
    bits.push(
      <span key="r" className="resolved-mark">
        <CircleCheckBig size={13} aria-hidden /> Resolved
      </span>,
    );
  }
  if (card.owner)
    bits.push(
      <span key="o" className="meta-item">
        <User size={12} aria-hidden /> {card.owner}
      </span>,
    );
  if (card.due)
    bits.push(
      <span key="d" className="meta-item">
        <CalendarDays size={12} aria-hidden /> {card.due}
      </span>,
    );
  if (!bits.length) return null;
  return <div className="card-meta">{bits}</div>;
}
