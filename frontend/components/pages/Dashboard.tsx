"use client";
import { useState } from "react";
import { ChevronRight, LoaderCircle, Mic, Play } from "lucide-react";
import {
  allFindings,
  bars,
  conflictsFound,
  fmtClock,
  fmtDay,
  fmtSpan,
  gapsFound,
  inRange,
  replayHref,
  sum,
  useLibrary,
  type Range,
} from "@/core/library";
import { demoReplayHref } from "@/core/site";
import { CountTag, Empty, FindingTag, Seg, StatCard } from "../kit";
import { SignInGate } from "../SignIn";
import { Toasts, useToasts } from "../Toasts";

export function Dashboard() {
  const lib = useLibrary();
  const [range, setRange] = useState<Range>("7d");
  const rows = inRange(lib.rows, range);
  const demo = demoReplayHref();
  const first = lib.user?.name.trim().split(/\s+/)[0];
  const recorded = sum(rows, (r) => r.duration_s || 0);
  const findings = allFindings(lib.rows).filter((f) => f.kind !== "answered").slice(0, 4);
  const recent = lib.rows.slice(0, 4);
  const toasts = useToasts();
  const signedIn = !lib.loading && !!lib.user;

  return (
    <div className="wrap">
      <div className="head">
        <div>
          <h1 className="h-greet">{first ? `Hello ${first}` : "Hello"}</h1>
          <p className="sub">Your meetings, and what they missed.</p>
        </div>
        {signedIn && (
          <Seg label="Range" value={range} onChange={setRange} options={[{ id: "7d", label: "7d" }, { id: "30d", label: "30d" }, { id: "90d", label: "90d" }]} />
        )}
      </div>

      <section className="hero" aria-label="Start a meeting">
        <div>
          <h2>Start a meeting</h2>
          <p>Pick your mic, or your mic plus a Google Meet or Zoom tab. Muse Voice transcribes, Muse Spark maps the flow and flags what nobody covered.</p>
          <div className="mc-row" style={{ marginTop: 20 }}>
            <a className="mc-btn mc-btn-primary" href="/meet">
              <Mic strokeWidth={1.75} aria-hidden />
              New meeting
            </a>
            {demo && (
              <a className="mc-btn mc-btn-secondary" href={demo}>
                <Play strokeWidth={1.75} aria-hidden />
                Watch demo replay
              </a>
            )}
          </div>
        </div>
        <div className="hero-models">
          <span className="lab">Running on Meta models</span>
          <div className="models">
            <span className="model">
              <i />
              Muse Voice Transcribe
            </span>
            <span className="model">
              <i />
              Muse Spark
            </span>
            <span className="model">
              <i />
              Muse Image
            </span>
          </div>
        </div>
      </section>

      {lib.loading ? (
        <p className="loading" role="status">
          <LoaderCircle size={18} className="spin" aria-hidden /> Loading…
        </p>
      ) : !lib.user ? (
        <SignInGate onError={(m) => toasts.show(m, "error")} />
      ) : (
        <>
          <section className="grid4" aria-label={`Last ${range}`}>
            <StatCard value={String(rows.length)} label="Meetings" data={bars(rows, range, () => 1)} unit="meetings" />
            <StatCard value={fmtSpan(recorded)} label="Recorded" data={bars(rows, range, (r) => (r.duration_s || 0) / 60)} unit="min" fmt={(v) => String(Math.round(v))} />
            <StatCard value={String(sum(rows, gapsFound))} label="Gaps found" color="var(--gap)" data={bars(rows, range, gapsFound)} unit="gaps" />
            <StatCard
              value={String(sum(rows, conflictsFound))}
              label="Conflicts caught"
              color="var(--conflict)"
              data={bars(rows, range, conflictsFound)}
              unit="conflicts"
            />
          </section>

          {lib.error && (
            <p className="form-error" role="alert">
              {lib.error}
            </p>
          )}

          <section className="grid2">
            <div className="mc-card list-card recent">
              <div className="card-h">
                <h3>Recent meetings</h3>
                <a className="link" href="/meetings">
                  All meetings →
                </a>
              </div>
              {!lib.loading && recent.length === 0 && (
                <Empty title="No meetings yet">
                  <p>When you stop a meeting it shows up here, with its replay and report.</p>
                  <a className="mc-btn mc-btn-primary mc-btn-sm" href="/meet">
                    New meeting
                  </a>
                </Empty>
              )}
              {recent.map((m) => (
                <div key={m.rid} className="mc-mrow dash">
                  <div style={{ minWidth: 0 }}>
                    <a className="t" href={replayHref(m.rid)} title={m.title || undefined}>
                      {m.title || "Untitled meeting"}
                    </a>
                    <div className="s">
                      {fmtDay(m.date)} · <span className="mc-time">{fmtClock(m.duration_s || 0)}</span>
                    </div>
                  </div>
                  <div className="mc-row nowrap">
                    <CountTag kind="gap" n={m.gaps || 0} />
                    <CountTag kind="conflict" n={m.conflicts || 0} />
                  </div>
                  <div className="mc-row nowrap">
                    <a className="mc-btn mc-btn-primary mc-btn-sm" href={replayHref(m.rid)}>
                      Replay
                    </a>
                    <a className="mc-btn mc-btn-outline mc-btn-sm" href={replayHref(m.rid, { report: true })}>
                      Report
                    </a>
                  </div>
                </div>
              ))}
            </div>

            <div className="mc-card list-card">
              <div className="card-h">
                <h3>Latest findings</h3>
                <a className="link" href="/findings">
                  All findings →
                </a>
              </div>
              {!lib.loading && findings.length === 0 && (
                <Empty title="No findings yet">
                  <p>Gaps, conflicts and bugs Muse Spark finds in your meetings land here, each with the moment it happened.</p>
                </Empty>
              )}
              {findings.map((f) => (
                <a key={`${f.rid}:${f.id}`} className="frow dash-f" href={replayHref(f.rid, { t: f.t, f: f.id })}>
                  <FindingTag kind={f.kind} />
                  <span style={{ minWidth: 0 }}>
                    <span className="t">{f.text}</span>
                    <span className="s">
                      {f.meeting} · <span className="mc-time">{fmtClock(f.t)}</span>
                    </span>
                  </span>
                  <ChevronRight size={16} strokeWidth={1.75} className="chev" aria-hidden />
                </a>
              ))}
            </div>
          </section>
        </>
      )}
      <Toasts {...toasts} />
    </div>
  );
}
