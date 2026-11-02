"use client";
import { useMemo, useState } from "react";
import { Download, Play, Search } from "lucide-react";
import type { FindingKind } from "@/core/account";
import { allFindings, fmtClock, fmtDay, replayHref, useLibrary, type MeetingFinding } from "@/core/library";
import { Empty, FindingTag, Seg } from "../kit";

type Tab = "all" | FindingKind;

/** Open gaps and conflicts of every meeting as a text file to bring to the next meeting. */
function exportOpen(list: MeetingFinding[]) {
  const open = list.filter((f) => f.kind === "gap" || (f.kind === "conflict" && !f.settled) || (f.kind === "bug" && !f.fixed));
  const byMeeting = new Map<string, MeetingFinding[]>();
  for (const f of open) byMeeting.set(f.rid, [...(byMeeting.get(f.rid) ?? []), f]);
  const out = ["# Open questions from MinuteCanvas", ""];
  for (const fs of byMeeting.values()) {
    out.push(`## ${fs[0].meeting} (${fmtDay(fs[0].date)})`, "");
    for (const f of [...fs].sort((a, b) => a.t - b.t)) {
      const where = f.part ? ` [${f.part}]` : "";
      if (f.kind === "conflict") {
        out.push(`- Conflict${where}: ${f.text}`);
        for (const q of f.quotes.slice(0, 2)) out.push(`  - ${fmtClock(q.t)} ${q.speaker}: “${q.text}”`);
      } else out.push(`- ${f.kind === "bug" ? "Bug" : "Gap"}${where}: ${f.text}`);
    }
    out.push("");
  }
  const blob = new Blob([out.join("\n")], { type: "text/markdown" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "minutecanvas-open-questions.md";
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

export function Findings() {
  const lib = useLibrary();
  const [tab, setTab] = useState<Tab>("all");
  const [q, setQ] = useState("");
  const all = useMemo(() => allFindings(lib.rows), [lib.rows]);
  const count = (k: FindingKind) => all.filter((f) => f.kind === k).length;
  const list = useMemo(() => {
    const w = q.trim().toLowerCase();
    return all.filter(
      (f) =>
        (tab === "all" || f.kind === tab) &&
        (!w || f.text.toLowerCase().includes(w) || f.meeting.toLowerCase().includes(w) || (f.part ?? "").toLowerCase().includes(w)),
    );
  }, [all, tab, q]);
  const older = lib.rows.filter((r) => !r.findings).length;
  const openCount = count("gap") + all.filter((f) => (f.kind === "conflict" && !f.settled) || (f.kind === "bug" && !f.fixed)).length;

  return (
    <div className="wrap">
      <div className="head">
        <div>
          <h1 className="h-page">Findings</h1>
          <p className="sub">Everything Muse Spark caught across your meetings. Open one to play the moment it happened.</p>
        </div>
        <button className="mc-btn mc-btn-outline" onClick={() => exportOpen(all)} disabled={!openCount}>
          <Download strokeWidth={1.75} aria-hidden />
          Export open questions
        </button>
      </div>

      <div className="grid4">
        <div className="mc-card mc-stat">
          <div className="n" style={{ color: "var(--gap)" }}>
            {count("gap")}
          </div>
          <div className="l">Gaps</div>
          <div className="hint">Branches nobody covered</div>
        </div>
        <div className="mc-card mc-stat">
          <div className="n" style={{ color: "var(--conflict)" }}>
            {count("conflict")}
          </div>
          <div className="l">Conflicts</div>
          <div className="hint">Two quotes that disagree</div>
        </div>
        <div className="mc-card mc-stat">
          <div className="n" style={{ color: "var(--bug)" }}>
            {count("bug")}
          </div>
          <div className="l">Bugs</div>
          <div className="hint">Fail paths people named</div>
        </div>
        <div className="mc-card mc-stat">
          <div className="n" style={{ color: "var(--ok)" }}>
            {count("answered")}
          </div>
          <div className="l">Answered</div>
          <div className="hint">Gaps closed later in the meeting</div>
        </div>
      </div>

      <div className="toolbar">
        <Seg
          label="Show"
          value={tab}
          onChange={setTab}
          options={[
            { id: "all", label: "All" },
            { id: "gap", label: "Gaps" },
            { id: "conflict", label: "Conflicts" },
            { id: "bug", label: "Bugs" },
            { id: "answered", label: "Answered" },
          ]}
        />
        <span className="grow" />
        <label className="search narrow">
          <Search size={16} strokeWidth={1.75} aria-hidden />
          <input placeholder="Search findings" aria-label="Search findings" value={q} onChange={(e) => setQ(e.target.value)} />
        </label>
      </div>

      <section className="mc-card list-card tight">
        {!lib.loading && list.length === 0 ? (
          <Empty title={all.length ? "Nothing matches" : "No findings yet"}>
            <p>
              {all.length
                ? "Try another tab or word."
                : "When Muse Spark finds a gap, a conflict or a bug in one of your meetings, it shows up here with the moment it happened."}
            </p>
          </Empty>
        ) : (
          list.map((f) => (
            <a key={`${f.rid}:${f.id}`} className="frow finding" href={replayHref(f.rid, { t: f.t, f: f.id })}>
              <span>
                <FindingTag kind={f.kind} />
              </span>
              <span style={{ minWidth: 0 }}>
                <span className="t">{f.text}</span>
                <span className="s">
                  {f.meeting}
                  {f.part ? ` · ${f.part}` : ""}
                  {f.kind === "answered" && f.answer ? ` · Answered: ${f.answer}` : ""}
                  {f.kind === "bug" && f.fixed ? " · Fixed later in the meeting" : ""}
                  {f.kind === "conflict" && f.settled ? ` · Settled${f.answer ? `: ${f.answer}` : ""}` : ""}
                </span>
              </span>
              <span className="mc-time">{fmtClock(f.t)}</span>
              <span className="mc-btn mc-btn-outline mc-btn-sm">
                <Play strokeWidth={1.75} aria-hidden />
                Play
              </span>
            </a>
          ))
        )}
      </section>
      {older > 0 && (
        <p className="hint" style={{ margin: 0 }}>
          {older === 1 ? "1 meeting was" : `${older} meetings were`} recorded before findings were kept per meeting; open them from Meetings to see their
          Flow Map.
        </p>
      )}
    </div>
  );
}
