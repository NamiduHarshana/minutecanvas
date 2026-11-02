"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { Ellipsis, Link2, LoaderCircle, Mic, Search, Trash2 } from "lucide-react";
import { AccountError, deleteReplay, type MeetingRow } from "@/core/account";
import { conflictsFound, findingCount, fmtClock, fmtDay, gapsFound, replayHref, stamp, useLibrary } from "@/core/library";
import { SITE } from "@/core/site";
import { CountTag, Empty, Seg } from "../kit";
import { SignInGate } from "../SignIn";
import { Toasts, useToasts } from "../Toasts";

type Filter = "all" | "gaps" | "conflicts";
type Sort = "new" | "long" | "found";

function expiresIn(r: MeetingRow): string {
  if (r.expires === null && r.created) return "Kept";            // the pinned demo replay
  const end = r.expires ? r.expires * 1000 : stamp(r) + SITE.keepDays * 86400000;
  const days = Math.max(0, Math.ceil((end - Date.now()) / 86400000));
  return days === 0 ? "today" : `in ${days} day${days === 1 ? "" : "s"}`;
}

export function Meetings() {
  const lib = useLibrary();
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<Sort>("new");
  const toasts = useToasts();

  const rows = useMemo(() => {
    const words = q.trim().toLowerCase();
    let list = lib.rows.filter((r) => !words || (r.title || "untitled meeting").toLowerCase().includes(words));
    if (filter === "gaps") list = list.filter((r) => gapsFound(r) > 0);
    if (filter === "conflicts") list = list.filter((r) => conflictsFound(r) > 0);
    if (sort === "long") list = [...list].sort((a, b) => (b.duration_s || 0) - (a.duration_s || 0));
    if (sort === "found") list = [...list].sort((a, b) => findingCount(b) - findingCount(a));
    return list;
  }, [lib.rows, q, filter, sort]);

  async function onDelete(r: MeetingRow) {
    if (!window.confirm(`Delete "${r.title || "Untitled meeting"}"? Its replay link stops working for everyone. This can't be undone.`)) return;
    try {
      await deleteReplay(r.rid);
      lib.drop(r.rid);
      toasts.show("Meeting deleted.");
    } catch (e) {
      toasts.show(e instanceof AccountError ? e.message : "Couldn't delete the meeting.", "error");
      if (e instanceof AccountError && e.status === 404) lib.drop(r.rid);
    }
  }

  async function onCopy(r: MeetingRow) {
    const url = `${window.location.origin}${replayHref(r.rid)}`;
    try {
      await navigator.clipboard.writeText(url);
      toasts.show("Replay link copied. Anyone with the link can watch the meeting again.");
    } catch {
      window.prompt("Copy the replay link:", url);
    }
  }

  return (
    <div className="wrap">
      <div className="head">
        <div>
          <h1 className="h-page">Meetings</h1>
          <p className="sub">Every meeting you recorded. Replays are kept for {SITE.keepDays} days.</p>
        </div>
        <a className="mc-btn mc-btn-primary" href="/meet">
          <Mic strokeWidth={1.75} aria-hidden />
          New meeting
        </a>
      </div>

      {lib.loading ? (
        <p className="loading" role="status">
          <LoaderCircle size={18} className="spin" aria-hidden /> Loading…
        </p>
      ) : !lib.user ? (
        <SignInGate onError={(m) => toasts.show(m, "error")} />
      ) : (
        <>
          {lib.error && (
            <p className="form-error" role="alert">
              {lib.error}
            </p>
          )}

          <div className="toolbar">
            <label className="search">
              <Search size={16} strokeWidth={1.75} aria-hidden />
              <input placeholder="Search titles" aria-label="Search titles" value={q} onChange={(e) => setQ(e.target.value)} />
            </label>
            <Seg
              label="Show"
              value={filter}
              onChange={setFilter}
              options={[
                { id: "all", label: "All" },
                { id: "gaps", label: "With gaps" },
                { id: "conflicts", label: "With conflicts" },
              ]}
            />
            <span className="grow" />
            <Seg
              label="Sort"
              value={sort}
              onChange={setSort}
              options={[
                { id: "new", label: "Newest" },
                { id: "long", label: "Longest" },
                { id: "found", label: "Most findings" },
              ]}
            />
          </div>

          <section className="mc-card scroll table-card">
            {!lib.loading && rows.length === 0 ? (
              lib.rows.length === 0 ? (
                <Empty title="No meetings yet">
                  <p>When you stop a meeting it shows up here, with its replay and report.</p>
                  <a className="mc-btn mc-btn-primary mc-btn-sm" href="/meet">
                    New meeting
                  </a>
                </Empty>
              ) : (
                <Empty title="No meetings match">
                  <p>Try another word or filter.</p>
                </Empty>
              )
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Meeting</th>
                    <th>Date</th>
                    <th>Length</th>
                    <th>Findings</th>
                    <th>Expires</th>
                    <th>
                      <span className="sr">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.rid}>
                      <td>
                        <a className="t-link" href={replayHref(r.rid)}>
                          {r.title || "Untitled meeting"}
                        </a>
                      </td>
                      <td className="c2">{fmtDay(r.date)}</td>
                      <td>
                        <span className="mc-time c2">{fmtClock(r.duration_s || 0)}</span>
                      </td>
                      <td>
                        <div className="mc-row nowrap">
                          <CountTag kind="gap" n={r.gaps || 0} />
                          <CountTag kind="conflict" n={r.conflicts || 0} />
                        </div>
                      </td>
                      <td className="cm">{expiresIn(r)}</td>
                      <td>
                        <div className="mc-row nowrap end">
                          <a className="mc-btn mc-btn-primary mc-btn-sm" href={replayHref(r.rid)}>
                            Replay
                          </a>
                          <a className="mc-btn mc-btn-outline mc-btn-sm" href={replayHref(r.rid, { report: true })}>
                            Report
                          </a>
                          <RowMenu onCopy={() => onCopy(r)} onDelete={() => onDelete(r)} title={r.title || "Untitled meeting"} />
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
          <p className="hint" style={{ margin: 0 }}>
            Meetings you record before signing in stay hidden in this browser. When you sign in, you choose whether to add them to your account.
          </p>
        </>
      )}
      <Toasts {...toasts} />
    </div>
  );
}

function RowMenu({ onCopy, onDelete, title }: { onCopy: () => void; onDelete: () => void; title: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  return (
    <div className="menu-wrap" ref={ref}>
      <button className="mc-ib" aria-label={`More actions for ${title}`} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <Ellipsis size={16} strokeWidth={2} aria-hidden />
      </button>
      {open && (
        <div className="mc-menu" role="menu">
          <button
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onCopy();
            }}
          >
            <Link2 size={16} strokeWidth={1.75} aria-hidden /> Copy link
          </button>
          <button
            role="menuitem"
            className="danger"
            onClick={() => {
              setOpen(false);
              onDelete();
            }}
          >
            <Trash2 size={16} strokeWidth={1.75} aria-hidden /> Delete
          </button>
        </div>
      )}
    </div>
  );
}
