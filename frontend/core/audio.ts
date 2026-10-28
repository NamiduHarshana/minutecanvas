// Microphone (+ optional Chrome tab audio) -> AudioWorklet -> PCM16 mono 24 kHz, 100 ms chunks.
import type { AudioSource } from "./types";

export const SAMPLE_RATE = 24000;

export class CaptureError extends Error {}

interface FocusController {
  setFocusBehavior?: (b: "focus-captured-surface" | "no-focus-change") => void;
}

export type MicPermission = "granted" | "prompt" | "denied" | "unknown";

/** Current microphone permission, without prompting. "unknown" when the browser can't tell. */
export async function micPermission(): Promise<MicPermission> {
  try {
    const st = await navigator.permissions.query({ name: "microphone" as PermissionName });
    return st.state as MicPermission;
  } catch {
    return "unknown";
  }
}

/** Shows the browser's microphone prompt now (setup screen), so Start doesn't stop to ask later. */
export async function requestMic(): Promise<MicPermission> {
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    s.getTracks().forEach((t) => t.stop());
    return "granted";
  } catch (e) {
    return (e as DOMException)?.name === "NotAllowedError" ? "denied" : "unknown";
  }
}

export function tabAudioSupported(): boolean {
  if (typeof navigator === "undefined") return false;
  if (!navigator.mediaDevices || typeof navigator.mediaDevices.getDisplayMedia !== "function") return false;
  const brands = (navigator as Navigator & { userAgentData?: { brands?: { brand: string }[] } }).userAgentData?.brands;
  if (brands) return brands.some((b) => /Chromium|Google Chrome|Microsoft Edge/.test(b.brand));
  return /Chrome\//.test(navigator.userAgent) && !/Firefox|FxiOS/.test(navigator.userAgent);
}

export class AudioCapture {
  private ctx: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private streams: MediaStream[] = [];
  level = 0;
  tabActive = false;
  onChunk: ((pcm: Int16Array) => void) | null = null;
  onTabEnded: (() => void) | null = null;

  /** Must be called from a click handler (tab sharing needs a user gesture). */
  async open(source: AudioSource): Promise<void> {
    if (!window.isSecureContext) throw new CaptureError("The microphone only works over HTTPS (or localhost).");
    if (!navigator.mediaDevices?.getUserMedia) throw new CaptureError("This browser can't record audio.");

    // Tab first: getDisplayMedia needs the click's user activation, which a mic prompt could use up.
    let tab: MediaStream | null = null;
    if (source === "mic_tab") {
      // Chrome normally jumps to the shared tab. CaptureController keeps MinuteCanvas in front
      // (Chrome 109+; other browsers ignore it), so nobody has to switch back to see the board.
      const Ctl = (window as unknown as { CaptureController?: new () => FocusController }).CaptureController;
      const controller = Ctl ? new Ctl() : null;
      const stayHere = () => {
        try {
          controller?.setFocusBehavior?.("no-focus-change");
        } catch {
          /* too early / already set / not a tab: fine */
        }
      };
      stayHere();
      try {
        tab = await navigator.mediaDevices.getDisplayMedia({
          video: true,
          audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
          // Chrome-only hints (ignored elsewhere)
          ...({ preferCurrentTab: false, selfBrowserSurface: "exclude", systemAudio: "include", surfaceSwitching: "include" } as object),
          ...(controller ? { controller } : {}),
        } as DisplayMediaStreamOptions);
        stayHere(); // older Chrome only accepts it right after the share starts
      } catch {
        throw new CaptureError("Tab sharing was cancelled.");
      }
      if (tab.getAudioTracks().length === 0) {
        tab.getTracks().forEach((t) => t.stop());
        throw new CaptureError("No tab audio. Pick the Meet/Zoom tab and turn on “Also share tab audio”.");
      }
      this.streams.push(tab);
    }

    let mic: MediaStream;
    try {
      mic = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (e) {
      this.close();
      const name = (e as DOMException)?.name;
      throw new CaptureError(
        name === "NotAllowedError"
          ? "Microphone is blocked. Allow it from the icon in the address bar, then try again."
          : name === "NotFoundError"
            ? "No microphone found."
            : "Couldn't open the microphone.",
      );
    }
    this.streams.push(mic);

    const ctx = new AudioContext({ latencyHint: "interactive" });
    this.ctx = ctx;
    await ctx.audioWorklet.addModule("/pcm-worklet.js");
    const node = new AudioWorkletNode(ctx, "pcm-capture", {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      channelCount: 1,
      channelCountMode: "explicit",
      channelInterpretation: "speakers",
      processorOptions: { targetRate: SAMPLE_RATE },
    });
    this.node = node;
    node.port.onmessage = (ev: MessageEvent<{ pcm: Int16Array; level: number }>) => {
      this.level = ev.data.level;
      this.onChunk?.(ev.data.pcm);
    };

    // mic + tab are summed into the worklet input
    ctx.createMediaStreamSource(mic).connect(node);
    if (tab) {
      ctx.createMediaStreamSource(new MediaStream(tab.getAudioTracks())).connect(node);
      this.tabActive = true;
      tab.getAudioTracks()[0].addEventListener("ended", () => {
        this.tabActive = false;
        this.onTabEnded?.();
      });
    }
    // keep the graph pulled: worklet -> silent gain -> speakers
    const mute = ctx.createGain();
    mute.gain.value = 0;
    node.connect(mute).connect(ctx.destination);
    await ctx.resume();
  }

  close() {
    this.streams.forEach((s) => s.getTracks().forEach((t) => t.stop()));
    this.streams = [];
    if (this.node) this.node.port.onmessage = null;
    this.node = null;
    this.ctx?.close().catch(() => {});
    this.ctx = null;
    this.tabActive = false;
    this.level = 0;
  }
}
