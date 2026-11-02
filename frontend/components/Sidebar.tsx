"use client";
// The live meeting's right rail: Transcript | Open questions, and "Muse Spark found" (gaps, conflicts, bugs, answers).
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Check, Pencil } from "lucide-react";
import { useReplay } from "@/core/replay";
import type { Card, FlowEvent } from "@/core/types";
import type { Activity, MeetingState } from "@/core/store";
import { fmtTime, speakerName, speakerTones } from "@/core/store";
import type { FindingKind } from "@/core/account";
import { FindingTag } from "./kit";

const FRESH_MS = 8000;

export function Sidebar({
  state,
  questions,
  readOnly,
  onRename,
  onOpenCard,
  onOpenFlow,
  onResolve,
}: {
  state: MeetingState;
  questions: Card[];
  readOnly: boolean;
  onRename: (label: string, name: string) => void;
  onOpenCard: (id: string) => void;
  onOpenFlow: (e: FlowEvent) => void;
  onResolve: (c: Card) => void;
}) {
  const open = questions.filter((q) => !q.resolved);
  const [tab, setTab] = useState<"transcript" | "questions">("transcript");
  return (
    <aside className="rail" aria-label="Transcript and findings">
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "transcript"} className={tab === "transcript" ? "on" : ""} onClick={() => setTab("transcript")}>
          Transcript
        </button>
        <button role="tab" aria-selected={tab === "questions"} className={tab === "questions" ? "on" : ""} onClick={() => setTab("questions")}>
          Open questions {open.length > 0 && <span className="mc-tag mc-tag-gap mc-tag-sm">{open.length}</span>}
        </button>
      </div>
      {tab === "transcript" ? (
        <Transcript state={state} readOnly={readOnly} onRename={onRename} />
      ) : (
        <div className="rail-body">
          {open.length === 0 && <p className="rail-empty">No open questions.</p>}
          {open.map((q) => (
            <div key={q.id} className="oq">
              <button className="oq-title" onClick={() => onOpenCard(q.id)}>
                {q.flow_ref && <FindingTag kind="gap" small />}
                <span>{q.title}</span>
              </button>
              {!readOnly && (
                <button className="mc-ib ghost" title="Mark answered" aria-label="Mark answered" onClick={() => onResolve(q)}>
                  <Check size={15} strokeWidth={2} aria-hidden />
                </button>
              )}
            </div>
          ))}
        </div>
      )}
      <Found items={state.activity} onOpenCard={onOpenCard} onOpenFlow={onOpenFlow} />
    </aside>
  );
}

const FLOW_KIND: Partial<Record<FlowEvent["kind"], { kind: FindingKind; label?: string; done?: boolean }>> = {
  gap: { kind: "gap" },
  gap_resolved: { kind: "answered", done: true },
  gap_removed: { kind: "answered", label: "Not a gap", done: true },
  contradiction: { kind: "conflict" },
  contradiction_resolved: { kind: "answered", label: "Settled", done: true },
  bug: { kind: "bug" },
  bug_fixed: { kind: "answered", label: "Fixed", done: true },
};

/** What Muse Spark found on its own, newest first. */
function Found({ items, onOpenCard, onOpenFlow }: { items: Activity[]; onOpenCard: (id: string) => void; onOpenFlow: (e: FlowEvent) => void }) {
  const [now, setNow] = useState(() => Date.now());
  const newest = items.length ? items[items.length - 1].at : 0;
  useEffect(() => {
    if (!newest || Date.now() - newest > FRESH_MS) return;
    setNow(Date.now());
    const id = setTimeout(() => setNow(Date.now()), FRESH_MS + 100);
    return () => clearTimeout(id);
  }, [newest]);
  const rows = [...items].reverse().flatMap((a) => {
    const fresh = now - a.at < FRESH_MS;
    if (a.kind === "flow") {
      const k = FLOW_KIND[a.event.kind];
      if (!k) return [];
      return [{ id: a.id, fresh, done: !!k.done, kind: k.kind, label: k.label, text: a.event.text, open: () => onOpenFlow(a.event) }];
    }
    if (a.kind === "resolved")
      return [{ id: a.id, fresh, done: true, kind: "answered" as FindingKind, label: undefined, text: a.card.title, open: () => onOpenCard(a.cardId) }];
    return [];
  });
  return (
    <section className="found" aria-label="Muse Spark found">
      <span className="lab">Muse Spark found</span>
      {rows.length === 0 ? (
        <p className="hint">Gaps, conflicts and answered questions show up here as Muse Spark finds them.</p>
      ) : (
        <div className="found-list" aria-live="polite">
          {rows.map((r) => (
            <button key={r.id} className={`frow ${r.fresh ? "fresh" : ""} ${r.done ? "done" : ""}`} onClick={r.open}>
              <FindingTag kind={r.kind} label={r.label} />
              <span className="t">{r.text}</span>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

function Transcript({
  state,
  readOnly,
  onRename,
}: {
  state: MeetingState;
  readOnly: boolean;
  onRename: (label: string, name: string) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const [editing, setEditing] = useState<number | null>(null); // line id
  const partials = Object.values(state.partials).filter((p) => p.text);
  const replay = useReplay();
  const focusLine = replay?.focusLine ?? null;
  const tone = speakerTones(state.transcript);

  const onScroll = () => {
    const el = box.current;
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };
  useLayoutEffect(() => {
    const el = box.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [state.transcript.length, state.partials]);
  useLayoutEffect(() => {
    if (focusLine == null) return;
    box.current?.querySelector(`[data-line="${focusLine}"]`)?.scrollIntoView({ block: "center" });
  }, [focusLine, state.transcript.length]);

  const voiceNote =
    state.serverState === "recording" && (state.voice === "connecting" || state.voice === "reconnecting") ? "Connecting to the transcriber…" : null;

  return (
    <div className="rail-body" ref={box} onScroll={onScroll} aria-live="polite">
      {state.transcript.length === 0 && partials.length === 0 && (
        <p className="rail-empty">{state.serverState === "paused" ? "Paused." : "Waiting for someone to speak…"}</p>
      )}
      {state.transcript.map((l) => (
        <p key={l.id} className={`tline ${l.id === focusLine ? "focus" : ""}`} data-line={l.id}>
          {replay ? (
            <button className="mc-time tline-time" onClick={() => replay.jumpToSaid(l.t)} title="Play from this line">
              {fmtTime(l.t)}
            </button>
          ) : (
            <span className="mc-time">{fmtTime(l.t)}</span>
          )}
          <span>
            <SpeakerName
              label={l.speaker}
              tone={tone(l.speaker)}
              name={speakerName(state.speakers, l.speaker)}
              editing={editing === l.id}
              readOnly={readOnly}
              onEdit={() => setEditing(l.id)}
              onDone={(name) => {
                setEditing(null);
                if (name !== null) onRename(l.speaker, name);
              }}
            />{" "}
            {l.text}
          </span>
        </p>
      ))}
      {partials.map((p) => (
        <p key={p.turn} className="tline partial">
          <span className="mc-time">…</span>
          <span>
            <b className={tone(p.speaker)}>{p.speaker ? speakerName(state.speakers, p.speaker) : "Speaking"}:</b> {p.text}
          </span>
        </p>
      ))}
      {voiceNote && <p className="rail-empty">{voiceNote}</p>}
    </div>
  );
}

function SpeakerName({
  label,
  tone,
  name,
  editing,
  readOnly,
  onEdit,
  onDone,
}: {
  label: string;
  tone: string;
  name: string;
  editing: boolean;
  readOnly: boolean;
  onEdit: () => void;
  onDone: (name: string | null) => void;
}) {
  const [val, setVal] = useState(name);
  const finished = useRef(false);
  useEffect(() => {
    if (editing) {
      finished.current = false;
      setVal(name.startsWith("Speaker ") ? "" : name);
    }
  }, [editing, name]);
  const finish = (v: string | null) => {
    if (finished.current) return;
    finished.current = true;
    onDone(v);
  };
  if (editing) {
    return (
      <input
        className="spk-input"
        value={val}
        autoFocus
        maxLength={40}
        placeholder={`Name for speaker ${label}`}
        onChange={(e) => setVal(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") finish(val.trim());
          if (e.key === "Escape") finish(null);
        }}
        onBlur={() => finish(val.trim())}
      />
    );
  }
  return (
    <button className={`spk ${tone}`} onClick={readOnly ? undefined : onEdit} title={readOnly ? name : "Rename this speaker"} disabled={readOnly}>
      {name}:{!readOnly && <Pencil size={10} className="spk-pen" aria-hidden />}
    </button>
  );
}
