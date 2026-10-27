"use client";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, ChevronLeft, ChevronRight, FileDown, Plus } from "lucide-react";
import type { Report as ReportT } from "@/core/types";
import { preloadImages } from "@/core/images";
import { exportSlidesPdf, nextFrames, safeName } from "@/core/export";
import { SlideView, allCards, buildSlides } from "./Slides";

const W = 1280;
const H = 720;

export function Report({ report, onBack, onNew }: { report: ReportT; onBack: () => void; onNew: () => void }) {
  const slides = useMemo(() => buildSlides(report), [report]);
  const [i, setI] = useState(0);
  const [scale, setScale] = useState(0.5);
  const [exporting, setExporting] = useState<string | null>(null);
  const stage = useRef<HTMLDivElement>(null);
  const exportBox = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const el = stage.current;
    if (!el) return;
    const fit = () => {
      const cs = getComputedStyle(el);
      const w = el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
      const h = el.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
      setScale(Math.max(0.1, Math.min(w / W, h / H)));
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => setI((x) => Math.min(x, slides.length - 1)), [slides.length]);

  const go = useCallback((d: number) => setI((x) => Math.max(0, Math.min(slides.length - 1, x + d))), [slides.length]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === "INPUT") return;
      if (e.key === "ArrowLeft") go(-1);
      if (e.key === "ArrowRight" || e.key === " ") {
        e.preventDefault();
        go(1);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go]);

  // warm the image cache so slides and the PDF show illustrations at once
  useEffect(() => {
    void preloadImages([report.cover_image_url, ...allCards(report).map((c) => c.image_url)]);
  }, [report]);

  async function exportPdf() {
    setExporting("Preparing…");
    try {
      await preloadImages([report.cover_image_url, ...allCards(report).map((c) => c.image_url)]);
      await nextFrames(3);
      const els = Array.from(exportBox.current?.querySelectorAll<HTMLElement>(".slide") || []);
      await exportSlidesPdf(els, `${safeName(report.title)}-report.pdf`, (k) => setExporting(`Slide ${k + 1} / ${els.length}`));
    } catch (e) {
      console.error(e);
      alert("Export failed. Please try again.");
    } finally {
      setExporting(null);
    }
  }

  return (
    <div className="report">
      <header className="report-bar">
        <button className="btn" onClick={onBack}>
          <ArrowLeft size={15} aria-hidden /> Board
        </button>
        <h1 className="tb-title">{report.title}</h1>
        <div className="tb-right">
          <button className="btn btn-primary" onClick={exportPdf} disabled={!!exporting}>
            <FileDown size={15} aria-hidden /> {exporting || "Export PDF"}
          </button>
          <button className="btn" onClick={onNew}>
            <Plus size={15} aria-hidden /> New meeting
          </button>
        </div>
      </header>

      <div className="stage" ref={stage}>
        <div className="slide-frame" style={{ width: W * scale, height: H * scale }}>
          <div key={i} className="slide-anim" style={{ transform: `scale(${scale})`, transformOrigin: "0 0", width: W, height: H }}>
            <SlideView slide={slides[i]} report={report} />
          </div>
        </div>
      </div>

      <nav className="slide-nav">
        <button className="icon-btn big" onClick={() => go(-1)} disabled={i === 0} aria-label="Previous slide">
          <ChevronLeft size={22} aria-hidden />
        </button>
        <div className="dots">
          {slides.map((_, k) => (
            <button key={k} className={k === i ? "on" : ""} onClick={() => setI(k)} aria-label={`Slide ${k + 1}`} />
          ))}
        </div>
        <span className="slide-count">
          {i + 1} / {slides.length}
        </span>
        <button className="icon-btn big" onClick={() => go(1)} disabled={i === slides.length - 1} aria-label="Next slide">
          <ChevronRight size={22} aria-hidden />
        </button>
      </nav>

      {exporting && (
        <div className="offscreen" aria-hidden>
          <div ref={exportBox}>
            {slides.map((s, k) => (
              <SlideView key={k} slide={s} report={report} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
