// The signed-in app's data: the visitor (Google account or "not signed in"), their meetings, and the numbers the
// Dashboard, Meetings, Findings and Usage pages show. Signed in: the account's list. Signed out: nothing — the records
// pages ask to sign in (someone else using this browser must not see the meetings). Meetings made here while signed
// out are offered to the account after sign-in (ClaimPrompt), never added silently. Numbers are real or absent.
"use client";
import { useCallback, useEffect, useState } from "react";
import { AUTH_EVENT, AccountError, currentAuth, myMeetings, type Finding, type FindingKind, type MeetingRow, type User } from "./account";

export interface Library {
  loading: boolean;            // true on the server render and until the first check in the browser
  user: User | null;
  rows: MeetingRow[];          // newest first; always empty when signed out
  error: string | null;
  reload: () => void;
  drop: (rid: string) => void; // after a delete
}

function newestFirst(rows: MeetingRow[]): MeetingRow[] {
  return [...rows].sort((a, b) => stamp(b) - stamp(a));
}
export function stamp(r: MeetingRow): number {
  const d = Date.parse(r.date);
  return isNaN(d) ? (r.created ?? 0) * 1000 : d;
}

export function useLibrary(): Library {
  const [loading, setLoading] = useState(true);
  const [user, setUser] = useState<User | null>(null);
  const [rows, setRows] = useState<MeetingRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [n, setN] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setError(null);
      const a = currentAuth();
      if (a) {
        try {
          const j = await myMeetings();
          if (cancelled) return;
          setUser(currentAuth()?.user ?? a.user);
          setRows(newestFirst(j.meetings));
          setLoading(false);
          return;
        } catch (e) {
          if (cancelled) return;
          if (!(e instanceof AccountError && e.status === 401)) {
            // server unreachable: still signed in, the list just couldn't load
            setError(e instanceof AccountError ? e.message : "Couldn't load your meetings.");
            setUser(a.user);
            setRows([]);
            setLoading(false);
            return;
          }
        }
      }
      if (cancelled) return;
      setUser(null);
      setRows([]);
      setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [n]);

  useEffect(() => {
    const again = () => setN((x) => x + 1);
    window.addEventListener(AUTH_EVENT, again);
    window.addEventListener("storage", again);
    return () => {
      window.removeEventListener(AUTH_EVENT, again);
      window.removeEventListener("storage", again);
    };
  }, []);

  const reload = useCallback(() => setN((x) => x + 1), []);
  const drop = useCallback((rid: string) => setRows((r) => r.filter((x) => x.rid !== rid)), []);
  return { loading, user, rows, error, reload, drop };
}

/** The signed-in user only (sidebar footer): no meeting list, no server call. */
export function useUser(): User | null | undefined {
  const [user, setUser] = useState<User | null | undefined>(undefined);
  useEffect(() => {
    const sync = () => setUser(currentAuth()?.user ?? null);
    sync();
    window.addEventListener(AUTH_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(AUTH_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  return user;
}

// ---------------- numbers ----------------
export type Range = "7d" | "30d" | "90d";
export const RANGES: Range[] = ["7d", "30d", "90d"];
export const rangeDays = (r: Range) => (r === "7d" ? 7 : r === "30d" ? 30 : 90);

const DAY = 86400000;
function startOfDay(ms: number): number {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

/** Meetings from the first day of the range (today counts as day 1). */
export function inRange(rows: MeetingRow[], r: Range, now = Date.now()): MeetingRow[] {
  const from = startOfDay(now) - (rangeDays(r) - 1) * DAY;
  return rows.filter((x) => stamp(x) >= from);
}

export interface Bar {
  value: number;
  label: string;       // axis label ("" = no label under this bar)
  tip: string;         // tooltip date
}

/** Per-day bars (7d, 30d) or per-week bars (90d: 13 weeks) of value(row), oldest first. */
export function bars(rows: MeetingRow[], r: Range, value: (x: MeetingRow) => number, now = Date.now()): Bar[] {
  const today = startOfDay(now);
  const weekly = r === "90d";
  const step = weekly ? 7 * DAY : DAY;
  const count = weekly ? 13 : rangeDays(r);
  const first = weekly ? today - 90 * DAY + DAY : today - (count - 1) * DAY;
  const out: Bar[] = [];
  // axis: every day for 7d (27 28 … 31 1 Nov 2); every 5th day for 30d and every 3rd week for 90d, the month on the
  // first label and wherever it changes
  const every = r === "7d" ? 1 : r === "30d" ? 5 : 3;
  let lastMonth = -1;
  for (let k = 0; k < count; k++) {
    const from = first + k * step;
    const to = k === count - 1 ? today + DAY : from + step;
    const v = rows.filter((x) => stamp(x) >= from && stamp(x) < to).reduce((s, x) => s + value(x), 0);
    const d = new Date(from);
    const day = d.getDate();
    let label = "";
    if ((count - 1 - k) % every === 0) {
      const newMonth = d.getMonth() !== lastMonth && (r !== "7d" || day === 1);
      label = newMonth ? `${day} ${d.toLocaleDateString(undefined, { month: "short" })}` : String(day);
      lastMonth = d.getMonth();
    }
    out.push({ value: v, label, tip: weekly ? `Week of ${fmtDay(from)}` : fmtDay(from) });
  }
  return out;
}

export const sum = (rows: MeetingRow[], f: (x: MeetingRow) => number) => rows.reduce((s, x) => s + f(x), 0);

/** Gaps found in a meeting: still open + answered later (meetings saved before v6.19: open ones only). */
export function gapsFound(r: MeetingRow): number {
  if (r.findings) return r.findings.filter((f) => f.kind === "gap" || f.kind === "answered").length;
  return r.gaps || 0;
}
/** Conflicts caught (open or settled). */
export function conflictsFound(r: MeetingRow): number {
  if (r.findings) return r.findings.filter((f) => f.kind === "conflict").length;
  return r.conflicts || 0;
}
export function findingCount(r: MeetingRow): number {
  return r.findings ? r.findings.length : (r.gaps || 0) + (r.conflicts || 0);
}

export interface MeetingFinding extends Finding {
  rid: string;
  meeting: string;
  date: string;
}
/** Every finding of these meetings: newest meeting first, then the latest found first. */
export function allFindings(rows: MeetingRow[]): MeetingFinding[] {
  const out: MeetingFinding[] = [];
  for (const r of rows) {
    const list = [...(r.findings ?? [])].sort((a, b) => b.t - a.t);
    for (const f of list) out.push({ ...f, rid: r.rid, meeting: r.title || "Untitled meeting", date: r.date });
  }
  return out;
}

export const KIND_WORD: Record<FindingKind, string> = { gap: "Gap", conflict: "Conflict", bug: "Bug", answered: "Answered" };

// ---------------- formatting ----------------
export function fmtDay(ms: number | string): string {
  const d = new Date(ms);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}
/** "2 h 41 m", "41 m", "35 s". */
export function fmtSpan(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} m`;
  return `${Math.floor(m / 60)} h ${m % 60} m`;
}
/** Meeting clock: 02:57, 1:04:10. */
export function fmtClock(sec: number): string {
  const t = Math.max(0, Math.floor(sec));
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const ss = String(t % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${String(m).padStart(2, "0")}:${ss}`;
}
export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
export const fmtNum = (n: number) => n.toLocaleString();

export function replayHref(rid: string, opts: { report?: boolean; t?: number; f?: string } = {}): string {
  let h = `/replay?id=${encodeURIComponent(rid)}`;
  if (opts.report) h += "&view=report";
  if (opts.t != null) h += `&t=${Math.max(0, Math.floor(opts.t))}`;
  if (opts.f) h += `&f=${encodeURIComponent(opts.f)}`;
  return h;
}
