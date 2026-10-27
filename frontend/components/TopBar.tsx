"use client";
import { useEffect, useRef, useState } from "react";
import { ChevronDown, Download, FileImage, FileText, Pause, Play, Presentation, Square } from "lucide-react";
import type { ConnStatus } from "@/core/client";
import type { ServerState } from "@/core/types";
import { fmtTime } from "@/core/store";
import { Logo } from "./Logo";

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
}) {
  const live = serverState === "recording" || serverState === "paused";
  return (
    <header className="topbar">
      <div className="tb-left">
        <Logo small />
        <span className="tb-sep" />
        <h1 className="tb-title" title={title || ""}>
          {title || (serverState === "created" ? "Starting…" : "Untitled meeting")}
        </h1>
      </div>
      <div className="tb-right">
        <RecTimer state={serverState} clock={clock} />
        {sourceLabel && live && (
          <span className="tb-source">
            <LevelMeter level={level} paused={serverState === "paused"} />
            {sourceLabel}
          </span>
        )}
        <ConnBadge conn={conn} voice={voice} done={serverState === "done"} />
        {live && (
          <>
            {serverState === "paused" ? (
              <button className="btn" onClick={onResume}>
                <Play size={15} aria-hidden /> Resume
              </button>
            ) : (
              <button className="btn" onClick={onPause}>
                <Pause size={15} aria-hidden /> Pause
              </button>
            )}
            <button className="btn btn-danger" onClick={onStop}>
              <Square size={13} fill="currentColor" aria-hidden /> Stop
            </button>
          </>
        )}
        {hasReport && (
          <button className="btn btn-primary" onClick={onShowReport}>
            <Presentation size={15} aria-hidden /> Report
          </button>
        )}
        <ExportMenu disabled={exporting} onExport={onExport} />
      </div>
    </header>
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
    <span className={`rec rec-${state}`}>
      <span className="rec-dot" />
      {label} <b>{fmtTime(t)}</b>
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

function ConnBadge({ conn, voice, done }: { conn: ConnStatus; voice: string; done: boolean }) {
  if (done) return null;
  let cls = "ok";
  let text = "Connected";
  if (conn === "connecting") {
    cls = "wait";
    text = "Connecting…";
  } else if (conn === "reconnecting") {
    cls = "wait";
    text = "Reconnecting…";
  } else if (conn === "offline") {
    cls = "bad";
    text = "Offline — keeping audio";
  } else if (conn === "closed") {
    cls = "bad";
    text = "Disconnected";
  } else if (voice === "reconnecting" || voice === "connecting") {
    cls = "wait";
    text = "Transcriber reconnecting…";
  } else if (voice === "failed") {
    cls = "bad";
    text = "Transcriber failed";
  }
  return (
    <span className={`conn conn-${cls}`} role="status">
      <span className="conn-dot" />
      {text}
    </span>
  );
}

function ExportMenu({ disabled, onExport }: { disabled: boolean; onExport: (k: "png" | "pdf") => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  return (
    <div className="menu-wrap" ref={ref}>
      <button className="btn" onClick={() => setOpen((o) => !o)} disabled={disabled} aria-haspopup="menu" aria-expanded={open}>
        <Download size={15} aria-hidden /> {disabled ? "Exporting…" : "Export"} <ChevronDown size={14} aria-hidden />
      </button>
      {open && (
        <div className="menu" role="menu">
          <button
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onExport("png");
            }}
          >
            <FileImage size={15} aria-hidden /> Board as PNG
          </button>
          <button
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onExport("pdf");
            }}
          >
            <FileText size={15} aria-hidden /> Board as PDF
          </button>
        </div>
      )}
    </div>
  );
}
