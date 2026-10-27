// Connection to the backend: session create, WebSocket with heartbeat + auto-reconnect,
// audio frames with byte offsets, and resend of the last 60 s after a reconnect.
import type { ClientMsg, ServerMsg, ServerState } from "./types";

export const BACKEND_URL = (process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000").replace(/\/+$/, "");
const BYTES_PER_SEC = 24000 * 2;
const KEEP_BYTES = 60 * BYTES_PER_SEC;     // resend buffer: last 60 s
const DEAD_AFTER_MS = 5000;                 // no server message for 5 s = dead socket
const CONNECT_TIMEOUT_MS = 6000;            // socket open + auth + hello
const BACKOFF_MS = [300, 1000, 2000, 3000];
const STORE_KEY = "minutecanvas.session";

export type ConnStatus = "connecting" | "connected" | "reconnecting" | "offline" | "closed";

export interface StoredSession {
  sid: string;
  token: string;
  createdAt: number;
}

export class ApiError extends Error {}

export async function createSession(opts: { title?: string; keywords: string[]; access_code?: string }): Promise<StoredSession> {
  let res: Response;
  try {
    res = await fetch(`${BACKEND_URL}/api/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: opts.title || null,
        keywords: opts.keywords,
        access_code: opts.access_code || null,
      }),
    });
  } catch {
    throw new ApiError("Can't reach the MinuteCanvas server. Check your internet connection.");
  }
  if (!res.ok) {
    let detail = "";
    try {
      detail = (await res.json()).detail;
    } catch {
      /* ignore */
    }
    if (res.status === 401) throw new ApiError("Wrong access code.");
    throw new ApiError(typeof detail === "string" && detail ? detail : `Server error (${res.status}).`);
  }
  const j = await res.json();
  const s: StoredSession = { sid: j.session_id, token: j.token, createdAt: Date.now() };
  saveStored(s);
  return s;
}

export function loadStored(): StoredSession | null {
  try {
    const raw = sessionStorage.getItem(STORE_KEY);
    return raw ? (JSON.parse(raw) as StoredSession) : null;
  } catch {
    return null;
  }
}
function saveStored(s: StoredSession) {
  try {
    sessionStorage.setItem(STORE_KEY, JSON.stringify(s));
  } catch {
    /* private mode: refresh-rejoin just won't work */
  }
}
export function clearStored() {
  try {
    sessionStorage.removeItem(STORE_KEY);
  } catch {
    /* ignore */
  }
}

type Desired = "recording" | "paused" | "stopped";

export class MeetingClient {
  readonly sid: string;
  readonly token: string;
  onMessage: (m: ServerMsg) => void = () => {};
  onConn: (s: ConnStatus) => void = () => {};
  onFatal: (message: string) => void = () => {};

  private ws: WebSocket | null = null;
  private authed = false;
  private lastMsgAt = 0;
  private connectStartedAt = 0;
  private attempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private watchdog: ReturnType<typeof setInterval> | null = null;
  private closed = false;
  private queue: ClientMsg[] = [];

  // audio
  private started = false;       // server has an audio clock for us
  private desired: Desired = "recording";
  private offset = 0;            // next byte offset to send
  private chunks: { end: number; frame: Uint8Array }[] = [];
  private serverMeetingT = 0;
  private serverState: ServerState = "created";
  framesSent = 0;
  resentFrames = 0;

  constructor(s: StoredSession) {
    this.sid = s.sid;
    this.token = s.token;
  }

  // ---------------- lifecycle ----------------
  connect() {
    this.closed = false;
    window.addEventListener("online", this.handleOnline);
    window.addEventListener("offline", this.handleOffline);
    this.watchdog = setInterval(this.tick, 1000);
    this.open();
  }

  /** Close for good (meeting finished or user left). */
  close() {
    this.closed = true;
    window.removeEventListener("online", this.handleOnline);
    window.removeEventListener("offline", this.handleOffline);
    if (this.watchdog) clearInterval(this.watchdog);
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.dropSocket(false);
    this.onConn("closed");
  }

  private open() {
    if (this.closed) return;
    const url = `${BACKEND_URL.replace(/^http/, "ws")}/ws/${encodeURIComponent(this.sid)}`;
    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    this.authed = false;
    this.connectStartedAt = Date.now();
    this.onConn(this.attempt === 0 && !this.started ? "connecting" : "reconnecting");
    ws.onopen = () => ws.send(JSON.stringify({ type: "auth", token: this.token }));
    ws.onmessage = (ev) => this.handleMessage(ev);
    ws.onclose = (ev) => {
      if (this.ws !== ws) return;
      this.ws = null;
      this.authed = false;
      if (ev.code === 4401) return this.fatal("This meeting has expired on the server.");
      if (ev.code === 4000) return this.fatal("This meeting was opened in another tab.");
      this.scheduleReconnect();
    };
    ws.onerror = () => {
      /* onclose follows */
    };
  }

  /** Detach and close the current socket without waiting for it (it may be dead). */
  private dropSocket(reconnect: boolean) {
    const ws = this.ws;
    this.ws = null;
    this.authed = false;
    if (ws) {
      ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null;
      try {
        ws.close(1000);
      } catch {
        /* ignore */
      }
    }
    if (reconnect) this.scheduleReconnect();
  }

  private scheduleReconnect() {
    if (this.closed || this.reconnectTimer) return;
    if (!navigator.onLine) {
      this.onConn("offline");
      // "online" usually fires; retry anyway in case it doesn't
      this.reconnectTimer = setTimeout(() => {
        this.reconnectTimer = null;
        this.open();
      }, 3000);
      return;
    }
    this.onConn("reconnecting");
    const delay = BACKOFF_MS[Math.min(this.attempt, BACKOFF_MS.length - 1)];
    this.attempt++;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.open();
    }, delay);
  }

  private fatal(message: string) {
    this.close();
    this.onFatal(message);
  }

  private tick = () => {
    if (this.closed || !this.ws) return;
    const now = Date.now();
    if (this.authed && now - this.lastMsgAt > DEAD_AFTER_MS) this.dropSocket(true);
    else if (!this.authed && now - this.connectStartedAt > CONNECT_TIMEOUT_MS) this.dropSocket(true);
  };

  private handleOnline = () => {
    if (this.closed || this.ws) return;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.attempt = 0;
    this.open();
  };

  private handleOffline = () => {
    if (this.closed) return;
    this.dropSocket(false);
    this.onConn("offline");
    this.scheduleReconnect();
  };

  // ---------------- messages ----------------
  private handleMessage(ev: MessageEvent) {
    if (typeof ev.data !== "string") return;
    this.lastMsgAt = Date.now();
    let m: ServerMsg;
    try {
      m = JSON.parse(ev.data);
    } catch {
      return;
    }
    switch (m.type) {
      case "hello":
        this.authed = true;
        this.attempt = 0;
        this.serverState = m.state;
        this.serverMeetingT = m.meeting_t;
        this.onConn("connected");
        this.afterHello(m.state, m.audio_bytes_received);
        break;
      case "ping":
        this.serverMeetingT = m.meeting_t;
        break;
      case "state":
        this.serverState = m.state;
        if ((m.state === "recording" || m.state === "paused") && !this.started) {
          this.started = true;
          this.offset = 0;
        }
        break;
      case "error":
        if (m.code === "bad_token") {
          this.fatal("This meeting has expired on the server.");
          return;
        }
        break;
    }
    this.onMessage(m);
    // meeting finished (live, or already finished when rejoining): nothing more to stream
    if ((m.type === "state" || m.type === "hello") && m.state === "done") this.close();
  }

  private afterHello(state: ServerState, received: number) {
    if (state === "created") {
      if (this.desired !== "stopped") this.sendNow({ type: "start" });
    } else if (state === "recording" || state === "paused") {
      if (!this.started) {
        // rejoin after a page refresh: continue the server's audio clock
        this.started = true;
        this.offset = received;
        this.chunks = [];
        this.desired = state;
      } else {
        this.resendFrom(received);
      }
      if (this.desired === "stopped") this.sendNow({ type: "stop" });
      else if (this.desired !== state) this.sendNow({ type: this.desired === "paused" ? "pause" : "resume" });
    }
    const q = this.queue;
    this.queue = [];
    q.forEach((msg) => this.send(msg));
  }

  private sendNow(msg: ClientMsg) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
  }

  send(msg: ClientMsg) {
    if (this.authed && this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
    else if (this.queue.length < 50) this.queue.push(msg);
  }

  // ---------------- controls ----------------
  pause() {
    this.desired = "paused";
    this.sendNow({ type: "pause" });
  }
  resume() {
    this.desired = "recording";
    this.sendNow({ type: "resume" });
  }
  stop() {
    this.desired = "stopped";
    this.sendNow({ type: "stop" });
  }
  get capturing() {
    return this.started && this.desired === "recording";
  }
  get state() {
    return this.serverState;
  }
  /** Bytes of audio captured so far (debug / tests). */
  get capturedBytes() {
    return this.offset;
  }

  /** Meeting clock in seconds (local while capturing, so it never jumps). */
  meetingSeconds(): number {
    return this.started ? Math.max(this.offset / BYTES_PER_SEC, this.serverMeetingT) : this.serverMeetingT;
  }

  // ---------------- audio ----------------
  pushPcm(pcm: Int16Array) {
    if (!this.capturing || this.closed) return;
    const len = pcm.byteLength;
    const frame = new Uint8Array(8 + len);
    new DataView(frame.buffer).setBigUint64(0, BigInt(this.offset), true);
    frame.set(new Uint8Array(pcm.buffer, pcm.byteOffset, len), 8);
    this.offset += len;
    this.chunks.push({ end: this.offset, frame });
    while (this.chunks.length && this.chunks[0].end < this.offset - KEEP_BYTES) this.chunks.shift();
    if (this.authed && this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(frame);
      this.framesSent++;
    }
  }

  /** After a reconnect: send only what the server didn't get (it trims any overlap). */
  private resendFrom(received: number) {
    if (!this.ws) return;
    for (const c of this.chunks) {
      if (c.end > received) {
        this.ws.send(c.frame);
        this.resentFrames++;
      }
    }
  }

  // ---------------- images / report (Bearer token) ----------------
  async fetchBlob(path: string): Promise<Blob> {
    const res = await fetch(`${BACKEND_URL}${path}`, { headers: { Authorization: `Bearer ${this.token}` } });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.blob();
  }
}
