// Card images need the session token, so they are fetched as blobs and shown via blob: URLs
// (blob URLs also make PNG/PDF export work — no cross-origin images in the DOM).
"use client";
import { useEffect, useState } from "react";

type Fetcher = (path: string) => Promise<Blob>;

let fetcher: Fetcher | null = null;
const cache = new Map<string, Promise<string>>();
const ready = new Map<string, string>();

export function setImageFetcher(f: Fetcher | null) {
  fetcher = f;
  if (!f) {
    ready.forEach((u) => URL.revokeObjectURL(u));
    ready.clear();
    cache.clear();
  }
}

export function loadImage(path: string): Promise<string> {
  let p = cache.get(path);
  if (!p) {
    if (!fetcher) return Promise.reject(new Error("no session"));
    const f = fetcher;
    p = (async () => {
      let lastErr: unknown;
      for (let i = 0; i < 3; i++) {
        try {
          const url = URL.createObjectURL(await f(path));
          ready.set(path, url);
          return url;
        } catch (e) {
          lastErr = e;
          await new Promise((r) => setTimeout(r, 800 * (i + 1)));
        }
      }
      cache.delete(path); // allow a later retry
      throw lastErr;
    })();
    cache.set(path, p);
  }
  return p;
}

/** Wait until every given image is loaded (used before export). */
export async function preloadImages(paths: (string | null | undefined)[]) {
  await Promise.allSettled(paths.filter((p): p is string => !!p).map(loadImage));
}

export function useImage(path: string | null | undefined): { url: string | null; failed: boolean } {
  const [state, setState] = useState<{ path: string | null; url: string | null; failed: boolean }>(() => ({
    path: path ?? null,
    url: path ? ready.get(path) ?? null : null,
    failed: false,
  }));
  useEffect(() => {
    if (!path) return;
    const known = ready.get(path);
    if (known) {
      setState({ path, url: known, failed: false });
      return;
    }
    let alive = true;
    loadImage(path).then(
      (url) => alive && setState({ path, url, failed: false }),
      () => alive && setState({ path, url: null, failed: true }),
    );
    return () => {
      alive = false;
    };
  }, [path]);
  if (!path) return { url: null, failed: false };
  if (state.path !== path) return { url: ready.get(path) ?? null, failed: false };
  return { url: state.url, failed: state.failed };
}
