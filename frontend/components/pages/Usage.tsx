"use client";
import { useState } from "react";
import { LoaderCircle } from "lucide-react";
import { bars, fmtClock, fmtNum, fmtSpan, inRange, replayHref, sum, useLibrary, type Range } from "@/core/library";
import type { MeetingRow } from "@/core/account";
import { Empty, Seg, StatCard } from "../kit";
import { SignInGate } from "../SignIn";
import { Toasts, useToasts } from "../Toasts";

const u = (r: MeetingRow) => r.usage;

export function Usage() {
  const lib = useLibrary();
  const [range, setRange] = useState<Range>("7d");
  const rows = inRange(lib.rows, range);
  const voice = sum(rows, (r) => u(r)?.voice_s ?? 0);
  const calls = sum(rows, (r) => u(r)?.spark_calls ?? 0);
  const tools = sum(rows, (r) => u(r)?.tool_calls ?? 0);
  const imgs = sum(rows, (r) => u(r)?.images ?? 0);
  const head = (model: string, kind: string) => (
    <div className="mc-row" style={{ justifyContent: "space-between" }}>
      <span className="lab">{model}</span>
      <span className="mc-tag mc-tag-plain">{kind}</span>
    </div>
  );
  const older = rows.filter((r) => !r.usage).length;
  const toasts = useToasts();
  const signedIn = !lib.loading && !!lib.user;

  return (
    <div className="wrap">
      <div className="head">
        <div>
          <h1 className="h-page">Usage</h1>
          <p className="sub">How much Meta AI work went into your meetings.</p>
        </div>
        {signedIn && (
          <Seg label="Range" value={range} onChange={setRange} options={[{ id: "7d", label: "7d" }, { id: "30d", label: "30d" }, { id: "90d", label: "90d" }]} />
        )}
      </div>

      {lib.loading ? (
        <p className="loading" role="status">
          <LoaderCircle size={18} className="spin" aria-hidden /> Loading…
        </p>
      ) : !lib.user ? (
        <SignInGate onError={(m) => toasts.show(m, "error")} />
      ) : (
        <div className="grid4">
          <StatCard
            head={head("Muse Voice", "Voice")}
            value={fmtSpan(voice)}
            label="Transcribed"
            data={bars(rows, range, (r) => (u(r)?.voice_s ?? 0) / 60)}
            unit="min"
            fmt={(v) => String(Math.round(v))}
          />
          <StatCard head={head("Muse Spark", "Spark")} value={fmtNum(calls)} label="Agent calls" data={bars(rows, range, (r) => u(r)?.spark_calls ?? 0)} unit="calls" />
          <StatCard
            head={head("Muse Spark", "Spark")}
            value={fmtNum(tools)}
            label="Tool calls checked"
            data={bars(rows, range, (r) => u(r)?.tool_calls ?? 0)}
            unit="tool calls"
          />
          <StatCard head={head("Muse Image", "Image")} value={fmtNum(imgs)} label="Card pictures" data={bars(rows, range, (r) => u(r)?.images ?? 0)} unit="pictures" />
        </div>
      )}

      <section className="mc-card">
        <div className="card-h">
          <h3>What each model does in MinuteCanvas</h3>
        </div>
        <div className="grid2 models-grid">
          <div className="model-row">
            <span className="mc-tag mc-tag-plain">Voice</span>
            <span>
              <b>Muse Voice Transcribe</b>
              <span className="hint">Live transcript with speaker labels, streamed in 24 kHz audio.</span>
            </span>
          </div>
          <div className="model-row">
            <span className="mc-tag mc-tag-plain">Spark</span>
            <span>
              <b>Muse Spark</b>
              <span className="hint">Tool-calling agent: draws the Flow Map, checks every quote, flags gaps, conflicts and bugs, writes the board.</span>
            </span>
          </div>
          <div className="model-row">
            <span className="mc-tag mc-tag-plain">Image</span>
            <span>
              <b>Muse Image</b>
              <span className="hint">One small illustration per board card.</span>
            </span>
          </div>
          <div className="model-row">
            <span className="mc-tag mc-tag-plain">Wonder</span>
            <span>
              <b>Muse Wonder</b>
              <span className="hint">Writes the report&rsquo;s summary and key takeaways after Stop, and answers questions about a meeting on its replay page.</span>
            </span>
          </div>
        </div>
      </section>

      {signedIn && (
        <>
          <section className="mc-card scroll table-card">
            {!lib.loading && rows.length === 0 ? (
              <Empty title={`No meetings in the last ${range.replace("d", " days")}`}>
                <p>Usage shows up here after you stop a meeting.</p>
                <a className="mc-btn mc-btn-primary mc-btn-sm" href="/meet">
                  New meeting
                </a>
              </Empty>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Meeting</th>
                    <th>Transcribed</th>
                    <th>Spark calls</th>
                    <th>Tools used</th>
                    <th>Pictures</th>
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
                      <td>
                        <span className="mc-time c2">{fmtClock(r.usage?.voice_s ?? r.duration_s ?? 0)}</span>
                      </td>
                      <td>
                        <span className="mc-time c2">{r.usage ? fmtNum(r.usage.spark_calls) : "–"}</span>
                      </td>
                      <td>
                        <span className="mc-time c2">{r.usage ? fmtNum(r.usage.tool_calls) : "–"}</span>
                      </td>
                      <td>
                        <span className="mc-time c2">{r.usage ? fmtNum(r.usage.images) : "–"}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
          {older > 0 && (
            <p className="hint" style={{ margin: 0 }}>
              “–”: {older === 1 ? "this meeting was" : "these meetings were"} recorded before usage was kept per meeting.
            </p>
          )}
        </>
      )}
      <Toasts {...toasts} />
    </div>
  );
}
