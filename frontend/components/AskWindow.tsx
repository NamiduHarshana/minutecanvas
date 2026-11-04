"use client";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { LoaderCircle, Send, X } from "lucide-react";
import { fmtTime } from "@/core/store";
import { FindingTag } from "./kit";
import { ReplayError, askReplay, type AskAnswer } from "@/core/replay";

type QA = { id: number; q: string; a?: AskAnswer; err?: string };
const ASK_EXAMPLES = ["What was decided?", "What is still open?"];

/** v6.25: "Ask about this meeting" window (replay page header, and the live page after Stop). Muse Wonder answers only
 * from the transcript; each answer shows the real transcript lines it is based on (from the server, not written by the
 * model). On the replay page a line closes the window and plays the replay there (onJump); on the live page it opens
 * the replay at that moment in a new tab (quoteHref). Kept mounted while closed, so the questions and answers stay. */
export function AskWindow({
  rid,
  open,
  onClose,
  onJump,
  quoteHref,
}: {
  rid: string;
  open: boolean;
  onClose: () => void;
  onJump?: (t: number) => void;
  quoteHref?: (t: number) => string;
}) {
  const [text, setText] = useState("");
  const [items, setItems] = useState<QA[]>([]);
  const [busy, setBusy] = useState(false);
  const list = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLInputElement>(null);
  useLayoutEffect(() => {
    const el = list.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [items, busy, open]);
  useEffect(() => {
    if (!open) return;
    field.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  async function ask(q: string) {
    q = q.trim();
    if (q.length < 3 || busy || !rid) return;
    const id = Date.now();
    setItems((xs) => [...xs, { id, q }]);
    setText("");
    setBusy(true);
    try {
      const a = await askReplay(rid, q);
      setItems((xs) => xs.map((x) => (x.id === id ? { ...x, a } : x)));
    } catch (e) {
      const err = e instanceof ReplayError ? e.message : "Couldn't answer right now. Please try again.";
      setItems((xs) => xs.map((x) => (x.id === id ? { ...x, err } : x)));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="overlay" hidden={!open} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal ask-modal" role="dialog" aria-modal="true" aria-labelledby="ask-h">
        <div className="modal-head">
          <h2 id="ask-h" className="ask-title">Ask about this meeting</h2>
          <span className="mc-tag mc-tag-plain">Muse Wonder</span>
          <button className="icon-btn ask-close" onClick={onClose} aria-label="Close">
            <X size={18} aria-hidden />
          </button>
        </div>
        <div className="askp">
          <div className="ask-list" ref={list} aria-live="polite">
            {items.length === 0 && (
              <div className="ask-empty">
                <p className="hint">Muse Wonder answers only from what was said in this meeting and shows the lines it used.</p>
                <div className="mc-row">
                  {ASK_EXAMPLES.map((q) => (
                    <button key={q} className="mc-btn mc-btn-outline mc-btn-sm" onClick={() => ask(q)} disabled={busy}>
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {items.map((x) => (
              <div key={x.id} className="qa">
                <div className="qa-q">{x.q}</div>
                {!x.a && !x.err && (
                  <p className="hint qa-wait" role="status">
                    <LoaderCircle size={14} className="spin" aria-hidden /> Muse Wonder is reading the transcript…
                  </p>
                )}
                {x.err && <p className="qa-err" role="alert">{x.err}</p>}
                {x.a && (
                  <>
                    <p className={x.a.covered ? "qa-a" : "qa-a qa-none"}>{x.a.answer}</p>
                    {x.a.quotes.map((q, k) => {
                      const body = (
                        <>
                          <span className="mc-time">{fmtTime(q.t)}</span>
                          <span>
                            “{q.text}”<small>{q.speaker}</small>
                          </span>
                        </>
                      );
                      return quoteHref ? (
                        <a key={k} className="mc-quote" href={quoteHref(q.t)} target="_blank" rel="noopener" title="Play the replay from this moment">
                          {body}
                        </a>
                      ) : (
                        <button key={k} className="mc-quote" onClick={() => (onClose(), onJump?.(q.t))} title="Play from this moment">
                          {body}
                        </button>
                      );
                    })}
                    {x.a.findings.map((f) => {
                      const body = (
                        <>
                          <span className="mc-time">{fmtTime(f.t)}</span>
                          <span className="qa-find-body">
                            <FindingTag kind={f.kind} small />
                            <span>{f.text}</span>
                            {f.step && <small>at “{f.step}”</small>}
                          </span>
                        </>
                      );
                      return quoteHref ? (
                        <a key={f.id} className="mc-quote qa-find" href={quoteHref(f.t)} target="_blank" rel="noopener" title="Open the replay at this finding">
                          {body}
                        </a>
                      ) : (
                        <button key={f.id} className="mc-quote qa-find" onClick={() => (onClose(), onJump?.(f.t))} title="Play from this finding">
                          {body}
                        </button>
                      );
                    })}
                    {x.a.by && <span className="qa-by">Answered by {x.a.by} · only from what this meeting said and found</span>}
                  </>
                )}
              </div>
            ))}
          </div>
          <form
            className="ask-form"
            onSubmit={(e) => {
              e.preventDefault();
              ask(text);
            }}
          >
            <input
              ref={field}
              className="input"
              value={text}
              onChange={(e) => setText(e.target.value)}
              maxLength={300}
              placeholder="Ask about this meeting"
              aria-label="Ask about this meeting"
              disabled={!rid}
            />
            <button className="mc-btn mc-btn-primary mc-btn-sm" type="submit" disabled={busy || text.trim().length < 3} aria-label="Ask">
              <Send strokeWidth={1.75} aria-hidden /> Ask
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
