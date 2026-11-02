"use client";
import { useEffect, useRef, useState } from "react";
import { LoaderCircle } from "lucide-react";
import {
  AccountError,
  authExpiry,
  deleteAllMyData,
  googleClientId,
  loadGoogle,
  localMeetings,
  signInWithGoogle,
  signOut,
} from "@/core/account";
import { fmtDay, useLibrary } from "@/core/library";
import { useTheme, type ThemePref } from "@/core/theme";
import { SITE } from "@/core/site";
import { Initials, Seg } from "../kit";
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
        <SignIn onError={(m) => toasts.show(m, "error")} />
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

function SignIn({ onError }: { onError: (m: string) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<"loading" | "ready" | "off" | "error" | "busy">("loading");
  const [local, setLocal] = useState(0);
  const err = useRef(onError);
  err.current = onError;

  useEffect(() => {
    setLocal(localMeetings().length);
    let cancelled = false;
    googleClientId().then(
      (cid) => {
        if (cancelled) return;
        if (!cid) return setState("off");
        loadGoogle().then(
          (g) => {
            if (cancelled || !box.current) return;
            g.accounts.id.initialize({
              client_id: cid,
              callback: async (r: { credential?: string }) => {
                if (!r.credential) return;
                setState("busy");
                try {
                  await signInWithGoogle(r.credential);       // the page reloads its data on the sign-in event
                } catch (e) {
                  err.current(e instanceof AccountError ? e.message : "Sign-in failed. Please try again.");
                  setState("ready");
                }
              },
              auto_select: false,
              cancel_on_tap_outside: true,
              use_fedcm_for_button: true,
            });
            box.current.innerHTML = "";
            g.accounts.id.renderButton(box.current, {
              theme: document.documentElement.dataset.theme === "light" ? "outline" : "filled_black",
              size: "large", text: "signin_with", shape: "pill", logo_alignment: "left", width: 280 });
            setState("ready");
          },
          () => !cancelled && setState("error"),
        );
      },
      () => !cancelled && setState("error"),
    );
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section className="mc-card signin">
      <div className="card-h">
        <h3>Sign in</h3>
        <span className="hint">Optional</span>
      </div>
      <p className="lead-s">Sign in with Google to keep every meeting in one list on any device, with its replay, report and findings.</p>
      <ul className="points">
        <li>We keep only your email address and name.</li>
        <li>You don’t need to sign in to run a meeting.</li>
        {local > 0 && state !== "off" && (
          <li>
            <b>
              {local} meeting{local === 1 ? "" : "s"} made in this browser
            </b>{" "}
            will be added to your account when you sign in.
          </li>
        )}
      </ul>
      <div ref={box} className="gis" hidden={state !== "ready"} />
      {state === "loading" && (
        <p className="loading" role="status">
          <LoaderCircle size={16} className="spin" aria-hidden /> Loading Google sign-in…
        </p>
      )}
      {state === "busy" && (
        <p className="loading" role="status">
          <LoaderCircle size={16} className="spin" aria-hidden /> Signing in…
        </p>
      )}
      {state === "off" && <p className="hint">Sign-in isn’t set up on this server, so your meetings stay in this browser.</p>}
      {state === "error" && (
        <p className="form-error" role="alert">
          Google sign-in couldn’t load. Check your connection or any blocker, then reload the page.
        </p>
      )}
      {state !== "off" && (
        <p className="hint" style={{ margin: 0 }}>
          By signing in you agree to the <a href="/privacy">privacy policy</a>.
        </p>
      )}
    </section>
  );
}
