"use client";
import { useEffect, useState } from "react";
import type { MeetingState } from "@/core/store";
import { speakerName } from "@/core/store";

type Tone = "on" | "busy" | "idle" | "bad";

/** The live Voice → Spark → Image pipeline strip, so viewers can see each Meta model working. */
export function ChainStrip({ state }: { state: MeetingState }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(id);
  }, []);

  const paused = state.serverState === "paused";
  const stopping = state.serverState === "stopping";

  // ---- Voice ----
  const talking = Object.values(state.partials).find((p) => p.text);
  let vTone: Tone = "on";
  let vMain = "Listening";
  if (paused) (vTone = "idle"), (vMain = "Paused");
  else if (stopping) (vTone = "idle"), (vMain = "Finished");
  else if (state.voice === "failed") (vTone = "bad"), (vMain = "Stopped working");
  else if (state.voice !== "live") (vTone = "busy"), (vMain = "Connecting…");
  else if (talking) (vTone = "busy"), (vMain = `${talking.speaker ? speakerName(state.speakers, talking.speaker) : "Someone"} is talking`);
  const lines = state.transcript.length;
  const vTitle = `${lines} ${lines === 1 ? "line" : "lines"}`;

  // ---- Spark ----
  const sp = state.spark;
  const queued = Math.max(0, lines - sp.cursor);
  const mapping = state.flowStatus?.state === "thinking";
  let sTone: Tone = "idle";
  let sMain: string;
  if (sp.state === "thinking") {
    sTone = "busy";
    sMain = `Reading ${sp.linesRead ?? 0} new ${sp.linesRead === 1 ? "line" : "lines"} · ${Math.max(0, Math.round((now - (sp.startedAt ?? now)) / 1000))}s`;
  } else if (mapping) {
    sTone = "busy";
    sMain = state.flowStatus?.mode === "gap_review" || state.flowStatus?.mode === "gap_check" ? "Checking the flow for gaps" : "Drawing the Flow Map";
  } else if (sp.state === "error") {
    sTone = "bad";
    sMain = "Will retry next round";
  } else if (queued) {
    sTone = "on";
    sMain = `${queued} new ${queued === 1 ? "line" : "lines"} queued`;
  } else sMain = stopping ? "Final check" : "Waiting for new talk";
  const sTitle = sp.rounds > 0 ? `Last round: +${sp.added} cards, ${sp.updated} updated, ${sp.seconds ?? "–"}s` : "Reads the conversation every ~40 s";

  // ---- Image ----
  const cards = Object.values(state.cards);
  const drawing = cards.filter((c) => c.image_status === "pending").length;
  const drawn = cards.filter((c) => c.image_status === "ready").length;
  const iTone: Tone = drawing ? "busy" : drawn ? "on" : "idle";
  const iMain = drawing
    ? `Drawing ${drawing} ${drawing === 1 ? "illustration" : "illustrations"}`
    : drawn
      ? `${drawn} ${drawn === 1 ? "card" : "cards"} illustrated`
      : "Waiting for cards";
  const iTitle = state.imagesLeft != null && state.imagesLeft < 10 ? `${state.imagesLeft} pictures left` : `${drawn} drawn`;

  return (
    <section className="pipe" aria-label="Meta models at work">
      <Stage kind="Voice" model="Muse Voice" main={vMain} tone={vTone} title={vTitle} />
      <Stage kind="Spark" model="Muse Spark" main={sMain} tone={sTone} title={sTitle} />
      <Stage kind="Image" model="Muse Image" main={iMain} tone={iTone} title={iTitle} />
    </section>
  );
}

function Stage({ kind, model, main, tone, title }: { kind: string; model: string; main: string; tone: Tone; title: string }) {
  return (
    <div className={`p ${tone === "busy" ? "on" : tone === "bad" ? "bad" : tone === "idle" ? "idle" : ""}`} title={`${model}: ${main} · ${title}`}>
      <span className="mc-tag mc-tag-plain">{kind}</span>
      <span>
        <em>{model}</em>
        <b>{main}</b>
      </span>
    </div>
  );
}
