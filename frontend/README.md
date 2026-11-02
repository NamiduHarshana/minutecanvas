# MinuteCanvas frontend

Next.js (App Router, TypeScript). Talks to the backend over HTTPS + WebSocket (protocol: `../backend/README.md`).

## Run locally

```bash
cd frontend
npm install
cp .env.example .env.local        # NEXT_PUBLIC_BACKEND_URL=http://localhost:8000
npm run dev                        # http://localhost:3000
```
Backend in another terminal with `MOCK_META=1` in `backend/.env` = fake transcript/cards/images, 0 credits.
Use **Chrome**. `Mic + Tab` needs Chrome's “Also share tab audio”.

## Deploy (Cloud Run, us-central1)

1. Deploy the backend first and copy its URL.
2. Put it in `frontend/.env.production` → `NEXT_PUBLIC_BACKEND_URL=https://minutecanvas-api-….run.app` (baked in at build time).
3. Deploy:
```bash
gcloud run deploy minutecanvas-web --source frontend --project minutecanvas-nh2026 --region us-central1 \
  --allow-unauthenticated --max-instances 2 --memory 512Mi
```
4. Set the backend's `FRONTEND_ORIGIN` to this frontend URL (exact, no trailing slash) and redeploy the backend.

## Pages
- Public (top nav, no sidebar): `/` landing · `/privacy`
- App (left sidebar): `/dashboard` · `/meet` new meeting (becomes the full-screen live meeting) · `/meetings` · `/findings` · `/usage` · `/account` (Google sign-in, theme, delete all)
- `/replay?id=…` full-screen replay: `&view=report` opens the report, `&f=<finding id>` opens a finding, `&t=<seconds>` starts there
- Records need sign-in: signed out, Dashboard / Meetings / Findings / Usage show only "Sign in to see your meetings" (another person on the same computer must not see them). Starting a meeting never needs sign-in. Meetings made signed out stay hidden in local storage; after sign-in a dialog asks "Add N meetings made in this browser to your account?" (Add = claim, Not mine = forget them in this browser).
- Theme: dark by default, follows the OS; Account › Appearance (or the sidebar ⋯ menu) overrides it. Report slides and the board export stay light.
- Design: tokens in `app/tokens.css` (from the approved "MinuteCanvas Design System"), components and screens in `app/globals.css`.
- Landing settings in `.env.production`: `NEXT_PUBLIC_DEMO_VIDEO_ID`, `NEXT_PUBLIC_DEMO_REPLAY_ID`, `NEXT_PUBLIC_CONTACT_EMAIL`.
- Google sign-in needs no frontend setting: the page asks the backend (`GET /api/auth/config`) for the client id.

## How it works
- Mic opens **before** the session starts; audio → AudioWorklet (`public/pcm-worklet.js`) → PCM16 mono 24 kHz, 100 ms frames with a uint64 byte-offset header.
- Last 60 s of audio kept in the browser; after a reconnect only what the server didn't get is resent (`hello.audio_bytes_received`).
- Heartbeat: no server message for 5 s = dead socket → reconnect (also on browser offline/online). Page refresh → “Rejoin meeting”.
- Images are fetched with the session token and shown as `blob:` URLs, so PNG/PDF export works. All text is rendered as text (React), never as HTML.
- Debug: add `?debug` to the URL → `window.__mc` is the live client.
