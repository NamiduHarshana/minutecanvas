// Replay: the saved message log of a meeting, played back through the same reducer as the live app.
"use client";
import { createContext, useContext } from "react";
import type { ServerMsg } from "./types";
import type { Finding, Usage } from "./account";
import { BACKEND_URL } from "./client";
import { initialState, reducer, type MeetingState } from "./store";

export interface ReplayData {
  v: number;
  title: string | null;
  date: string;
  duration_s: number;
  speakers: Record<string, string>;
  events: [number, ServerMsg][];      // [meeting time when it was sent live, message]
  // v6.19 (missing in older replays: the page works them out from the events)
  timeline?: { speakers: { speaker: string; t: number; t_end: number }[]; parts: { part: string; t: number; t_end: number }[] };
  findings?: Finding[];
  usage?: Usage;
}

export class ReplayError extends Error {}

export function replayUrl(id: string): string {
  return `${window.location.origin}/replay?id=${encodeURIComponent(id)}`;
}

export async function fetchReplay(id: string): Promise<ReplayData> {
  if (!/^[A-Za-z0-9_-]{16,64}$/.test(id)) throw new ReplayError("This replay link is not valid.");
  let res: Response;
  try {
    res = await fetch(`${BACKEND_URL}/api/replays/${encodeURIComponent(id)}`);
  } catch {
    throw new ReplayError("Can't reach the MinuteCanvas server. Check your internet connection.");
  }
  if (res.status === 404) throw new ReplayError("This replay doesn't exist or was removed.");
  if (res.status === 429) throw new ReplayError("Too many requests. Please try again in a minute.");
  if (!res.ok) throw new ReplayError(`Server error (${res.status}).`);
  const j = (await res.json()) as ReplayData;
  if (!j || !Array.isArray(j.events)) throw new ReplayError("This replay can't be read.");
  return j;
}

/** State after the first `n` events, without highlights (a seek shouldn't flash every card). */
export function stateAt(events: ReplayData["events"], n: number): MeetingState {
  let s: MeetingState = { ...initialState, conn: "closed" };
  for (let k = 0; k < n; k++) s = reducer(s, { type: "server", msg: events[k][1] });
  return { ...s, flash: {}, flowFlash: {}, activity: [], toasts: [] };
}

/** Index of the first event later than time t (events are sorted by time). */
export function eventIndexAt(events: ReplayData["events"], t: number): number {
  let lo = 0;
  let hi = events.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (events[mid][0] <= t) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/** Live-only bits of the UI read this: inside a replay, quotes and lines become "play from here" buttons. */
export interface ReplayCtx {
  jumpToSaid: (t: number) => void;    // t = when it was said (line / card / evidence time)
  focusLine: number | null;           // transcript line the last jump landed on
}
export const ReplayContext = createContext<ReplayCtx | null>(null);
export const useReplay = () => useContext(ReplayContext);
