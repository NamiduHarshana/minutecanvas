"""v6.22: the public site (no sign-in) can't be blocked or have its Meta credits emptied by one person.

- a created meeting that is never started stops counting as live after UNSTARTED_TTL_S and is removed
- recording with a browser connected but no audio at all for NO_AUDIO_STOP_S -> auto Stop (report kept)
- DAILY_MEETING_MINUTES: Start..end-of-voice minutes of the last 24 h, server-wide; new meetings refused past it
- per-IP limits count a whole IPv6 /64 as one client; the limiter drops stale keys
"""
import asyncio
import json
import time

import httpx
import pytest
import websockets

from app.config import settings
from app.main import app
from app.security import RateLimiter, rate_key
from app.session import Session, SessionManager
from tests.test_e2e import ORIGIN, Browser, create, server  # noqa: F401  (pytest fixture)


# ---------------- per-IP key ----------------
def test_rate_key_ipv6_prefix():
    assert rate_key("2402:4000:1234:5678:aaaa::1") == rate_key("2402:4000:1234:5678:bbbb:cccc:dddd:eeee")
    assert rate_key("2402:4000:1234:5678::1") == "2402:4000:1234:5678::/64"
    assert rate_key("2402:4000:1234:5679::1") != rate_key("2402:4000:1234:5678::1")
    assert rate_key("52.46.55.69") == "52.46.55.69"
    assert rate_key("::ffff:52.46.55.69") == "52.46.55.69"          # IPv4 written as IPv6 = the IPv4 address
    assert rate_key("unknown") == "unknown"


def test_ipv6_address_hopping_is_one_client():
    lim = RateLimiter(4, 3600)
    assert all(lim.allow(f"2402:4000:1:2::{i:x}") for i in range(4))
    assert not lim.allow("2402:4000:1:2::ffff")                   # 5th address of the same /64 -> refused
    assert lim.allow("2402:4000:1:3::1")                          # another /64 = another client


def test_limiter_drops_stale_keys(monkeypatch):
    lim = RateLimiter(1, 0.05)
    monkeypatch.setattr(RateLimiter, "PRUNE_AT", 50)
    for i in range(50):
        lim.allow(f"10.0.0.{i}")
    time.sleep(0.1)
    lim.allow("10.0.1.1")
    assert len(lim._hits) == 1                                     # 50 stale keys dropped, only the new one left


def test_limiter_forgets_ips_after_its_window():
    lim = RateLimiter(5, 0.05)
    lim.allow("10.0.0.1")
    lim.allow("2402:4000:1:2::1")
    time.sleep(0.12)
    lim.allow("10.0.0.9")                                          # any call after a window drops stale IPs
    assert set(lim._hits) == {"10.0.0.9"}


# ---------------- daily meeting minutes ----------------
def _mgr() -> SessionManager:
    m = SessionManager.__new__(SessionManager)          # no store, no tasks: only the counting is tested
    m.sessions, m.past_intervals = {}, []
    return m


def test_used_minutes_counts_last_24h_only():
    m = _mgr()
    now = 1_000_000.0
    day = 24 * 3600
    m.past_intervals = [(now - day - 600, now - day - 60),        # ended before the window: dropped
                        (now - day - 300, now - day + 300),       # half inside: 5 min
                        (now - 3600, now - 3000)]                 # 10 min
    s = Session(object(), [], None, "1.2.3.4")
    s.started_wall = now - 120                                    # running: 2 min so far
    m.sessions[s.id] = s
    assert m.used_minutes(now) == pytest.approx(17.0)
    assert len(m.past_intervals) == 2


def test_removed_meeting_keeps_counting():
    m = _mgr()
    s = Session(object(), [], None, "1.2.3.4")
    s.started_wall, s.ended_wall = time.time() - 600, time.time() - 60
    m.sessions[s.id] = s
    m._forget(s.id)
    assert s.id not in m.sessions and m.used_minutes() == pytest.approx(9.0, abs=0.05)


def test_never_started_counts_nothing():
    m = _mgr()
    s = Session(object(), [], None, "1.2.3.4")
    m.sessions[s.id] = s
    m._forget(s.id)
    assert m.used_minutes() == 0 and m.past_intervals == []


async def test_daily_limit_refuses_new_meetings(server, monkeypatch):
    monkeypatch.setattr(settings, "daily_meeting_minutes", 10.0)
    assert (await create(server)).status_code == 200
    app.state.sessions.past_intervals.append((time.time() - 700, time.time() - 50))     # ~10.8 min today
    r = await create(server)
    assert r.status_code == 429 and r.json()["detail"] == \
        "MinuteCanvas has reached today's meeting limit. Please try again later."
    monkeypatch.setattr(settings, "daily_meeting_minutes", 0.0)                         # 0 = no limit
    assert (await create(server)).status_code == 200


async def test_daily_minutes_measured_start_to_stop(server):
    r = (await create(server)).json()
    b = Browser(server["ws"], r["session_id"], r["token"])
    await b.connect()
    s = app.state.sessions.get(r["session_id"])
    assert s.billed_interval(time.time()) is None                 # created, not started: nothing billed
    await b.ws.send(json.dumps({"type": "start"}))
    await asyncio.sleep(0.3)
    assert s.started_wall is not None and s.ended_wall is None
    b.capture(1.0)
    await b.send_from(0)
    await b.ws.send(json.dumps({"type": "stop"}))
    for _ in range(100):
        if s.state == "done":
            break
        await asyncio.sleep(0.1)
    assert s.state == "done" and s.ended_wall is not None and s.ended_wall >= s.started_wall
    assert app.state.sessions.used_minutes() > 0


# ---------------- never started ----------------
async def test_unstarted_meetings_free_the_slot(server, monkeypatch):
    monkeypatch.setattr(settings, "max_active_sessions", 1)
    monkeypatch.setattr(settings, "unstarted_ttl_s", 0.4)
    first = (await create(server)).json()
    assert (await create(server)).status_code == 503                # the one slot is taken
    await asyncio.sleep(0.6)
    assert app.state.sessions.active_count() == 0                   # expired: no longer live
    second = await create(server)
    assert second.status_code == 200
    # the old one is removed by the sweep; the new one (not expired) stays
    await app.state.sessions.cleanup_once()
    assert app.state.sessions.get(first["session_id"]) is None
    assert app.state.sessions.get(second.json()["session_id"]) is not None


async def test_late_start_is_refused(server, monkeypatch):
    monkeypatch.setattr(settings, "unstarted_ttl_s", 0.3)
    r = (await create(server)).json()
    b = Browser(server["ws"], r["session_id"], r["token"])
    await b.connect()
    await asyncio.sleep(0.5)
    await b.ws.send(json.dumps({"type": "start"}))
    await asyncio.sleep(0.4)
    err = b.of("error")
    assert err and err[-1]["code"] == "bad_token"                  # the app shows "This meeting has expired..."
    assert b.ws.close_code is not None
    assert app.state.sessions.get(r["session_id"]).state == "created"   # never recorded, nothing billed
    assert app.state.sessions.get(r["session_id"]).started_wall is None


async def test_normal_start_is_not_affected(server):
    r = (await create(server)).json()
    b = Browser(server["ws"], r["session_id"], r["token"])
    await b.connect()
    await b.ws.send(json.dumps({"type": "start"}))
    await asyncio.sleep(0.3)
    assert app.state.sessions.get(r["session_id"]).state == "recording"
    assert app.state.sessions.active_count() == 1


# ---------------- connected, no audio ----------------
async def _recording(server):
    r = (await create(server)).json()
    b = Browser(server["ws"], r["session_id"], r["token"])
    await b.connect()
    await b.ws.send(json.dumps({"type": "start"}))
    await asyncio.sleep(0.3)
    return b, app.state.sessions.get(r["session_id"])


async def _until(cond, s=5.0):
    for _ in range(int(s / 0.1)):
        if cond():
            return True
        await asyncio.sleep(0.1)
    return cond()


async def test_no_audio_auto_stop(server, monkeypatch):
    monkeypatch.setattr(settings, "no_audio_stop_s", 0.5)
    b, s = await _recording(server)
    await asyncio.sleep(0.7)
    await app.state.sessions.cleanup_once()
    assert await _until(lambda: s.state == "done", 15)
    assert any("No audio reached the server" in n.get("message", "") for n in b.of("notice"))
    assert s.report is not None                                     # Stop as usual: the report is kept


async def test_streaming_browser_keeps_recording(server, monkeypatch):
    monkeypatch.setattr(settings, "no_audio_stop_s", 0.6)
    b, s = await _recording(server)
    for _ in range(8):                                              # quiet room: frames keep coming
        b.capture(0.1)
        await b.send_from(len(b.captured) - 4800)
        await asyncio.sleep(0.1)
    await app.state.sessions.cleanup_once()
    await asyncio.sleep(0.2)
    assert s.state == "recording"


async def test_paused_and_resume_not_stopped(server, monkeypatch):
    monkeypatch.setattr(settings, "no_audio_stop_s", 0.4)
    b, s = await _recording(server)
    await b.ws.send(json.dumps({"type": "pause"}))
    await asyncio.sleep(0.6)
    await app.state.sessions.cleanup_once()
    await asyncio.sleep(0.2)
    assert s.state == "paused"                                      # a long pause is allowed (his decision)
    await b.ws.send(json.dumps({"type": "resume"}))
    await asyncio.sleep(0.1)
    await app.state.sessions.cleanup_once()                         # clock restarted at Resume
    await asyncio.sleep(0.2)
    assert s.state == "recording"


async def test_dropped_browser_uses_the_10_min_rule(server, monkeypatch):
    monkeypatch.setattr(settings, "no_audio_stop_s", 0.3)
    b, s = await _recording(server)
    await b.ws.close()
    await asyncio.sleep(0.5)
    assert s.socket is None
    await app.state.sessions.cleanup_once()
    await asyncio.sleep(0.2)
    assert s.state == "recording"                                   # may still come back and resend its audio


async def test_ws_reject_bad_origin_still(server):
    r = (await create(server)).json()
    with pytest.raises(websockets.exceptions.InvalidStatus):
        await websockets.connect(f"{server['ws']}/ws/{r['session_id']}", origin="https://evil.example")
    async with httpx.AsyncClient() as c:
        assert (await c.post(f"{server['http']}/api/sessions", json={}, headers={"Origin": "https://evil.example"})
                ).status_code == 403
