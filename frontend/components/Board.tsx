"use client";
import { useEffect, useRef, useState } from "react";
import { Check, Ellipsis, Pencil, Sparkles, Trash2 } from "lucide-react";
import type { Card } from "@/core/types";
import { COLUMNS, COLUMN_META } from "@/core/types";
import type { Flash, MeetingState } from "@/core/store";
import { fmtTime } from "@/core/store";
import { CardMeta, Illus } from "./ui";
import { FindingTag } from "./kit";

export interface CardActions {
  open: (id: string) => void;
  edit: (id: string) => void;
  remove: (id: string) => void;
  toggleDone: (c: Card) => void;
}

const FLASH_TEXT: Record<Flash["kind"], string> = { updated: "Updated", resolved: "Resolved", done: "Done" };

/** The board: Goals / Decisions / Actions / Updates / Questions. Empty columns stay hidden until something lands. */
export function Board({
  state,
  cards,
  actions,
  readOnly,
}: {
  state: MeetingState;
  cards: Card[];
  actions: CardActions;
  readOnly: boolean;
}) {
  const newestId = cards.length ? cards[cards.length - 1].id : null;
  const cols = COLUMNS.map((col) => ({ col, list: cards.filter((c) => c.column === col) })).filter((x) => x.list.length > 0);
  return (
    <section className="board-sec" aria-label="Board">
      <div className="card-h">
        <span className="lab">Board</span>
        <span className="hint">Empty columns stay hidden until something lands in them</span>
      </div>
      {cols.length === 0 ? (
        <div className="board-empty">
          <Sparkles size={22} strokeWidth={1.75} aria-hidden />
          <span>
            <b>Cards appear here as people speak.</b>
            Muse Spark reads the conversation about every 40 seconds; Muse Image draws each card.
          </span>
        </div>
      ) : (
        <div className="board">
          {cols.map(({ col, list }) => (
            <div key={col} className="bcol">
              <span className={`lab col-${col}`}>
                {COLUMN_META[col].label} · {list.length}
              </span>
              {list.map((c) => (
                <BoardCard key={c.id} card={c} isNew={c.id === newestId} flash={state.flash[c.id]} actions={actions} readOnly={readOnly} />
              ))}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function hasMeta(c: Card): boolean {
  return (c.column === "updates" && !!c.status) || !!c.owner || !!c.due;
}

function BoardCard({
  card: c,
  isNew,
  flash,
  actions,
  readOnly,
}: {
  card: Card;
  isNew: boolean;
  flash?: Flash;
  actions: CardActions;
  readOnly: boolean;
}) {
  const [menu, setMenu] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!menu) return;
    const close = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setMenu(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [menu]);
  const struck = c.column === "actions" && c.done;
  const answered = c.column === "questions" && c.resolved;
  const line = answered && c.answer ? `Answered: ${c.answer}` : c.detail;
  return (
    <article
      className={`bcard ${flash ? "flash" : ""} ${struck ? "is-done" : ""} ${answered ? "is-answered" : ""} ${c.conflict_ref ? "is-conflict" : ""}`}
      onClick={() => actions.open(c.id)}
      tabIndex={0}
      onKeyDown={(e) => e.key === "Enter" && e.target === e.currentTarget && actions.open(c.id)}
      title={`${c.flow_ref ? "Found" : "Said"} at ${fmtTime(c.t)}`}
    >
      <Illus card={c} className="img" />
      {(flash || isNew) && (
        <span className="bcard-flag">{flash ? <span className="badge-upd">{FLASH_TEXT[flash.kind]}</span> : <span className="badge-new">New</span>}</span>
      )}
      <div className="bcard-body">
        <div className="bcard-title">
          {c.column === "actions" && (
            <button
              className={`tick ${c.done ? "on" : ""}`}
              aria-label={c.done ? "Mark not done" : "Mark done"}
              disabled={readOnly}
              onClick={(e) => {
                e.stopPropagation();
                actions.toggleDone(c);
              }}
            >
              {c.done && <Check size={12} strokeWidth={3} aria-hidden />}
            </button>
          )}
          <b>{c.title}</b>
        </div>
        {c.flow_ref && !answered && <FindingTag kind="gap" small />}
        {answered && c.flow_ref && <FindingTag kind="answered" small />}
        {c.conflict_ref && <FindingTag kind="conflict" label={c.column === "decisions" ? "Not decided" : "Conflict"} small />}
        {hasMeta(c) ? <CardMeta card={c} /> : line && <span className="bcard-line">{line}</span>}
      </div>
      {!readOnly && (
        <div className="bcard-menu" ref={ref} onClick={(e) => e.stopPropagation()}>
          <button className="mc-ib" aria-label="Card options" onClick={() => setMenu((m) => !m)}>
            <Ellipsis size={15} strokeWidth={2} aria-hidden />
          </button>
          {menu && (
            <div className="mc-menu" role="menu">
              <button
                role="menuitem"
                onClick={() => {
                  setMenu(false);
                  actions.edit(c.id);
                }}
              >
                <Pencil size={15} strokeWidth={1.75} aria-hidden /> Edit
              </button>
              <button
                role="menuitem"
                className="danger"
                onClick={() => {
                  setMenu(false);
                  actions.remove(c.id);
                }}
              >
                <Trash2 size={15} strokeWidth={1.75} aria-hidden /> Delete
              </button>
            </div>
          )}
        </div>
      )}
    </article>
  );
}
