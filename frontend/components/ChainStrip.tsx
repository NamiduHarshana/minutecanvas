"use client";
import { useEffect, useState } from "react";
import { AudioLines, ImageIcon, Sparkles } from "lucide-react";
import type { MeetingState } from "@/core/store";
import { speakerName } from "@/core/store";

type Tone = "on" | "busy" | "idle" | "bad";

/** The live Voice → Spark → Image chain, so viewers can see each Meta model working. */
export function ChainStrip({ state }: { state: MeetingState }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(id);
  }, []);

  const paused = state.serverState === "paused";
  const stopping = state.serverState === "stopping";

  // ---- Voice ----
  const speakers = new Set(state.transcript.map((l) => l.speaker));
  const talking = Object.values(state.partials).find((p) => p.text);
  let vTone: Tone = "on";
  let vMain = "Listening";
  if (paused) (vTone = "idle"), (vMain = "Paused");
  else if (stopping) (vTone = "idle"), (vMain = "Finished");
  else if (state.voice === "failed") (vTone = "bad"), (vMain = "Stopped");
  else if (state.voice !== "live") (vTone = "busy"), (vMain = "Connecting…");
  else if (talking) (vTone = "busy"), (vMain = `${talking.speaker ? speakerName(state.speakers, talking.speaker) : "Someone"} is talking`);
  const vSub = `${state.transcript.length} ${state.transcript.length === 1 ? "line" : "lines"} · ${speakers.size} ${speakers.size === 1 ? "speaker" : "speakers"}`;

  // ---- Spark ----
  const sp = state.spark;
  const queued = Math.max(0, state.transcript.length - sp.cursor);
  let sTone: Tone = "idle";
  let sMain: string;
  let sSub: string;
  if (sp.state === "thinking") {
    sTone = "busy";
    sMain = `Reading ${sp.linesRead ?? 0} new ${sp.linesRead === 1 ? "line" : "lines"}`;
    sSub = `Finding goals, decisions, actions… ${Math.max(0, Math.round((now - (sp.startedAt ?? now)) / 1000))}s`;
  } else {
    sMain = sp.state === "error" ? "Will retry next round" : queued ? `${queued} new ${queued === 1 ? "line" : "lines"} queued` : "Waiting for new talk";
    if (sp.state === "error") sTone = "bad";
    else if (queued) sTone = "on";
    sSub =
      sp.rounds > 0
        ? `Last round: +${sp.added} ${sp.added === 1 ? "card" : "cards"} · ${sp.updated} updated · ${sp.seconds ?? "–"}s`
        : "Reads the conversation every ~40 s";
  }

  // ---- Image ----
  const cards = Object.values(state.cards);
  const drawing = cards.filter((c) => c.image_status === "pending").length;
  const drawn = cards.filter((c) => c.image_status === "ready").length;
  const iTone: Tone = drawing ? "busy" : drawn ? "on" : "idle";
  const iMain = drawing ? `Drawing ${drawing} ${drawing === 1 ? "illustration" : "illustrations"}` : drawn ? "All cards illustrated" : "Waiting for cards";
  const iSub = `${drawn} drawn${state.imagesLeft != null && state.imagesLeft < 10 ? ` · ${state.imagesLeft} left` : ""}`;

  return (
    <section className="chain" aria-label="Live AI chain">
      <Stage icon={<AudioLines size={18} />} model="Muse Voice Transcribe" main={vMain} sub={vSub} tone={vTone} />
      <Flow active={!paused && (queued > 0 || sp.state === "thinking")} />
      <Stage icon={<Sparkles size={18} />} model="Muse Spark" main={sMain} sub={sSub} tone={sTone} />
      <Flow active={drawing > 0} />
      <Stage icon={<ImageIcon size={18} />} model="Muse Image" main={iMain} sub={iSub} tone={iTone} />
    </section>
  );
}

function Stage({ icon, model, main, sub, tone }: { icon: React.ReactNode; model: string; main: string; sub: string; tone: Tone }) {
  return (
    <div className={`stage-box tone-${tone}`}>
      <span className="stage-icon" aria-hidden>
        {icon}
      </span>
      <div className="stage-text">
        <span className="stage-model">{model}</span>
        <b className="stage-main">{main}</b>
        <span className="stage-sub">{sub}</span>
      </div>
    </div>
  );
}

function Flow({ active }: { active: boolean }) {
  return (
    <span className={`flow ${active ? "active" : ""}`} aria-hidden>
      <i />
      <i />
      <i />
    </span>
  );
}
