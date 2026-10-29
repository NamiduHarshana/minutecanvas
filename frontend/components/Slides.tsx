"use client";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Bug, CalendarDays, Check, CircleCheckBig, CircleHelp, Clock, GitBranch, ListChecks, ShieldCheck, TriangleAlert, Users } from "lucide-react";
import type { Card, Column, Evidence, FlowBug, FlowContradiction, FlowGap, Report } from "@/core/types";
import { COLUMN_META } from "@/core/types";
import { useImage } from "@/core/images";
import { fmtTime } from "@/core/store";
import { ColumnIcon, Illus, StatusBadge } from "./ui";
import { Logo } from "./Logo";
import { FlowDiagram } from "./FlowDiagram";
import { layoutFlow } from "@/core/flowLayout";

const FLOW_BOX = { w: 1120, h: 440 };

const GRID_PER_PAGE = 6;
const ROWS_PER_PAGE = 6;
const OWNERS_PER_PAGE = 4;
const TASKS_PER_OWNER = 6;

type GridSection = { column: Column; title: string; cards: Card[] };
export type Slide =
  | { kind: "cover" }
  | { kind: "takeaways" }
  | { kind: "grid"; section: GridSection; items: Card[]; page: number; pages: number }
  | { kind: "next"; groups: OwnerGroup[]; page: number; pages: number }
  | { kind: "answered"; items: Card[]; page: number; pages: number }
  | { kind: "flow" }
  | { kind: "gaps"; gaps: FlowGap[]; bugs: FlowBug[]; conflicts: FlowContradiction[]; page: number; pages: number };

const GAPS_PER_PAGE = 4;
const CONFLICTS_PER_PAGE = 2;

/** Open Flow Map gaps (their question cards are shown on the gaps slide, not in Open questions). */
export function openGaps(r: Report): FlowGap[] {
  return (r.flow?.gaps || []).filter((g) => !g.resolved && !g.dismissed);
}

interface OwnerGroup {
  owner: string;
  tasks: Card[];
  cont: boolean; // continued from the previous column
}

function chunk<T>(a: T[], n: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < a.length; i += n) out.push(a.slice(i, i + n));
  return out;
}

export function allCards(r: Report): Card[] {
  return [...r.goals, ...r.decisions, ...r.actions, ...r.updates, ...r.open_questions, ...(r.answered_questions || [])];
}

/** Cover → Key takeaways → Flow map → Gaps & conflicts → Goals → Team updates → Decisions → Next steps → Open questions → Answered. Empty sections are skipped. */
export function buildSlides(r: Report): Slide[] {
  const out: Slide[] = [{ kind: "cover" }, { kind: "takeaways" }];
  const flow = r.flow;
  if (flow && flow.steps.length >= 2) out.push({ kind: "flow" });
  if (flow) {
    const gaps = openGaps(r);
    const bugs = flow.bugs.filter((b) => !b.fixed);
    const conflicts = flow.contradictions.filter((c) => !c.resolved);
    const left = [...gaps.map((g) => ({ g })), ...bugs.map((b) => ({ b }))];
    const pages = Math.max(Math.ceil(left.length / GAPS_PER_PAGE), Math.ceil(conflicts.length / CONFLICTS_PER_PAGE));
    for (let k = 0; k < pages; k++) {
      const l = left.slice(k * GAPS_PER_PAGE, (k + 1) * GAPS_PER_PAGE);
      out.push({
        kind: "gaps",
        gaps: l.flatMap((x) => ("g" in x ? [x.g] : [])),
        bugs: l.flatMap((x) => ("b" in x ? [x.b] : [])),
        conflicts: conflicts.slice(k * CONFLICTS_PER_PAGE, (k + 1) * CONFLICTS_PER_PAGE),
        page: k + 1,
        pages,
      });
    }
  }
  const grid = (column: Column, title: string, cards: Card[]) => {
    // balanced pages: 8 cards → 4 + 4, not 6 + 2
    const n = Math.ceil(cards.length / GRID_PER_PAGE);
    const pages = chunk(cards, Math.ceil(cards.length / Math.max(1, n)));
    pages.forEach((items, k) => out.push({ kind: "grid", section: { column, title, cards }, items, page: k + 1, pages: pages.length }));
  };
  grid("goals", "Goals", r.goals);
  grid("updates", "Team updates", r.updates);
  grid("decisions", "Decisions", r.decisions);

  // Next steps: actions grouped by owner (Unassigned last), long lists split into extra columns
  const byOwner = new Map<string, Card[]>();
  for (const a of r.actions) {
    const k = a.owner?.trim() || "Unassigned";
    byOwner.set(k, [...(byOwner.get(k) || []), a]);
  }
  const owners = [...byOwner.entries()].sort((x, y) => (x[0] === "Unassigned" ? 1 : y[0] === "Unassigned" ? -1 : y[1].length - x[1].length));
  const cols: OwnerGroup[] = owners.flatMap(([owner, tasks]) => chunk(tasks, TASKS_PER_OWNER).map((t, k) => ({ owner, tasks: t, cont: k > 0 })));
  // balanced pages (5 columns → 3 + 2, not 4 + 1)
  const nPages = Math.ceil(cols.length / OWNERS_PER_PAGE);
  const nextPages = chunk(cols, Math.ceil(cols.length / Math.max(1, nPages)));
  nextPages.forEach((groups, k) => out.push({ kind: "next", groups, page: k + 1, pages: nextPages.length }));

  const gapCards = new Set(openGaps(r).map((g) => g.card_id));
  grid("questions", "Open questions", r.open_questions.filter((q) => !gapCards.has(q.id)));
  const answered = chunk(r.answered_questions || [], ROWS_PER_PAGE);
  answered.forEach((items, k) => out.push({ kind: "answered", items, page: k + 1, pages: answered.length }));
  return out;
}

export function fmtDuration(s: number) {
  const m = Math.round(s / 60);
  if (m < 1) return `${Math.max(0, Math.round(s))} sec`;
  return m >= 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min` : `${m} min`;
}
function fmtDate(iso: string) {
  try {
    return new Date(iso).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
  } catch {
    return "";
  }
}

export function SlideView({ slide, report }: { slide: Slide; report: Report }) {
  switch (slide.kind) {
    case "cover":
      return <CoverSlide report={report} />;
    case "takeaways":
      return <TakeawaysSlide report={report} />;
    case "grid":
      return <GridSlide slide={slide} />;
    case "next":
      return <NextStepsSlide slide={slide} />;
    case "answered":
      return <AnsweredSlide slide={slide} />;
    case "flow":
      return <FlowSlide report={report} />;
    case "gaps":
      return <GapsSlide slide={slide} report={report} />;
  }
}

// ---------------------------------------------------------------- cover
function CoverSlide({ report }: { report: Report }) {
  const { url } = useImage(report.cover_image_url);
  const names = Object.values(report.speakers);
  const items = allCards(report).length;
  return (
    <div className="slide cover">
      <div className="cover-text">
        <Logo />
        <h1>{report.title}</h1>
        <p className="cover-facts">
          <span>
            <CalendarDays size={18} aria-hidden /> {fmtDate(report.date)}
          </span>
          <span>
            <Clock size={18} aria-hidden /> {fmtDuration(report.duration_s)}
          </span>
          {names.length > 0 && (
            <span>
              <Users size={18} aria-hidden /> {names.length} {names.length === 1 ? "speaker" : "speakers"}
            </span>
          )}
        </p>
        <p className="cover-sub">
          {items} {items === 1 ? "item" : "items"} drawn live while the meeting happened
        </p>
      </div>
      <div className="cover-art">
        {url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={url} alt="" />
        ) : (
          <div className="cover-art-empty">
            <Logo />
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- takeaways
const TAKE_FS = [27, 24, 21, 19, 17];

function TakeawaysSlide({ report }: { report: Report }) {
  const take = report.takeaways || [];
  // step the text size down until the takeaways + summary fit the slide (never cut off)
  const mainRef = useRef<HTMLDivElement>(null);
  const [fs, setFs] = useState(0);
  const [pass, setPass] = useState(0);
  const textKey = take.join("\n") + "\n" + (report.summary || "");
  useLayoutEffect(() => setFs(0), [textKey]);
  useLayoutEffect(() => {
    const el = mainRef.current;
    if (el && el.scrollHeight > el.clientHeight + 1 && fs < TAKE_FS.length - 1) setFs(fs + 1);
  }, [fs, textKey, pass]);
  useEffect(() => {
    let alive = true;
    // re-measure once web fonts are in (they change line breaks)
    document.fonts?.ready.then(() => {
      if (!alive) return;
      setFs(0);
      setPass((p) => p + 1);
    });
    return () => {
      alive = false;
    };
  }, []);
  const answered = report.answered_questions?.length || 0;
  const gaps = openGaps(report);
  const gapCards = new Set(gaps.map((g) => g.card_id));
  const stats: [Column | "gaps", string, number][] = [
    ["goals", "Goals", report.goals.length],
    ["decisions", "Decisions", report.decisions.length],
    ["actions", "Action items", report.actions.length],
    ["updates", "Team updates", report.updates.length],
    ["questions", answered ? `Open · ${answered} answered` : "Open questions", report.open_questions.filter((q) => !gapCards.has(q.id)).length],
  ];
  if (report.flow?.steps.length) stats.push(["gaps", "Gaps nobody covered", gaps.length]);
  return (
    <div className="slide">
      <SlideHead title="Key takeaways" />
      <div className="take-body">
        <div className="take-main" ref={mainRef} style={{ "--take-fs": `${TAKE_FS[fs]}px` } as React.CSSProperties}>
          {take.length ? (
            <>
              <ol className="take-list">
                {take.map((t, k) => (
                  <li key={k}>
                    <span className="take-num">{k + 1}</span>
                    <span>{t}</span>
                  </li>
                ))}
              </ol>
              {report.summary && <p className="take-summary">{report.summary}</p>}
            </>
          ) : (
            <p className="summary-text">{report.summary || "No summary available."}</p>
          )}
        </div>
        <div className="take-stats">
          {stats.map(([col, label, n]) => (
            <div
              key={col}
              className="stat"
              style={(col === "gaps" ? { "--col": "#b45309", "--soft": "#fff7e8" } : { "--col": COLUMN_META[col].color, "--soft": COLUMN_META[col].soft }) as React.CSSProperties}
            >
              <span className="stat-icon">{col === "gaps" ? <GitBranch size={18} aria-hidden /> : <ColumnIcon column={col} size={18} />}</span>
              <b>{n}</b>
              <span>{label}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- card grids
function GridSlide({ slide }: { slide: Extract<Slide, { kind: "grid" }> }) {
  const { section, items, page, pages } = slide;
  const m = COLUMN_META[section.column];
  const n = items.length;
  const layout = n === 1 ? "g1" : n === 2 ? "g2" : n === 3 ? "g3" : n === 4 ? "g4" : "g6";
  return (
    <div className="slide" style={{ "--col": m.color, "--soft": m.soft } as React.CSSProperties}>
      <SlideHead
        title={section.title}
        column={section.column}
        page={pages > 1 ? `${page} / ${pages}` : undefined}
        sub={`${section.cards.length} ${section.cards.length === 1 ? "item" : "items"}`}
      />
      <div className={`sgrid ${layout}`}>
        {items.map((c) => (
          <article key={c.id} className="scard">
            <Illus card={c} className="scard-illus" eager />
            <div className="scard-text">
              {c.column === "updates" && c.status && <StatusBadge status={c.status} />}
              <h3>{c.title}</h3>
              {c.detail && <p>{c.detail}</p>}
              <div className="scard-meta">
                {c.owner && <span>{c.owner}</span>}
                {c.due && <span>Due {c.due}</span>}
                <span className="scard-time">{fmtTime(c.t)}</span>
              </div>
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- next steps
const AVATAR = ["#4f46e5", "#0e7490", "#b45309", "#be185d", "#15803d", "#7c3aed", "#c2410c"];
function avatarColor(name: string) {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return AVATAR[h % AVATAR.length];
}
function initials(name: string) {
  const parts = name.replace(/^Speaker\s+/i, "").split(/\s+/).filter(Boolean);
  return (parts.length > 1 ? parts[0][0] + parts[1][0] : (parts[0] || "?").slice(0, 2)).toUpperCase();
}

function NextStepsSlide({ slide }: { slide: Extract<Slide, { kind: "next" }> }) {
  const m = COLUMN_META.actions;
  return (
    <div className="slide" style={{ "--col": m.color, "--soft": m.soft } as React.CSSProperties}>
      <SlideHead title="Next steps" column="actions" page={slide.pages > 1 ? `${slide.page} / ${slide.pages}` : undefined} sub="Who does what" />
      <div className="next-cols" style={{ gridTemplateColumns: `repeat(${Math.max(slide.groups.length, 2)}, minmax(0, 1fr))` }}>
        {slide.groups.map((g, k) => {
          const un = g.owner === "Unassigned";
          return (
            <section key={k} className={`owner-col ${un ? "unassigned" : ""}`}>
              <header>
                <span className="avatar" style={{ background: un ? "#98a2b3" : avatarColor(g.owner) }}>
                  {un ? "?" : initials(g.owner)}
                </span>
                <div>
                  <b>{g.owner}</b>
                  <span>
                    {g.cont ? "continued" : `${g.tasks.length} ${g.tasks.length === 1 ? "task" : "tasks"}`}
                    {un && !g.cont ? " · needs an owner" : ""}
                  </span>
                </div>
              </header>
              <ul>
                {g.tasks.map((t) => (
                  <li key={t.id} className={t.done ? "is-done" : ""}>
                    <span className={`check ${t.done ? "on" : ""}`}>{t.done && <Check size={12} strokeWidth={3} aria-hidden />}</span>
                    <div>
                      <h4>{t.title}</h4>
                      <span className={`due ${t.due ? "" : "none"}`}>{t.due ? `Due ${t.due}` : "No due date"}</span>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- answered questions
function AnsweredSlide({ slide }: { slide: Extract<Slide, { kind: "answered" }> }) {
  const m = COLUMN_META.decisions;
  return (
    <div className="slide" style={{ "--col": m.color, "--soft": m.soft } as React.CSSProperties}>
      <SlideHead
        title="Answered during the meeting"
        icon={<ListChecks size={24} aria-hidden />}
        page={slide.pages > 1 ? `${slide.page} / ${slide.pages}` : undefined}
      />
      <div className="ans-list">
        {slide.items.map((q) => (
          <div key={q.id} className="ans-row">
            <Illus card={q} className="ans-illus" eager />
            <div>
              <h3>{q.title}</h3>
              <p>
                <CircleCheckBig size={15} aria-hidden /> {q.answer || "Answered"}
              </p>
            </div>
            <span className="ans-time">
              {fmtTime(q.t)}
              {q.updated_t != null && ` → ${fmtTime(q.updated_t)}`}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- shared
function SlideHead({ title, column, page, sub, icon }: { title: string; column?: Column; page?: string; sub?: string; icon?: React.ReactNode }) {
  return (
    <div className="slide-head">
      {(column || icon) && <span className="slide-icon">{icon || <ColumnIcon column={column!} size={24} />}</span>}
      <div className="slide-titles">
        <h2>{title}</h2>
        {sub && <span className="slide-sub">{sub}</span>}
      </div>
      {page && <span className="slide-page">{page}</span>}
      <span className="slide-brand">
        <Logo small />
      </span>
    </div>
  );
}

// ---------------------------------------------------------------- flow map
function FlowSlide({ report }: { report: Report }) {
  const flow = report.flow!;
  const gaps = openGaps(report).length;
  const conflicts = flow.contradictions.filter((c) => !c.resolved).length;
  const bugs = flow.bugs.filter((b) => !b.fixed).length;
  // same layout call as FlowDiagram (box 1120 x 440): show the "↪" legend only when the map uses connectors
  const hasJumps = layoutFlow(flow, FLOW_BOX).nodes.some((n) => n.type === "step" && (n.jumps?.length ?? 0) > 0);
  const sub = [
    `${flow.steps.length} steps`,
    `${gaps} ${gaps === 1 ? "gap" : "gaps"}`,
    conflicts ? `${conflicts} ${conflicts === 1 ? "conflict" : "conflicts"}` : "",
    bugs ? `${bugs} open ${bugs === 1 ? "bug" : "bugs"}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <div className="slide" style={{ "--col": "#4f46e5", "--soft": "#eef0fe" } as React.CSSProperties}>
      <SlideHead title="Flow map" sub={sub} icon={<GitBranch size={24} aria-hidden />} />
      <div className="fslide-map">
        <FlowDiagram flow={flow} className="fslide-svg" box={FLOW_BOX} />
      </div>
      <div className="fslide-foot">
        <span className="fm-legend">
          <i className="lg-solid" /> discussed
          <i className="lg-dash" /> gap: nobody said
          <i className="lg-fail" /> fail path
          {hasJumps && (
            <>
              <b className="lg-jump">↪ 7</b> continues at step 7
            </>
          )}
        </span>
        <span className="fslide-check">
          <ShieldCheck size={15} aria-hidden /> Every box is backed by a quote checked against the transcript
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- gaps & conflicts
function SlideQuote({ ev, report, tone }: { ev: Evidence; report: Report; tone?: string }) {
  const who = report.speakers[ev.speaker] || `Speaker ${ev.speaker}`;
  return (
    <blockquote className={`gq ${tone ?? ""}`}>
      <p>“{ev.quote}”</p>
      <footer>
        {who} · {fmtTime(ev.t)}
      </footer>
    </blockquote>
  );
}

function GapsSlide({ slide, report }: { slide: Extract<Slide, { kind: "gaps" }>; report: Report }) {
  const step = (id: string | null) => report.flow?.steps.find((s) => s.id === id)?.label;
  return (
    <div className="slide" style={{ "--col": "#b45309", "--soft": "#fff7e8" } as React.CSSProperties}>
      <SlideHead
        title="What was missed"
        sub="Branches nobody covered and statements that conflict"
        icon={<CircleHelp size={24} aria-hidden />}
        page={slide.pages > 1 ? `${slide.page} / ${slide.pages}` : undefined}
      />
      <div className="gslide">
        <section>
          <h3 className="gs-head gap">
            <CircleHelp size={18} aria-hidden /> Nobody covered
          </h3>
          {slide.page === 1 && slide.gaps.length === 0 && slide.bugs.length === 0 && <p className="gs-none">No open gaps.</p>}
          {slide.gaps.map((g) => (
            <div key={g.id} className="gs-item gap">
              <b>{g.question}</b>
              {step(g.step) && <span className="gs-step">at “{step(g.step)}”</span>}
              {g.evidence[0] && <SlideQuote ev={g.evidence[0]} report={report} />}
            </div>
          ))}
          {slide.bugs.map((b) => (
            <div key={b.id} className="gs-item bug">
              <b>
                <Bug size={16} aria-hidden /> {b.text}
              </b>
              {step(b.step) && <span className="gs-step">on “{step(b.step)}”</span>}
              {b.evidence[0] && <SlideQuote ev={b.evidence[0]} report={report} tone="bug" />}
            </div>
          ))}
        </section>
        <section>
          <h3 className="gs-head conflict">
            <TriangleAlert size={18} aria-hidden /> Conflicting statements
          </h3>
          {/* later pages: an empty column just means everything fit on page 1 */}
          {slide.page === 1 && slide.conflicts.length === 0 && <p className="gs-none">No conflicting statements found.</p>}
          {slide.conflicts.map((c) => (
            <div key={c.id} className="gs-item conflict">
              <b>{c.note}</b>
              <div className="gs-vs">
                <SlideQuote ev={c.a} report={report} tone="conflict" />
                <span>vs</span>
                <SlideQuote ev={c.b} report={report} tone="conflict" />
              </div>
            </div>
          ))}
          {slide.conflicts.length > 0 && <p className="gs-none">MinuteCanvas shows both quotes. It never decides who is right.</p>}
        </section>
      </div>
    </div>
  );
}
