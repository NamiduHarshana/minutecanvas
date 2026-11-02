"use client";
// New meeting: title, words, audio source, microphone permission (with a way back from "Never allow"), consent.
import { useCallback, useEffect, useState } from "react";
import { AudioLines, Check, Copy, KeyRound, Mic, MicOff, MonitorSpeaker } from "lucide-react";
import type { AudioSource } from "@/core/types";
import { micPermission, requestMic, tabAudioSupported, type MicPermission } from "@/core/audio";

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
  "What we say is transcribed live by Meta's AI. MinuteCanvas does not store the audio; it saves a text replay of the notes (transcript, board and flow) that I can share, deleted after 30 days. Tell me if you'd rather I didn't.";

type MicState = MicPermission | "missing";

async function micName(): Promise<string> {
  try {
    const list = await navigator.mediaDevices.enumerateDevices();
    const ins = list.filter((d) => d.kind === "audioinput");
    const d = ins.find((x) => x.deviceId === "default") ?? ins[0];
    return (d?.label || "").replace(/^Default - /, "");
  } catch {
    return "";
  }
}

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
  const [showCode, setShowCode] = useState(false);
  const [source, setSource] = useState<AudioSource>("mic");
  const [noticed, setNoticed] = useState(false);
  const [tabOk, setTabOk] = useState(true);
  const [mic, setMic] = useState<MicState>("unknown");
  const [device, setDevice] = useState("");
  const [asking, setAsking] = useState(false);
  const [stillBlocked, setStillBlocked] = useState(false);
  const [copied, setCopied] = useState(false);

  const check = useCallback(async () => {
    const p = await micPermission();
    setMic((m) => (m === "missing" && p !== "granted" && p !== "denied" ? m : p));
    if (p === "granted") setDevice(await micName());
    return p;
  }, []);

  // know the permission up front, and notice when it is switched back on in the address bar (no reload needed)
  useEffect(() => {
    let stop = () => {};
    check();
    navigator.permissions
      ?.query({ name: "microphone" as PermissionName })
      .then((st) => {
        const on = () => {
          setStillBlocked(false);
          check();
        };
        st.addEventListener("change", on);
        stop = () => st.removeEventListener("change", on);
      })
      .catch(() => {});
    // browsers without permission change events: look again when the page gets focus back
    const onFocus = () => check();
    window.addEventListener("focus", onFocus);
    return () => {
      stop();
      window.removeEventListener("focus", onFocus);
    };
  }, [check]);

  async function allowMic() {
    setAsking(true);
    setStillBlocked(false);
    const r = await requestMic();
    if (r === "granted") {
      setMic("granted");
      setDevice(await micName());
    } else if (r === "denied") setMic("denied");
    else setMic((await micPermission()) === "granted" ? "granted" : "missing");
    setAsking(false);
  }

  async function tryAgain() {
    const p = await check();
    if (p === "denied") setStillBlocked(true);
    else if (p !== "granted") allowMic();
  }

  async function copyNotice() {
    try {
      await navigator.clipboard.writeText(NOTICE);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {
      window.prompt("Copy this notice:", NOTICE);
    }
  }

  useEffect(() => {
    setTabOk(tabAudioSupported());
    try {
      const saved = JSON.parse(localStorage.getItem(LS_KEY) || "{}");
      if (typeof saved.keywords === "string") setKeywords(saved.keywords);
      if (typeof saved.accessCode === "string" && saved.accessCode) {
        setAccessCode(saved.accessCode);
        setShowCode(true);
      }
      if (saved.source === "mic" || saved.source === "mic_tab") setSource(saved.source);
    } catch {
      /* ignore */
    }
  }, []);

  // Start can't stop for a mic prompt: Chrome would already be showing the tab picker / the shared tab.
  // "unknown" = this browser can't tell before asking (Start asks).
  const micReady = mic === "granted" || mic === "unknown";
  const ready = noticed && micReady && !busy;

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready) return;
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
    <div className="wrap narrow-760">
      <div>
        <h1 className="h-page">New meeting</h1>
        <p className="sub">Three steps, then MinuteCanvas listens and draws.</p>
      </div>

      {canRejoin && (
        <div className="rejoin">
          <span>A meeting from this tab is still running on the server.</span>
          <button type="button" className="mc-btn mc-btn-primary mc-btn-sm" onClick={onRejoin} disabled={busy}>
            Rejoin meeting
          </button>
        </div>
      )}

      <form onSubmit={submit} className="mc-card setup-card">
        <div className="field">
          <label htmlFor="mt">
            Meeting title <span className="opt">(optional)</span>
          </label>
          <input id="mt" className="input" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={80} placeholder="Muse Spark names it if you leave this empty" />
        </div>
        <div className="field">
          <label htmlFor="mw">Names and project words</label>
          <input id="mw" className="input" value={keywords} onChange={(e) => setKeywords(e.target.value)} maxLength={600} placeholder="Kasun, Amali, Stripe, MinuteCanvas" />
          <span className="hint">Comma-separated. Helps Muse Voice spell them right.</span>
        </div>

        <div className="field" role="group" aria-labelledby="audio-l">
          <span className="flabel" id="audio-l">
            Audio
          </span>
          <div className="choices">
            <button type="button" className={`choice ${source === "mic" ? "on" : ""}`} onClick={() => setSource("mic")} aria-pressed={source === "mic"}>
              <Mic size={22} strokeWidth={1.75} aria-hidden />
              <span>
                <b>Mic</b>
                <span>Everyone is in the room</span>
              </span>
            </button>
            <button
              type="button"
              className={`choice ${source === "mic_tab" ? "on" : ""}`}
              onClick={() => tabOk && setSource("mic_tab")}
              aria-pressed={source === "mic_tab"}
              disabled={!tabOk}
            >
              <MonitorSpeaker size={22} strokeWidth={1.75} aria-hidden />
              <span>
                <b>Mic + meeting tab</b>
                <span>{tabOk ? "Google Meet or Zoom in Chrome" : "Needs Chrome on a computer"}</span>
              </span>
            </button>
          </div>
          <span className="hint">
            {source === "mic_tab"
              ? "Next, Chrome asks which tab to share. Pick the meeting tab and turn on “Also share tab audio”. Use headphones so your mic doesn’t pick up the speakers."
              : "Put the laptop where it can hear everyone."}
          </span>
        </div>

        {(mic === "prompt" || mic === "unknown") && (
          <div className="mc-row">
            <button type="button" className="mc-btn mc-btn-outline" onClick={allowMic} disabled={asking}>
              <Mic strokeWidth={1.75} aria-hidden />
              {asking ? "Waiting for your answer…" : "Allow microphone"}
            </button>
            <span className="hint">Chrome asks once. Choose “Allow while visiting the site”.</span>
          </div>
        )}

        {mic === "denied" && (
          <div className="alert" role="alert">
            <MicOff className="ic" size={22} strokeWidth={1.75} aria-hidden />
            <div style={{ flex: 1 }}>
              <b>The microphone is blocked for this site.</b>
              <div style={{ color: "var(--ink-2)", fontSize: 13.5 }}>MinuteCanvas can’t ask again — Chrome only lets you turn it back on:</div>
              <ol>
                <li>
                  Click the <b>site icon</b> at the left of the address bar.
                </li>
                <li>
                  Switch <b>Microphone</b> to <b>Allow</b>.
                </li>
                <li>Come back here. This page notices by itself — no reload needed.</li>
              </ol>
              <div className="mc-row" style={{ marginTop: 12 }}>
                <button type="button" className="mc-btn mc-btn-secondary mc-btn-sm" onClick={tryAgain}>
                  Try again
                </button>
                <a className="link" href="https://support.google.com/chrome/answer/2693767" target="_blank" rel="noopener">
                  Chrome help ↗
                </a>
                {stillBlocked && <span className="hint">Still blocked. Switch it to Allow in the address bar first.</span>}
              </div>
            </div>
          </div>
        )}

        {mic === "missing" && (
          <div className="alert" role="alert">
            <MicOff className="ic" size={22} strokeWidth={1.75} aria-hidden />
            <div style={{ flex: 1 }}>
              <b>No microphone found.</b>
              <div style={{ color: "var(--ink-2)", fontSize: 13.5 }}>Plug in or turn on a microphone, then try again.</div>
              <div className="mc-row" style={{ marginTop: 12 }}>
                <button type="button" className="mc-btn mc-btn-secondary mc-btn-sm" onClick={allowMic}>
                  Try again
                </button>
              </div>
            </div>
          </div>
        )}

        {mic === "granted" && (
          <div className="mc-row">
            <span className="mc-tag mc-tag-ok">
              <Check strokeWidth={2} aria-hidden />
              Microphone ready
            </span>
            {device && <span className="hint">{device}</span>}
          </div>
        )}

        {showCode ? (
          <div className="field">
            <label htmlFor="ac">Access code</label>
            <input
              id="ac"
              className="input"
              value={accessCode}
              onChange={(e) => setAccessCode(e.target.value)}
              maxLength={100}
              type="password"
              autoComplete="off"
              placeholder="Only if this server asks for one"
            />
          </div>
        ) : (
          <button type="button" className="link link-btn" onClick={() => setShowCode(true)}>
            <KeyRound size={14} strokeWidth={1.75} aria-hidden /> Have an access code?
          </button>
        )}

        <label className="check">
          <input type="checkbox" checked={noticed} onChange={(e) => setNoticed(e.target.checked)} />
          <span>
            <b>Everyone in this meeting knows it is being transcribed.</b>
            <span className="hint">Audio goes to Meta for live transcription and is never stored. A text replay is saved after Stop for 30 days.</span>
          </span>
        </label>

        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}

        <div className="setup-foot">
          <button type="button" className="mc-btn mc-btn-outline mc-btn-sm" onClick={copyNotice}>
            {copied ? <Check strokeWidth={2} aria-hidden /> : <Copy strokeWidth={1.75} aria-hidden />}
            {copied ? "Copied — paste it in the meeting chat" : "Copy notice for the meeting chat"}
          </button>
          <button
            type="submit"
            className="mc-btn mc-btn-primary mc-btn-lg"
            disabled={!ready}
            title={!noticed ? "Tick the box above first" : !micReady ? "Allow the microphone first" : undefined}
          >
            <AudioLines strokeWidth={1.75} aria-hidden />
            {busy ? "Starting…" : "Start recording"}
          </button>
        </div>
      </form>
    </div>
  );
}
