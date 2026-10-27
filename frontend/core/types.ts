// Shapes of the backend protocol (see backend/README.md)

export type Column = "goals" | "decisions" | "actions" | "updates" | "questions";
export type UpdateStatus = "done" | "in_progress" | "blocked";
export type ImageStatus = "none" | "pending" | "ready" | "failed" | "skipped";
export type ServerState = "created" | "recording" | "paused" | "stopping" | "done";

export interface Card {
  id: string;
  column: Column;
  title: string;
  detail: string;
  owner: string | null;
  due: string | null;
  status: UpdateStatus | null;
  done: boolean;
  resolved: boolean;
  answer: string | null;
  t: number;
  updated_t: number | null;
  image_url: string | null;
  image_status: ImageStatus;
  source: "ai" | "user";
  user_locked: boolean;
}

export interface Line {
  id: number;
  turn: string;
  speaker: string;
  text: string;
  t: number;
}

export interface PartialLine {
  turn: string;
  speaker: string | null;
  text: string;
}

export interface Report {
  title: string;
  date: string;
  duration_s: number;
  summary: string;
  takeaways?: string[];             // newer backend
  cover_image_url: string | null;
  speakers: Record<string, string>;
  goals: Card[];
  updates: Card[];
  decisions: Card[];
  actions: Card[];
  open_questions: Card[];
  answered_questions?: Card[];      // newer backend
}

export interface Snapshot {
  session_id: string;
  state: ServerState;
  audio_bytes_received: number;
  meeting_t: number;
  board: { title: string | null; cards: Card[] };
  transcript: Line[];
  partials: PartialLine[];
  speakers: Record<string, string>;
  voice: string;
  report: Report | null;
  limits: { images_left: number; spark_calls_left: number };
}

export type StopStep = "transcript" | "cards" | "images" | "report";

export type ServerMsg =
  | ({ type: "hello" } & Snapshot)
  | { type: "ping"; audio_bytes_received: number; meeting_t: number; backlog_s: number }
  | { type: "state"; state: ServerState; step?: StopStep }
  | { type: "voice_status"; voice: string; gen?: number }
  | ({ type: "transcript_partial" } & PartialLine)
  | { type: "transcript_partial_clear"; turn: string }
  | ({ type: "transcript_final" } & Line)
  | { type: "cards_added"; cards: Card[] }
  | { type: "cards_updated"; cards: Card[] }
  | { type: "card_deleted"; id: string }
  | { type: "board_title"; title: string }
  | { type: "speakers"; speakers: Record<string, string> }
  | { type: "spark_status"; state: "thinking" | "idle" | "error"; seconds?: number }
  | { type: "notice"; message: string }
  | { type: "report_ready"; report: Report }
  | { type: "error"; code?: string; message: string };

export type ClientMsg =
  | { type: "start" | "pause" | "resume" | "stop" }
  | { type: "rename_speaker"; label: string; name: string }
  | { type: "card_edit"; id: string; fields: Partial<Pick<Card, "title" | "detail" | "owner" | "due" | "status" | "done" | "resolved">> }
  | { type: "card_delete"; id: string };

export type AudioSource = "mic" | "mic_tab";

export const COLUMNS: Column[] = ["goals", "decisions", "actions", "updates", "questions"];

export const COLUMN_META: Record<Column, { label: string; single: string; color: string; soft: string }> = {
  goals: { label: "Goals", single: "Goal", color: "#7c3aed", soft: "#f3effe" },
  decisions: { label: "Decisions", single: "Decision", color: "#16a34a", soft: "#ecf8f0" },
  actions: { label: "Actions", single: "Action item", color: "#2563eb", soft: "#eef3fe" },
  updates: { label: "Updates", single: "Update", color: "#0e7490", soft: "#e9f6f8" },
  questions: { label: "Questions", single: "Open question", color: "#d97706", soft: "#fdf5e7" },
};

export const STATUS_META: Record<UpdateStatus, { label: string; color: string; soft: string }> = {
  done: { label: "Done", color: "#15803d", soft: "#e7f6ec" },
  in_progress: { label: "In progress", color: "#1d4ed8", soft: "#e8effd" },
  blocked: { label: "Blocked", color: "#dc2626", soft: "#fdecec" },
};
