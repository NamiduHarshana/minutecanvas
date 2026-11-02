"use client";
import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ChevronDown, Download, ExternalLink, FileImage, FileText, Link2, Pause, Play, Presentation, Square, Trash2 } from "lucide-react";
import type { ConnStatus } from "@/core/client";
import type { ServerState } from "@/core/types";
import { fmtTime } from "@/core/store";

export interface ReplayActions {
  href: string;
  onCopy: () => void;
  onDelete: (() => void) | null;     // only the browser that made the meeting (owner key)
}

/** Live meeting top bar: ← Dashboard, title, recording clock, source, connection, Pause / Stop (then Replay, Export, Report). */
export function TopBar({
  title,
  serverState,
  conn,
  voice,
  sourceLabel,
  clock,
  level,
  hasReport,
  exporting,
  onPause,
  onResume,
  onStop,
  onExport,
  onShowReport,
  replay,
}: {
  title: string | null;
  serverState: ServerState;
  conn: ConnStatus;
  voice: string;
  sourceLabel: string | null;
  clock: () => number;
  level: () => number;
  hasReport: boolean;
  exporting: boolean;
  onPause: () => void;
  onResume: () => void;
  onStop: () => void;
  onExport: (kind: "png" | "pdf") => void;
  onShowReport: () => void;
  replay?: ReplayActions | null;
}) {
  const live = serverState === "recording" || serverState === "paused";
  const done = serverState === "done";
  return (
    <header className="topbar">
      <a className="back" href="/dashboard">
        <ArrowLeft size={15} strokeWidth={2} aria-hidden /> Dashboard
      </a>
      <h1 className="ttl" title={title || ""}>
        {title || (serverState === "created" ? "Starting…" : "Untitled meeting")}
      </h1>
      <span className="grow" />
      <RecTimer state={serverState} clock={clock} />
      {sourceLabel && live && (
        <span className="mc-tag mc-tag-plain">
          <LevelMeter level={level} paused={serverState === "paused"} />
          {sourceLabel}
        </span>
      )}
      <ConnTag conn={conn} voice={voice} done={done} />
      {live && (
        <>
          {serverState === "paused" ? (
            <button className="mc-btn mc-btn-outline mc-btn-sm" onClick={onResume}>
              <Play strokeWidth={1.75} aria-hidden /> Resume
            </button>
          ) : (
            <button className="mc-btn mc-btn-outline mc-btn-sm" onClick={onPause}>
              <Pause strokeWidth={1.75} aria-hidden /> Pause
            </button>
          )}
          <button className="mc-btn mc-btn-stop mc-btn-sm" onClick={onStop}>
            <Square size={12} fill="currentColor" strokeWidth={0} aria-hidden /> Stop
          </button>
        </>
      )}
      {replay && <ReplayMenu replay={replay} />}
      {(done || hasReport) && <ExportMenu disabled={exporting} onExport={onExport} />}
      {hasReport && (
        <button className="mc-btn mc-btn-primary mc-btn-sm" onClick={onShowReport}>
          <Presentation strokeWidth={1.75} aria-hidden /> Report
        </button>
      )}
    </header>
  );
}

export function useMenu() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  return { open, setOpen, ref };
}

function ReplayMenu({ replay }: { replay: ReplayActions }) {
  const { open, setOpen, ref } = useMenu();
  return (
    <div className="menu-wrap" ref={ref}>
      <button className="mc-btn mc-btn-outline mc-btn-sm" onClick={() => setOpen((o) => !o)} aria-haspopup="menu" aria-expanded={open}>
        <Link2 strokeWidth={1.75} aria-hidden /> Replay <ChevronDown strokeWidth={1.75} aria-hidden />
      </button>
      {open && (
        <div className="mc-menu" role="menu">
          <button
            role="menuitem"
            onClick={() => {
              setOpen(false);
              replay.onCopy();
            }}
          >
            <Link2 size={16} strokeWidth={1.75} aria-hidden /> Copy replay link
          </button>
          <a role="menuitem" href={replay.href} target="_blank" rel="noopener" onClick={() => setOpen(false)}>
            <ExternalLink size={16} strokeWidth={1.75} aria-hidden /> Open replay
          </a>
          {replay.onDelete && (
            <button
              role="menuitem"
              className="danger"
              onClick={() => {
                setOpen(false);
                replay.onDelete?.();
              }}
            >
              <Trash2 size={16} strokeWidth={1.75} aria-hidden /> Delete replay
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function RecTimer({ state, clock }: { state: ServerState; clock: () => number }) {
  const [t, setT] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setT(clock()), 250);
    return () => clearInterval(id);
  }, [clock]);
  const label =
    state === "recording" ? "Recording" : state === "paused" ? "Paused" : state === "stopping" ? "Finishing" : state === "done" ? "Ended" : "Starting";
  return (
    <span className={`rec rec-${state}`} role="status">
      <i aria-hidden />
      {label} <span className="mc-time">{fmtTime(t)}</span>
    </span>
  );
}

function LevelMeter({ level, paused }: { level: () => number; paused: boolean }) {
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const id = setInterval(() => {
      const el = ref.current;
      if (!el) return;
      const v = paused ? 0 : Math.min(1, Math.sqrt(level()) * 2.2);
      el.style.setProperty("--lvl", v.toFixed(2));
    }, 100);
    return () => clearInterval(id);
  }, [level, paused]);
  return (
    <span className="lvl" ref={ref} aria-hidden>
      <i />
      <i />
      <i />
    </span>
  );
}

function ConnTag({ conn, voice, done }: { conn: ConnStatus; voice: string; done: boolean }) {
  if (done) return null;
  let cls = "mc-tag-ok";
  let wait = false;
  let text = "Connected";
  if (conn === "connecting") (cls = "mc-tag-plain"), (wait = true), (text = "Connecting…");
  else if (conn === "reconnecting") (cls = "mc-tag-plain"), (wait = true), (text = "Reconnecting…");
  else if (conn === "offline") (cls = "mc-tag-bug"), (text = "Offline — keeping audio");
  else if (conn === "closed") (cls = "mc-tag-bug"), (text = "Disconnected");
  else if (voice === "reconnecting" || voice === "connecting") (cls = "mc-tag-plain"), (wait = true), (text = "Transcriber reconnecting…");
  else if (voice === "failed") (cls = "mc-tag-bug"), (text = "Transcriber failed");
  return (
    <span className={`mc-tag ${cls} ${wait ? "conn-wait" : ""}`} role="status">
      <span className="conn-dot" aria-hidden />
      {text}
    </span>
  );
}

function ExportMenu({ disabled, onExport }: { disabled: boolean; onExport: (k: "png" | "pdf") => void }) {
  const { open, setOpen, ref } = useMenu();
  return (
    <div className="menu-wrap" ref={ref}>
      <button className="mc-btn mc-btn-outline mc-btn-sm" onClick={() => setOpen((o) => !o)} disabled={disabled} aria-haspopup="menu" aria-expanded={open}>
        <Download strokeWidth={1.75} aria-hidden /> {disabled ? "Exporting…" : "Export"} <ChevronDown strokeWidth={1.75} aria-hidden />
      </button>
      {open && (
        <div className="mc-menu" role="menu">
          <button
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onExport("png");
            }}
          >
            <FileImage size={16} strokeWidth={1.75} aria-hidden /> Board as PNG
          </button>
          <button
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onExport("pdf");
            }}
          >
            <FileText size={16} strokeWidth={1.75} aria-hidden /> Board as PDF
          </button>
        </div>
      )}
    </div>
  );
}
