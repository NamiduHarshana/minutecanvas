"""v6.18: delete replay (owner key / signed-in owner), 30-day expiry + pinned demo replay, Google sign-in,
"My meetings" (claim this browser's meetings on sign-in), delete all my data."""
import asyncio
import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.accounts import AuthError, GoogleVerifier, Library, Tokens, uid_for
from app.config import settings
from app.main import app
from app.replay import GcsStore, LocalStore, key_sha, meta_key
from tests.test_e2e import ORIGIN, Browser, server  # noqa: F401  (pytest fixture)

CID = "test-client.apps.googleusercontent.com"


async def _meeting(server, headers=None, seconds=3):
    async with httpx.AsyncClient() as c:
        r = (await c.post(f"{server['http']}/api/sessions", json={"keywords": ["Claude"]},
                          headers={"Origin": ORIGIN, **(headers or {})})).json()
    b = Browser(server["ws"], r["session_id"], r["token"])
    await b.connect()
    await b.ws.send(json.dumps({"type": "start"}))
    t0 = time.monotonic()
    for i in range(int(seconds * 10)):
        b.capture(0.1)
        await b.ws.send((len(b.captured) - 4800).to_bytes(8, "little") + bytes(b.captured[-4800:]))
        await asyncio.sleep(max(0, t0 + (i + 1) * 0.1 - time.monotonic()))
    await asyncio.sleep(1.5)
    await b.ws.send(json.dumps({"type": "stop"}))
    for _ in range(300):
        if b.of("replay_ready"):
            break
        await asyncio.sleep(0.1)
    return r, b


@pytest.fixture
def store(tmp_path):
    st = LocalStore(str(tmp_path))
    app.state.sessions.store = st             # the library follows the store
    yield st


@pytest.fixture
def login(monkeypatch):
    """Google check replaced (its own test is below): credential "google:<sub>:<email>" -> that user."""
    monkeypatch.setattr(settings, "google_client_id", CID)

    async def fake_verify(cred: str):
        _, sub, email, _pad = cred.split(":")
        return {"sub": sub, "email": email, "name": email.split("@")[0].title()}

    monkeypatch.setattr(app.state.google, "verify", fake_verify)

    async def do(srv, sub="111", email="namidu@example.com"):
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{srv['http']}/api/auth/google", json={"credential": f"google:{sub}:{email}:" + "x" * 20},
                             headers={"Origin": ORIGIN})
            assert r.status_code == 200, r.text
            return r.json()["token"]
    return do


def _bearer(tok):
    return {"Authorization": f"Bearer {tok}", "Origin": ORIGIN}


async def test_owner_key_deletes_replay(server, store, tmp_path):
    r, b = await _meeting(server)
    ready = b.of("replay_ready")[0]
    rid, key = ready["replay_id"], ready["replay_key"]
    assert ready["info"]["duration_s"] > 2 and "gaps" in ready["info"] and "conflicts" in ready["info"]
    meta = json.loads((tmp_path / meta_key(rid)).read_text())
    assert meta["key_sha"] == key_sha(key) and meta["owner"] is None and key not in json.dumps(meta)
    # the key never goes into the replay itself; rejoin (session token) gets it back
    assert key not in (tmp_path / "replays" / rid / "events.json.gz").read_bytes().decode("latin1")
    s = app.state.sessions.get(r["session_id"])
    assert s.snapshot()["replay_key"] == key and s.snapshot()["replay_info"]["date"]
    async with httpx.AsyncClient() as c:
        u = f"{server['http']}/api/replays/{rid}"
        assert (await c.get(u)).status_code == 200
        # CORS preflight lets the page send DELETE + X-Replay-Key
        pre = await c.options(u, headers={"Origin": ORIGIN, "Access-Control-Request-Method": "DELETE",
                                          "Access-Control-Request-Headers": "x-replay-key"})
        assert pre.status_code == 200 and "DELETE" in pre.headers["access-control-allow-methods"]
        assert (await c.delete(u, headers={"Origin": ORIGIN, "X-Replay-Key": "wrong" * 5})).status_code == 404
        assert (await c.delete(u, headers={"Origin": "https://evil.example", "X-Replay-Key": key})).status_code == 403
        assert (await c.delete(u, headers={"Origin": ORIGIN})).status_code == 404
        d = await c.delete(u, headers={"Origin": ORIGIN, "X-Replay-Key": key})
        assert d.status_code == 200 and d.json() == {"deleted": rid}
        snap = s.snapshot()                                 # a rejoin no longer offers the dead link
        assert snap["replay_id"] is None and snap["replay_key"] is None and snap["replay_info"] is None
        assert (await c.get(u)).status_code == 404
        img = [x for x in json.dumps(b.events).split('"') if x.startswith(f"/api/sessions/{r['session_id']}/images/")]
        assert img, "meeting had card images"
        assert (await c.get(f"{server['http']}/api/replays/{rid}/images/{img[0].rsplit('/', 1)[1]}")).status_code == 404
        assert (await c.delete(u, headers={"Origin": ORIGIN, "X-Replay-Key": key})).status_code == 404
    assert not (tmp_path / "replays" / rid).exists()


async def test_expiry_and_pinned(server, store, tmp_path, monkeypatch):
    _, b = await _meeting(server)
    rid = b.of("replay_ready")[0]["replay_id"]
    _, b2 = await _meeting(server)
    rid2 = b2.of("replay_ready")[0]["replay_id"]
    for x in (rid, rid2):                                  # both 31 days old
        p = tmp_path / meta_key(x)
        m = json.loads(p.read_text())
        m["created"] -= 31 * 86400
        p.write_text(json.dumps(m))
    monkeypatch.setattr(settings, "pinned_replays", {rid2})
    app.state.sessions.store = store                      # fresh library: no cached meta
    async with httpx.AsyncClient() as c:
        assert (await c.get(f"{server['http']}/api/replays/{rid}")).status_code == 404
        assert (await c.get(f"{server['http']}/api/replays/{rid2}")).status_code == 200      # the demo replay
    for _ in range(50):
        if not (tmp_path / "replays" / rid).exists():
            break
        await asyncio.sleep(0.05)
    assert not (tmp_path / "replays" / rid).exists() and (tmp_path / "replays" / rid2).exists()


async def test_sweep(tmp_path, monkeypatch):
    st = LocalStore(str(tmp_path))
    lib = Library(st)
    now = time.time()
    for rid, age in (("a" * 22, 31), ("b" * 22, 29), ("c" * 22, 40)):
        await st.put(f"replays/{rid}/events.json.gz", b"x", "application/gzip")
        await st.put(f"replays/{rid}/images/{'f' * 32}", b"x", "image/webp")
        await lib.put_meta({"rid": rid, "created": now - age * 86400, "key_sha": "", "owner": None})
    monkeypatch.setattr(settings, "pinned_replays", {"c" * 22})
    assert await Library(st).sweep() == 1
    assert sorted(p.name for p in (tmp_path / "replays").iterdir()) == ["b" * 22, "c" * 22]


async def test_sign_in_claim_and_dashboard(server, store, login):
    # two meetings before signing in (kept in the browser with their owner keys)
    _, b1 = await _meeting(server)
    _, b2 = await _meeting(server)
    k1, k2 = b1.of("replay_ready")[0], b2.of("replay_ready")[0]
    tok = await login(server)
    other = await login(server, sub="222", email="friend@example.com")
    async with httpx.AsyncClient() as c:
        h = f"{server['http']}"
        assert (await c.get(f"{h}/api/auth/config")).json() == {"google_client_id": CID}
        assert (await c.get(f"{h}/api/me/meetings")).status_code == 401
        assert (await c.get(f"{h}/api/me/meetings", headers=_bearer(tok + "x"))).status_code == 401
        me = (await c.get(f"{h}/api/me/meetings", headers=_bearer(tok))).json()
        assert me == {"user": {"email": "namidu@example.com", "name": "Namidu"}, "meetings": []}
        items = [{"rid": k1["replay_id"], "key": k1["replay_key"]}, {"rid": k2["replay_id"], "key": "wrong-key"},
                 {"rid": "../../x", "key": "k"}]
        cl = (await c.post(f"{h}/api/me/meetings/claim", json={"items": items}, headers=_bearer(tok))).json()
        assert cl["added"] == 1 and [m["rid"] for m in cl["meetings"]] == [k1["replay_id"]]
        row = cl["meetings"][0]
        assert set(row) == {"rid", "title", "date", "duration_s", "gaps", "conflicts", "created", "expires"}
        assert row["expires"] - row["created"] == 30 * 86400
        # someone else can't take it, even with the key
        cl2 = (await c.post(f"{h}/api/me/meetings/claim", json={"items": items[:1]}, headers=_bearer(other))).json()
        assert cl2["added"] == 0 and cl2["meetings"] == []
        # claiming twice changes nothing
        cl3 = (await c.post(f"{h}/api/me/meetings/claim", json={"items": items[:1]}, headers=_bearer(tok))).json()
        assert [m["rid"] for m in cl3["meetings"]] == [k1["replay_id"]]
        # owner deletes from the dashboard (login token, no key); others get 404
        u = f"{h}/api/replays/{k1['replay_id']}"
        assert (await c.delete(u, headers=_bearer(other))).status_code == 404
        assert (await c.delete(u, headers=_bearer(tok))).status_code == 200
        assert (await c.get(f"{h}/api/me/meetings", headers=_bearer(tok))).json()["meetings"] == []
        # the key of meeting 2 still works without signing in
        assert (await c.get(f"{h}/api/replays/{k2['replay_id']}")).status_code == 200


async def test_signed_in_meeting_goes_to_account_and_delete_all(server, store, login, tmp_path):
    tok = await login(server)
    _, b1 = await _meeting(server, headers={"Authorization": f"Bearer {tok}"})
    _, b2 = await _meeting(server, headers={"Authorization": f"Bearer {tok}"})
    _, b3 = await _meeting(server, headers={"Authorization": "Bearer not-a-token"})   # bad token = signed out
    rids = [b.of("replay_ready")[0]["replay_id"] for b in (b1, b2)]
    async with httpx.AsyncClient() as c:
        h = server["http"]
        ms = (await c.get(f"{h}/api/me/meetings", headers=_bearer(tok))).json()["meetings"]
        assert sorted(m["rid"] for m in ms) == sorted(rids)
        assert ms[0]["created"] >= ms[1]["created"]                         # newest first
        assert json.loads((tmp_path / meta_key(b3.of("replay_ready")[0]["replay_id"])).read_text())["owner"] is None
        assert (await c.delete(f"{h}/api/me", headers={**_bearer(tok), "Origin": "https://evil.example"})).status_code == 403
        d = await c.delete(f"{h}/api/me", headers=_bearer(tok))
        assert d.json() == {"deleted_meetings": 2}
        for rid in rids:
            assert (await c.get(f"{h}/api/replays/{rid}")).status_code == 404
        assert (await c.get(f"{h}/api/replays/{b3.of('replay_ready')[0]['replay_id']}")).status_code == 200
        assert (await c.get(f"{h}/api/me/meetings", headers=_bearer(tok))).json()["meetings"] == []
    uid = uid_for("111")
    assert not (tmp_path / "users" / f"{uid}.json").exists()


async def test_sign_in_off_without_client_id(server, monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "")
    async with httpx.AsyncClient() as c:
        assert (await c.get(f"{server['http']}/api/auth/config")).json() == {"google_client_id": ""}
        r = await c.post(f"{server['http']}/api/auth/google", json={"credential": "x" * 30}, headers={"Origin": ORIGIN})
        assert r.status_code == 404


def test_tokens():
    t = Tokens("secret")
    tok = t.make("u1", "a@b.c", "A")
    assert t.read(tok)["uid"] == "u1" and t.from_header(f"Bearer {tok}")["email"] == "a@b.c"
    body, sig = tok.split(".")
    assert t.read(body + "x." + sig) is None and t.read(tok + "x") is None and Tokens("other").read(tok) is None
    assert t.from_header(tok) is None and t.read("") is None
    old = Tokens("secret")
    import app.accounts as acc
    real = acc.time.time
    acc.time.time = lambda: real() - 20 * 86400                          # made 20 days ago (AUTH_DAYS 14)
    try:
        stale = old.make("u1", "a@b.c", "A")
    finally:
        acc.time.time = real
    assert t.read(stale) is None


async def test_google_verifier():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"keys": [{**jwk, "kid": "k1", "alg": "RS256", "use": "sig"}]},
                              headers={"cache-control": "public, max-age=20000"})

    v = GoogleVerifier(CID, httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    def tok(**over):
        now = int(time.time())
        c = {"iss": "https://accounts.google.com", "aud": CID, "sub": "1234", "email": "n@gmail.com",
             "email_verified": True, "name": "Namidu Harshana", "iat": now, "exp": now + 3600, **over}
        c = {k: v for k, v in c.items() if v is not None}
        return jwt.encode(c, key, algorithm="RS256", headers={"kid": "k1"})

    assert await v.verify(tok()) == {"sub": "1234", "email": "n@gmail.com", "name": "Namidu Harshana"}
    assert (await v.verify(tok(iss="accounts.google.com")))["sub"] == "1234"
    for bad in (tok(aud="someone-else"), tok(iss="https://evil.example"), tok(exp=int(time.time()) - 100),
                tok(email_verified=False), tok(email=None), "not.a.jwt"):
        with pytest.raises(AuthError):
            await v.verify(bad)
    # signed by another key
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode({"iss": "accounts.google.com", "aud": CID, "sub": "1", "email": "x@y.z", "email_verified": True,
                         "iat": int(time.time()), "exp": int(time.time()) + 60}, other, algorithm="RS256",
                        headers={"kid": "k1"})
    with pytest.raises(AuthError):
        await v.verify(forged)
    # unsigned ("alg": "none") is refused
    with pytest.raises(AuthError):
        await v.verify(jwt.encode({"sub": "1", "aud": CID}, None, algorithm="none", headers={"kid": "k1"}))
    # keys cached (one fetch for the good tokens; an unknown kid refetches)
    assert len(calls) == 1
    with pytest.raises(AuthError):
        await v.verify(jwt.encode({"sub": "1"}, key, algorithm="RS256", headers={"kid": "k9"}))
    assert len(calls) == 2
    with pytest.raises(AuthError):
        await GoogleVerifier("").verify(tok())


async def test_gcs_delete_and_list():
    seen = []

    def handler(req: httpx.Request):
        seen.append((req.method, str(req.url)))
        if "metadata.google.internal" in req.url.host:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3599})
        if req.method == "GET" and req.url.path == "/storage/v1/b/bkt/o":
            p = req.url.params
            if p.get("matchGlob"):
                assert p["matchGlob"] == "replays/*/meta.json"
                return httpx.Response(200, json={"items": [{"name": "replays/r1/meta.json"}]})
            assert p["prefix"] == "replays/r1/"
            if not p.get("pageToken"):
                return httpx.Response(200, json={"items": [{"name": "replays/r1/meta.json"}], "nextPageToken": "n"})
            return httpx.Response(200, json={"items": [{"name": "replays/r1/images/a"}]})
        if req.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(404)

    g = GcsStore("bkt", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await g.list_meta() == ["replays/r1/meta.json"]
    await g.delete_prefix("replays/r1/")
    dels = sorted(u for m, u in seen if m == "DELETE")
    assert dels == ["https://storage.googleapis.com/storage/v1/b/bkt/o/replays%2Fr1%2Fimages%2Fa",
                    "https://storage.googleapis.com/storage/v1/b/bkt/o/replays%2Fr1%2Fmeta.json"]
    await g.delete_prefix("users/u1.json")
    assert ("DELETE", "https://storage.googleapis.com/storage/v1/b/bkt/o/users%2Fu1.json") in seen
