// Public site settings, baked in at build time (frontend/.env.production on Cloud Run).
export const SITE = {
  // YouTube id of the demo video (e.g. "dQw4w9WgXcQ"). Empty: the landing page shows the demo replay instead.
  demoVideoId: (process.env.NEXT_PUBLIC_DEMO_VIDEO_ID || "").trim(),
  // replay id of the public demo meeting (also add it to the backend's PINNED_REPLAYS so it is kept)
  demoReplayId: (process.env.NEXT_PUBLIC_DEMO_REPLAY_ID || "").trim(),
  contactEmail: (process.env.NEXT_PUBLIC_CONTACT_EMAIL || "").trim(),
  github: "https://github.com/NamiduHarshana/minutecanvas",
  keepDays: 30,
};

export const demoReplayHref = () => (SITE.demoReplayId ? `/replay?id=${encodeURIComponent(SITE.demoReplayId)}` : "");
