// PNG / PDF export with html-to-image + jsPDF.
// Images are blob: URLs (same origin), so they embed fine. JPEG inside the PDF keeps it small.
import { getFontEmbedCSS, toJpeg, toPng } from "html-to-image";

let fontCss: Promise<string> | null = null;
function fonts(el: HTMLElement) {
  if (!fontCss) fontCss = getFontEmbedCSS(el).catch(() => "");
  return fontCss;
}

export function nextFrames(n = 2): Promise<void> {
  return new Promise((resolve) => {
    const step = (k: number) => (k <= 0 ? resolve() : requestAnimationFrame(() => step(k - 1)));
    step(n);
  });
}

/** Wait for every <img> inside el to finish loading (or fail). */
export async function imagesSettled(el: HTMLElement, timeoutMs = 15000) {
  const imgs = Array.from(el.querySelectorAll("img"));
  await Promise.race([
    Promise.all(imgs.map((img) => (img.complete ? Promise.resolve() : new Promise((r) => ((img.onload = r), (img.onerror = r)))))),
    new Promise((r) => setTimeout(r, timeoutMs)),
  ]);
}

function download(dataUrl: string, filename: string) {
  const a = document.createElement("a");
  a.href = dataUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export function safeName(s: string | null | undefined) {
  return (s || "meeting").replace(/[^\w\- ]+/g, "").trim().replace(/\s+/g, "-").slice(0, 60) || "meeting";
}

export async function exportPng(el: HTMLElement, filename: string) {
  await imagesSettled(el);
  const url = await toPng(el, { pixelRatio: 2, backgroundColor: "#ffffff", fontEmbedCSS: await fonts(el) });
  download(url, filename);
}

/** One tall page sized to the element. */
export async function exportSinglePdf(el: HTMLElement, filename: string) {
  await imagesSettled(el);
  const { jsPDF } = await import("jspdf");
  const w = el.offsetWidth;
  const h = el.offsetHeight;
  const jpg = await toJpeg(el, { pixelRatio: 2, quality: 0.9, backgroundColor: "#ffffff", fontEmbedCSS: await fonts(el) });
  const pdf = new jsPDF({ orientation: w > h ? "landscape" : "portrait", unit: "px", format: [w, h], hotfixes: ["px_scaling"] });
  pdf.addImage(jpg, "JPEG", 0, 0, w, h);
  pdf.save(filename);
}

/** One slide per page (16:9). */
export async function exportSlidesPdf(slides: HTMLElement[], filename: string, onProgress?: (i: number) => void) {
  if (!slides.length) return;
  const { jsPDF } = await import("jspdf");
  const w = slides[0].offsetWidth;
  const h = slides[0].offsetHeight;
  const pdf = new jsPDF({ orientation: "landscape", unit: "px", format: [w, h], hotfixes: ["px_scaling"] });
  const css = await fonts(slides[0]);
  for (let i = 0; i < slides.length; i++) {
    onProgress?.(i);
    await imagesSettled(slides[i]);
    const jpg = await toJpeg(slides[i], { pixelRatio: 1.5, quality: 0.88, backgroundColor: "#ffffff", fontEmbedCSS: css });
    if (i > 0) pdf.addPage([w, h], "landscape");
    pdf.addImage(jpg, "JPEG", 0, 0, w, h);
  }
  pdf.save(filename);
}
