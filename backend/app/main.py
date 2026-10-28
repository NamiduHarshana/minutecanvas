"""MinuteCanvas backend: FastAPI + WebSockets.

HTTP
  GET  /healthz
  POST /api/sessions                       -> {session_id, token, sample_rate}
  GET  /api/sessions/{sid}/images/{img}    -> card image (proxied, CORS-safe for export)
  GET  /api/sessions/{sid}/report          (Authorization: Bearer <token>)
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

from .config import SAMPLE_RATE, settings
from .meta_api import MetaClient
from .security import TokenBucket, client_ip, clean_text, origin_allowed
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
    yield
    await app.state.sessions.shutdown()
    await app.state.meta.aclose()


app = FastAPI(title="MinuteCanvas API", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
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
    ip = client_ip(request.headers, request.client.host if request.client else None, settings.trusted_proxy_hops)
    if mgr.active_count() >= settings.max_active_sessions:
        raise HTTPException(503, "too many live sessions right now, try again later")
    if not mgr.ip_limiter.allow(ip):
        raise HTTPException(429, "too many sessions, try again later")
    keywords = [k for k in (clean_text(x, 40) for x in body.keywords[:30]) if k]
    s = mgr.create(keywords, clean_text(body.title, 80) or None, ip)
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
    elif t == "card_delete":
        cid = str(m.get("id", ""))
        if s.board.user_delete(cid):
            await s.send({"type": "card_deleted", "id": cid})
    # "pong" and anything else: just keeps the connection alive
