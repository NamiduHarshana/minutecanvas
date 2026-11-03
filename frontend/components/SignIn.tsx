"use client";
// Google sign-in card (Account page, and the "Sign in to see your meetings" state of the records pages), and the
// "Add N meetings made in this browser to your account?" question asked after signing in.
import { useEffect, useRef, useState } from "react";
import { LoaderCircle } from "lucide-react";
import {
  AccountError,
  claimLocal,
  forgetLocal,
  googleClientId,
  loadGoogle,
  localMeetings,
  pendingLocal,
  signInWithGoogle,
} from "@/core/account";
import { plural, useUser } from "@/core/library";

const OFF_TEXT = "Sign-in isn’t set up on this server. Meetings still run, and each meeting’s replay link still works.";

export function SignInCard({
  title,
  badge,
  lead,
  onError,
}: {
  title: string;
  badge?: string;
  lead: string;
  onError: (m: string) => void;
}) {
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
                  await signInWithGoogle(r.credential); // the page reloads its data on the sign-in event
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
              size: "large",
              text: "signin_with",
              shape: "pill",
              logo_alignment: "left",
              width: 280,
              locale: "en", // the site is English; without this Google uses the browser language (e.g. Sinhala)
            });
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
        <h3>{title}</h3>
        {badge && <span className="hint">{badge}</span>}
      </div>
      <p className="lead-s">{lead}</p>
      <ul className="points">
        <li>We keep only your email address and name.</li>
        <li>You don’t need to sign in to run a meeting.</li>
        {local > 0 && state !== "off" && (
          <li>
            <b>{plural(local, "meeting")} made in this browser</b> while nobody was signed in. After you sign in, you choose whether to add
            them to your account.
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
      {state === "off" && <p className="hint">{OFF_TEXT}</p>}
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

/** Records pages, signed out: no meeting data at all, only the way in. */
export function SignInGate({ onError }: { onError: (m: string) => void }) {
  return (
    <SignInCard
      title="Sign in to see your meetings"
      lead="Your meetings, findings and usage are kept in your Google account, so nobody else using this browser can see them."
      onError={onError}
    />
  );
}

/** Signed in, and this browser holds meetings the account doesn't have: ask before adding them (shared computers). */
export function ClaimPrompt() {
  const user = useUser();
  const [pending, setPending] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const addRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!user || !localMeetings().length) {
      setPending(0);
      return;
    }
    let cancelled = false;
    pendingLocal().then(
      (p) => !cancelled && setPending(p.length),
      () => {}, // server unreachable: ask on the next page load
    );
    return () => {
      cancelled = true;
    };
  }, [user]);

  useEffect(() => {
    if (pending) addRef.current?.focus();
  }, [pending]);

  if (!user || !pending) return null;

  async function add() {
    setBusy(true);
    setError(null);
    try {
      await claimLocal(); // the pages reload on the event it sends
      setPending(0);
    } catch (e) {
      setError(e instanceof AccountError ? e.message : "Couldn't add the meetings. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  function notMine() {
    forgetLocal();
    setPending(0);
  }

  return (
    <div className="overlay">
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="claim-h" aria-describedby="claim-p">
        <h2 id="claim-h">
          Add {plural(pending, "meeting")} made in this browser to your account?
        </h2>
        <p id="claim-p">
          {pending === 1 ? "It was" : "They were"} recorded here while nobody was signed in. If {pending === 1 ? "it isn’t" : "they aren’t"} yours,
          choose Not mine: {pending === 1 ? "it is" : "they are"} removed from this browser and nothing is added to your account.
        </p>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <div className="mc-row">
          <button ref={addRef} className="mc-btn mc-btn-primary" onClick={add} disabled={busy}>
            {busy ? "Adding…" : "Add"}
          </button>
          <button className="mc-btn mc-btn-outline" onClick={notMine} disabled={busy}>
            Not mine
          </button>
        </div>
      </div>
    </div>
  );
}
