"use client";
// Share link page: plays a finished meeting from 00:00. The saved server messages go through the same reducer as
// the live app, at the meeting time they were sent, so the transcript, board and Flow Map appear in the same order.
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { LoaderCircle, Pause, Play, Presentation, RotateCcw, RotateCw, TriangleAlert, Bug, CircleHelp, PanelRightOpen, Trash2 } from "lucide-react";
import type { Card, FlowEvent, ServerMsg } from "@/core/types";
import { BACKEND_URL } from "@/core/client";
import { fmtTime, initialState, orderedCards, reducer, type MeetingState } from "@/core/store";
import { setImageFetcher } from "@/core/images";
import { ReplayContext, ReplayError, eventIndexAt, fetchReplay, stateAt, type ReplayCtx, type ReplayData } from "@/core/replay";
import { AccountError, deleteReplay, localKey } from "@/core/account";
import { Logo } from "./Logo";
import { Board, type CardActions } from "./Board";
import { Sidebar } from "./Sidebar";
import { Report } from "./Report";
import { FlowMap, type FlowFocus } from "./FlowMap";

const SPEEDS = [1, 1.5, 2, 4, 8];
const TICK_MS = 100;
const BIG_JUMP_S = 3;           // a forward jump longer than this is a seek (no flashes), shorter is normal playback
const noop = () => {};
const BoardM = memo(Board);
const SidebarM = memo(Sidebar);
const FlowMapM = memo(FlowMap);
type Marker = { t: number; kind: "gap" | "conflict" | "bug"; text: string };

function advance(s: MeetingState, events: ReplayData["events"], from: number, to: number): MeetingState {
  for (let k = from; k < to; k++) s = reducer(s, { type: "server", msg: events[k][1] as ServerMsg });
  return s;
}

export default function Replay() {
  const [data, setData] = useState<ReplayData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [state, setState] = useState<MeetingState>({ ...initialState, conn: "closed" });
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState(1);
  const [focusLine, setFocusLine] = useState<number | null>(null);
  const [view, setView] = useState<"board" | "report">("board");
  const [showSidebar, setShowSidebar] = useState(true);
  const [flowFocus, setFlowFocus] = useState<FlowFocus | null>(null);
  const [rid, setRid] = useState("");
  const [canDelete, setCanDelete] = useState(false);
  const [deleted, setDeleted] = useState(false);
  const openReport = useRef(false);     // ?view=report (from "My meetings")
  const tRef = useRef(0);
  const applied = useRef(0);          // events already in `state`
  const stateRef = useRef(state);
  stateRef.current = state;

  // ---------------- load ----------------
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const id = q.get("id") || "";
    openReport.current = q.get("view") === "report";
    setRid(id);
    setCanDelete(!!localKey(id));
    setImageFetcher(async (p) => {
      const r = await fetch(`${BACKEND_URL}${p}`);
      if (!r.ok) throw new Error(`image ${r.status}`);
      return r.blob();
    });
    fetchReplay(id).then(
      (d) => {
        setData(d);
        document.title = `${d.title || "Meeting"} — MinuteCanvas replay`;
      },
      (e) => setError(e instanceof ReplayError ? e.message : "This replay can't be loaded."),
    );
    return () => setImageFetcher(null);
  }, []);

  const events = data?.events ?? null;
  const duration = data ? Math.max(data.duration_s, events?.length ? events[events.length - 1][0] : 0) : 0;

  // opened as "Report" from My meetings: jump to the end and show the report
  useEffect(() => {
    if (!events || !openReport.current) return;
    openReport.current = false;
    const s = stateAt(events, events.length);
    if (!s.report) return;
    applied.current = events.length;
    tRef.current = duration;
    stateRef.current = s;
    setState(s);
    setT(duration);
    setPlaying(false);
    setView("report");
  }, [events, duration]);

  async function onDelete() {
    if (!window.confirm("Delete this replay? The link stops working for everyone. This can't be undone.")) return;
    try {
      await deleteReplay(rid);
      setPlaying(false);
      setDeleted(true);
    } catch (e) {
      window.alert(e instanceof AccountError ? e.message : "Couldn't delete the replay. Please try again.");
    }
  }

  // ---------------- clock ----------------
  const moveTo = useCallback(
    (target: number, seek: boolean) => {
      if (!events) return;
      const to = Math.max(0, Math.min(duration, target));
      const idx = eventIndexAt(events, to);
      let s = stateRef.current;
      if (idx < applied.current) {
        s = stateAt(events, idx);                                 // back: rebuild from 00:00
      } else if (idx > applied.current) {
        s = advance(s, events, applied.current, idx);
        if (seek) s = { ...s, flash: {}, flowFlash: {}, activity: [], toasts: [] };
      }
      applied.current = idx;
      tRef.current = to;
      // "Updated" badges: 15 s of real time, like the live app
      const now = Date.now();
      const old = Object.keys(s.flash).filter((k) => now - s.flash[k].at > 15000);
      if (old.length) {
        const flash = { ...s.flash };
        old.forEach((k) => delete flash[k]);
        s = { ...s, flash };
      }
      if (s !== stateRef.current) {
        stateRef.current = s;
        setState(s);
      }
      setT(to);
    },
    [events, duration],
  );

  useEffect(() => {
    if (!events || !playing) return;
    let last = performance.now();
    const id = setInterval(() => {
      const now = performance.now();
      const next = tRef.current + ((now - last) / 1000) * speed;
      last = now;
      moveTo(next, false);
      if (next >= duration) setPlaying(false);
    }, TICK_MS);
    return () => clearInterval(id);
  }, [events, playing, speed, duration, moveTo]);

  const seek = useCallback(
    (target: number) => {
      setFocusLine(null);
      moveTo(target, Math.abs(target - tRef.current) > BIG_JUMP_S || target < tRef.current);
    },
    [moveTo],
  );

  /** Jump to when something was said: the moment its transcript line arrived (so the line is on screen). */
  const jumpToSaid = useCallback(
    (said: number) => {
      if (!events) return;
      const hit = events.find(([, m]) => m.type === "transcript_final" && m.t >= said - 0.05);
      const target = hit ? hit[0] : said;
      moveTo(target, true);
      setFocusLine(hit && hit[1].type === "transcript_final" ? hit[1].id : null);
      setView("board");
      setPlaying(true);
    },
    [events, moveTo],
  );

  // keyboard: space = play/pause, arrows = 5 s
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (view !== "board" || tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA" || tag === "BUTTON") return;
      if (e.key === " ") {
        e.preventDefault();
        togglePlay();
      } else if (e.key === "ArrowRight") seek(tRef.current + 5);
      else if (e.key === "ArrowLeft") seek(tRef.current - 5);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  function togglePlay() {
    if (playing) return setPlaying(false);
    if (tRef.current >= duration - 0.05) seek(0);    // at the end: play again from 00:00
    setPlaying(true);
  }

  // ---------------- derived ----------------
  const markers = useMemo<Marker[]>(() => {
    if (!events) return [];
    const seen = new Set<string>();
    const out: Marker[] = [];
    for (const [et, m] of events) {
      if (m.type !== "flow") continue;
      for (const e of m.events) {
        const kind = e.kind === "gap" ? "gap" : e.kind === "contradiction" ? "conflict" : e.kind === "bug" ? "bug" : null;
        if (!kind || seen.has(e.id)) continue;
        seen.add(e.id);
        out.push({ t: et, kind, text: e.text });
      }
    }
    return out;
  }, [events]);

  // names the host gave the speakers are used for the whole replay
  const shown = useMemo<MeetingState>(
    () => (data ? { ...state, speakers: { ...state.speakers, ...data.speakers } } : state),
    [state, data],
  );
  const cards = useMemo(() => orderedCards(shown), [shown]);
  const questions = useMemo(() => cards.filter((c) => c.column === "questions"), [cards]);
  const ctx = useMemo<ReplayCtx>(() => ({ jumpToSaid, focusLine }), [jumpToSaid, focusLine]);
  const focusFlow = useCallback((kind: FlowFocus["kind"], id: string) => setFlowFocus((f) => ({ kind, id, n: (f?.n ?? 0) + 1 })), []);
  const openFlowEvent = useCallback(
    (e: FlowEvent) => {
      if (e.kind === "gap" || e.kind === "gap_resolved") focusFlow("gap", e.id);
      else if (e.kind === "contradiction" || e.kind === "contradiction_resolved") focusFlow("conflict", e.id);
      else if (e.step) focusFlow("step", e.step);
    },
    [focusFlow],
  );
  const openCard = useCallback(
    (id: string) => {
      const c = stateRef.current.cards[id];
      if (c) jumpToSaid(c.t);
    },
    [jumpToSaid],
  );
  // stable props: the clock re-renders this page 10 times a second, the board and the Flow Map shouldn't follow
  const actions = useMemo<CardActions>(() => ({ open: openCard, edit: openCard, remove: noop, toggleDone: (c: Card) => openCard(c.id) }), [openCard]);
  const hideSidebar = useCallback(() => setShowSidebar(false), []);

  // ---------------- render ----------------
  if (error || deleted) {
    return (
      <div className="rp-center">
        <a href="/" aria-label="MinuteCanvas home">
          <Logo />
        </a>
        <h2>{deleted ? "Replay deleted" : "Replay not available"}</h2>
        <p className="muted">{deleted ? "The transcript, board, Flow Map and images of this meeting were removed. The link no longer works." : error}</p>
        <a className="btn btn-primary" href="/meet">
          Start a meeting
        </a>
      </div>
    );
  }
  if (!data || !events) {
    return (
      <div className="rp-center" role="status">
        <LoaderCircle size={22} className="spin" aria-hidden />
        <p className="muted">Loading the replay…</p>
      </div>
    );
  }

  if (view === "report" && shown.report) {
    return <Report report={shown.report} onBack={() => setView("board")} onNew={() => (window.location.href = "/meet")} />;
  }

  const ended = t >= duration - 0.05;
  return (
    <ReplayContext.Provider value={ctx}>
      <div className={`app rp-app ${showSidebar ? "" : "no-sidebar"}`}>
        <header className="topbar">
          <div className="tb-left">
            <a href="/" aria-label="MinuteCanvas home">
              <Logo small />
            </a>
            <span className="tb-sep" />
            <h1 className="tb-title" title={shown.title || data.title || ""}>
              {shown.title || data.title || "Untitled meeting"}
            </h1>
            <span className="rp-badge">Replay</span>
            {data.date && <span className="rp-date muted">{new Date(data.date).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}</span>}
          </div>
          <div className="tb-right">
            {canDelete && (
              <button className="btn btn-ghost-danger" onClick={onDelete} title="You made this meeting on this browser">
                <Trash2 size={15} aria-hidden /> Delete replay
              </button>
            )}
            {shown.report && (
              <button className="btn btn-primary" onClick={() => setView("report")}>
                <Presentation size={15} aria-hidden /> Report
              </button>
            )}
          </div>
        </header>
        <main className="main rp-main">
          <div className="rp-bar" role="group" aria-label="Replay controls">
            <button className="btn btn-primary rp-play" onClick={togglePlay} aria-label={playing ? "Pause" : "Play"}>
              {playing ? <Pause size={16} fill="currentColor" aria-hidden /> : <Play size={16} fill="currentColor" aria-hidden />}
            </button>
            <button className="icon-btn" onClick={() => seek(t - 10)} aria-label="Back 10 seconds" title="Back 10 s">
              <RotateCcw size={16} aria-hidden />
            </button>
            <button className="icon-btn" onClick={() => seek(t + 10)} aria-label="Forward 10 seconds" title="Forward 10 s">
              <RotateCw size={16} aria-hidden />
            </button>
            <span className="rp-time">
              <b>{fmtTime(t)}</b> / {fmtTime(duration)}
            </span>
            <div className="rp-track">
              <input
                type="range"
                min={0}
                max={Math.max(1, duration)}
                step={0.1}
                value={t}
                onChange={(e) => seek(Number(e.target.value))}
                aria-label="Meeting time"
              />
              {markers.map((m, k) => (
                <button
                  key={k}
                  className={`rp-mark ${m.kind}`}
                  style={{ left: `${(m.t / Math.max(1, duration)) * 100}%` }}
                  onClick={() => seek(m.t)}
                  title={`${fmtTime(m.t)} · ${m.kind === "gap" ? "Gap" : m.kind === "conflict" ? "Conflict" : "Bug"}: ${m.text}`}
                  aria-label={`${m.kind} at ${fmtTime(m.t)}`}
                />
              ))}
            </div>
            <select className="rp-speed" value={speed} onChange={(e) => setSpeed(Number(e.target.value))} aria-label="Speed">
              {SPEEDS.map((x) => (
                <option key={x} value={x}>
                  {x}×
                </option>
              ))}
            </select>
          </div>
          {markers.length > 0 && (
            <p className="rp-legend muted small">
              <span><CircleHelp size={12} aria-hidden /> gap</span>
              <span><TriangleAlert size={12} aria-hidden /> conflict</span>
              <span><Bug size={12} aria-hidden /> bug</span>
              — marks on the timeline show when Muse Spark found them. Click a card, a transcript time or a quote time to play from that moment.
            </p>
          )}
          {ended && shown.report && (
            <div className="report-banner" role="status">
              <span className="rb-text">
                <b>End of the meeting.</b>
                <span className="rb-found muted">The report has the summary, decisions, actions and what was missed.</span>
              </span>
              <button className="btn btn-primary" onClick={() => setView("report")}>
                <Presentation size={15} aria-hidden /> Open report
              </button>
            </div>
          )}
          {shown.flow && shown.flow.steps.length > 0 && (
            <FlowMapM
              flow={shown.flow}
              stats={shown.flowStats}
              status={shown.flowStatus}
              flash={shown.flowFlash}
              speakers={shown.speakers}
              live={false}
              focus={flowFocus}
              onCheckGaps={noop}
              onOpenCard={openCard}
            />
          )}
          <BoardM state={shown} cards={cards} actions={actions} readOnly />
        </main>
        {showSidebar ? (
          <SidebarM
            state={shown}
            questions={questions}
            readOnly
            onHide={hideSidebar}
            onRename={noop}
            onOpenCard={openCard}
            onOpenFlow={openFlowEvent}
            onResolve={noop}
          />
        ) : (
          <button className="show-sidebar btn" onClick={() => setShowSidebar(true)}>
            <PanelRightOpen size={15} aria-hidden /> Panel
          </button>
        )}
      </div>
    </ReplayContext.Provider>
  );
}
