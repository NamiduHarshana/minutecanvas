"use client";
import type { Card } from "@/core/types";
import { COLUMNS, COLUMN_META } from "@/core/types";
import { fmtTime } from "@/core/store";
import { CardMeta, ColumnIcon, Illus } from "./ui";
import { Logo } from "./Logo";

/** Clean full board for PNG/PDF export (no scroll limits, no buttons). */
export function ExportSheet({ title, cards, meetingT }: { title: string | null; cards: Card[]; meetingT: number }) {
  const date = new Date().toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
  return (
    <div className="sheet">
      <div className="sheet-head">
        <Logo small />
        <h1>{title || "Meeting board"}</h1>
        <span>
          {date} · {fmtTime(meetingT)}
        </span>
      </div>
      <div className="sheet-cols">
        {COLUMNS.map((col) => {
          const list = cards.filter((c) => c.column === col);
          const m = COLUMN_META[col];
          return (
            <div key={col} className="column" style={{ "--col": m.color, "--soft": m.soft } as React.CSSProperties}>
              <div className="col-head">
                <span className="col-icon">
                  <ColumnIcon column={col} size={15} />
                </span>
                <span className="col-name">{m.label}</span>
                <span className="col-count">{list.length}</span>
              </div>
              <div className="col-body">
                {list.length === 0 && <div className="col-empty">—</div>}
                {list.map((c) => (
                  <div key={c.id} className={`sheet-card ${c.column === "actions" && c.done ? "is-done" : ""}`}>
                    <Illus card={c} className="sheet-illus" eager />
                    <div className="sheet-text">
                      <h4>
                        {c.column === "actions" && c.done ? "✓ " : ""}
                        {c.title}
                      </h4>
                      {c.detail && <p>{c.detail}</p>}
                      {c.column === "questions" && c.resolved && c.answer && <p className="sheet-answer">→ {c.answer}</p>}
                      <CardMeta card={c} />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
      <p className="sheet-foot">Made live with MinuteCanvas · Meta Muse Voice Transcribe, Muse Spark &amp; Muse Image</p>
    </div>
  );
}
