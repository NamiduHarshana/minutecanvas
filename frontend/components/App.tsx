"use client";
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { LoaderCircle, Mic, MonitorSpeaker, PanelRightOpen, X } from "lucide-react";
import type { AudioSource, Card, ClientMsg } from "@/core/types";
import { AudioCapture, CaptureError, tabAudioSupported } from "@/core/audio";
import { ApiError, MeetingClient, StoredSession, checkStored, clearStored, createSession, loadStored } from "@/core/client";
import { initialState, orderedCards, reducer } from "@/core/store";
import { preloadImages, setImageFetcher } from "@/core/images";
import { exportPng, exportSinglePdf, nextFrames, safeName } from "@/core/export";
import { Setup, SetupValues } from "./Setup";
import { TopBar } from "./TopBar";
import { Board, CardActions, Latest } from "./Board";
import { CardModal } from "./CardModal";
import { Sidebar } from "./Sidebar";
import { Report } from "./Report";
import { ExportSheet } from "./ExportSheet";
import { ChainStrip } from "./ChainStrip";
import { SparkFeed } from "./SparkFeed";

type EditFields = Extract<ClientMsg, { type: "card_edit" }>["fields"];
const SOURCE_LABEL: Record<AudioSource, string> = { mic: "Mic", mic_tab: "Mic + Tab" };
const STEPS: { key: string; label: string }[] = [
  { key: "transcript", label: "Finishing the transcript" },
  { key: "cards", label: "Final pass with Muse Spark" },
  { key: "images", label: "Waiting for Muse Image illustrations" },
  { key: "report", label: "Writing the report" },
];

export default function App() {
  const [phase, setPhase] = useState<"setup" | "meeting">("setup");
  const [view, setView] = useState<"board" | "report">("board");
  const [state, dispatch] = useReducer(reducer, initialState);
  const [busy, setBusy] = useState(false);
  const [setupError, setSetupError] = useState<string | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);
  const [source, setSource] = useState<AudioSource | null>(null);
  const [needMic, setNeedMic] = useState(false);
  const [micError, setMicError] = useState<string | null>(null);
  const [stored, setStored] = useState<StoredSession | null>(null);
  const [showSidebar, setShowSidebar] = useState(true);
  const [modal, setModal] = useState<{ id: string; edit: boolean } | null>(null);
  const [exporting, setExporting] = useState<"png" | "pdf" | null>(null);
  const clientRef = useRef<MeetingClient | null>(null);
  const audioRef = useRef<AudioCapture | null>(null);
  const sheetRef = useRef<HTMLDivElement>(null);
  const shownReport = useRef(false);

  const cards = useMemo(() => orderedCards(state), [state]);
  const questions = useMemo(() => cards.filter((c) => c.column === "questions"), [cards]);
  const live = state.serverState === "recording" || state.serverState === "paused";
  const readOnly = state.serverState === "done";

  // offer "Rejoin" only if the server still has this tab's meeting
  useEffect(() => {
    const s = loadStored();
    if (!s) return;
    let cancelled = false;
    checkStored(s).then((ok) => {
      if (!cancelled) setStored(ok ? s : null);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // ---------------- wiring ----------------
  const attachAudio = useCallback((audio: AudioCapture, client: MeetingClient) => {
    audioRef.current = audio;
    audio.onChunk = (pcm) => client.pushPcm(pcm);
    audio.onTabEnded = () => dispatch({ type: "toast", text: "Tab sharing ended. Still recording your mic.", kind: "error" });
  }, []);

  const startClient = useCallback((s: StoredSession) => {
    const client = new MeetingClient(s);
    clientRef.current = client;
    client.onMessage = (msg) => dispatch({ type: "server", msg });
    client.onConn = (conn) => dispatch({ type: "conn", conn });
    client.onFatal = (message) => {
      audioRef.current?.close();
      clearStored(); // the server no longer has this meeting
      setStored(null);
      setFatal(message);
    };
    setImageFetcher((p) => client.fetchBlob(p));
    if (new URLSearchParams(window.location.search).has("debug")) (window as unknown as { __mc: MeetingClient }).__mc = client;
    client.connect();
    return client;
  }, []);

  async function onStart(v: SetupValues) {
    setBusy(true);
    setSetupError(null);
    const audio = new AudioCapture();
    try {
      await audio.open(v.source); // mic first (macOS permission delay must not starve the transcriber)
    } catch (e) {
      audio.close();
      setSetupError(e instanceof CaptureError ? e.message : "Couldn't start audio.");
      setBusy(false);
      return;
    }
    let s: StoredSession;
    try {
      s = await createSession({ title: v.title, keywords: v.keywords, access_code: v.accessCode });
    } catch (e) {
      audio.close();
      setSetupError(e instanceof ApiError ? e.message : "Couldn't create the meeting.");
      setBusy(false);
      return;
    }
    dispatch({ type: "reset" });
    shownReport.current = false;
    setSource(v.source);
    const client = startClient(s);
    attachAudio(audio, client);
    setView("board");
    setPhase("meeting");
    setBusy(false);
  }

  function onRejoin() {
    const s = loadStored();
    if (!s) return;
    dispatch({ type: "reset" });
    shownReport.current = false;
    startClient(s);
    setView("board");
    setPhase("meeting");
  }

  // after a refresh-rejoin the mic must be re-opened with a click
  useEffect(() => {
    if (phase === "meeting" && live && !audioRef.current && state.helloCount > 0) setNeedMic(true);
    if (!live) setNeedMic(false);
  }, [phase, live, state.helloCount]);

  async function reopenMic(src: AudioSource) {
    setMicError(null);
    const audio = new AudioCapture();
    try {
      await audio.open(src);
    } catch (e) {
      audio.close();
      setMicError(e instanceof CaptureError ? e.message : "Couldn't open the microphone.");
      return;
    }
    if (clientRef.current) attachAudio(audio, clientRef.current);
    setSource(src);
    setNeedMic(false);
  }

  // meeting finished: release the mic, show the report once
  useEffect(() => {
    if (state.serverState === "stopping" || state.serverState === "done") {
      audioRef.current?.close();
      audioRef.current = null;
    }
    if (state.report && !shownReport.current) {
      shownReport.current = true;
      setView("report");
    }
  }, [state.serverState, state.report]);

  // warn before closing the tab mid-meeting
  useEffect(() => {
    if (!live && state.serverState !== "stopping") return;
    const h = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [live, state.serverState]);

  // expire "Updated" badges and toasts
  useEffect(() => {
    const id = setInterval(() => {
      const now = Date.now();
      const old = Object.entries(state.flash)
        .filter(([, f]) => now - f.at > 15000)
        .map(([k]) => k);
      if (old.length) dispatch({ type: "clearFlash", ids: old });
    }, 1000);
    return () => clearInterval(id);
  }, [state.flash]);
  useEffect(() => {
    if (!state.toasts.length) return;
    const t = state.toasts[0];
    const id = setTimeout(() => dispatch({ type: "dismissToast", id: t.id }), t.kind === "error" ? 9000 : 6000);
    return () => clearTimeout(id);
  }, [state.toasts]);

  // ---------------- controls ----------------
  const clock = useCallback(() => clientRef.current?.meetingSeconds() ?? 0, []);
  const level = useCallback(() => audioRef.current?.level ?? 0, []);
  const send = (m: ClientMsg) => clientRef.current?.send(m);
  const dismissActivity = useCallback((id: number) => dispatch({ type: "dismissActivity", id }), []);

  function onStop() {
    if (!window.confirm("Stop the meeting? Recording ends and the report is built.")) return;
    clientRef.current?.stop();
    audioRef.current?.close();
    audioRef.current = null;
  }

  function newMeeting() {
    if (live && !window.confirm("This meeting is still recording. Leave it?")) return;
    clientRef.current?.close();
    clientRef.current = null;
    audioRef.current?.close();
    audioRef.current = null;
    setImageFetcher(null);
    clearStored();
    setStored(null);
    dispatch({ type: "reset" });
    setFatal(null);
    setModal(null);
    setPhase("setup");
  }

  const actions: CardActions = {
    open: (id) => setModal({ id, edit: false }),
    edit: (id) => setModal({ id, edit: true }),
    remove: (id) => {
      if (window.confirm("Delete this card? The AI won’t add it again.")) send({ type: "card_delete", id });
    },
    toggleDone: (c: Card) => send({ type: "card_edit", id: c.id, fields: { done: !c.done } }),
  };

  async function onExport(kind: "png" | "pdf") {
    setExporting(kind);
    try {
      await preloadImages(cards.map((c) => c.image_url));
      await nextFrames(3);
      const el = sheetRef.current?.firstElementChild as HTMLElement | null;
      if (!el) return;
      const name = `${safeName(state.title)}-board`;
      if (kind === "png") await exportPng(el, `${name}.png`);
      else await exportSinglePdf(el, `${name}.pdf`);
    } catch (e) {
      console.error(e);
      dispatch({ type: "toast", text: "Export failed. Please try again.", kind: "error" });
    } finally {
      setExporting(null);
    }
  }

  // ---------------- render ----------------
  if (phase === "setup") {
    return <Setup busy={busy} error={setupError} onStart={onStart} canRejoin={!!stored} onRejoin={onRejoin} />;
  }

  if (view === "report" && state.report) {
    return <Report report={state.report} onBack={() => setView("board")} onNew={newMeeting} />;
  }

  return (
    <div className={`app ${showSidebar ? "" : "no-sidebar"}`}>
      <TopBar
        title={state.title}
        serverState={state.serverState}
        conn={state.conn}
        voice={state.voice}
        sourceLabel={source && audioRef.current ? SOURCE_LABEL[source] : null}
        clock={clock}
        level={level}
        hasReport={!!state.report}
        exporting={!!exporting}
        onPause={() => clientRef.current?.pause()}
        onResume={() => clientRef.current?.resume()}
        onStop={onStop}
        onExport={onExport}
        onShowReport={() => setView("report")}
      />
      <main className="main">
        {state.serverState === "paused" && <div className="paused-bar">Paused — nothing is being transcribed. Press Resume to continue.</div>}
        {(live || state.serverState === "stopping") && <ChainStrip state={state} />}
        <Latest cards={cards} actions={actions} flash={state.flash} />
        <Board state={state} cards={cards} actions={actions} readOnly={readOnly} />
      </main>
      {showSidebar ? (
        <Sidebar
          state={state}
          questions={questions}
          readOnly={readOnly}
          onHide={() => setShowSidebar(false)}
          onRename={(label, name) => send({ type: "rename_speaker", label, name })}
          onOpenCard={(id) => setModal({ id, edit: false })}
          onResolve={(q) => send({ type: "card_edit", id: q.id, fields: { resolved: true } })}
        />
      ) : (
        <button className="show-sidebar btn" onClick={() => setShowSidebar(true)}>
          <PanelRightOpen size={15} aria-hidden /> Transcript
        </button>
      )}

      {modal && (
        <CardModal
          cards={cards}
          id={modal.id}
          startEditing={modal.edit}
          readOnly={readOnly}
          onNavigate={(id) => setModal({ id, edit: false })}
          onClose={() => setModal(null)}
          onSave={(id, fields: EditFields) => send({ type: "card_edit", id, fields })}
          onDelete={(id) => {
            if (window.confirm("Delete this card? The AI won’t add it again.")) {
              send({ type: "card_delete", id });
              setModal(null);
            }
          }}
        />
      )}

      {state.serverState === "stopping" && (
        <div className="overlay">
          <div className="finishing" role="status">
            <h2>Building your report</h2>
            <ol>
              {STEPS.map((s, k) => {
                const cur = STEPS.findIndex((x) => x.key === state.stopStep);
                const st = k < cur ? "done" : k === cur ? "now" : "todo";
                return (
                  <li key={s.key} className={st}>
                    {st === "now" ? <LoaderCircle size={16} className="spin" aria-hidden /> : <span className="step-dot" />}
                    {s.label}
                  </li>
                );
              })}
            </ol>
          </div>
        </div>
      )}

      {needMic && !fatal && (
        <div className="overlay">
          <div className="finishing">
            <h2>Reconnect your microphone</h2>
            <p className="muted">The meeting is still running. Your browser needs a click to use the mic again.</p>
            <div className="source-pick">
              <button className="source on" onClick={() => reopenMic("mic")}>
                <Mic size={20} aria-hidden />
                <b>Mic</b>
              </button>
              <button className="source" onClick={() => reopenMic("mic_tab")} disabled={!tabAudioSupported()}>
                <MonitorSpeaker size={20} aria-hidden />
                <b>Mic + Tab</b>
              </button>
            </div>
            {micError && <p className="form-error">{micError}</p>}
          </div>
        </div>
      )}

      {fatal && (
        <div className="overlay">
          <div className="finishing">
            <h2>Meeting disconnected</h2>
            <p className="muted">{fatal}</p>
            <button className="btn btn-primary" onClick={newMeeting}>
              Back to start
            </button>
          </div>
        </div>
      )}

      <SparkFeed items={state.activity} onOpen={(id) => setModal({ id, edit: false })} onDismiss={dismissActivity} />

      <div className="toasts" aria-live="polite">
        {state.toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`}>
            <span>{t.text}</span>
            <button className="icon-btn" onClick={() => dispatch({ type: "dismissToast", id: t.id })} aria-label="Dismiss">
              <X size={14} aria-hidden />
            </button>
          </div>
        ))}
      </div>

      {exporting &&
        createPortal(
          <div className="offscreen" ref={sheetRef} aria-hidden>
            <ExportSheet title={state.title} cards={cards} meetingT={clock()} />
          </div>,
          document.body,
        )}
    </div>
  );
}
