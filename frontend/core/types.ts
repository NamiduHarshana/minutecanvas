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
  flow_ref?: string | null;        // questions made from a Flow Map gap
  conflict_ref?: string | null;    // states one side of an unsettled Flow Map conflict: not decided
}

// ---------------- Live Flow Map ----------------
export type StepKind = "start" | "step" | "decision" | "end";

/** A transcript line the agent quoted; the backend checked the words are really there. */
export interface Evidence {
  line: number;
  speaker: string;
  t: number;
  quote: string;
}

export interface FlowStep { id: string; label: string; kind: StepKind; t: number; evidence: Evidence[]; updated_t: number | null }
export interface FlowLink { id: string; src: string; dst: string; label: string; type: "normal" | "fail"; t: number; evidence: Evidence[] }
export interface FlowBug { id: string; step: string; text: string; t: number; evidence: Evidence[]; fixed: boolean; fix: Evidence | null }
export interface FlowGap {
  id: string; step: string; branch: string; question: string; t: number; evidence: Evidence[];
  resolved: boolean; answer: string | null; resolved_by: Evidence | null; dismissed: boolean; card_id: string | null;
}
export interface FlowContradiction {
  id: string; step: string | null; note: string; a: Evidence; b: Evidence; t: number;
  resolved: boolean; answer: string | null; resolved_by: Evidence | null;
}
export interface FlowData {
  steps: FlowStep[];
  links: FlowLink[];
  bugs: FlowBug[];
  gaps: FlowGap[];
  contradictions: FlowContradiction[];
}
export type FlowEventKind =
  | "step_added" | "step_updated" | "link_added" | "link_updated" | "bug" | "bug_fixed"
  | "gap" | "gap_moved" | "gap_resolved" | "contradiction" | "contradiction_resolved";
export interface FlowEvent { kind: FlowEventKind; id: string; step?: string | null; text: string }
export interface FlowStats { accepted: number; rejected: number; rounds: number }
export type FlowMode = "live" | "gap_review" | "gap_check" | "final";

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
  flow?: FlowData;                  // Flow Map backend
}

export interface Snapshot {
  session_id: string;
  state: ServerState;
  audio_bytes_received: number;
  meeting_t: number;
  board: { title: string | null; cards: Card[] };
  flow?: FlowData;
  flow_stats?: FlowStats;
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
  | { type: "flow"; flow: FlowData; events: FlowEvent[]; stats: FlowStats }
  | { type: "flow_status"; state: "thinking" | "idle" | "error"; mode: FlowMode; seconds?: number; accepted?: number; rejected?: number; gaps?: number }
  | { type: "notice"; message: string }
  | { type: "report_ready"; report: Report }
  | { type: "error"; code?: string; message: string };

export type ClientMsg =
  | { type: "start" | "pause" | "resume" | "stop" | "gap_review" }
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
