# MinuteCanvas backend

FastAPI + WebSockets. Browser audio → **Muse Voice Transcribe** (live transcript, speaker labels) → **Muse Spark** every ~40 s (board cards as JSON) → **Muse Image** (card illustrations) → final report (summary + takeaways by **Muse Wonder**, Muse Spark as the fallback).

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

### Transcript test bench (real Spark, no audio, no images)
Runs scripted meetings (`bench/meetings.py`, each with an answer key) through the real Cards + Flow pipeline and
scores steps, arrows, arrows between parts, real / false gaps, real / false conflicts, stale cards and the layout.
A 30-min meeting takes a few minutes. Costs only Spark calls (the total is printed).
```bash
python -m bench.bench                                   # all meetings
python -m bench.bench --only grooming_app               # one meeting
python -m bench.bench --extract debug/<run>.json leetcode_site   # add a live run's transcript (kept out of git)
python -m bench.bench --score debug/<run>.json --meeting leetcode_site   # score a live run, 0 credits
python scripts/replay.py debug/<run>.json               # re-apply a run's Flow calls to the current code, 0 credits
```
The layout part runs on any Node 18+ (`node scripts/run-ts.mjs scripts/layoutcheck.ts …` in frontend/, needs `npm install` there once); without Node it says "skipped".

## Deploy (AWS: one EC2 server behind CloudFront)

Live at **https://d56fdtsmdd5nf.cloudfront.net**. Frontend and backend share that one origin.

```
browser ──HTTPS/WSS──> CloudFront ──HTTPS :443──> EC2 (Ubuntu 24.04, t3.small, Elastic IP)
                                                   └─ Docker Compose: caddy (:80/:443, Let's Encrypt cert for <elastic-ip-dashed>.sslip.io)
                                                        ├─ /api/*, /ws/*  -> backend  (this folder, uvicorn :8080, user appuser)
                                                        └─ everything else -> frontend (Next.js standalone :8080, user app)
```
- **Server access:** AWS Systems Manager Session Manager only (EC2 role with `AmazonSSMManagedInstanceCore`); no SSH key, no port 22.
- **Security group:** 443 only from the CloudFront origin-facing managed prefix list; 80 open for the Let's Encrypt HTTP-01 check and the redirect.
- **CloudFront:** origin = the sslip.io name, HTTPS only. Default behaviour: redirect HTTP→HTTPS, all methods, `CachingDisabled` + origin request policy `AllViewerExceptHostHeader` (keeps the query string, `Authorization` and `Origin`; WebSockets pass through). `/_next/static/*`: `CachingOptimized`.
- **Secrets:** `META_API_KEY` and `AUTH_SECRET` live in a root-only (`chmod 600`) env file on the server, read by Compose (`env_file`); never in the repo or the image.
- **Backend env:** `MOCK_META=0`, `DEBUG_DUMP=0`, `REPLAY_DIR=/data` (a host folder owned by uid 1000, kept across restarts and reboots), `FRONTEND_ORIGIN` = the CloudFront URL (exact, no trailing slash), `GOOGLE_CLIENT_ID`, `TRUSTED_PROXY_HOPS=2`, `SESSIONS_PER_IP_PER_HOUR=4`, `MAX_SESSION_MINUTES=70`, `MAX_ACTIVE_SESSIONS=5`, `DAILY_MEETING_MINUTES=600` (defaults: `UNSTARTED_TTL_S=120`, `NO_AUDIO_STOP_S=180`).
- **One backend container:** sessions live in memory, so run exactly one. Restarting it ends running meetings.
- **Client IP:** CloudFront appends the viewer IP to `X-Forwarded-For` and Caddy (`trusted_proxies static 0.0.0.0/0`, safe because 443 is CloudFront-only) appends CloudFront's, so the real IP is 2 hops from the right: `TRUSTED_PROXY_HOPS=2`. Check the log line `session … created (x-forwarded-for=… -> ip used …)`: the IP used must be the viewer's own.
- **Frontend:** `frontend/.env.production` (in the repo, public values) is baked in at build time.
- **Update to a new commit** (on the server): `git fetch && git checkout <commit>` in the repo folder, then `docker compose build` and `docker compose up -d` in the deploy folder.

## Security
API key only on the backend (root-only env file on the server) · session token as the first WS message · Origin check on WS + CORS for the frontend only · rate limits (sessions per IP — a whole IPv6 /64 counts as one —, live sessions, meeting minutes per day server-wide, a meeting not started within 2 min stops holding a live slot, recording with no audio at all for 3 min stops, Spark/image caps, WS messages per second, max frame size, audio can't arrive faster than real time) · card text sanitised (frontend must still use `textContent`) · audio never stored (replays keep text, board, Flow Map and card images only; random 128-bit id, reads rate-limited per IP) · images and report need `Authorization: Bearer <token>`.

## Protocol (for the frontend)

1. `POST /api/sessions` `{title?, keywords?: [], access_code?}` → `{session_id, token, sample_rate: 24000}`
2. Open `wss://…/ws/{session_id}`, first message `{"type":"auth","token":"…"}` → server sends `hello` (full state, `audio_bytes_received`).
3. Open the mic **before** sending `{"type":"start"}`.
4. Audio = binary frames: `[uint64 little-endian byte offset][PCM16 mono 24 kHz]`, ~100 ms each. Keep the last 60 s in the browser; after a reconnect resend from `hello.audio_bytes_received`.
5. Server `ping` every 2 s → no message for 5 s = dead socket → reconnect.

Client → server: `start` · `pause` · `resume` · `stop` · `rename_speaker {label,name}` · `card_edit {id,fields}` · `card_delete {id}` · `gap_review` (Flow Map "Check gaps")

Server → client: `hello` · `ping` · `state` · `voice_status` · `transcript_partial` · `transcript_partial_clear` · `transcript_final` · `cards_added` · `cards_updated` · `card_deleted` · `board_title` · `speakers` · `spark_status` · `flow {flow,events,stats}` · `flow_status` · `notice` · `report_ready` · `replay_ready {replay_id, replay_key, info}` (after Stop, before the final `state: done`; `replay_key` = secret owner key, never inside the replay) · `error`

Card: `{id, column: goals|decisions|actions|updates|questions, title, detail, owner, due, status: done|in_progress|blocked, done, resolved, answer, t, updated_t, image_url, image_status, source, user_locked}`
Image URLs are relative (`/api/sessions/…/images/…`): fetch them with `Authorization: Bearer <token>`, then `URL.createObjectURL(blob)` (blob URLs also make PNG/PDF export work). Render all text with `textContent` only.

## Replay (share link)
Every message sent to the browser (except `hello`, `ping`, `voice_status`, `notice`, `error`) is logged with the meeting time it was sent at. After Stop the log and the card images are saved (`REPLAY_BUCKET` = Cloud Storage bucket, else the local folder `REPLAY_DIR`, default `backend/replays`) under a random 128-bit replay id, which is the access key of the link `/replay?id=<id>`. Audio is never saved.
- `GET /api/replays/{id}` → `{v, title, date, duration_s, speakers, events: [[t, msg], ...]}` (gzipped)
- `GET /api/replays/{id}/images/{img}` → card image
The replay page feeds the messages through the same reducer as the live app, so the transcript, board and Flow Map appear in the same order and at the same meeting time.
- `DELETE /api/replays/{id}` with `X-Replay-Key: <owner key>` (the browser that made the meeting) or `Authorization: Bearer <login token>` (the signed-in owner) → removes everything of that replay.
- Kept `REPLAY_KEEP_DAYS` (30) days: older replays are refused on read and removed by a sweep every 6 h; ids in `PINNED_REPLAYS` (the public demo) are kept. `replays/<id>/meta.json` holds the creation time, the sha256 of the owner key, the owner account and the dashboard row. v6.19: the row also has `bugs`, `answered`, `usage {voice_s, spark_calls, tool_calls, images}` and `findings [{id, kind gap|answered|conflict|bug, text, branch, step, part, t, answer, settled, fixed, quotes [{t, speaker, text}]}]`; the replay file (`events.json.gz`) also carries `findings`, `usage` and `timeline {speakers [{speaker,t,t_end}], parts [{part,t,t_end}]}`.

## My meetings (Google sign-in, optional)
Starting a meeting never needs an account. `GOOGLE_CLIENT_ID` + `AUTH_SECRET` turn on sign-in for the My meetings page:
- `GET /api/auth/config` → `{google_client_id}` · `POST /api/auth/google {credential}` (Google ID token, checked against Google's keys, our client id, issuer, expiry, verified email) → `{token, user}` (our HMAC token, `AUTH_DAYS` 14)
- `GET /api/me/meetings` · `POST /api/me/meetings/claim {items:[{rid,key}]}` (meetings made in this browser before sign-in) · `DELETE /api/me` (every replay of the account + the account list)
- Stored: `users/<sha256(sub)[:32]>.json` = email, name, replay ids. No database: same bucket as the replays.


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
- A contradiction is settled only by a later line where the group picks one value (code rejects the agent quoting one of the two conflicting lines; a second-opinion call checks the rest, `VERIFY_FLOW=1`).
- Cards that state one side of an open contradiction get `Card.conflict_ref` (shown as "Not decided" / "Conflict"); they leave the report's Decisions until it is settled.
- If the API rejects `tools`, the agent switches to the same calls as JSON (`FLOW_JSON_SCHEMA`) automatically.
- Settings: `FLOW_ENABLED=1`, `MAX_FLOW_CALLS=150`. `MOCK_META=1` plays a scripted sofa-shop client meeting
  through the same quote check (one wrong quote on purpose to show reject → retry).
