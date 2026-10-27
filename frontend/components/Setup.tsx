"use client";
import { useEffect, useState } from "react";
import { AudioLines, Mic, MonitorSpeaker, ShieldCheck } from "lucide-react";
import type { AudioSource } from "@/core/types";
import { tabAudioSupported } from "@/core/audio";
import { Logo } from "./Logo";

export interface SetupValues {
  title: string;
  keywords: string[];
  accessCode: string;
  source: AudioSource;
}

const LS_KEY = "minutecanvas.setup";

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
    if (!noticed || busy) return;
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
              <small>Next, pick the meeting tab and turn on “Also share tab audio”. Use headphones so the mic doesn’t pick up the speakers.</small>
            )}
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

          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}

          <button type="submit" className="btn btn-primary btn-lg" disabled={!noticed || busy}>
            <AudioLines size={18} aria-hidden />
            {busy ? "Starting…" : "Start recording"}
          </button>
        </form>
      </div>
      <p className="setup-foot">Built with Meta Muse Voice Transcribe · Muse Spark · Muse Image</p>
    </main>
  );
}
