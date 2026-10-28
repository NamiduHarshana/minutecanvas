"use client";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { CalendarDays, Check, CircleCheckBig, Clock, ListChecks, Users } from "lucide-react";
import type { Card, Column, Report } from "@/core/types";
import { COLUMNS, COLUMN_META } from "@/core/types";
import { useImage } from "@/core/images";
import { fmtTime } from "@/core/store";
import { ColumnIcon, Illus, StatusBadge } from "./ui";
import { Logo } from "./Logo";

const GRID_PER_PAGE = 6;
const ROWS_PER_PAGE = 6;
const OWNERS_PER_PAGE = 4;
const TASKS_PER_OWNER = 6;

type GridSection = { column: Column; title: string; cards: Card[] };
export type Slide =
  | { kind: "cover" }
  | { kind: "takeaways" }
  | { kind: "timeline" }
  | { kind: "grid"; section: GridSection; items: Card[]; page: number; pages: number }
  | { kind: "next"; groups: OwnerGroup[]; page: number; pages: number }
  | { kind: "answered"; items: Card[]; page: number; pages: number };

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

/** Cover → Key takeaways → Timeline (3+ items) → Goals → Team updates → Decisions → Next steps → Open questions → Answered. Empty sections are skipped. */
export function buildSlides(r: Report): Slide[] {
  const out: Slide[] = [{ kind: "cover" }, { kind: "takeaways" }];
  if (allCards(r).length >= 3) out.push({ kind: "timeline" }); // 1–2 dots on an empty chart says nothing
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

  grid("questions", "Open questions", r.open_questions);
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
    case "timeline":
      return <TimelineSlide report={report} />;
    case "grid":
      return <GridSlide slide={slide} />;
    case "next":
      return <NextStepsSlide slide={slide} />;
    case "answered":
      return <AnsweredSlide slide={slide} />;
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
  const stats: [Column, string, number][] = [
    ["goals", "Goals", report.goals.length],
    ["decisions", "Decisions", report.decisions.length],
    ["actions", "Action items", report.actions.length],
    ["updates", "Team updates", report.updates.length],
    ["questions", answered ? `Open · ${answered} answered` : "Open questions", report.open_questions.length],
  ];
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
            <div key={col} className="stat" style={{ "--col": COLUMN_META[col].color, "--soft": COLUMN_META[col].soft } as React.CSSProperties}>
              <span className="stat-icon">
                <ColumnIcon column={col} size={18} />
              </span>
              <b>{n}</b>
              <span>{label}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- timeline
const LANE_H = 92;
const TRACK_W = 940;
const DOT = 44;

function niceStep(total: number) {
  for (const s of [15, 30, 60, 120, 300, 600, 900, 1800, 3600]) if (total / s <= 8) return s;
  return 3600;
}

function TimelineSlide({ report }: { report: Report }) {
  const cards = allCards(report);
  const end = Math.max(1, report.duration_s, ...cards.map((c) => Math.max(c.t, c.updated_t ?? 0)));
  const x = (t: number) => (Math.min(Math.max(t, 0), end) / end) * TRACK_W;
  const step = niceStep(end);
  const ticks: number[] = [];
  for (let t = 0; t <= end + 0.01; t += step) ticks.push(t);

  return (
    <div className="slide">
      <SlideHead title="Meeting timeline" sub={`When each item came up · ${fmtTime(report.duration_s)} total`} />
      <div className="tl">
        {COLUMNS.map((col) => {
          const lane = cards.filter((c) => c.column === col).sort((a, b) => a.t - b.t);
          const m = COLUMN_META[col];
          // two sub-rows so close items don't cover each other
          let lastTop = -1e9;
          let lastBottom = -1e9;
          const placed = lane.map((c) => {
            const px = x(c.t);
            const gapTop = px - lastTop;
            const gapBot = px - lastBottom;
            const row = gapTop >= DOT + 4 ? 0 : gapBot >= DOT + 4 ? 1 : gapBot > gapTop ? 1 : 0;
            if (row) lastBottom = px;
            else lastTop = px;
            return { c, px, row };
          });
          return (
            <div key={col} className="tl-lane" style={{ "--col": m.color, "--soft": m.soft, height: LANE_H } as React.CSSProperties}>
              <div className="tl-label">
                <span className="col-icon">
                  <ColumnIcon column={col} size={15} />
                </span>
                {m.label}
                <span className="tl-count">{lane.length}</span>
              </div>
              <div className="tl-track" style={{ width: TRACK_W }}>
                <span className="tl-line" />
                {placed
                  .filter(({ c }) => c.column === "questions" && c.resolved && c.updated_t != null && c.updated_t > c.t)
                  .map(({ c }, k) => (
                    <span
                      key={`bar-${c.id}`}
                      className="tl-answer"
                      style={{ left: x(c.t), width: Math.max(4, x(c.updated_t!) - x(c.t)), top: LANE_H / 2 - 3 + ((k % 3) - 1) * 9 }}
                    >
                      <CircleCheckBig size={14} aria-hidden />
                    </span>
                  ))}
                {placed.map(({ c, px, row }) => (
                  <div
                    key={c.id}
                    className="tl-dot"
                    style={{ left: px - DOT / 2, top: row ? LANE_H / 2 + 1 : LANE_H / 2 - DOT - 1 }}
                    title={`${fmtTime(c.t)} · ${c.title}`}
                  >
                    <Illus card={c} className="tl-illus" eager />
                  </div>
                ))}
              </div>
            </div>
          );
        })}
        <div className="tl-axis" style={{ width: TRACK_W }}>
          {ticks.map((t) => (
            <span key={t} style={{ left: x(t) }}>
              {fmtTime(t)}
            </span>
          ))}
        </div>
      </div>
      <p className="tl-note">
        <CircleCheckBig size={14} aria-hidden /> Green bar = a question that was answered later in the meeting
      </p>
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
