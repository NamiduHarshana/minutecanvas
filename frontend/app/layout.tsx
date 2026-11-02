import type { Metadata, Viewport } from "next";
import "@fontsource-variable/inter";
import "@fontsource-variable/archivo";
import "@fontsource-variable/jetbrains-mono";
import "@xyflow/react/dist/style.css";
import "./tokens.css";
import "./globals.css";
import { THEME_BOOT } from "@/core/theme";
import { ThemeSync } from "@/components/ThemeSync";

export const metadata: Metadata = {
  title: "MinuteCanvas — see what your meeting missed",
  description: "Listens to a live meeting, draws the flow people describe and circles what nobody covered. Built with Meta Muse models.",
  icons: { icon: "/icon.svg" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f6f6f8" },
    { media: "(prefers-color-scheme: dark)", color: "#0f1012" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // data-theme is set before the first paint by THEME_BOOT (dark by default)
    <html lang="en" data-theme="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body>
        <ThemeSync />
        {children}
      </body>
    </html>
  );
}
