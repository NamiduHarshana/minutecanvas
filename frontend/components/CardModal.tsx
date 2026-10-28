"use client";
import { useEffect, useState } from "react";
import { Check, ChevronLeft, ChevronRight, Clock, GitBranch, Pencil, Trash2, TriangleAlert, X } from "lucide-react";
import type { Card, ClientMsg, UpdateStatus } from "@/core/types";
import { COLUMN_META, STATUS_META } from "@/core/types";
import { fmtTime } from "@/core/store";
import { Illus, StatusBadge, TypeChip } from "./ui";

type Fields = Extract<ClientMsg, { type: "card_edit" }>["fields"];

export function CardModal({
  cards,
  id,
  startEditing,
  readOnly,
  onNavigate,
  onClose,
  onSave,
  onDelete,
  onShowFlow,
  onShowConflict,
}: {
  cards: Card[];
  id: string;
  startEditing: boolean;
  readOnly: boolean;
  onNavigate: (id: string) => void;
  onClose: () => void;
  onSave: (id: string, fields: Fields) => void;
  onDelete: (id: string) => void;
  onShowFlow?: (gapId: string) => void;
  onShowConflict?: (conflictId: string) => void;
}) {
  const idx = cards.findIndex((c) => c.id === id);
  const card = idx >= 0 ? cards[idx] : null;
  const [editing, setEditing] = useState(startEditing && !readOnly);

  useEffect(() => setEditing(startEditing && !readOnly), [id, startEditing, readOnly]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") return editing ? setEditing(false) : onClose();
      if (editing) return;
      if (e.key === "ArrowLeft" && idx > 0) onNavigate(cards[idx - 1].id);
      if (e.key === "ArrowRight" && idx < cards.length - 1) onNavigate(cards[idx + 1].id);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editing, idx, cards, onClose, onNavigate]);

  useEffect(() => {
    if (!card) onClose(); // deleted while open
  }, [card, onClose]);
  if (!card) return null;

  const m = COLUMN_META[card.column];
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <button className="nav-arrow left" aria-label="Previous card" disabled={idx <= 0} onClick={() => onNavigate(cards[idx - 1].id)}>
        <ChevronLeft size={26} aria-hidden />
      </button>
      <div className="modal" role="dialog" aria-modal="true" aria-label={card.title} style={{ "--col": m.color } as React.CSSProperties}>
        <div className="modal-head">
          <TypeChip card={card} />
          <span className="modal-count">
            {idx + 1} / {cards.length}
          </span>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <X size={18} aria-hidden />
          </button>
        </div>
        <div className="modal-body">
          <Illus card={card} className="modal-illus" eager />
          <div className="modal-text">
            {editing ? (
              <EditForm key={card.id} card={card} onCancel={() => setEditing(false)} onSave={(f) => (onSave(card.id, f), setEditing(false))} />
            ) : (
              <>
                <h2 className="modal-title">{card.title}</h2>
                {card.detail && <p className="modal-detail">{card.detail}</p>}
                {card.conflict_ref && (
                  <button
                    className="btn conflict-ref-btn"
                    onClick={() => onShowConflict?.(card.conflict_ref!)}
                    disabled={!onShowConflict}
                  >
                    <TriangleAlert size={14} aria-hidden /> Conflicting statements · not settled yet · show both quotes
                  </button>
                )}
                {card.flow_ref && onShowFlow && (
                  <button className="btn flow-ref-btn" onClick={() => onShowFlow(card.flow_ref!)}>
                    <GitBranch size={14} aria-hidden /> Found by the Flow Map · show it
                  </button>
                )}
                {card.column === "questions" && card.resolved && (
                  <div className="answer-box">
                    <b>
                      <Check size={14} aria-hidden /> Resolved
                    </b>
                    {card.answer && <p>{card.answer}</p>}
                  </div>
                )}
                <dl className="facts">
                  {card.column === "updates" && card.status && (
                    <div>
                      <dt>Status</dt>
                      <dd>
                        <StatusBadge status={card.status} />
                      </dd>
                    </div>
                  )}
                  {(card.column === "actions" || card.owner) && (
                    <div>
                      <dt>Owner</dt>
                      <dd>{card.owner || "Unassigned"}</dd>
                    </div>
                  )}
                  {(card.column === "actions" || card.due) && (
                    <div>
                      <dt>Due</dt>
                      <dd>{card.due || "Not set"}</dd>
                    </div>
                  )}
                  {card.column === "actions" && (
                    <div>
                      <dt>To-do</dt>
                      <dd>{card.done ? "✓ Done" : "Open"}</dd>
                    </div>
                  )}
                </dl>
                <p className="modal-time">
                  <Clock size={13} aria-hidden /> Said at {fmtTime(card.t)}
                  {card.updated_t != null && <> · updated at {fmtTime(card.updated_t)}</>}
                  {card.user_locked && <> · edited by you</>}
                </p>
                {!readOnly && (
                  <div className="modal-actions">
                    <button className="btn" onClick={() => setEditing(true)}>
                      <Pencil size={14} aria-hidden /> Edit
                    </button>
                    <button className="btn btn-ghost-danger" onClick={() => onDelete(card.id)}>
                      <Trash2 size={14} aria-hidden /> Delete
                    </button>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>
      <button className="nav-arrow right" aria-label="Next card" disabled={idx >= cards.length - 1} onClick={() => onNavigate(cards[idx + 1].id)}>
        <ChevronRight size={26} aria-hidden />
      </button>
    </div>
  );
}

function EditForm({ card, onSave, onCancel }: { card: Card; onSave: (f: Fields) => void; onCancel: () => void }) {
  const [title, setTitle] = useState(card.title);
  const [detail, setDetail] = useState(card.detail);
  const [owner, setOwner] = useState(card.owner || "");
  const [due, setDue] = useState(card.due || "");
  const [status, setStatus] = useState<UpdateStatus>(card.status || "in_progress");
  const [done, setDone] = useState(card.done);
  const [resolved, setResolved] = useState(card.resolved);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) return;
    const f: Fields = { title: title.trim(), detail: detail.trim(), owner: owner.trim() || null, due: due.trim() || null };
    if (card.column === "updates") f.status = status;
    if (card.column === "actions") f.done = done;
    if (card.column === "questions") f.resolved = resolved;
    onSave(f);
  }

  return (
    <form className="edit-form" onSubmit={submit}>
      <label className="field">
        <span>Title</span>
        <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={80} autoFocus required />
      </label>
      <label className="field">
        <span>Detail</span>
        <textarea value={detail} onChange={(e) => setDetail(e.target.value)} maxLength={280} rows={3} />
      </label>
      <div className="field-row">
        <label className="field">
          <span>Owner</span>
          <input value={owner} onChange={(e) => setOwner(e.target.value)} maxLength={40} />
        </label>
        <label className="field">
          <span>Due</span>
          <input value={due} onChange={(e) => setDue(e.target.value)} maxLength={40} />
        </label>
      </div>
      {card.column === "updates" && (
        <div className="field">
          <span>Status</span>
          <div className="seg">
            {(Object.keys(STATUS_META) as UpdateStatus[]).map((s) => (
              <button type="button" key={s} className={status === s ? "on" : ""} onClick={() => setStatus(s)}>
                {STATUS_META[s].label}
              </button>
            ))}
          </div>
        </div>
      )}
      {card.column === "actions" && (
        <label className="inline-check">
          <input type="checkbox" checked={done} onChange={(e) => setDone(e.target.checked)} /> Done
        </label>
      )}
      {card.column === "questions" && (
        <label className="inline-check">
          <input type="checkbox" checked={resolved} onChange={(e) => setResolved(e.target.checked)} /> Resolved
        </label>
      )}
      <p className="hint">After you edit a card, the AI won’t change its text.</p>
      <div className="modal-actions">
        <button type="submit" className="btn btn-primary">
          Save
        </button>
        <button type="button" className="btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
