// "My meetings": this browser's meeting list (with each replay's secret owner key), Google sign-in, delete.
// Starting a meeting never needs sign-in, but the records pages do (someone else using this browser must not see
// them). Meetings made signed out stay hidden in this browser; after signing in the user is asked whether to add
// them to the account (the owner key proves they were made here) or to remove them from this browser ("Not mine").
"use client";
import { BACKEND_URL } from "./client";

const MEETINGS_KEY = "minutecanvas.meetings";
const AUTH_KEY = "minutecanvas.auth";
export const AUTH_EVENT = "minutecanvas-auth";   // sign-in / sign-out: the sidebar and pages reload their data

export interface MeetingRow {
  rid: string;
  title: string | null;
  date: string;
  duration_s: number;
  gaps: number;           // open gaps at the end of the meeting
  conflicts: number;      // open conflicts at the end
  created?: number;      // unix seconds (server)
  expires?: number | null;
  // v6.19 (null / missing for meetings saved before it)
  bugs?: number | null;
  answered?: number | null;
  usage?: Usage | null;
  findings?: Finding[] | null;
}
export type FindingKind = "gap" | "conflict" | "bug" | "answered";
export interface FindingQuote {
  t: number;
  speaker: string;
  text: string;
}
/** A gap, answered gap, conflict or bug of a meeting's Flow Map, with the quotes it was checked against. */
export interface Finding {
  id: string;
  kind: FindingKind;
  text: string;
  branch?: string;
  step?: string | null;
  part?: string | null;
  t: number;               // meeting time it was found
  answer?: string | null;
  settled?: boolean;       // conflicts
  fixed?: boolean;         // bugs
  quotes: FindingQuote[];
}
export interface Usage {
  voice_s: number;
  spark_calls: number;
  tool_calls: number;
  images: number;
  wonder_calls?: number;   // v6.24 (older replays: missing)
}
export interface LocalMeeting extends MeetingRow {
  key: string;           // owner key: deletes the replay without signing in
}
export interface User {
  email: string;
  name: string;
}
interface Auth {
  token: string;
  user: User;
}

export class AccountError extends Error {
  constructor(message: string, readonly status = 0) {
    super(message);
  }
}

// ---------------- this browser's meetings ----------------
export function localMeetings(): LocalMeeting[] {
  try {
    const v = JSON.parse(localStorage.getItem(MEETINGS_KEY) || "[]");
    return Array.isArray(v) ? v.filter((m) => m && typeof m.rid === "string" && typeof m.key === "string") : [];
  } catch {
    return [];
  }
}
function saveLocal(list: LocalMeeting[]) {
  try {
    localStorage.setItem(MEETINGS_KEY, JSON.stringify(list.slice(-200)));
  } catch {
    /* private mode: the meeting can still be deleted from its live page */
  }
}
export function addLocalMeeting(m: LocalMeeting) {
  const list = localMeetings().filter((x) => x.rid !== m.rid);
  list.push(m);
  saveLocal(list);
  window.dispatchEvent(new Event(AUTH_EVENT));
}
export function removeLocalMeeting(rid: string) {
  saveLocal(localMeetings().filter((x) => x.rid !== rid));
}
export function localKey(rid: string): string | null {
  return localMeetings().find((m) => m.rid === rid)?.key ?? null;
}

// ---------------- sign-in ----------------
export function currentAuth(): Auth | null {
  try {
    const a = JSON.parse(localStorage.getItem(AUTH_KEY) || "null");
    return a && typeof a.token === "string" && a.user ? (a as Auth) : null;
  } catch {
    return null;
  }
}
function saveAuth(a: Auth | null) {
  const had = !!currentAuth();
  try {
    if (a) localStorage.setItem(AUTH_KEY, JSON.stringify(a));
    else localStorage.removeItem(AUTH_KEY);
  } catch {
    /* ignore */
  }
  if (had !== !!a || a) window.dispatchEvent(new Event(AUTH_EVENT));
}

/** When the login token stops working (the token's body is plain JSON; only its signature is secret). */
export function authExpiry(): Date | null {
  const a = currentAuth();
  if (!a) return null;
  try {
    const body = a.token.split(".")[0].replace(/-/g, "+").replace(/_/g, "/");
    const exp = Number(JSON.parse(atob(body + "=".repeat((4 - (body.length % 4)) % 4))).exp);
    return exp ? new Date(exp * 1000) : null;
  } catch {
    return null;
  }
}
/** Authorization header when signed in (a meeting started signed in goes straight into "My meetings"). */
export function authHeader(): Record<string, string> {
  const a = currentAuth();
  return a ? { Authorization: `Bearer ${a.token}` } : {};
}

async function call(path: string, init: RequestInit = {}, auth = true): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(`${BACKEND_URL}${path}`, {
      ...init,
      headers: { ...(init.body ? { "Content-Type": "application/json" } : {}), ...(auth ? authHeader() : {}), ...(init.headers || {}) },
      cache: "no-store",
    });
  } catch {
    throw new AccountError("Can't reach the MinuteCanvas server. Check your internet connection.");
  }
  if (res.status === 401 && auth) {
    saveAuth(null); // token expired or the server's secret changed
    throw new AccountError("Your sign-in has expired. Please sign in again.", 401);
  }
  if (!res.ok) {
    let detail = "";
    try {
      detail = (await res.json()).detail;
    } catch {
      /* ignore */
    }
    if (res.status === 429) throw new AccountError("Too many requests. Please try again in a minute.", 429);
    throw new AccountError(typeof detail === "string" && detail ? detail : `Server error (${res.status}).`, res.status);
  }
  return res;
}

/** Google client id from the server ("" = sign-in is off on this server). */
export async function googleClientId(): Promise<string> {
  const j = await (await call("/api/auth/config", {}, false)).json();
  return typeof j.google_client_id === "string" ? j.google_client_id : "";
}

/** Google ID token -> our login token. This browser's meetings are only added after the user says so (pendingLocal). */
export async function signInWithGoogle(credential: string): Promise<User> {
  const j = await (await call("/api/auth/google", { method: "POST", body: JSON.stringify({ credential }) }, false)).json();
  saveAuth({ token: j.token, user: j.user });
  return j.user as User;
}

export function signOut() {
  saveAuth(null);
  // the meetings are in the account now; a shared computer shouldn't keep their delete keys
  saveLocal([]);
  try {
    (window as unknown as { google?: GoogleId }).google?.accounts.id.disableAutoSelect();
  } catch {
    /* ignore */
  }
}

export async function myMeetings(): Promise<{ user: User; meetings: MeetingRow[] }> {
  return (await call("/api/me/meetings")).json();
}

/** Signed in: this browser's meetings that aren't in the account yet (made while nobody was signed in, or by
 *  someone else). Ones already in the account (recorded while signed in) are dropped from this browser silently. */
export async function pendingLocal(): Promise<LocalMeeting[]> {
  const local = localMeetings();
  if (!local.length || !currentAuth()) return [];
  const mine = new Set((await myMeetings()).meetings.map((m) => m.rid));
  const pending = local.filter((m) => !mine.has(m.rid));
  if (pending.length !== local.length) saveLocal(pending);
  return pending;
}

/** "Add": this browser's meetings -> the account (the server checks each owner key and skips other people's),
 *  then the browser forgets them. Returns how many were added. */
export async function claimLocal(): Promise<number> {
  const items = localMeetings().map((m) => ({ rid: m.rid, key: m.key }));
  let added = 0;
  if (items.length) {
    const j = await (await call("/api/me/meetings/claim", { method: "POST", body: JSON.stringify({ items }) })).json();
    added = Number(j.added) || 0;
  }
  forgetLocal();
  return added;
}

/** "Not mine": remove this browser's meetings from this browser; nothing goes into the account. */
export function forgetLocal() {
  saveLocal([]);
  window.dispatchEvent(new Event(AUTH_EVENT));
}

/** Owner key (this browser made it) or the signed-in owner. */
export async function deleteReplay(rid: string): Promise<void> {
  const key = localKey(rid);
  try {
    await call(`/api/replays/${encodeURIComponent(rid)}`, { method: "DELETE", headers: key ? { "X-Replay-Key": key } : {} });
  } catch (e) {
    if (e instanceof AccountError && e.status === 404) {
      removeLocalMeeting(rid); // already gone (or expired)
      throw new AccountError("This replay was already deleted, or you can't delete it from this browser.", 404);
    }
    throw e;
  }
  removeLocalMeeting(rid);
}

export async function deleteAllMyData(): Promise<number> {
  const j = await (await call("/api/me", { method: "DELETE" })).json();
  signOut();
  return j.deleted_meetings as number;
}

// ---------------- Google Identity Services ----------------
export interface GoogleId {
  accounts: {
    id: {
      initialize: (o: Record<string, unknown>) => void;
      renderButton: (el: HTMLElement, o: Record<string, unknown>) => void;
      disableAutoSelect: () => void;
    };
  };
}

let gisPromise: Promise<GoogleId> | null = null;
export function loadGoogle(): Promise<GoogleId> {
  const w = window as unknown as { google?: GoogleId };
  if (w.google?.accounts?.id) return Promise.resolve(w.google);
  if (!gisPromise) {
    gisPromise = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "https://accounts.google.com/gsi/client";
      s.async = true;
      s.onload = () => (w.google?.accounts?.id ? resolve(w.google) : reject(new Error("gsi")));
      s.onerror = () => {
        gisPromise = null;
        reject(new Error("gsi"));
      };
      document.head.appendChild(s);
    });
  }
  return gisPromise;
}

// ---------------- formatting ----------------
export function fmtDuration(s: number): string {
  const m = Math.round(s / 60);
  if (m < 1) return "under 1 min";
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}
export function fmtDate(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" });
}
export function daysLeft(expires?: number | null): number | null {
  if (!expires) return null;
  return Math.max(0, Math.ceil((expires * 1000 - Date.now()) / 86400000));
}
