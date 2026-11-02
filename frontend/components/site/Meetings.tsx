"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { CircleHelp, LoaderCircle, LogOut, Play, Presentation, Trash2, TriangleAlert } from "lucide-react";
import {
  AccountError,
  claimLocal,
  currentAuth,
  daysLeft,
  deleteAllMyData,
  deleteReplay,
  fmtDate,
  fmtDuration,
  googleClientId,
  loadGoogle,
  localMeetings,
  signInWithGoogle,
  signOut,
  type MeetingRow,
  type User,
} from "@/core/account";

type View =
  | { k: "loading" }
  | { k: "signin"; clientId: string; local: number }
  | { k: "off"; rows: MeetingRow[] }          // sign-in not set up on this server: this browser's meetings
  | { k: "list"; user: User; rows: MeetingRow[] };

export function Meetings() {
  const [view, setView] = useState<View>({ k: "loading" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const showList = useCallback(async () => {
    const a = currentAuth();
    if (!a) return false;
    const rows = await claimLocal();
    const now = currentAuth();          // claimLocal clears an expired sign-in
    if (!now) return false;
    setView({ k: "list", user: now.user, rows });
    return true;
  }, []);

  const start = useCallback(async () => {
    setError(null);
    try {
      if (await showList()) return;
    } catch (e) {
      if (!(e instanceof AccountError && e.status === 401)) {
        setError(e instanceof AccountError ? e.message : "Couldn't load your meetings.");
        setView({ k: "signin", clientId: "", local: localMeetings().length });
        return;
      }
      setNotice(e.message);
    }
    try {
      const cid = await googleClientId();
      if (!cid) setView({ k: "off", rows: localMeetings() });
      else setView({ k: "signin", clientId: cid, local: localMeetings().length });
    } catch (e) {
      setError(e instanceof AccountError ? e.message : "Couldn't reach the MinuteCanvas server.");
      setView({ k: "signin", clientId: "", local: localMeetings().length });
    }
  }, [showList]);

  useEffect(() => {
    start();
  }, [start]);

  async function onCredential(credential: string) {
    setError(null);
    setNotice(null);
    setView({ k: "loading" });
    try {
      await signInWithGoogle(credential);
      await showList();
    } catch (e) {
      setError(e instanceof AccountError ? e.message : "Sign-in failed. Please try again.");
      start();
    }
  }

  async function onDelete(row: MeetingRow) {
    if (!window.confirm(`Delete "${row.title || "Untitled meeting"}"? Its replay link stops working for everyone. This can't be undone.`)) return;
    setBusy(row.rid);
    setError(null);
    try {
      await deleteReplay(row.rid);
      setNotice("Meeting deleted.");
    } catch (e) {
      setError(e instanceof AccountError ? e.message : "Couldn't delete the meeting.");
    } finally {
      setBusy(null);
    }
    setView((v) => (v.k === "list" || v.k === "off" ? { ...v, rows: v.rows.filter((r) => r.rid !== row.rid) } : v));
  }

  async function onDeleteAll() {
    if (!window.confirm("Delete all your meetings and your My meetings list? Every replay link stops working. This can't be undone.")) return;
    setBusy("all");
    setError(null);
    try {
      const n = await deleteAllMyData();
      setNotice(`Deleted ${n} meeting${n === 1 ? "" : "s"} and your account's list. You are signed out.`);
      start();
    } catch (e) {
      setError(e instanceof AccountError ? e.message : "Couldn't delete everything. Please try again.");
    } finally {
      setBusy(null);
    }
  }

  function onSignOut() {
    signOut();
    setNotice("You are signed out.");
    start();
  }

  return (
    <section className="s-doc mm">
      <div className="s-wrap">
        <div className="mm-head">
          <div>
            <h1>My meetings</h1>
            {view.k === "list" && (
              <p className="mm-user">
                Signed in as <b>{view.user.name}</b> ({view.user.email})
              </p>
            )}
          </div>
          {view.k === "list" && (
            <div className="mm-head-actions">
              <a className="s-btn s-btn-brand s-btn-sm" href="/meet">
                Start a meeting
              </a>
              <button className="s-btn s-btn-line s-btn-sm" onClick={onSignOut}>
                <LogOut size={15} aria-hidden /> Sign out
              </button>
            </div>
          )}
        </div>

        {notice && (
          <p className="mm-note" role="status">
            {notice}
          </p>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}

        {view.k === "loading" && (
          <p className="mm-loading" role="status">
            <LoaderCircle size={18} className="spin" aria-hidden /> Loading…
          </p>
        )}

        {view.k === "signin" && <SignIn clientId={view.clientId} local={view.local} onCredential={onCredential} />}

        {view.k === "off" && (
          <>
            <p className="mm-lead">Sign-in isn&rsquo;t set up on this server, so this page shows the meetings made in this browser.</p>
            <Rows rows={view.rows} busy={busy} onDelete={onDelete} />
          </>
        )}

        {view.k === "list" && (
          <>
            <Rows rows={view.rows} busy={busy} onDelete={onDelete} />
            <div className="mm-danger">
              <div>
                <b>Delete all my data</b>
                <p>Removes every meeting in this account (transcripts, boards, Flow Maps, pictures) and your email and name.</p>
              </div>
              <button className="s-btn s-btn-line s-btn-sm mm-danger-btn" onClick={onDeleteAll} disabled={busy === "all"}>
                <Trash2 size={15} aria-hidden /> {busy === "all" ? "Deleting…" : "Delete all my data"}
              </button>
            </div>
          </>
        )}
      </div>
    </section>
  );
}

function SignIn({ clientId, local, onCredential }: { clientId: string; local: number; onCredential: (c: string) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const cb = useRef(onCredential);
  cb.current = onCredential;
  const [gisError, setGisError] = useState(false);

  useEffect(() => {
    if (!clientId) return;
    let cancelled = false;
    loadGoogle().then(
      (g) => {
        if (cancelled || !box.current) return;
        g.accounts.id.initialize({
          client_id: clientId,
          callback: (r: { credential?: string }) => r.credential && cb.current(r.credential),
          auto_select: false,
          cancel_on_tap_outside: true,
          use_fedcm_for_button: true,
        });
        box.current.innerHTML = "";
        g.accounts.id.renderButton(box.current, { theme: "outline", size: "large", text: "signin_with", shape: "pill", logo_alignment: "left", width: 280 });
      },
      () => !cancelled && setGisError(true),
    );
    return () => {
      cancelled = true;
    };
  }, [clientId]);

  return (
    <div className="mm-signin">
      <p className="mm-lead">Sign in with Google to see every meeting you recorded, open its replay or report, and delete it.</p>
      <ul className="mm-points">
        <li>We keep only your email address and name.</li>
        <li>You don&rsquo;t need to sign in to run a meeting.</li>
        {local > 0 && (
          <li>
            <b>
              {local} meeting{local === 1 ? "" : "s"} made in this browser
            </b>{" "}
            will be added to your account when you sign in.
          </li>
        )}
      </ul>
      {clientId ? <div ref={box} className="mm-gis" /> : null}
      {gisError && <p className="form-error">Google sign-in couldn&rsquo;t load. Check your connection or any blocker, then reload the page.</p>}
      <p className="mm-fine">
        By signing in you agree to the <a href="/privacy">privacy policy</a>.
      </p>
    </div>
  );
}

function Rows({ rows, busy, onDelete }: { rows: MeetingRow[]; busy: string | null; onDelete: (r: MeetingRow) => void }) {
  if (!rows.length) {
    return (
      <div className="mm-empty">
        <p>No meetings yet. When you stop a meeting, it shows up here with its replay and report.</p>
        <a className="s-btn s-btn-brand" href="/meet">
          Start a meeting
        </a>
      </div>
    );
  }
  return (
    <ul className="mm-list">
      {rows.map((r) => {
        const left = daysLeft(r.expires);
        const href = `/replay?id=${encodeURIComponent(r.rid)}`;
        return (
          <li key={r.rid} className="mm-row">
            <div className="mm-main">
              <a className="mm-title" href={href}>
                {r.title || "Untitled meeting"}
              </a>
              <p className="mm-meta">
                <span>{fmtDate(r.date)}</span>
                <span>{fmtDuration(r.duration_s)}</span>
                {left !== null && <span>Deleted in {left} day{left === 1 ? "" : "s"}</span>}
              </p>
            </div>
            <div className="mm-found">
              <span className={r.gaps ? "mm-gaps" : "mm-zero"} title="Open gaps at the end of the meeting">
                <CircleHelp size={14} aria-hidden /> {r.gaps} gap{r.gaps === 1 ? "" : "s"}
              </span>
              <span className={r.conflicts ? "mm-conf" : "mm-zero"} title="Open conflicts at the end of the meeting">
                <TriangleAlert size={14} aria-hidden /> {r.conflicts} conflict{r.conflicts === 1 ? "" : "s"}
              </span>
            </div>
            <div className="mm-actions">
              <a className="s-btn s-btn-line s-btn-sm" href={href}>
                <Play size={14} aria-hidden /> Replay
              </a>
              <a className="s-btn s-btn-line s-btn-sm" href={`${href}&view=report`}>
                <Presentation size={14} aria-hidden /> Report
              </a>
              <button className="s-btn s-btn-line s-btn-sm mm-del" onClick={() => onDelete(r)} disabled={busy === r.rid} aria-label={`Delete ${r.title || "meeting"}`}>
                <Trash2 size={14} aria-hidden /> {busy === r.rid ? "Deleting…" : "Delete"}
              </button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
