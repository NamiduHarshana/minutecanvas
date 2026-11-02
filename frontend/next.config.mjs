/** @type {import('next').NextConfig} */

// Backend origin for the Content-Security-Policy (same value the app is built with).
const backend = (process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000").replace(/\/+$/, "");
const backendWs = backend.replace(/^http/, "ws");

const GIS = "https://accounts.google.com/gsi/";           // Google sign-in (My meetings page only)
const YT = "https://www.youtube-nocookie.com";            // demo video, loaded only after a click

const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline' ${GIS}client`,     // Next.js inline bootstrap scripts + Google sign-in
  `style-src 'self' 'unsafe-inline' ${GIS}style`,
  "img-src 'self' blob: data:",
  "font-src 'self' data:",
  `connect-src 'self' ${backend} ${backendWs} ${GIS} blob: data:`,
  `frame-src ${GIS} ${YT}`,
  "media-src 'self' blob:",
  "worker-src 'self' blob:",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "object-src 'none'",
].join("; ");

const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  async headers() {
    const headers = [
      { key: "X-Content-Type-Options", value: "nosniff" },
      // Google sign-in and YouTube embeds need the origin; cross-origin requests get only the origin, never the
      // path, so a replay link's id never reaches another site
      { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
      { key: "X-Frame-Options", value: "DENY" },
      // mic + tab capture only on our own page
      { key: "Permissions-Policy", value: "microphone=(self), display-capture=(self), camera=()" },
    ];
    // dev mode needs eval for hot reload, so the CSP is production-only
    if (process.env.NODE_ENV === "production") headers.push({ key: "Content-Security-Policy", value: csp });
    return [{ source: "/:path*", headers }];
  },
};
export default nextConfig;
