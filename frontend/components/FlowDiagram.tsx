"use client";
// Static SVG Flow Map for the report slide and the board export (same layout as the live map).
import type { FlowData, StepKind } from "@/core/types";
import { edgePath, layoutFlow, wrapText } from "@/core/flowLayout";
import { conflictTag, issueTag, stepIcon, stepNote } from "@/core/flowLook";

const KIND: Record<StepKind, { label: string; color: string; soft: string }> = {
  start: { label: "START", color: "#16a34a", soft: "#ecf8f0" },
  step: { label: "STEP", color: "#4f46e5", soft: "#eef0fe" },
  decision: { label: "DECISION", color: "#d97706", soft: "#fdf5e7" },
  end: { label: "END", color: "#101828", soft: "#f2f4f7" },
};

function clip(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1).trimEnd() + "…" : s;
}

export function FlowDiagram({ flow, className, box }: { flow: FlowData; className?: string; box?: { w: number; h: number } }) {
  const L = layoutFlow(flow, box);
  if (!L.nodes.length) return null;
  const pad = 8;
  return (
    <svg
      className={className}
      viewBox={`${-pad} ${-pad} ${L.width + pad * 2} ${L.height + pad * 2}`}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label="Flow map"
      fontFamily="Inter Variable, Inter, system-ui, sans-serif"
    >
      <defs>
        {(["normal", "fail", "gap"] as const).map((k) => (
          <marker key={k} id={`fd-arrow-${k}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill={k === "fail" ? "#dc2626" : k === "gap" ? "#d97706" : "#7c8597"} />
          </marker>
        ))}
      </defs>
      {L.edges.map((e) => (
        <path
          key={e.id}
          d={edgePath(e)}
          fill="none"
          stroke={e.kind === "fail" ? "#dc2626" : e.kind === "gap" ? "#d97706" : "#7c8597"}
          strokeWidth={e.kind === "fail" ? 2.2 : 1.8}
          strokeDasharray={e.kind === "gap" ? "6 5" : undefined}
          markerEnd={`url(#fd-arrow-${e.kind})`}
        />
      ))}
      {L.edges
        .filter((e) => e.label && e.labelPos)
        .map((e) => {
          const w = e.label.length * 6.4 + 12;
          return (
            <g key={`lbl-${e.id}`} transform={`translate(${e.labelPos!.x - w / 2},${e.labelPos!.y - 10})`}>
              <rect width={w} height={20} rx={5} fill="#fff" stroke="#e4e7ec" />
              <text x={w / 2} y={14} textAnchor="middle" fontSize={11.5} fontWeight={600} fill={e.kind === "fail" ? "#b91c1c" : "#344054"}>
                {e.label}
              </text>
            </g>
          );
        })}
      {L.nodes.map((n) => {
        if (n.type === "gap") {
          const lines = wrapText(`${n.gap.branch}?`, 22, 2);
          return (
            <g key={n.id} transform={`translate(${n.x},${n.y})`}>
              <rect width={n.w} height={n.h} rx={10} fill="#fffaf0" stroke="#d97706" strokeWidth={1.5} strokeDasharray="5 4" />
              <text x={14} y={lines.length > 1 ? 19 : 25} fontSize={13} fontWeight={700} fill="#92400e">
                {lines.map((l, k) => (
                  <tspan key={k} x={14} dy={k ? 15 : 0}>
                    {l}
                  </tspan>
                ))}
              </text>
              <text x={14} y={n.h - 9} fontSize={9.5} fontWeight={800} letterSpacing="0.06em" fill="#b45309">
                NOBODY SAID
              </text>
            </g>
          );
        }
        const k = KIND[n.step.kind];
        const Icon = stepIcon(n.step);
        const lines = wrapText(n.step.label, 19, 2);
        const note = stepNote(flow, n.step);
        const bug = n.bugs > 0;
        const conflict = !bug && n.conflicts > 0;
        const tags: { text: string; fill: string }[] = [];
        if (n.bugs > 0) tags.push({ text: issueTag(n.bugs), fill: "#dc2626" });
        if (n.conflicts > 0) tags.push({ text: conflictTag(n.conflicts), fill: "#b45309" });
        const tagW = (t: string) => t.length * 6.3 + 14;
        const titleY = lines.length > 1 ? 48 : 56;
        return (
          <g key={n.id} transform={`translate(${n.x},${n.y})`}>
            <rect
              width={n.w}
              height={n.h}
              rx={11}
              fill={bug ? "#fff7f7" : conflict ? "#fffbf3" : "#fff"}
              stroke={bug ? "#f08b8b" : conflict ? "#f1c27d" : "#cfd4dc"}
              strokeWidth={1.5}
            />
            <rect width={5} height={n.h} rx={2} fill={bug ? "#dc2626" : k.color} />
            <circle cx={23} cy={18} r={9.5} fill={k.soft} />
            <text x={23} y={21.8} textAnchor="middle" fontSize={11} fontWeight={800} fill={k.color}>
              {n.num}
            </text>
            <text x={38} y={21.5} fontSize={9.5} fontWeight={800} letterSpacing="0.06em" fill={k.color}>
              {k.label}
            </text>
            <rect x={13} y={titleY - 17 + (lines.length > 1 ? 8 : 0)} width={24} height={24} rx={7} fill={bug ? "#fde4e4" : k.soft} />
            <Icon
              x={17}
              y={titleY - 13 + (lines.length > 1 ? 8 : 0)}
              width={16}
              height={16}
              strokeWidth={2.1}
              color={bug ? "#dc2626" : k.color}
              aria-hidden
            />
            <text x={45} y={titleY} fontSize={14} fontWeight={650} fill="#101828">
              {lines.map((l, i) => (
                <tspan key={i} x={45} dy={i ? 16 : 0}>
                  {l}
                </tspan>
              ))}
            </text>
            {note && (
              <text
                x={13}
                y={n.h - 10}
                fontSize={11}
                fontWeight={note.tone === "quote" ? 400 : 600}
                fontStyle={note.tone === "quote" ? "italic" : undefined}
                fill={note.tone === "bug" ? "#b91c1c" : note.tone === "conflict" ? "#b45309" : "#667085"}
              >
                {clip(note.tone === "quote" ? `“${note.text}”` : note.text, 34)}
              </text>
            )}
            {tags.map((t, i) => {
              const right = tags.slice(0, i).reduce((acc, x) => acc + tagW(x.text) + 4, 0);
              const w = tagW(t.text);
              return (
                <g key={t.text} transform={`translate(${n.w - 8 - right - w},-10)`}>
                  <rect width={w} height={19} rx={9.5} fill={t.fill} />
                  <text x={w / 2} y={13.5} textAnchor="middle" fontSize={10.5} fontWeight={700} fill="#fff">
                    {t.text}
                  </text>
                </g>
              );
            })}
          </g>
        );
      })}
    </svg>
  );
}
