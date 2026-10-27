// Meeting state, driven by server messages.
import type { Card, Line, PartialLine, Report, ServerMsg, ServerState, StopStep } from "./types";
import type { ConnStatus } from "./client";

export type Flash = { kind: "updated" | "resolved" | "done"; at: number };

/** Something Muse Spark did on its own to an existing card (shown as a callout). */
export interface Activity {
  id: number;
  at: number;
  kind: Flash["kind"];
  cardId: string;
  card: Card;
  before: Card;
}

export interface SparkRound {
  state: "idle" | "thinking" | "error";
  seconds?: number;
  lastAt?: number;      // when the last round finished
  startedAt?: number;   // when the current round started
  linesRead?: number;   // new transcript lines in the current/last round
  added: number;        // cards added in the current/last round
  updated: number;      // cards updated by AI in the current/last round
  cursor: number;       // transcript length when the last round started
  rounds: number;
}

export interface MeetingState {
  serverState: ServerState;
  stopStep: StopStep | null;
  conn: ConnStatus;
  voice: string;
  spark: SparkRound;
  activity: Activity[];
  title: string | null;
  cards: Record<string, Card>;
  order: string[];
  transcript: Line[];
  partials: Record<string, PartialLine>;
  speakers: Record<string, string>;
  report: Report | null;
  flash: Record<string, Flash>;
  toasts: { id: number; text: string; kind: "info" | "error" }[];
  imagesLeft: number | null;
  helloCount: number;
}

export const initialState: MeetingState = {
  serverState: "created",
  stopStep: null,
  conn: "connecting",
  voice: "idle",
  spark: { state: "idle", added: 0, updated: 0, cursor: 0, rounds: 0 },
  activity: [],
  title: null,
  cards: {},
  order: [],
  transcript: [],
  partials: {},
  speakers: {},
  report: null,
  flash: {},
  toasts: [],
  imagesLeft: null,
  helloCount: 0,
};

export type Action =
  | { type: "server"; msg: ServerMsg }
  | { type: "conn"; conn: ConnStatus }
  | { type: "toast"; text: string; kind?: "info" | "error" }
  | { type: "dismissToast"; id: number }
  | { type: "clearFlash"; ids: string[] }
  | { type: "dismissActivity"; id: number }
  | { type: "reset" };

let toastId = 1;

function addToast(s: MeetingState, text: string, kind: "info" | "error" = "info"): MeetingState {
  if (s.toasts.some((t) => t.text === text)) return s;
  return { ...s, toasts: [...s.toasts.slice(-3), { id: toastId++, text, kind }] };
}

/** Only AI updates change updated_t; image arrivals and the user's own edits don't flash. */
function flashFor(prev: Card | undefined, next: Card): Flash["kind"] | null {
  if (!prev || next.updated_t == null || next.updated_t === prev.updated_t) return null;
  if (next.column === "questions" && next.resolved && !prev.resolved) return "resolved";
  if (next.column === "actions" && next.done && !prev.done) return "done";
  return "updated";
}

let activityId = 1;

export function reducer(s: MeetingState, a: Action): MeetingState {
  switch (a.type) {
    case "reset":
      return initialState;
    case "conn":
      return { ...s, conn: a.conn };
    case "toast":
      return addToast(s, a.text, a.kind);
    case "dismissToast":
      return { ...s, toasts: s.toasts.filter((t) => t.id !== a.id) };
    case "dismissActivity":
      return { ...s, activity: s.activity.filter((x) => x.id !== a.id) };
    case "clearFlash": {
      const flash = { ...s.flash };
      a.ids.forEach((id) => delete flash[id]);
      return { ...s, flash };
    }
    case "server":
      return onServer(s, a.msg);
  }
}

function onServer(s: MeetingState, m: ServerMsg): MeetingState {
  switch (m.type) {
    case "hello": {
      const cards: Record<string, Card> = {};
      m.board.cards.forEach((c) => (cards[c.id] = c));
      const partials: Record<string, PartialLine> = {};
      m.partials.forEach((p) => (partials[p.turn] = p));
      return {
        ...s,
        serverState: m.state,
        stopStep: m.state === "stopping" ? s.stopStep : null,
        voice: m.voice,
        title: m.board.title,
        cards,
        order: m.board.cards.map((c) => c.id),
        transcript: m.transcript,
        partials,
        speakers: m.speakers,
        report: m.report,
        imagesLeft: m.limits?.images_left ?? null,
        helloCount: s.helloCount + 1,
        spark: s.helloCount === 0 ? { ...s.spark, cursor: m.transcript.length } : s.spark,
      };
    }
    case "state":
      return { ...s, serverState: m.state, stopStep: m.state === "stopping" ? m.step ?? s.stopStep : null };
    case "voice_status": {
      let next = { ...s, voice: m.voice };
      if (m.voice === "failed") next = addToast(next, "Transcription stopped working. Try Stop, then start a new meeting.", "error");
      return next;
    }
    case "transcript_partial":
      return { ...s, partials: { ...s.partials, [m.turn]: { turn: m.turn, speaker: m.speaker, text: m.text } } };
    case "transcript_partial_clear": {
      if (!s.partials[m.turn]) return s;
      const partials = { ...s.partials };
      delete partials[m.turn];
      return { ...s, partials };
    }
    case "transcript_final": {
      if (s.transcript.some((l) => l.id === m.id)) return s;
      const line: Line = { id: m.id, turn: m.turn, speaker: m.speaker, text: m.text, t: m.t };
      return { ...s, transcript: [...s.transcript, line] };
    }
    case "cards_added": {
      const cards = { ...s.cards };
      const order = [...s.order];
      m.cards.forEach((c) => {
        if (!cards[c.id]) order.push(c.id);
        cards[c.id] = c;
      });
      const spark = s.spark.state === "thinking" ? { ...s.spark, added: s.spark.added + m.cards.length } : s.spark;
      return { ...s, cards, order, spark };
    }
    case "cards_updated": {
      const cards = { ...s.cards };
      const flash = { ...s.flash };
      const now = Date.now();
      const acts: Activity[] = [];
      m.cards.forEach((c) => {
        const prev = cards[c.id];
        if (!prev) return; // deleted meanwhile
        const k = flashFor(prev, c);
        if (k) {
          flash[c.id] = { kind: k, at: now };
          acts.push({ id: activityId++, at: now, kind: k, cardId: c.id, card: c, before: prev });
        }
        cards[c.id] = c;
      });
      const spark = acts.length && s.spark.state === "thinking" ? { ...s.spark, updated: s.spark.updated + acts.length } : s.spark;
      const activity = acts.length ? [...s.activity, ...acts].slice(-4) : s.activity;
      return { ...s, cards, flash, spark, activity };
    }
    case "card_deleted": {
      if (!s.cards[m.id]) return s;
      const cards = { ...s.cards };
      delete cards[m.id];
      return { ...s, cards, order: s.order.filter((i) => i !== m.id) };
    }
    case "board_title":
      return { ...s, title: m.title };
    case "speakers":
      return { ...s, speakers: m.speakers };
    case "spark_status": {
      const now = Date.now();
      if (m.state === "thinking") {
        const linesRead = Math.max(0, s.transcript.length - s.spark.cursor);
        return { ...s, spark: { ...s.spark, state: "thinking", startedAt: now, linesRead, added: 0, updated: 0, cursor: s.transcript.length } };
      }
      if (m.state === "idle") return { ...s, spark: { ...s.spark, state: "idle", seconds: m.seconds, lastAt: now, rounds: s.spark.rounds + 1 } };
      // failed round: the server keeps those lines for the next round
      return { ...s, spark: { ...s.spark, state: "error", lastAt: now, cursor: Math.max(0, s.spark.cursor - (s.spark.linesRead ?? 0)) } };
    }
    case "notice":
      return addToast(s, m.message);
    case "report_ready":
      return { ...s, report: m.report };
    case "error":
      return addToast(s, m.message, "error");
    case "ping":
      return s;
  }
  return s;
}

// ---------------- helpers ----------------
export function speakerName(speakers: Record<string, string>, label: string | null): string {
  if (!label) return "…";
  return speakers[label] || `Speaker ${label}`;
}

export function fmtTime(sec: number): string {
  const t = Math.max(0, Math.floor(sec));
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const ss = String(t % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${String(m).padStart(2, "0")}:${ss}`;
}

export function orderedCards(s: MeetingState): Card[] {
  return s.order.map((id) => s.cards[id]).filter(Boolean);
}
