"""MinuteCanvas backend: FastAPI + WebSockets.

HTTP
  GET  /healthz
  POST /api/sessions                       -> {session_id, token, sample_rate}
  GET  /api/sessions/{sid}/images/{img}    -> card image (proxied, CORS-safe for export)
  GET  /api/sessions/{sid}/report          (Authorization: Bearer <token>)
  GET  /api/replays/{rid}                  -> gzipped replay log (the random replay id is the access key)
  GET  /api/replays/{rid}/images/{img}     -> card image of a replay
  DELETE /api/replays/{rid}                (X-Replay-Key: <owner key> or Authorization: Bearer <login token>)
  GET  /api/auth/config                    -> {"google_client_id"} ("" = sign-in off)
  POST /api/auth/google                    {"credential": Google ID token} -> {token, user}
  GET  /api/me/meetings                    (Bearer login token) -> {user, meetings}
  POST /api/me/meetings/claim              {"items":[{"rid","key"}]}: this browser's meetings -> the account
  DELETE /api/me                           deletes every replay of the account + the account's list
WebSocket
  /ws/{sid}   first message {"type":"auth","token":...}; audio = binary frames
              [8-byte little-endian byte offset][PCM16 mono 24 kHz]
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .accounts import AuthError, GoogleVerifier, Tokens, uid_for
from .config import SAMPLE_RATE, settings
from .meta_api import MetaClient, image_mime
from .replay import events_key, image_key, valid_img, valid_rid
from .security import RateLimiter, TokenBucket, client_ip, clean_text, origin_allowed
from .session import SessionManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("minutecanvas")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.meta_api_key and not settings.mock_meta:
        log.warning("META_API_KEY is not set (set MOCK_META=1 to work without credits)")
    app.state.meta = MetaClient()
    app.state.sessions = SessionManager(app.state.meta)
    app.state.sessions.start()
    app.state.replay_limiter = RateLimiter(settings.replay_reads_per_ip_per_min, 60)
    app.state.login_limiter = RateLimiter(settings.logins_per_ip_per_hour, 3600)
    app.state.google = GoogleVerifier(settings.google_client_id)
    app.state.tokens = Tokens(settings.auth_secret)
    yield
    await app.state.sessions.shutdown()
    await app.state.meta.aclose()


app = FastAPI(title="MinuteCanvas API", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origins,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Replay-Key"],
    max_age=600,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@app.get("/healthz")
async def healthz():
    return {"ok": True}


class CreateSession(BaseModel):
    access_code: str | None = Field(default=None, max_length=100)
    title: str | None = Field(default=None, max_length=120)
    keywords: list[str] = Field(default_factory=list, max_length=50)


@app.post("/api/sessions")
async def create_session(body: CreateSession, request: Request):
    origin = request.headers.get("origin")
    if origin and not origin_allowed(origin, settings.frontend_origins):
        raise HTTPException(403, "origin not allowed")
    if settings.access_code and not secrets.compare_digest(body.access_code or "", settings.access_code):
        raise HTTPException(401, "wrong access code")
    mgr: SessionManager = request.app.state.sessions
    ip = _ip(request)
    if mgr.active_count() >= settings.max_active_sessions:
        raise HTTPException(503, "too many live sessions right now, try again later")
    if mgr.daily_limit_reached():            # protects the Meta credits (public site, no sign-in needed)
        raise HTTPException(429, "MinuteCanvas has reached today's meeting limit. Please try again later.")
    if not mgr.ip_limiter.allow(ip):
        raise HTTPException(429, "too many sessions, try again later")
    keywords = [k for k in (clean_text(x, 40) for x in body.keywords[:30]) if k]
    s = mgr.create(keywords, clean_text(body.title, 80) or None, ip)
    # signed in (optional): the replay goes straight into the account's "My meetings"
    s.owner = _user(request, required=False)
    log.info("session %s created (x-forwarded-for=%r -> ip used for rate limit=%s)",
             s.id, request.headers.get("x-forwarded-for"), ip)
    return {"session_id": s.id, "token": s.token, "sample_rate": SAMPLE_RATE}


def _authed_session(sid: str, request: Request):
    s = request.app.state.sessions.get(sid)
    auth = request.headers.get("authorization", "")
    if not s or not secrets.compare_digest(auth, f"Bearer {s.token}"):
        raise HTTPException(404)
    return s


@app.get("/api/sessions/{sid}")
async def get_session(sid: str, request: Request):
    """Rejoin check. Needs Authorization: Bearer <token>; 404 if the session is gone."""
    s = _authed_session(sid, request)
    return {"state": s.state}


@app.get("/api/sessions/{sid}/images/{img_id}")
async def get_image(sid: str, img_id: str, request: Request):
    """Needs Authorization: Bearer <token>. Frontend: fetch -> blob -> URL.createObjectURL."""
    s = _authed_session(sid, request)
    item = s.images.get(img_id)
    if not item:
        raise HTTPException(404)
    raw, mime = item
    return Response(raw, media_type=mime, headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/sessions/{sid}/report")
async def get_report(sid: str, request: Request):
    s = _authed_session(sid, request)
    if not s.report:
        raise HTTPException(409, "report not ready")
    return s.report


def _ip(request: Request) -> str:
    return client_ip(request.headers, request.client.host if request.client else None, settings.trusted_proxy_hops)


def _library(request: Request):
    lib = request.app.state.sessions.library
    if lib is None:
        raise HTTPException(404)
    return lib


async def _replay_store(request: Request, rid: str):
    lib = _library(request)
    if not valid_rid(rid):
        raise HTTPException(404)
    if not request.app.state.replay_limiter.allow(_ip(request)):
        raise HTTPException(429, "too many requests, try again later")
    if not await lib.readable(rid):          # deleted, older than REPLAY_KEEP_DAYS, or never existed
        raise HTTPException(404)
    return lib.store


@app.get("/api/replays/{rid}")
async def get_replay(rid: str, request: Request):
    data = await (await _replay_store(request, rid)).get(events_key(rid))
    if data is None:
        raise HTTPException(404)
    # stored gzipped: the browser un-gzips it on the fly. no-store: a deleted replay must not live on in a cache
    return Response(data, media_type="application/json",
                    headers={"Content-Encoding": "gzip", "Cache-Control": "no-store"})


@app.get("/api/replays/{rid}/images/{img_id}")
async def get_replay_image(rid: str, img_id: str, request: Request):
    if not valid_img(img_id):
        raise HTTPException(404)
    raw = await (await _replay_store(request, rid)).get(image_key(rid, img_id))
    if raw is None:
        raise HTTPException(404)
    return Response(raw, media_type=image_mime(raw) or "image/webp", headers={"Cache-Control": "private, max-age=3600"})


@app.delete("/api/replays/{rid}")
async def delete_replay(rid: str, request: Request):
    lib = _library(request)
    origin = request.headers.get("origin")
    if origin and not origin_allowed(origin, settings.frontend_origins):
        raise HTTPException(403, "origin not allowed")
    if not valid_rid(rid) or not request.app.state.replay_limiter.allow(_ip(request)):
        raise HTTPException(404)
    m = await lib.can_delete(rid, request.headers.get("x-replay-key"), _user(request, required=False))
    if m is None:
        raise HTTPException(404)            # same answer for "no such replay" and "not yours"
    try:
        await lib.delete(rid, m)
    except Exception:  # noqa: BLE001
        raise HTTPException(502, "couldn't delete the replay, please try again") from None
    request.app.state.sessions.forget_replay(rid)
    return {"deleted": rid}


# ---------------- Google sign-in + My meetings ----------------
def _user(request: Request, required: bool = True) -> dict | None:
    u = request.app.state.tokens.from_header(request.headers.get("authorization"))
    if u is None and required:
        raise HTTPException(401, "please sign in")
    return u


@app.get("/api/auth/config")
async def auth_config():
    return {"google_client_id": settings.google_client_id if _accounts_on() else ""}


def _accounts_on() -> bool:
    return bool(settings.google_client_id) and settings.replay_enabled


class GoogleLogin(BaseModel):
    credential: str = Field(min_length=20, max_length=4096)


@app.post("/api/auth/google")
async def auth_google(body: GoogleLogin, request: Request):
    origin = request.headers.get("origin")
    if origin and not origin_allowed(origin, settings.frontend_origins):
        raise HTTPException(403, "origin not allowed")
    if not _accounts_on():
        raise HTTPException(404, "sign-in is off on this server")
    if not request.app.state.login_limiter.allow(_ip(request)):
        raise HTTPException(429, "too many sign-ins, try again later")
    try:
        g = await request.app.state.google.verify(body.credential)
    except AuthError as e:
        raise HTTPException(401, str(e)) from None
    user = {"uid": uid_for(g["sub"]), "email": g["email"], "name": g["name"]}
    token = request.app.state.tokens.make(user["uid"], user["email"], user["name"])
    return {"token": token, "user": {"email": user["email"], "name": user["name"]}}


@app.get("/api/me/meetings")
async def my_meetings(request: Request):
    u = _user(request)
    return {"user": {"email": u["email"], "name": u["name"]}, "meetings": await _library(request).meetings(u)}


class ClaimItem(BaseModel):
    rid: str = Field(max_length=64)
    key: str = Field(max_length=100)


class Claim(BaseModel):
    items: list[ClaimItem] = Field(default_factory=list, max_length=200)


@app.post("/api/me/meetings/claim")
async def claim_meetings(body: Claim, request: Request):
    u = _user(request)
    lib = _library(request)
    added = await lib.claim(u, [(i.rid, i.key) for i in body.items])
    return {"added": added, "meetings": await lib.meetings(u)}


@app.delete("/api/me")
async def delete_me(request: Request):
    origin = request.headers.get("origin")
    if origin and not origin_allowed(origin, settings.frontend_origins):
        raise HTTPException(403, "origin not allowed")
    u = _user(request)
    try:
        gone = await _library(request).delete_user(u)
    except Exception:  # noqa: BLE001
        raise HTTPException(502, "couldn't delete everything, please try again") from None
    for rid in gone:
        request.app.state.sessions.forget_replay(rid)
    return {"deleted_meetings": len(gone)}


@app.websocket("/ws/{sid}")
async def ws_endpoint(ws: WebSocket, sid: str):
    if not origin_allowed(ws.headers.get("origin"), settings.frontend_origins):
        await ws.close(code=1008)          # rejected before accept -> HTTP 403
        return
    await ws.accept()
    s = ws.app.state.sessions.get(sid)
    try:
        first = await asyncio.wait_for(ws.receive_text(), timeout=settings.auth_timeout_s)
        token = json.loads(first).get("token", "")
    except Exception:  # noqa: BLE001
        token = ""
    if not s or not isinstance(token, str) or not secrets.compare_digest(token, s.token):
        await ws.send_text(json.dumps({"type": "error", "code": "bad_token", "message": "session not found"}))
        await ws.close(code=4401)
        return

    await s.attach(ws)
    hb = asyncio.create_task(_heartbeat(s, ws))
    bucket = TokenBucket(settings.ws_msgs_per_s, settings.ws_msg_burst)
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            s.last_seen = time.monotonic()
            data, text = msg.get("bytes"), msg.get("text")
            if data:
                if len(data) > settings.ws_max_audio_frame:
                    await _kick(ws, "audio frame too big")
                    break
                s.on_audio_frame(data)
            elif text:
                if len(text) > settings.ws_max_text or not bucket.take():
                    await _kick(ws, "too many or too large messages")
                    break
                await _handle_text(s, text)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hb.cancel()
        s.detach(ws)


async def _kick(ws: WebSocket, reason: str):
    log.warning("closing socket: %s", reason)
    try:
        await ws.close(code=1008, reason=reason)
    except Exception:  # noqa: BLE001
        pass


async def _heartbeat(s, ws: WebSocket):
    """Server -> browser every 2 s. Browser treats 5 s of silence as a dead socket."""
    while s.socket is ws:
        await s.send({"type": "ping", "audio_bytes_received": s.audio.received_bytes,
                      "meeting_t": round(s.meeting_t, 1), "backlog_s": round(s.audio.backlog_s, 1)})
        await asyncio.sleep(settings.heartbeat_s)


async def _handle_text(s, text: str):
    try:
        m = json.loads(text)
    except ValueError:
        return
    if not isinstance(m, dict):
        return
    t = m.get("type")
    if t == "start":
        await s.start()
    elif t == "pause":
        await s.set_paused(True)
    elif t == "resume":
        await s.set_paused(False)
    elif t == "stop":
        asyncio.create_task(s.stop())
    elif t == "rename_speaker":
        await s.rename_speaker(str(m.get("label", "")), str(m.get("name", "")))
    elif t == "card_edit":
        c = s.board.user_edit(str(m.get("id", "")), m.get("fields") or {})
        if c:
            await s.send({"type": "cards_updated", "cards": [c.public()]})
            if c.flow_ref:
                await s.user_gap_card_changed(c)
    elif t == "card_delete":
        cid = str(m.get("id", ""))
        if s.board.user_delete(cid):
            await s.send({"type": "card_deleted", "id": cid})
            await s.user_gap_card_changed(None, deleted_id=cid)
    elif t == "gap_review":
        asyncio.create_task(s.gap_review())
    # "pong" and anything else: just keeps the connection alive
