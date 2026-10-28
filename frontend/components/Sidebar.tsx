"use client";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Check, GitBranch, PanelRightClose, Pencil, Sparkles } from "lucide-react";
import type { Card, FlowEvent } from "@/core/types";
import { SparkFeed } from "./SparkFeed";
import type { MeetingState } from "@/core/store";
import { fmtTime, speakerName } from "@/core/store";

const SPEAKER_COLORS = ["#4f46e5", "#0e7490", "#b45309", "#be185d", "#15803d", "#7c3aed", "#c2410c", "#334155"];
function speakerColor(label: string | null) {
  if (!label) return "#6b7280";
  let h = 0;
  for (const ch of label) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return SPEAKER_COLORS[h % SPEAKER_COLORS.length];
}

export function Sidebar({
  state,
  questions,
  readOnly,
  onHide,
  onRename,
  onOpenCard,
  onOpenFlow,
  onResolve,
}: {
  state: MeetingState;
  questions: Card[];
  readOnly: boolean;
  onHide: () => void;
  onRename: (label: string, name: string) => void;
  onOpenCard: (id: string) => void;
  onOpenFlow: (e: FlowEvent) => void;
  onResolve: (c: Card) => void;
}) {
  const open = questions.filter((q) => !q.resolved);
  const [tab, setTab] = useState<"transcript" | "questions">("transcript");
  // a new open question shows a dot on its tab until the tab is opened
  const [seen, setSeen] = useState(0);
  useEffect(() => {
    if (tab === "questions") setSeen(open.length);
  }, [tab, open.length]);
  const unseen = tab !== "questions" && open.length > seen;
  return (
    <aside className="sidebar">
      <div className="side-tabs" role="tablist">
        <button role="tab" aria-selected={tab === "transcript"} className={tab === "transcript" ? "on" : ""} onClick={() => setTab("transcript")}>
          Transcript
        </button>
        <button role="tab" aria-selected={tab === "questions"} className={tab === "questions" ? "on" : ""} onClick={() => setTab("questions")}>
          Open questions <span className={`count ${unseen ? "new" : ""}`}>{open.length}</span>
        </button>
        <button className="icon-btn" onClick={onHide} aria-label="Hide panel" title="Hide panel">
          <PanelRightClose size={16} aria-hidden />
        </button>
      </div>
      {tab === "transcript" ? (
        <Transcript state={state} readOnly={readOnly} onRename={onRename} />
      ) : (
        <div className="oq-list">
          {open.length === 0 && <p className="muted small">No open questions.</p>}
          {open.map((q) => (
            <div key={q.id} className={`oq-item ${state.flash[q.id] ? "flash" : ""}`}>
              <button className="oq-title" onClick={() => onOpenCard(q.id)}>
                {q.flow_ref && (
                  <span className="gap-tag" title="Found by the Flow Map: nobody covered this">
                    <GitBranch size={11} aria-hidden /> Gap
                  </span>
                )}
                {q.title}
              </button>
              {!readOnly && (
                <button className="icon-btn" title="Mark resolved" aria-label="Mark resolved" onClick={() => onResolve(q)}>
                  <Check size={15} aria-hidden />
                </button>
              )}
            </div>
          ))}
        </div>
      )}
      <section className="spark-log">
        <div className="panel-head">
          <h2>
            <Sparkles size={14} aria-hidden /> Muse Spark found
          </h2>
        </div>
        <SparkFeed items={state.activity} onOpen={onOpenCard} onOpenFlow={onOpenFlow} />
      </section>
    </aside>
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

  const onScroll = () => {
    const el = box.current;
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };
  useLayoutEffect(() => {
    const el = box.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [state.transcript.length, state.partials]);

  const voiceNote =
    state.serverState === "recording" && (state.voice === "connecting" || state.voice === "reconnecting")
      ? "Connecting to the transcriber…"
      : null;

  return (
    <section className="transcript">
      <div className="tr-lines" ref={box} onScroll={onScroll} aria-live="polite">
        {state.transcript.length === 0 && partials.length === 0 && (
          <p className="muted small tr-empty">{state.serverState === "paused" ? "Paused." : "Waiting for someone to speak…"}</p>
        )}
        {state.transcript.map((l) => (
          <p key={l.id} className="tr-line">
            <span className="tr-time">{fmtTime(l.t)}</span>
            <span className="tr-text">
              <SpeakerName
                label={l.speaker}
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
          <p key={p.turn} className="tr-line partial">
            <span className="tr-time">…</span>
            <span className="tr-text">
              <b style={{ color: speakerColor(p.speaker) }}>{p.speaker ? speakerName(state.speakers, p.speaker) : "Speaking"}:</b> {p.text}
            </span>
          </p>
        ))}
        {voiceNote && <p className="muted small">{voiceNote}</p>}
      </div>
    </section>
  );
}

function SpeakerName({
  label,
  name,
  editing,
  readOnly,
  onEdit,
  onDone,
}: {
  label: string;
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
    <button
      className="spk"
      style={{ color: speakerColor(label) }}
      onClick={readOnly ? undefined : onEdit}
      title={readOnly ? name : "Rename this speaker"}
      disabled={readOnly}
    >
      {name}:{!readOnly && <Pencil size={10} className="spk-pen" aria-hidden />}
    </button>
  );
}
