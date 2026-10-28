# MinuteCanvas backend

FastAPI + WebSockets. Browser audio → **Muse Voice Transcribe** (live transcript, speaker labels) → **Muse Spark** every ~40 s (board cards as JSON) → **Muse Image** (card illustrations) → final report.

## Run locally

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # put META_API_KEY in .env
uvicorn app.main:app --port 8000 --ws-ping-interval 20 --ws-ping-timeout 20
```

`MOCK_META=1` in `.env` = fake transcript/cards/images, 0 credits (use while building the UI).

## Test

```bash
pip install pytest pytest-asyncio
pytest -q                                   # fake Meta, no credits
python scripts/live_test.py meeting.wav --seconds 120 --keywords "Claude,MinuteCanvas"   # REAL API
```
First real run: set `DEBUG_VOICE=1` to log raw Meta voice messages.
`DEBUG_DUMP=1` (local testing only) saves the transcript and every Spark call (request + answer) to `backend/debug/<session>.json` (git-ignored).

## Deploy (Cloud Run, us-central1)

```bash
# once: API key in Secret Manager (never in code or env files)
printf "%s" "PASTE_KEY" | gcloud secrets create meta-api-key --data-file=- --project minutecanvas-nh2026
PN=$(gcloud projects describe minutecanvas-nh2026 --format='value(projectNumber)')
gcloud secrets add-iam-policy-binding meta-api-key --project minutecanvas-nh2026 \
  --member="serviceAccount:${PN}-compute@developer.gserviceaccount.com" --role=roles/secretmanager.secretAccessor

gcloud run deploy minutecanvas-api --source backend --project minutecanvas-nh2026 --region us-central1 \
  --allow-unauthenticated --max-instances 1 --timeout 3600 --session-affinity --no-cpu-throttling \
  --memory 512Mi --set-secrets META_API_KEY=meta-api-key:latest \
  --set-env-vars FRONTEND_ORIGIN=https://YOUR-FRONTEND.run.app,ACCESS_CODE=CHOOSE_ONE
```
- `--max-instances 1`: sessions live in memory.
- `--no-cpu-throttling`: the voice pacer must keep sending silence while the browser is reconnecting.
- `--timeout 3600`: WebSocket closes at 60 min; the browser reconnects and resends.
- After the first deploy, check the log line `session … created (x-forwarded-for=… -> ip used …)`: the IP used must be your real public IP. If it shows a Google IP instead, redeploy with `TRUSTED_PROXY_HOPS=2`.

## Security
API key only on the backend (Secret Manager) · session token as the first WS message · Origin check on WS + CORS for the frontend only · rate limits (sessions per IP, live sessions, Spark/image caps, WS messages per second, max frame size, audio can't arrive faster than real time) · card text sanitised (frontend must still use `textContent`) · audio never stored · images and report need `Authorization: Bearer <token>`.

## Protocol (for the frontend)

1. `POST /api/sessions` `{title?, keywords?: [], access_code?}` → `{session_id, token, sample_rate: 24000}`
2. Open `wss://…/ws/{session_id}`, first message `{"type":"auth","token":"…"}` → server sends `hello` (full state, `audio_bytes_received`).
3. Open the mic **before** sending `{"type":"start"}`.
4. Audio = binary frames: `[uint64 little-endian byte offset][PCM16 mono 24 kHz]`, ~100 ms each. Keep the last 60 s in the browser; after a reconnect resend from `hello.audio_bytes_received`.
5. Server `ping` every 2 s → no message for 5 s = dead socket → reconnect.

Client → server: `start` · `pause` · `resume` · `stop` · `rename_speaker {label,name}` · `card_edit {id,fields}` · `card_delete {id}` · `gap_review` (Flow Map "Check gaps")

Server → client: `hello` · `ping` · `state` · `voice_status` · `transcript_partial` · `transcript_partial_clear` · `transcript_final` · `cards_added` · `cards_updated` · `card_deleted` · `board_title` · `speakers` · `spark_status` · `flow {flow,events,stats}` · `flow_status` · `notice` · `report_ready` · `error`

Card: `{id, column: goals|decisions|actions|updates|questions, title, detail, owner, due, status: done|in_progress|blocked, done, resolved, answer, t, updated_t, image_url, image_status, source, user_locked}`
Image URLs are relative (`/api/sessions/…/images/…`): fetch them with `Authorization: Bearer <token>`, then `URL.createObjectURL(blob)` (blob URLs also make PNG/PDF export work). Render all text with `textContent` only.


## Live Flow Map (Spark Flow agent)

Every Spark round runs two calls in parallel: the cards call (JSON) and the **Flow agent** (function calling).
Tools: `add_step` · `link_steps` · `update_step` · `report_bug` · `flag_gap` · `flag_contradiction` · `resolve` (`app/prompts.py`).

- **Quote check** (`app/flow.py` → `check_quote`): every tool call must quote words from one real transcript line.
  The code finds that line; speaker and time come from the line, never from the model. A call whose quote isn't
  in the transcript is rejected and the error goes back to the agent, which gets one more turn to fix it.
- A flagged gap becomes a question in Open questions (`Card.flow_ref`); answering/deleting it updates the gap.
- `gap_review` runs a review pass now (cooldown `GAP_REVIEW_COOLDOWN_S`); Stop runs a final pass.
- While recording, an automatic `gap_check` pass runs every `AUTO_GAP_CHECK_S` (default 240 s, 0 = off) once the map has 3+ steps and changed since the last check (max 1 new gap). Live rounds only see ~45 s of talk; a missing path shows only after the group moves on.
- The Flow agent also gets older lines that mention numbers, so a price said 20 minutes ago is compared with a new one (contradictions).
- Cards that state one side of an open contradiction get `Card.conflict_ref` (shown as "Not decided" / "Conflict"); they leave the report's Decisions until it is settled.
- If the API rejects `tools`, the agent switches to the same calls as JSON (`FLOW_JSON_SCHEMA`) automatically.
- Settings: `FLOW_ENABLED=1`, `MAX_FLOW_CALLS=150`. `MOCK_META=1` plays a scripted sofa-shop client meeting
  through the same quote check (one wrong quote on purpose to show reject → retry).
