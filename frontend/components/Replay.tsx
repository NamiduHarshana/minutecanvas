"use client";
// Share link page: plays a finished meeting from 00:00. The saved server messages go through the same reducer as
// the live app, at the meeting time they were sent, so the transcript, board and Flow Map appear in the same order.
// v6.19: player + three-lane timeline (Speakers / Parts / Findings) and the finding panel with its quotes.
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, ChevronLeft, ChevronRight, Link2, LoaderCircle, Pause, Play, Presentation, Trash2 } from "lucide-react";
import type { Card, FlowData, ServerMsg } from "@/core/types";
import { BACKEND_URL } from "@/core/client";
import { fmtTime, initialState, orderedCards, reducer, speakerName, speakerTones, type MeetingState } from "@/core/store";
import { setImageFetcher } from "@/core/images";
import { ReplayContext, ReplayError, eventIndexAt, fetchReplay, replayUrl, stateAt, type ReplayCtx, type ReplayData } from "@/core/replay";
import { AccountError, deleteReplay, localKey, type Finding } from "@/core/account";
import { KIND_WORD } from "@/core/library";
import { Mark } from "./Logo";
import { Board, type CardActions } from "./Board";
import { Report } from "./Report";
import { FlowMap, type FlowFocus } from "./FlowMap";
import { FindingTag, Seg } from "./kit";

const SPEEDS = ["1", "2", "4", "8"] as const;
const TICK_MS = 100;
const BIG_JUMP_S = 3;           // a forward jump longer than this is a seek (no flashes), shorter is normal playback
const noop = () => {};
const BoardM = memo(Board);
const FlowMapM = memo(FlowMap);

type Lanes = NonNullable<ReplayData["timeline"]>;

function advance(s: MeetingState, events: ReplayData["events"], from: number, to: number): MeetingState {
  for (let k = from; k < to; k++) s = reducer(s, { type: "server", msg: events[k][1] as ServerMsg });
  return s;
}

// ---------- replays saved before v6.19: lanes and findings worked out from the log ----------
function lastFlow(events: ReplayData["events"]): FlowData | null {
  for (let k = events.length - 1; k >= 0; k--) {
    const m = events[k][1];
    if (m.type === "flow") return m.flow;
    if (m.type === "report_ready" && m.report.flow) return m.report.flow;
  }
  return null;
}

function lanesFromLog(events: ReplayData["events"], duration: number): Lanes {
  const lines = events.flatMap(([, m]) => (m.type === "transcript_final" ? [m] : []));
  const speakers: Lanes["speakers"] = [];
  lines.forEach((l, i) => {
    const end = Math.max(l.t + 1, Math.min(i + 1 < lines.length ? lines[i + 1].t : duration, l.t + 30));
    const last = speakers[speakers.length - 1];
    if (last && last.speaker === l.speaker && l.t - last.t_end <= 2) last.t_end = Math.max(last.t_end, end);
    else speakers.push({ speaker: l.speaker, t: l.t, t_end: end });
  });
  const flow = lastFlow(events);
  const times = new Map<string, number[]>();
  for (const s of flow?.steps ?? []) if (s.part) times.set(s.part, [...(times.get(s.part) ?? []), ...s.evidence.map((e) => e.t)]);
  const parts: Lanes["parts"] = [];
  for (const [part, ts] of times) {
    let seg: [number, number] | null = null;
    for (const t of [...ts].sort((a, b) => a - b)) {
      if (seg && t - seg[1] <= 60) seg[1] = t;
      else {
        if (seg) parts.push({ part, t: seg[0], t_end: seg[1] + 10 });
        seg = [t, t];
      }
    }
    if (seg) parts.push({ part, t: seg[0], t_end: seg[1] + 10 });
  }
  parts.sort((a, b) => a.t - b.t);
  return { speakers, parts };
}

function findingsFromLog(events: ReplayData["events"], names: Record<string, string>): Finding[] {
  const flow = lastFlow(events);
  if (!flow) return [];
  const step = (id: string | null) => flow.steps.find((s) => s.id === id);
  const q = (e: { t: number; speaker: string; quote: string }) => ({ t: e.t, speaker: speakerName(names, e.speaker), text: e.quote });
  const out: Finding[] = [];
  for (const g of flow.gaps) {
    if (g.dismissed) continue;
    const s = step(g.step);
    out.push({
      id: g.id, kind: g.resolved ? "answered" : "gap", text: g.question, branch: g.branch, step: s?.label ?? null, part: s?.part ?? null, t: g.t,
      answer: g.answer, quotes: [...g.evidence.slice(0, 3).map(q), ...(g.resolved && g.resolved_by ? [q(g.resolved_by)] : [])],
    });
  }
  for (const c of flow.contradictions) {
    const s = step(c.step);
    out.push({
      id: c.id, kind: "conflict", text: c.note, step: s?.label ?? null, part: s?.part ?? null, t: c.t, settled: c.resolved, answer: c.answer,
      quotes: [q(c.a), q(c.b), ...(c.resolved && c.resolved_by ? [q(c.resolved_by)] : [])],
    });
  }
  for (const b of flow.bugs) {
    const s = step(b.step);
    out.push({
      id: b.id, kind: "bug", text: b.text, step: s?.label ?? null, part: s?.part ?? null, t: b.t, fixed: b.fixed,
      quotes: [...b.evidence.slice(0, 3).map(q), ...(b.fixed && b.fix ? [q(b.fix)] : [])],
    });
  }
  return out.sort((a, b) => a.t - b.t);
}

const cap = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);

export default function Replay() {
  const [data, setData] = useState<ReplayData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [state, setState] = useState<MeetingState>({ ...initialState, conn: "closed" });
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState<(typeof SPEEDS)[number]>("1");
  const [focusLine, setFocusLine] = useState<number | null>(null);
  const [view, setView] = useState<"board" | "report">("board");
  const [flowFocus, setFlowFocus] = useState<FlowFocus | null>(null);
  const [rid, setRid] = useState("");
  const [canDelete, setCanDelete] = useState(false);
  const [deleted, setDeleted] = useState(false);
  const [fi, setFi] = useState(0);                 // selected finding
  const [copied, setCopied] = useState(false);
  const start = useRef<{ report: boolean; t: number | null; f: string | null }>({ report: false, t: null, f: null });
  const tRef = useRef(0);
  const applied = useRef(0);          // events already in `state`
  const stateRef = useRef(state);
  stateRef.current = state;

  // ---------------- load ----------------
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const id = q.get("id") || "";
    const at = Number(q.get("t"));
    start.current = { report: q.get("view") === "report", t: q.has("t") && isFinite(at) ? at : null, f: q.get("f") };
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
  const lanes = useMemo<Lanes>(() => (data && events ? data.timeline ?? lanesFromLog(events, duration) : { speakers: [], parts: [] }), [data, events, duration]);
  const findings = useMemo<Finding[]>(() => (data && events ? data.findings ?? findingsFromLog(events, data.speakers) : []), [data, events]);
  // the report exists from the start of the replay (header button), not only when playback reaches the end
  const finalReport = useMemo(() => {
    for (let k = (events?.length ?? 0) - 1; k >= 0; k--) {
      const m = events![k][1];
      if (m.type === "report_ready") return m.report;
    }
    return null;
  }, [events]);
  // findings found at (nearly) the same moment stay side by side on the timeline, inside the lane
  const findingPos = useMemo(() => {
    const D = Math.max(1, duration);
    const out: number[] = [];
    let last = -Infinity;
    for (const f of findings) {
      const x = Math.max(last + 1.9, (f.t / D) * 100);
      out.push(x);
      last = x;
    }
    const over = out.length ? out[out.length - 1] - 98.4 : 0;
    return over > 0 ? out.map((x) => Math.max(0, x - over)) : out;
  }, [findings, duration]);

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
      const prev = tRef.current;
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
      // playing past a finding: the panel follows it
      if (!seek && to > prev) {
        const k = findings.findIndex((f) => f.t > prev && f.t <= to);
        if (k >= 0) setFi(k);
      }
    },
    [events, duration, findings],
  );

  useEffect(() => {
    if (!events || !playing) return;
    let last = performance.now();
    const id = setInterval(() => {
      const now = performance.now();
      const next = tRef.current + ((now - last) / 1000) * Number(speed);
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

  const focusFlow = useCallback((kind: FlowFocus["kind"], id: string) => setFlowFocus((f) => ({ kind, id, n: (f?.n ?? 0) + 1 })), []);

  /** Open a finding: the moment it was found (the Flow Map shows it), highlighted on the map. */
  const openFinding = useCallback(
    (k: number) => {
      const f = findings[k];
      if (!f) return;
      setFi(k);
      setFocusLine(null);
      moveTo(f.t + 0.3, true);
      setView("board");
      const kind = f.kind === "conflict" ? "conflict" : f.kind === "bug" ? null : "gap";
      if (kind) setTimeout(() => focusFlow(kind, f.id), 60);
    },
    [findings, moveTo, focusFlow],
  );

  // opened from Meetings ("Report"), Findings ("Play") or a link with ?t=
  useEffect(() => {
    if (!events) return;
    const st = start.current;
    start.current = { report: false, t: null, f: null };
    if (st.report) {
      const s = stateAt(events, events.length);
      if (!s.report) return;
      applied.current = events.length;
      tRef.current = duration;
      stateRef.current = s;
      setState(s);
      setT(duration);
      setPlaying(false);
      setView("report");
      return;
    }
    const k = st.f ? findings.findIndex((f) => f.id === st.f) : -1;
    if (k >= 0) openFinding(k);
    else if (st.t != null) seek(st.t);
  }, [events, duration, findings, openFinding, seek]);

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

  async function copyLink() {
    const url = replayUrl(rid);
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {
      window.prompt("Copy the replay link:", url);
    }
  }

  function togglePlay() {
    if (playing) return setPlaying(false);
    if (tRef.current >= duration - 0.05) seek(0);    // at the end: play again from 00:00
    setPlaying(true);
  }

  // keyboard: space = play/pause, ‹ › (arrow keys) = previous / next finding (no findings: 5 s)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (view !== "board" || tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
      if (e.key === " ") {
        if (tag === "BUTTON" || tag === "A") return;      // a focused button / link handles its own space
        e.preventDefault();
        togglePlay();
      } else if (e.key === "ArrowRight") findings.length ? openFinding((fi + 1) % findings.length) : seek(tRef.current + 5);
      else if (e.key === "ArrowLeft") findings.length ? openFinding((fi - 1 + findings.length) % findings.length) : seek(tRef.current - 5);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // ---------------- derived ----------------
  // names the host gave the speakers are used for the whole replay
  const shown = useMemo<MeetingState>(() => (data ? { ...state, speakers: { ...state.speakers, ...data.speakers } } : state), [state, data]);
  const cards = useMemo(() => orderedCards(shown), [shown]);
  const ctx = useMemo<ReplayCtx>(() => ({ jumpToSaid, focusLine }), [jumpToSaid, focusLine]);
  const openCard = useCallback(
    (id: string) => {
      const c = stateRef.current.cards[id];
      if (c) jumpToSaid(c.t);
    },
    [jumpToSaid],
  );
  // stable props: the clock re-renders this page 10 times a second, the board and the Flow Map shouldn't follow
  const actions = useMemo<CardActions>(() => ({ open: openCard, edit: openCard, remove: noop, toggleDone: (c: Card) => openCard(c.id) }), [openCard]);

  // ---------------- render ----------------
  if (error || deleted) {
    return (
      <div className="rp-center">
        <a href="/" aria-label="MinuteCanvas home">
          <Mark size={40} />
        </a>
        <h2>{deleted ? "Replay deleted" : "Replay not available"}</h2>
        <p>{deleted ? "The transcript, board, Flow Map and images of this meeting were removed. The link no longer works." : error}</p>
        <div className="mc-row" style={{ justifyContent: "center" }}>
          <a className="mc-btn mc-btn-primary" href="/meet">
            Start a meeting
          </a>
          <a className="mc-btn mc-btn-outline" href="/dashboard">
            Dashboard
          </a>
        </div>
      </div>
    );
  }
  if (!data || !events) {
    return (
      <div className="rp-center" role="status">
        <LoaderCircle size={22} className="spin" aria-hidden />
        <p>Loading the replay…</p>
      </div>
    );
  }

  if (view === "report" && (shown.report ?? finalReport)) {
    return <Report report={(shown.report ?? finalReport)!} backLabel="Replay" onBack={() => setView("board")} onNew={() => (window.location.href = "/meet")} />;
  }

  const ended = t >= duration - 0.05;
  const title = shown.title || data.title || "Untitled meeting";
  const usage = data.usage;
  const D = Math.max(1, duration);
  const pct = (x: number) => `${Math.max(0, Math.min(100, (x / D) * 100))}%`;
  const width = (a: number, b: number) => `${Math.max(0.4, ((Math.min(b, D) - a) / D) * 100)}%`;
  const tone = speakerTones(lanes.speakers);
  const laneSeek = (e: React.MouseEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    seek(((e.clientX - r.left) / r.width) * D);
  };
  const report = shown.report ?? finalReport;
  const sel = findings[Math.min(fi, findings.length - 1)];

  return (
    <ReplayContext.Provider value={ctx}>
      <div className="live">
        <header className="topbar">
          <a className="back" href="/dashboard">
            <ArrowLeft size={15} strokeWidth={2} aria-hidden /> Dashboard
          </a>
          <h1 className="ttl" title={title}>
            {title}
          </h1>
          <span className="mc-tag mc-tag-brand">Replay</span>
          {data.date && <span className="hint">{new Date(data.date).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}</span>}
          <span className="grow" />
          <span className="hint usage-hint">
            Muse Voice <span className="mc-time c2">{fmtTime(usage?.voice_s ?? duration)}</span>
            {usage && (
              <>
                {" · "}Muse Spark <span className="mc-time c2">{usage.spark_calls} calls</span>
                {" · "}Muse Image <span className="mc-time c2">{usage.images} pictures</span>
              </>
            )}
          </span>
          <button className="mc-btn mc-btn-outline mc-btn-sm" onClick={copyLink}>
            <Link2 strokeWidth={1.75} aria-hidden /> {copied ? "Copied" : "Copy link"}
          </button>
          {report && (
            <button className="mc-btn mc-btn-primary mc-btn-sm" onClick={() => setView("report")}>
              <Presentation strokeWidth={1.75} aria-hidden /> Report
            </button>
          )}
          {canDelete && (
            <button className="mc-btn mc-btn-danger mc-btn-sm" onClick={onDelete} title="You made this meeting in this browser">
              <Trash2 strokeWidth={1.75} aria-hidden /> Delete
            </button>
          )}
        </header>

        <div className="work">
          <main className="stage" id="content">
            {ended && report && (
              <div className="banner" role="status">
                <span className="b-text">
                  <b>End of the meeting.</b>
                  <span className="b-found">The report has the summary, decisions, actions and what was missed.</span>
                </span>
                <button className="mc-btn mc-btn-primary mc-btn-sm" onClick={() => setView("report")}>
                  <Presentation strokeWidth={1.75} aria-hidden /> Open report
                </button>
              </div>
            )}

            <section className="mc-card player-card" aria-label="Player">
              <div className="player">
                <button className="play" onClick={togglePlay} aria-label={playing ? "Pause" : "Play"}>
                  {playing ? <Pause size={18} fill="currentColor" strokeWidth={0} aria-hidden /> : <Play size={18} fill="currentColor" strokeWidth={0} aria-hidden />}
                </button>
                <span className="clock">
                  {fmtTime(t)} <span>/ {fmtTime(duration)}</span>
                </span>
                <span className="grow" />
                {findings.length > 0 && (
                  <span className="hint">
                    Finding {Math.min(fi, findings.length - 1) + 1} of {findings.length} · ‹ › keys jump between findings
                  </span>
                )}
                <Seg label="Speed" value={speed} onChange={setSpeed} options={SPEEDS.map((x) => ({ id: x, label: `${x}×` }))} />
              </div>
              <div className="mc-tl">
                <span className="lab">Speakers</span>
                <div className="mc-lane" onClick={laneSeek} role="presentation">
                  {lanes.speakers.map((s, k) => (
                    <i
                      key={k}
                      className={tone(s.speaker).slice(1)}
                      style={{ left: pct(s.t), width: width(s.t, s.t_end) }}
                      title={`${speakerName(shown.speakers, s.speaker)} · ${fmtTime(s.t)}`}
                    />
                  ))}
                  <span className="mc-head" style={{ left: pct(t) }} />
                </div>
                <span className="lab">Parts</span>
                <div className="mc-lane" onClick={laneSeek} role="presentation">
                  {lanes.parts.map((p, k) => (
                    <i key={k} className="p1" style={{ left: pct(p.t), width: width(p.t, p.t_end) }} title={`${p.part} · ${fmtTime(p.t)}`} />
                  ))}
                  <span className="mc-head" style={{ left: pct(t) }} />
                </div>
                <span className="lab">Findings</span>
                <div className="mc-lane" onClick={laneSeek} role="presentation">
                  {findings.map((f, k) => (
                    <button
                      key={f.id}
                      className={`k-${f.kind} ${k === fi ? "sel" : ""}`}
                      style={{ left: `${findingPos[k]}%`, width: "1.6%" }}
                      title={`${KIND_WORD[f.kind]} · ${fmtTime(f.t)}: ${f.text}`}
                      aria-label={`${KIND_WORD[f.kind]} at ${fmtTime(f.t)}: ${f.text}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        openFinding(k);
                      }}
                    />
                  ))}
                  <span className="mc-head" style={{ left: pct(t) }} />
                </div>
                <span />
                <div className="tl-axis">
                  {[0, 0.25, 0.5, 0.75, 1].map((x) => (
                    <span key={x} className="mc-time">
                      {fmtTime(duration * x)}
                    </span>
                  ))}
                </div>
              </div>
            </section>

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
                stepsLabel={`at ${fmtTime(t)}`}
              />
            )}
            <BoardM state={shown} cards={cards} actions={actions} readOnly />
          </main>

          <aside className="rail rp" aria-label="Findings and transcript">
            {sel ? (
              <FindingPanel
                f={sel}
                index={Math.min(fi, findings.length - 1)}
                all={findings}
                onPrev={() => openFinding((fi - 1 + findings.length) % findings.length)}
                onNext={() => openFinding((fi + 1) % findings.length)}
                onQuote={jumpToSaid}
              />
            ) : (
              <div className="mc-fp">
                <h4>Findings</h4>
                <p className="why" style={{ marginTop: 10 }}>
                  Muse Spark found no gaps, conflicts or bugs in this meeting.
                </p>
              </div>
            )}
            <TranscriptCard state={shown} t={t} onJump={jumpToSaid} />
          </aside>
        </div>
      </div>
    </ReplayContext.Provider>
  );
}

function FindingPanel({
  f,
  index,
  all,
  onPrev,
  onNext,
  onQuote,
}: {
  f: Finding;
  index: number;
  all: Finding[];
  onPrev: () => void;
  onNext: () => void;
  onQuote: (t: number) => void;
}) {
  const same = all.filter((x) => x.kind === f.kind);
  const n = same.findIndex((x) => x.id === f.id) + 1;
  const times = f.quotes.map((q) => q.t);
  const from = Math.min(f.t, ...times);
  const to = Math.max(...(times.length ? times : [f.t]));
  let what = f.text;
  let why = "";
  let ask: { label: string; text: string; ok?: boolean } | null = null;
  if (f.kind === "gap") {
    what = cap(f.branch || f.text).replace(/\?$/, "");
    why = f.step ? `Nobody said what happens at the step “${f.step}”.` : "Nobody said what happens here.";
    ask = { label: "Ask next meeting", text: f.text };
  } else if (f.kind === "answered") {
    why = f.step ? `Flagged at the step “${f.step}”, then answered later in the meeting.` : "Flagged, then answered later in the meeting.";
    if (f.answer) ask = { label: "Answered", text: f.answer, ok: true };
  } else if (f.kind === "conflict") {
    why = f.settled ? "Two statements disagreed; the meeting settled it." : "Two statements that can’t both be true. MinuteCanvas never decides who is right.";
    if (f.settled && f.answer) ask = { label: "Settled", text: f.answer, ok: true };
  } else {
    why = `${f.step ? `A fail path at the step “${f.step}”.` : "A fail path someone named."}${f.fixed ? " Marked fixed later in the meeting." : ""}`;
  }
  return (
    <section className="mc-fp" aria-label="Finding">
      <div className="fp-head">
        <h4>
          {KIND_WORD[f.kind]} {n} of {same.length}
        </h4>
        <div className="fp-nav">
          <button className="mc-ib" aria-label="Previous finding" onClick={onPrev} disabled={all.length < 2}>
            <ChevronLeft size={16} strokeWidth={2} aria-hidden />
          </button>
          <button className="mc-ib" aria-label="Next finding" onClick={onNext} disabled={all.length < 2}>
            <ChevronRight size={16} strokeWidth={2} aria-hidden />
          </button>
        </div>
      </div>
      <div className="mc-row">
        <FindingTag kind={f.kind} />
        {f.part && <span className="mc-tag mc-tag-plain">{f.part}</span>}
        <span className="mc-time">{from === to ? fmtTime(from) : `${fmtTime(from)} – ${fmtTime(to)}`}</span>
      </div>
      <div className="what">{what}</div>
      <div className="why">{why}</div>
      {ask && (
        <div className={`ask ${ask.ok ? "ok" : ""}`}>
          <b>{ask.label}</b>
          {ask.text}
        </div>
      )}
      {f.quotes.length > 0 && (
        <>
          <h4 style={{ marginTop: ask ? 0 : 14 }}>What people said</h4>
          {f.quotes.map((q, k) => (
            <button key={k} className="mc-quote" onClick={() => onQuote(q.t)} title="Play from this moment">
              <span className="mc-time">{fmtTime(q.t)}</span>
              <span>
                “{q.text}”<small>{q.speaker}</small>
              </span>
            </button>
          ))}
        </>
      )}
      <span className="sr">{`Finding ${index + 1} of ${all.length}`}</span>
    </section>
  );
}

function TranscriptCard({ state, t, onJump }: { state: MeetingState; t: number; onJump: (t: number) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const lines = state.transcript;
  const last = lines.length ? lines[lines.length - 1].id : null;
  const tone = speakerTones(lines);
  useLayoutEffect(() => {
    const el = box.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines.length]);
  return (
    <section className="mc-card tcard" aria-label="Transcript">
      <span className="lab">Transcript at {fmtTime(t)}</span>
      <div className="tlines" ref={box}>
        {lines.length === 0 && <p className="rail-empty" style={{ padding: "8px 16px" }}>Nobody has spoken yet.</p>}
        {lines.map((l) => (
          <p key={l.id} className={`tline ${l.id === last ? "focus" : ""}`}>
            <button className="mc-time tline-time" onClick={() => onJump(l.t)} title="Play from this line">
              {fmtTime(l.t)}
            </button>
            <span>
              <b className={tone(l.speaker)}>{speakerName(state.speakers, l.speaker)}:</b> {l.text}
            </span>
          </p>
        ))}
      </div>
    </section>
  );
}
