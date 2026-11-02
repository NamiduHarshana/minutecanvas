// Theme: dark by default, follows the visitor's OS until they pick one in Account > Appearance (kept in this browser).
"use client";
import { useEffect, useState } from "react";

export type ThemePref = "system" | "dark" | "light";
const KEY = "minutecanvas.theme";
const EVENT = "minutecanvas-theme";

/** Runs in <head> before the first paint (inline script in the root layout), so there is no flash of the wrong theme. */
export const THEME_BOOT = `(function(){try{var p=localStorage.getItem("${KEY}");var l=p==="light"||(p!=="dark"&&window.matchMedia("(prefers-color-scheme: light)").matches);document.documentElement.setAttribute("data-theme",l?"light":"dark")}catch(e){document.documentElement.setAttribute("data-theme","dark")}})();`;

export function themePref(): ThemePref {
  try {
    const v = localStorage.getItem(KEY);
    return v === "dark" || v === "light" ? v : "system";
  } catch {
    return "system";
  }
}

function resolve(p: ThemePref): "dark" | "light" {
  if (p !== "system") return p;
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function applyTheme(p: ThemePref = themePref()) {
  document.documentElement.setAttribute("data-theme", resolve(p));
}

export function setThemePref(p: ThemePref) {
  try {
    if (p === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, p);
  } catch {
    /* private mode: this page only */
  }
  applyTheme(p);
  window.dispatchEvent(new Event(EVENT));
}

/** The visitor's choice, and the theme in use now. Follows OS changes while the choice is "system". */
export function useTheme(): { pref: ThemePref; theme: "dark" | "light"; set: (p: ThemePref) => void } {
  const [pref, setPref] = useState<ThemePref>("system");
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  useEffect(() => {
    const sync = () => {
      const p = themePref();
      setPref(p);
      setTheme(resolve(p));
      applyTheme(p);
    };
    sync();
    const mq = window.matchMedia?.("(prefers-color-scheme: light)");
    mq?.addEventListener?.("change", sync);
    window.addEventListener(EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      mq?.removeEventListener?.("change", sync);
      window.removeEventListener(EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  return { pref, theme, set: setThemePref };
}
