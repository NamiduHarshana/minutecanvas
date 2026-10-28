"use client";
import { useEffect, useRef, useState } from "react";
import { Check, EllipsisVertical, GitBranch, Pencil, Sparkles, Trash2 } from "lucide-react";
import type { Card } from "@/core/types";
import { COLUMNS, COLUMN_META } from "@/core/types";
import type { Flash, MeetingState } from "@/core/store";
import { fmtTime } from "@/core/store";
import { CardMeta, ColumnIcon, Illus, TypeChip } from "./ui";

export interface CardActions {
  open: (id: string) => void;
  edit: (id: string) => void;
  remove: (id: string) => void;
  toggleDone: (c: Card) => void;
}

const FLASH_TEXT: Record<Flash["kind"], string> = { updated: "Updated", resolved: "Resolved", done: "Done" };

export function Latest({ cards, actions, flash }: { cards: Card[]; actions: CardActions; flash: Record<string, Flash> }) {
  const latest = cards.slice(-3).reverse();
  return (
    <section className="latest" aria-label="Latest cards">
      <h2 className="section-label">Latest</h2>
      {latest.length === 0 ? (
        <div className="latest-empty">
          <Sparkles size={22} aria-hidden />
          <p>
            Cards appear here as people speak. <span>Muse Spark reads the conversation every ~40 seconds and Muse Image draws each card.</span>
          </p>
        </div>
      ) : (
        <div className="latest-grid">
          {latest.map((c, i) => (
            <article
              key={c.id}
              className={`latest-card ${flash[c.id] ? "flash" : ""}`}
              style={{ "--col": COLUMN_META[c.column].color } as React.CSSProperties}
              onClick={() => actions.open(c.id)}
              tabIndex={0}
              onKeyDown={(e) => e.key === "Enter" && actions.open(c.id)}
            >
              <Illus card={c} className="lc-illus" eager />
              <div className="lc-body">
                <div className="lc-top">
                  <TypeChip card={c} />
                  {i === 0 && <span className="badge-new">New</span>}
                  {flash[c.id] && <span className="badge-upd">{FLASH_TEXT[flash[c.id].kind]}</span>}
                </div>
                <h3 className="lc-title">{c.title}</h3>
                {c.detail && <p className="lc-detail">{c.detail}</p>}
                {c.column === "questions" && c.resolved && c.answer && <p className="lc-answer">{c.answer}</p>}
                <CardMeta card={c} />
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

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
  return (
    <section className="board" aria-label="Board">
      <div className="board-head">
        <h2 className="section-label">Board</h2>
      </div>
      <div className="columns">
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
                {list.length === 0 && <div className="col-empty">Nothing yet</div>}
                {list.map((c) => (
                  <SmallCard key={c.id} card={c} isNew={c.id === newestId} flash={state.flash[c.id]} actions={actions} readOnly={readOnly} />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function hasMeta(c: Card): boolean {
  return (c.column === "updates" && !!c.status) || (c.column === "questions" && c.resolved) || !!c.owner || !!c.due;
}

function SmallCard({
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
  return (
    <article
      className={`small-card ${flash ? "flash" : ""} ${struck ? "is-done" : ""} ${answered ? "is-answered" : ""}`}
      onClick={() => actions.open(c.id)}
      tabIndex={0}
      onKeyDown={(e) => e.key === "Enter" && e.target === e.currentTarget && actions.open(c.id)}
      title={`Said at ${fmtTime(c.t)}`}
    >
      <Illus card={c} className="sc-illus" />
      {(flash || isNew) && (
        <span className="sc-flag">
          {flash ? <span className="badge-upd">{FLASH_TEXT[flash.kind]}</span> : <span className="badge-new">New</span>}
        </span>
      )}
      <div className="sc-body">
        <div className="sc-title-row">
          {c.column === "actions" && (
            <button
              className={`check ${c.done ? "on" : ""}`}
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
          <h4 className="sc-title" title={c.title}>
            {c.title}
          </h4>
          {c.flow_ref && (
            <span className="sc-gap" title="Found by the Flow Map: nobody covered this" aria-label="Flow gap">
              <GitBranch size={12} aria-hidden />
            </span>
          )}
        </div>
        {/* one line only: owner / due / status when there are any, otherwise the detail */}
        {hasMeta(c) ? (
          <CardMeta card={c} />
        ) : (
          (answered && c.answer ? c.answer : c.detail) && <p className="sc-line">{answered && c.answer ? c.answer : c.detail}</p>
        )}
      </div>
      {!readOnly && (
        <div className="sc-menu" ref={ref} onClick={(e) => e.stopPropagation()}>
          <button className="icon-btn" aria-label="Card options" onClick={() => setMenu((m) => !m)}>
            <EllipsisVertical size={16} aria-hidden />
          </button>
          {menu && (
            <div className="menu menu-sm" role="menu">
              <button
                role="menuitem"
                onClick={() => {
                  setMenu(false);
                  actions.edit(c.id);
                }}
              >
                <Pencil size={14} aria-hidden /> Edit
              </button>
              <button
                role="menuitem"
                className="danger"
                onClick={() => {
                  setMenu(false);
                  actions.remove(c.id);
                }}
              >
                <Trash2 size={14} aria-hidden /> Delete
              </button>
            </div>
          )}
        </div>
      )}
    </article>
  );
}


