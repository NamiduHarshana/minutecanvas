"use client";
import { useEffect, useState } from "react";
import { AudioLines, CircleCheckBig, Copy, Mic, MicOff, MonitorSpeaker, ShieldCheck } from "lucide-react";
import type { AudioSource } from "@/core/types";
import { micPermission, requestMic, tabAudioSupported, type MicPermission } from "@/core/audio";
import { Logo } from "./Logo";

export interface SetupValues {
  title: string;
  keywords: string[];
  accessCode: string;
  source: AudioSource;
}

const LS_KEY = "minutecanvas.setup";

// for the Meet / Zoom chat, so people hear about it before recording starts
const NOTICE =
  "Heads-up: I'm using MinuteCanvas to take live notes of this meeting. " +
  "What we say is transcribed live by Meta's AI. MinuteCanvas does not store the audio. Tell me if you'd rather I didn't.";

export function Setup({
  busy,
  error,
  onStart,
  canRejoin,
  onRejoin,
}: {
  busy: boolean;
  error: string | null;
  onStart: (v: SetupValues) => void;
  canRejoin: boolean;
  onRejoin: () => void;
}) {
  const [title, setTitle] = useState("");
  const [keywords, setKeywords] = useState("");
  const [accessCode, setAccessCode] = useState("");
  const [source, setSource] = useState<AudioSource>("mic");
  const [noticed, setNoticed] = useState(false);
  const [tabOk, setTabOk] = useState(true);
  const [mic, setMic] = useState<MicPermission>("unknown");
  const [asking, setAsking] = useState(false);
  const [copied, setCopied] = useState(false);

  async function copyNotice() {
    try {
      await navigator.clipboard.writeText(NOTICE);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {
      window.prompt("Copy this notice:", NOTICE);
    }
  }

  // know the mic permission up front, and follow changes made from the address bar
  useEffect(() => {
    let stop = () => {};
    micPermission().then((p) => setMic(p));
    navigator.permissions
      ?.query({ name: "microphone" as PermissionName })
      .then((st) => {
        const on = () => setMic(st.state as MicPermission);
        st.addEventListener("change", on);
        stop = () => st.removeEventListener("change", on);
      })
      .catch(() => {});
    return () => stop();
  }, []);

  async function allowMic() {
    setAsking(true);
    setMic(await requestMic());
    setAsking(false);
  }
  // Start can't stop for a mic prompt: Chrome would already be showing the tab picker / the shared tab
  const micReady = mic === "granted" || mic === "unknown";

  useEffect(() => {
    setTabOk(tabAudioSupported());
    try {
      const saved = JSON.parse(localStorage.getItem(LS_KEY) || "{}");
      if (typeof saved.keywords === "string") setKeywords(saved.keywords);
      if (typeof saved.accessCode === "string") setAccessCode(saved.accessCode);
      if (saved.source === "mic" || saved.source === "mic_tab") setSource(saved.source);
    } catch {
      /* ignore */
    }
  }, []);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!noticed || busy || !micReady) return;
    const src = source === "mic_tab" && !tabOk ? "mic" : source;
    try {
      localStorage.setItem(LS_KEY, JSON.stringify({ keywords, accessCode, source: src }));
    } catch {
      /* ignore */
    }
    onStart({
      title: title.trim(),
      keywords: keywords
        .split(",")
        .map((k) => k.trim())
        .filter(Boolean)
        .slice(0, 30),
      accessCode: accessCode.trim(),
      source: src,
    });
  }

  return (
    <main className="setup">
      <div className="setup-card">
        <div className="setup-head">
          <Logo />
          <p className="setup-tag">Turns your meeting into an illustrated board while people speak.</p>
        </div>

        {canRejoin && (
          <div className="rejoin">
            <span>A meeting from this tab is still on the server.</span>
            <button type="button" className="btn btn-primary" onClick={onRejoin} disabled={busy}>
              Rejoin meeting
            </button>
          </div>
        )}

        <form onSubmit={submit} className="setup-form">
          <label className="field">
            <span>Meeting title (optional)</span>
            <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={80} placeholder="The AI names it if you leave this empty" />
          </label>

          <label className="field">
            <span>Names &amp; project words</span>
            <input
              value={keywords}
              onChange={(e) => setKeywords(e.target.value)}
              maxLength={600}
              placeholder="Kasun, Amali, Stripe, MinuteCanvas"
            />
            <small>Comma-separated. Helps the transcript spell them right.</small>
          </label>

          <fieldset className="field">
            <span>Audio</span>
            <div className="source-pick">
              <button type="button" className={`source ${source === "mic" ? "on" : ""}`} onClick={() => setSource("mic")} aria-pressed={source === "mic"}>
                <Mic size={20} aria-hidden />
                <b>Mic</b>
                <small>In-room meeting</small>
              </button>
              <button
                type="button"
                className={`source ${source === "mic_tab" ? "on" : ""}`}
                onClick={() => tabOk && setSource("mic_tab")}
                aria-pressed={source === "mic_tab"}
                disabled={!tabOk}
              >
                <MonitorSpeaker size={20} aria-hidden />
                <b>Mic + Tab</b>
                <small>{tabOk ? "Google Meet / Zoom tab" : "Chrome only"}</small>
              </button>
            </div>
            {source === "mic_tab" && tabOk && (
              <small>Next, pick the meeting tab and turn on “Also share tab audio”. MinuteCanvas stays on screen. Use headphones so the mic doesn’t pick up the speakers.</small>
            )}
            {mic === "granted" ? (
              <span className="mic-state ok">
                <CircleCheckBig size={14} aria-hidden /> Microphone allowed
              </span>
            ) : mic === "denied" ? (
              <span className="mic-state bad">
                <MicOff size={14} aria-hidden /> Microphone is blocked. Allow it from the icon in the address bar.
              </span>
            ) : mic === "prompt" ? (
              <button type="button" className="btn mic-allow" onClick={allowMic} disabled={asking}>
                <Mic size={15} aria-hidden /> {asking ? "Waiting for your answer…" : "Allow microphone"}
              </button>
            ) : null}
          </fieldset>

          <label className="field">
            <span>Access code</span>
            <input value={accessCode} onChange={(e) => setAccessCode(e.target.value)} maxLength={100} type="password" autoComplete="off" placeholder="If the server asks for one" />
          </label>

          <label className="notice-check">
            <input type="checkbox" checked={noticed} onChange={(e) => setNoticed(e.target.checked)} />
            <span>
              <b>Everyone in this meeting knows it is being transcribed.</b>
              <small>
                <ShieldCheck size={13} aria-hidden /> Audio goes to Meta for live transcription and is never stored.
              </small>
            </span>
          </label>
          <button type="button" className="btn copy-notice" onClick={copyNotice}>
            {copied ? <CircleCheckBig size={14} aria-hidden /> : <Copy size={14} aria-hidden />}
            {copied ? "Copied — paste it in the meeting chat" : "Copy notice for the meeting chat"}
          </button>

          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}

          <button type="submit" className="btn btn-primary btn-lg" disabled={!noticed || busy || !micReady}>
            <AudioLines size={18} aria-hidden />
            {busy ? "Starting…" : "Start recording"}
          </button>
        </form>
      </div>
      <p className="setup-foot">Built with Meta Muse Voice Transcribe · Muse Spark · Muse Image</p>
    </main>
  );
}
