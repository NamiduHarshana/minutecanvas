"use client";
// Design-system pieces shared by the app pages: finding tags, stat cards with bars, segmented controls, empty states.
import { useEffect, useState } from "react";
import { Bug, CircleCheck, CircleHelp, TriangleAlert, type LucideIcon } from "lucide-react";
import type { FindingKind } from "@/core/account";
import { KIND_WORD, type Bar } from "@/core/library";

export const KIND_ICON: Record<FindingKind, LucideIcon> = { gap: CircleHelp, conflict: TriangleAlert, bug: Bug, answered: CircleCheck };
export const KIND_CLASS: Record<FindingKind, string> = { gap: "mc-tag-gap", conflict: "mc-tag-conflict", bug: "mc-tag-bug", answered: "mc-tag-ok" };

/** Word + icon, never colour alone. */
export function FindingTag({ kind, label, small = false }: { kind: FindingKind; label?: string; small?: boolean }) {
  const Icon = KIND_ICON[kind];
  return (
    <span className={`mc-tag ${KIND_CLASS[kind]} ${small ? "mc-tag-sm" : ""}`}>
      <Icon strokeWidth={2} aria-hidden />
      {label ?? KIND_WORD[kind]}
    </span>
  );
}

/** "2 gaps" / "0 conflicts": a finding colour only when there is something; zero is a muted plain tag. */
export function CountTag({ kind, n }: { kind: "gap" | "conflict"; n: number }) {
  const word = kind === "gap" ? (n === 1 ? "gap" : "gaps") : n === 1 ? "conflict" : "conflicts";
  if (!n) return <span className="mc-tag mc-tag-plain">{`0 ${word}`}</span>;
  return <FindingTag kind={kind} label={`${n} ${word}`} />;
}

export function Seg<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { id: T; label: string }[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.id} type="button" className={o.id === value ? "on" : ""} aria-pressed={o.id === value} onClick={() => onChange(o.id)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** false on the server render and the first browser render, true after mount. Dates (locale, time zone, "today")
 *  differ between the server and the visitor's browser, so date text is only rendered after mount (no hydration
 *  mismatch). */
export function useMounted(): boolean {
  const [m, setM] = useState(false);
  useEffect(() => setM(true), []);
  return m;
}

/** A dashboard number with its bars (always brand); hover a bar for its date and value. Bar dates appear after mount. */
export function StatCard({
  value,
  label,
  color,
  data,
  unit,
  head,
  fmt = (v) => String(v),
}: {
  value: string;
  label: string;
  color?: string;
  data: Bar[];
  unit: string;
  head?: React.ReactNode;
  fmt?: (v: number) => string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const mounted = useMounted();
  const max = Math.max(0, ...data.map((d) => d.value));
  return (
    <div className="mc-card mc-stat">
      {head}
      <div className="n" style={color ? { color } : undefined}>
        {value}
      </div>
      <div className="l">{label}</div>
      <div className="mc-bars" style={{ gridTemplateColumns: `repeat(${data.length}, 1fr)`, gap: data.length > 10 ? 4 : 10 }} onMouseLeave={() => setHover(null)}>
        {data.map((d, k) => (
          <i
            key={k}
            style={{ height: d.value && max ? `${Math.max(4, Math.round((d.value / max) * 100))}%` : "3px" }}
            onMouseEnter={() => setHover(k)}
            aria-label={mounted ? `${d.tip}: ${fmt(d.value)} ${unit}` : undefined}
          />
        ))}
        {hover != null && mounted && (
          <span className="mc-tip" style={{ left: `${((hover + 0.5) / data.length) * 100}%` }} role="tooltip">
            <b>{fmt(data[hover].value)}</b> {unit}
            <span>{data[hover].tip}</span>
          </span>
        )}
      </div>
      <div className="mc-axis" style={{ gridTemplateColumns: `repeat(${data.length}, 1fr)`, gap: data.length > 10 ? 4 : 10 }}>
        {data.map((d, k) => (
          <span key={k}>{mounted ? d.label : ""}</span>
        ))}
      </div>
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="mc-empty">
      <b>{title}</b>
      {children}
    </div>
  );
}

export function Initials({ name, size = 36 }: { name: string; size?: number }) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const ini = ((parts[0]?.[0] ?? "") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase() || "?";
  return (
    <span className="mc-avatar" style={{ width: size, height: size, fontSize: Math.round(size * 0.36) }} aria-hidden>
      {ini}
    </span>
  );
}
