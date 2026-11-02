"use client";
import { useEffect, useState } from "react";
import { LoaderCircle } from "lucide-react";
import { AccountError, authExpiry, deleteAllMyData, signOut } from "@/core/account";
import { fmtDay, useLibrary } from "@/core/library";
import { useTheme, type ThemePref } from "@/core/theme";
import { SITE } from "@/core/site";
import { Initials, Seg } from "../kit";
import { SignInCard } from "../SignIn";
import { Toasts, useToasts } from "../Toasts";

export function Account() {
  const lib = useLibrary();
  const { pref, set } = useTheme();
  const toasts = useToasts();
  const [busy, setBusy] = useState(false);
  const [until, setUntil] = useState<Date | null>(null);
  useEffect(() => setUntil(authExpiry()), [lib.user]);

  async function onDeleteAll() {
    if (!window.confirm("Delete every meeting in this account and your name and email? Every replay link stops working. This can't be undone.")) return;
    setBusy(true);
    try {
      const n = await deleteAllMyData();
      toasts.show(`Deleted ${n} meeting${n === 1 ? "" : "s"} and your account. You are signed out.`);
    } catch (e) {
      toasts.show(e instanceof AccountError ? e.message : "Couldn't delete everything. Please try again.", "error");
    } finally {
      setBusy(false);
    }
  }

  const user = lib.user;
  const days = until ? Math.max(0, Math.ceil((until.getTime() - Date.now()) / 86400000)) : null;
  return (
    <div className="wrap narrow-860">
      <h1 className="h-page">Account</h1>
      {lib.loading ? (
        <p className="loading" role="status">
          <LoaderCircle size={18} className="spin" aria-hidden /> Loading…
        </p>
      ) : user ? (
        <>
          <section className="mc-card me-card">
            <Initials name={user.name} size={72} />
            <div className="me-main">
              <div className="me-name">{user.name}</div>
              <div className="hint">{user.email} · signed in with Google</div>
            </div>
            <button
              className="mc-btn mc-btn-outline"
              onClick={() => {
                signOut();
                toasts.show("You are signed out.");
              }}
            >
              Sign out
            </button>
          </section>

          <section className="mc-card">
            <div className="card-h">
              <h3>Profile</h3>
              <span className="hint">From your Google account</span>
            </div>
            <dl className="kv">
              <dt>Name</dt>
              <dd>{user.name}</dd>
              <dt>Email</dt>
              <dd>{user.email}</dd>
              <dt>Saved meetings</dt>
              <dd>
                {lib.rows.length} · replays kept {SITE.keepDays} days
              </dd>
              {until && (
                <>
                  <dt>Signed in until</dt>
                  <dd>
                    {fmtDay(until.getTime())} <span className="hint">({days} day{days === 1 ? "" : "s"})</span>
                  </dd>
                </>
              )}
            </dl>
            <p className="hint" style={{ margin: "16px 0 0" }}>
              To change your name or photo, change it in your Google account. MinuteCanvas only keeps your name and email.
            </p>
          </section>
        </>
      ) : (
        <SignInCard
          title="Sign in"
          badge="Optional"
          lead="Sign in with Google to see every meeting in one list on any device, with its replay, report and findings."
          onError={(m) => toasts.show(m, "error")}
        />
      )}

      <section className="mc-card">
        <div className="card-h">
          <h3>Appearance</h3>
        </div>
        <Seg<ThemePref>
          label="Theme"
          value={pref}
          onChange={set}
          options={[
            { id: "system", label: "Match my device" },
            { id: "dark", label: "Dark" },
            { id: "light", label: "Light" },
          ]}
        />
      </section>

      {user && (
        <section className="danger">
          <div style={{ maxWidth: 520 }}>
            <b>Delete all my data</b>
            <p className="hint" style={{ margin: "4px 0 0" }}>
              Removes every meeting in this account — transcripts, boards, Flow Maps, pictures — and your name and email. Replay links stop working for
              everyone. This can’t be undone.
            </p>
          </div>
          <button className="mc-btn mc-btn-danger" onClick={onDeleteAll} disabled={busy}>
            {busy ? "Deleting…" : "Delete everything"}
          </button>
        </section>
      )}
      <Toasts {...toasts} />
    </div>
  );
}
