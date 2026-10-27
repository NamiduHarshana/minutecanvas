"""Fake Meta realtime ASR server + fake HTTP transport, for tests (no credits)."""
from __future__ import annotations

import asyncio
import base64
import json
import time

import httpx
import websockets

BPS = 48_000


class FakeASR:
    """Checks the pacer like Meta does: closes 1008 if ingress falls >1.5 s below real time,
    records the max lead over real time (burst check)."""

    def __init__(self, emit_every_s: float = 2.0):
        self.emit_every = emit_every_s
        self.handshakes: list[dict] = []
        self.sessions = 0
        self.received = 0
        self.max_lead_s = 0.0
        self.min_lag_violation = False
        self.closed_codes: list[int] = []
        self.server = None
        self.port = None

    async def start(self):
        self.server = await websockets.serve(self._handler, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return f"ws://127.0.0.1:{self.port}"

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()

    async def _handler(self, ws):
        hs = json.loads(await ws.recv())
        self.handshakes.append(hs)
        self.sessions += 1
        await ws.send(json.dumps({"sessionId": f"s{self.sessions}"}))
        start = time.monotonic()
        got = 0
        next_emit = self.emit_every * BPS
        turn = 0
        try:
            async for msg in ws:
                if isinstance(msg, str):
                    if json.loads(msg).get("type") == "endStream":
                        await ws.send(json.dumps({"type": "speechComplete", "turnId": "end", "speaker": "A",
                                                  "transcript": "Final words before the end."}))
                        await ws.close(1000)
                        return
                    continue
                got += len(msg)
                self.received += len(msg)
                el = time.monotonic() - start
                self.max_lead_s = max(self.max_lead_s, got / BPS - el)
                if el > 2 and got / BPS < el - 1.5:
                    self.min_lag_violation = True
                    await ws.close(1008, "Ingress below real-time")
                    return
                if got >= next_emit:
                    next_emit += self.emit_every * BPS
                    turn += 1
                    sp = "A" if turn % 2 else "B"
                    await ws.send(json.dumps({"type": "transcript", "turnId": str(turn), "speaker": sp,
                                              "transcript": "We need to ship the"}))
                    await ws.send(json.dumps({"type": "speechComplete", "turnId": str(turn), "speaker": sp,
                                              "transcript": f"<b>Line {turn}</b>: we decided to ship the login API by Friday and Kasun will send docs."}))
        finally:
            self.closed_codes.append(ws.close_code or 0)


WEBP_1PX = base64.b64decode("UklGRiQAAABXRUJQVlA4IBgAAAAwAQCdASoBAAEAAwA0JaQAA3AA/vuUAAA=")


def fake_http(spark_calls: list, image_calls: list) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path.endswith("/chat/completions"):
            spark_calls.append(body)
            name = body["response_format"]["json_schema"]["name"]
            if name == "summary":
                content = {"title": "Client Sync", "summary": "We agreed to ship login.", "image_prompt": "a rocket"}
            else:
                n = len(spark_calls)
                content = {
                    "new_cards": [
                        {"column": "decisions", "title": "Ship login API by Friday", "detail": "<script>alert(1)</script>Agreed.",
                         "owner": None, "due": "Friday", "status": None, "image_prompt": "rocket"},
                        {"column": "updates", "title": f"Payment screen progress", "detail": f"call {n}",
                         "owner": "Kasun", "due": None, "status": "blocked" if n > 1 else "in_progress", "image_prompt": "card"},
                    ],
                    "updates": [], "resolved_questions": [], "meeting_title": "Client Sync",
                }
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})
        if request.url.path.endswith("/responses"):
            image_calls.append(body)
            return httpx.Response(200, json={"output": [{"type": "image_generation_call",
                                                         "result": base64.b64encode(WEBP_1PX * 20).decode()}]})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))
