"""End-to-end: real uvicorn + real WebSockets + fake Meta (ASR server, HTTP transport)."""
import asyncio
import json
import math
import struct
import time

import httpx
import pytest
import uvicorn
import websockets

from app.config import settings
from app.main import app
from tests.fake_meta import FakeASR, fake_http

ORIGIN = "http://localhost:3000"
BPS = 48_000


def tone(n_bytes: int, start_sample: int) -> bytes:
    n = n_bytes // 2
    return struct.pack(f"<{n}h", *(int(6000 * math.sin(2 * math.pi * 220 * (start_sample + i) / 24000)) for i in range(n)))


class Browser:
    """Simulates the frontend: captures audio continuously, keeps it, resends from server's count."""

    def __init__(self, base, sid, token):
        self.base, self.sid, self.token = base, sid, token
        self.captured = bytearray()
        self.events: list[dict] = []
        self.ws = None

    async def connect(self):
        self.ws = await websockets.connect(f"{self.base}/ws/{self.sid}", origin=ORIGIN)
        await self.ws.send(json.dumps({"type": "auth", "token": self.token}))
        hello = json.loads(await self.ws.recv())
        assert hello["type"] == "hello"
        self.events.append(hello)
        self.reader = asyncio.create_task(self._read())
        return hello

    async def _read(self):
        try:
            async for m in self.ws:
                self.events.append(json.loads(m))
        except Exception:
            pass

    def capture(self, seconds=0.1):
        self.captured += tone(int(BPS * seconds), len(self.captured) // 2)

    async def send_from(self, offset):
        chunk = 4800
        for o in range(offset, len(self.captured), chunk):
            await self.ws.send(o.to_bytes(8, "little") + bytes(self.captured[o:o + chunk]))

    def of(self, t):
        return [e for e in self.events if e.get("type") == t]


@pytest.fixture
async def server():
    asr = FakeASR(emit_every_s=1.5)
    settings.meta_asr_url = await asr.start()
    cfg = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", ws_ping_interval=20)
    srv = uvicorn.Server(cfg)
    task = asyncio.create_task(srv.serve())
    while not srv.started:
        await asyncio.sleep(0.05)
    port = srv.servers[0].sockets[0].getsockname()[1]
    spark, images = [], []
    app.state.meta.http = fake_http(spark, images)
    yield {"http": f"http://127.0.0.1:{port}", "ws": f"ws://127.0.0.1:{port}", "asr": asr, "spark": spark, "images": images}
    srv.should_exit = True
    await task
    await asr.stop()


async def create(srv, origin=ORIGIN):
    async with httpx.AsyncClient() as c:
        return await c.post(f"{srv['http']}/api/sessions", json={"keywords": ["Claude", "MinuteCanvas"]},
                            headers={"Origin": origin})


async def test_security(server):
    assert (await create(server, "https://evil.example")).status_code == 403
    r = await create(server)
    sid = r.json()["session_id"]
    with pytest.raises(websockets.exceptions.InvalidStatus):           # wrong Origin -> 403
        await websockets.connect(f"{server['ws']}/ws/{sid}", origin="https://evil.example")
    ws = await websockets.connect(f"{server['ws']}/ws/{sid}", origin=ORIGIN)
    await ws.send(json.dumps({"type": "auth", "token": "nope"}))
    assert json.loads(await ws.recv())["code"] == "bad_token"
    await asyncio.sleep(0.2)
    assert ws.close_code == 4401


async def test_full_meeting_with_drop_and_resend(server):
    r = (await create(server)).json()
    b = Browser(server["ws"], r["session_id"], r["token"])
    await b.connect()
    await b.ws.send(json.dumps({"type": "start"}))

    async def stream(seconds):
        t0 = time.monotonic()
        for i in range(int(seconds * 10)):
            b.capture(0.1)
            await b.ws.send((len(b.captured) - 4800).to_bytes(8, "little") + bytes(b.captured[-4800:]))
            await asyncio.sleep(max(0, t0 + (i + 1) * 0.1 - time.monotonic()))

    await stream(4)

    # --- network drop: socket dies, browser keeps capturing for 2 s ---
    b.reader.cancel()
    await b.ws.close()
    t0 = time.monotonic()
    for i in range(20):
        b.capture(0.1)
        await asyncio.sleep(max(0, t0 + (i + 1) * 0.1 - time.monotonic()))
    hello = await b.connect()
    await b.send_from(hello["audio_bytes_received"])                 # resend only what server missed
    assert hello["board"] is not None and len(hello["transcript"]) >= 1  # board restored

    await stream(4)
    await asyncio.sleep(1.5)
    await b.ws.send(json.dumps({"type": "rename_speaker", "label": "A", "name": "Namidu"}))
    await b.ws.send(json.dumps({"type": "stop"}))
    for _ in range(300):
        if b.of("report_ready"):
            break
        await asyncio.sleep(0.1)

    asr = server["asr"]
    # 0 lost audio: everything captured reached the server
    pings = b.of("ping")
    assert pings[-1]["audio_bytes_received"] == len(b.captured)
    # Meta rules: no ingress-below-real-time close, no burst (> 1 s ahead) even with the resend
    assert not asr.min_lag_violation, asr.closed_codes
    assert asr.max_lead_s < 1.0, asr.max_lead_s
    assert asr.sessions == 1
    hs = asr.handshakes[0]
    assert hs["mode"] == "DIARIZATION" and hs["audioEncoding"] == "PCM_24KHZ" and hs["keywords"] == ["Claude", "MinuteCanvas"]
    assert hs["authorization"]["accessToken"] == "Bearer test-key"
    assert asr.paths[0].startswith("/?sessionId=mc-")
    # speaker labels come from separate "speaker" events
    assert {f["speaker"] for f in b.of("transcript_final")} >= {"A", "B"}
    # spark fell back to the tested body after HTTP 400
    assert "reasoning_effort" not in server["spark"][0] and "strict" not in server["spark"][0]["response_format"]["json_schema"]

    finals = b.of("transcript_final")
    assert finals and all("<" not in f["text"] for f in finals)          # sanitised
    added = [c for e in b.of("cards_added") for c in e["cards"]]
    assert "<" not in json.dumps(added)
    # same-topic update merged: only ONE "Payment screen progress" card across all Spark calls
    assert sum(1 for c in added if c["title"].startswith("Payment")) == 1
    assert len(server["spark"]) >= 2
    # spark got existing cards (dedupe) and speaker names
    last_user = json.loads(server["spark"][-2]["messages"][1]["content"])
    assert last_user["current_board"]

    rep = b.of("report_ready")[0]["report"]
    assert rep["title"] == "Client Sync" and rep["summary"]
    assert rep["updates"][0]["status"] == "blocked"
    assert rep["cover_image_url"]

    # images served by our backend with CORS for export
    url = rep["cover_image_url"]
    async with httpx.AsyncClient() as c:
        assert (await c.get(server["http"] + url, headers={"Origin": ORIGIN})).status_code == 404   # no token
        img = await c.get(server["http"] + url, headers={"Origin": ORIGIN, "Authorization": f"Bearer {r['token']}"})
        assert img.status_code == 200 and img.headers["content-type"] == "image/webp"
        assert img.headers["access-control-allow-origin"] == ORIGIN
        rr = await c.get(f"{server['http']}/api/sessions/{r['session_id']}/report",
                         headers={"Authorization": f"Bearer {r['token']}"})
        assert rr.status_code == 200
        bad = await c.get(f"{server['http']}/api/sessions/{r['session_id']}/report",
                          headers={"Authorization": "Bearer x"})
        assert bad.status_code == 404
    cards = [c for e in b.of("cards_updated") for c in e["cards"]]
    assert any(c["image_status"] == "ready" for c in cards)


async def test_voice_rotation_keeps_audio(server):
    settings.voice_session_max_s = 3          # Meta 60-min cap, shrunk
    try:
        r = (await create(server)).json()
        b = Browser(server["ws"], r["session_id"], r["token"])
        await b.connect()
        await b.ws.send(json.dumps({"type": "start"}))
        t0 = time.monotonic()
        for i in range(80):
            b.capture(0.1)
            await b.ws.send((len(b.captured) - 4800).to_bytes(8, "little") + bytes(b.captured[-4800:]))
            await asyncio.sleep(max(0, t0 + (i + 1) * 0.1 - time.monotonic()))
        await b.ws.send(json.dumps({"type": "stop"}))
        for _ in range(300):
            if b.of("report_ready"):
                break
            await asyncio.sleep(0.1)
        asr = server["asr"]
        assert asr.sessions >= 2 and not asr.min_lag_violation
        speakers = {f["speaker"] for f in b.of("transcript_final")}
        assert any("·2" in s for s in speakers), speakers
        assert b.of("report_ready")
    finally:
        settings.voice_session_max_s = 58 * 60


async def test_ws_flood_and_fake_audio_are_blocked(server):
    r = (await create(server)).json()
    b = Browser(server["ws"], r["session_id"], r["token"])
    await b.connect()
    await b.ws.send(json.dumps({"type": "start"}))
    await asyncio.sleep(0.2)
    # audio far in the future (would allocate a huge silence gap) -> ignored
    await b.ws.send((10**12).to_bytes(8, "little") + b"\x01\x00" * 2400)
    # 60 s of audio in one go right after start -> only ~5 s accepted
    await b.ws.send((0).to_bytes(8, "little") + tone(48000 * 3, 0))
    await asyncio.sleep(0.6)
    ping = b.of("ping")[-1]
    assert ping["audio_bytes_received"] <= 48000 * 6
    # message flood -> socket closed 1008
    for _ in range(200):
        try:
            await b.ws.send(json.dumps({"type": "pong"}))
        except Exception:
            break
    await asyncio.sleep(0.5)
    assert b.ws.close_code == 1008


async def test_oversize_frame_kicked(server):
    r = (await create(server)).json()
    b = Browser(server["ws"], r["session_id"], r["token"])
    await b.connect()
    await b.ws.send(json.dumps({"type": "start"}))
    await b.ws.send((0).to_bytes(8, "little") + b"\x00" * 400_000)
    await asyncio.sleep(0.5)
    assert b.ws.close_code == 1008
