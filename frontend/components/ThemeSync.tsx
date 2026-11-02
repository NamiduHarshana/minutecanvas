"use client";
import { useTheme } from "@/core/theme";

/** Keeps <html data-theme> in step with the OS while the visitor's choice is "Match my device". */
export function ThemeSync() {
  useTheme();
  return null;
}
