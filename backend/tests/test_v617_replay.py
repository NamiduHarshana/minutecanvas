"""v6.17 Replay: message log with meeting times, saved after Stop, served by the share id (no session token)."""
import asyncio
import gzip
import json
import time

import httpx

from app.main import app
from app.replay import GcsStore, KEEP, LocalStore, Recorder, valid_rid
from tests.test_e2e import ORIGIN, Browser, create, server  # noqa: F401  (pytest fixture)


async def _meeting(server, seconds=6):
    r = (await create(server)).json()
    b = Browser(server["ws"], r["session_id"], r["token"])
    await b.connect()
    await b.ws.send(json.dumps({"type": "start"}))
    t0 = time.monotonic()
    for i in range(int(seconds * 10)):
        b.capture(0.1)
        await b.ws.send((len(b.captured) - 4800).to_bytes(8, "little") + bytes(b.captured[-4800:]))
        await asyncio.sleep(max(0, t0 + (i + 1) * 0.1 - time.monotonic()))
    await asyncio.sleep(1.5)
    await b.ws.send(json.dumps({"type": "rename_speaker", "label": "A", "name": "Namidu"}))
    await b.ws.send(json.dumps({"type": "stop"}))
    for _ in range(300):
        if b.of("replay_ready"):
            break
        await asyncio.sleep(0.1)
    return r, b


async def test_replay_saved_and_served(server, tmp_path):
    app.state.sessions.store = LocalStore(str(tmp_path))
    r, b = await _meeting(server)
    rid = b.of("replay_ready")[0]["replay_id"]
    assert valid_rid(rid)
    # the browser closes its socket on "done": the id must come before it
    kinds = [(e.get("type"), e.get("state")) for e in b.events]
    assert kinds.index(("replay_ready", None)) < kinds.index(("state", "done"))
    async with httpx.AsyncClient() as c:
        res = await c.get(f"{server['http']}/api/replays/{rid}", headers={"Origin": ORIGIN})
        assert res.status_code == 200 and res.headers["access-control-allow-origin"] == ORIGIN
        rep = res.json()                                    # httpx un-gzips like the browser
        assert rep["v"] == 1 and rep["title"] == "Client Sync" and rep["speakers"]["A"] == "Namidu"
        ev = rep["events"]
        types = [m["type"] for _, m in ev]
        assert set(types) <= KEEP and "ping" not in types and "hello" not in types
        # same order and content as the live socket (minus live-only messages)
        live = [m for m in b.events if m.get("type") in KEEP]
        assert [m["type"] for m in live] == types
        assert [m for _, m in ev if m["type"] == "transcript_final"] == b.of("transcript_final")
        # meeting times never go back, start near 0, end at the meeting length
        ts = [t for t, _ in ev]
        assert ts == sorted(ts) and ts[0] < 1.0 and abs(ts[-1] - rep["duration_s"]) < 0.2
        assert types[-2:] == ["report_ready", "state"] and ev[-1][1]["state"] == "done"
        # a line is logged when it arrived, never before it was said
        assert all(t >= m["t"] for t, m in ev if m["type"] == "transcript_final")
        # images: replay URLs only, served without the session token
        blob = json.dumps(ev)
        assert f"/api/sessions/{r['session_id']}/" not in blob and r["token"] not in blob
        urls = {c["image_url"] for _, m in ev if m["type"] == "cards_updated" for c in m["cards"] if c["image_url"]}
        cover = [m for _, m in ev if m["type"] == "report_ready"][0]["report"]["cover_image_url"]
        assert urls and cover.startswith(f"/api/replays/{rid}/images/")
        for u in urls | {cover}:
            img = await c.get(server["http"] + u)
            assert img.status_code == 200 and img.headers["content-type"] == "image/webp"
        # wrong / malformed ids
        assert (await c.get(f"{server['http']}/api/replays/{'x' * 22}")).status_code == 404
        assert (await c.get(f"{server['http']}/api/replays/..%2F..%2Fetc")).status_code == 404
        assert (await c.get(f"{server['http']}/api/replays/{rid}/images/nothex")).status_code == 404
        assert (await c.get(f"{server['http']}/api/replays/{rid}/images/{'a' * 32}")).status_code == 404
        # rejoin after Stop also gets the id
        s = app.state.sessions.get(r["session_id"])
        assert s.snapshot()["replay_id"] == rid and s.recorder is None
    assert (tmp_path / "replays" / rid / "events.json.gz").exists()


async def test_replay_disabled_without_store(server):
    app.state.sessions.store = None
    r, b = await _meeting(server, seconds=3)
    assert b.of("report_ready") and not b.of("replay_ready")
    async with httpx.AsyncClient() as c:
        assert (await c.get(f"{server['http']}/api/replays/{'a' * 22}")).status_code == 404


async def test_replay_save_failure_is_a_notice(server):
    class Broken:
        async def put(self, *a):
            raise OSError("disk full")

        async def get(self, key):
            return None

    app.state.sessions.store = Broken()
    r, b = await _meeting(server, seconds=3)
    for _ in range(30):
        if any("replay" in n["message"] for n in b.of("notice")):
            break
        await asyncio.sleep(0.1)
    assert b.of("report_ready") and not b.of("replay_ready")
    assert any("replay couldn't be saved" in n["message"] for n in b.of("notice"))


def test_recorder_copies_and_caps(monkeypatch):
    from app.config import settings
    rec = Recorder()
    msg = {"type": "cards_added", "cards": [{"id": "c1", "title": "x"}]}
    rec.add(1.234, msg)
    msg["cards"][0]["title"] = "changed later"
    rec.add(2.0, {"type": "ping"})
    assert rec.events == [[1.23, {"type": "cards_added", "cards": [{"id": "c1", "title": "x"}]}]]
    monkeypatch.setattr(settings, "replay_max_bytes", 100)
    rec.add(3.0, {"type": "transcript_final", "text": "y" * 200})
    rec.add(4.0, {"type": "state", "state": "done"})
    assert rec.full and len(rec.events) == 1
    out = json.loads(gzip.decompress(rec.payload("sid1", "rid1", "T", "d", 9.0, {})))
    assert out["duration_s"] == 9.0 and len(out["events"]) == 1


async def test_gcs_store_uses_metadata_token():
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        if "metadata.google.internal" in req.url.host:
            assert req.headers["Metadata-Flavor"] == "Google"
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3599})
        assert req.headers["Authorization"] == "Bearer tok"
        if req.method == "POST":
            assert req.url.params["name"] == "replays/r/events.json.gz" and req.url.params["uploadType"] == "media"
            return httpx.Response(200, json={})
        if req.url.path.endswith("missing"):
            return httpx.Response(404)
        assert req.url.raw_path.startswith(b"/storage/v1/b/bkt/o/replays%2Fr%2Fevents.json.gz")
        return httpx.Response(200, content=b"data")

    g = GcsStore("bkt", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await g.put("replays/r/events.json.gz", b"data", "application/gzip")
    assert await g.get("replays/r/events.json.gz") == b"data"
    assert await g.get("replays/r/missing") is None
    assert sum("metadata" in r.url.host for r in seen) == 1          # token cached
