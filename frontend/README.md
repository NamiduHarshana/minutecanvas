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

## How it works
- Mic opens **before** the session starts; audio → AudioWorklet (`public/pcm-worklet.js`) → PCM16 mono 24 kHz, 100 ms frames with a uint64 byte-offset header.
- Last 60 s of audio kept in the browser; after a reconnect only what the server didn't get is resent (`hello.audio_bytes_received`).
- Heartbeat: no server message for 5 s = dead socket → reconnect (also on browser offline/online). Page refresh → “Rejoin meeting”.
- Images are fetched with the session token and shown as `blob:` URLs, so PNG/PDF export works. All text is rendered as text (React), never as HTML.
- Debug: add `?debug` to the URL → `window.__mc` is the live client.
