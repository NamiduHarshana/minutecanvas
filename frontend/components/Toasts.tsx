"use client";
import { useCallback, useEffect, useState } from "react";
import { X } from "lucide-react";

type Toast = { id: number; text: string; kind: "info" | "error" };
let nextId = 1;

/** Small notices for the app pages (the live meeting has its own, driven by the meeting state). */
export function useToasts() {
  const [items, setItems] = useState<Toast[]>([]);
  const show = useCallback((text: string, kind: "info" | "error" = "info") => {
    setItems((t) => [...t.filter((x) => x.text !== text).slice(-2), { id: nextId++, text, kind }]);
  }, []);
  const dismiss = useCallback((id: number) => setItems((t) => t.filter((x) => x.id !== id)), []);
  useEffect(() => {
    if (!items.length) return;
    const t = items[0];
    const id = setTimeout(() => dismiss(t.id), t.kind === "error" ? 9000 : 5000);
    return () => clearTimeout(id);
  }, [items, dismiss]);
  return { items, show, dismiss };
}

export function Toasts({ items, dismiss }: { items: Toast[]; dismiss: (id: number) => void }) {
  return (
    <div className="toasts" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className={`toast ${t.kind}`}>
          <span>{t.text}</span>
          <button className="toast-x" onClick={() => dismiss(t.id)} aria-label="Dismiss">
            <X size={14} aria-hidden />
          </button>
        </div>
      ))}
    </div>
  );
}
