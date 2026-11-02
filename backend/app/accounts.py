"""Google sign-in (for "My meetings" only) and the meeting library: who owns which replay, delete, 30-day expiry.

Sign-in: the browser gets a Google ID token from Google Identity Services ("Sign in with Google"); we check its
signature (Google's public keys), audience (our client id), issuer, expiry and that the email is verified, then give
the browser our own login token (HMAC-signed, AUTH_DAYS). We keep only the email and the name.

  users/<uid>.json  {"v":1, "email", "name", "meetings": [rid, ...]}     uid = sha256("google:" + Google sub)[:32]

Owning a replay: replays/<rid>/meta.json has the sha256 of the meeting browser's secret owner key ("key_sha") and,
once someone signs in with that key or started the meeting signed in, "owner" = uid.
No database: Cloud Storage only (one Cloud Run instance, so a per-user asyncio lock is enough).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from collections import OrderedDict

import httpx
import jwt

from .config import settings
from .replay import expired, key_sha, meta_key, summary, valid_rid
from .security import clean_text

log = logging.getLogger("minutecanvas.accounts")

GOOGLE_CERTS = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")
MAX_MEETINGS = 500


class AuthError(Exception):
    pass


# ---------------- Google ID token ----------------
class GoogleVerifier:
    def __init__(self, client_id: str, http: httpx.AsyncClient | None = None, certs_url: str = GOOGLE_CERTS):
        self.client_id = client_id
        self.http = http or httpx.AsyncClient(timeout=10)
        self.certs_url = certs_url
        self._keys: dict[str, object] = {}
        self._keys_exp = 0.0
        self._lock = asyncio.Lock()

    async def _key(self, kid: str, refresh: bool = False):
        async with self._lock:
            if refresh or not self._keys or time.monotonic() > self._keys_exp or kid not in self._keys:
                r = await self.http.get(self.certs_url)
                r.raise_for_status()
                self._keys = {k["kid"]: jwt.PyJWK(k).key for k in r.json().get("keys", []) if k.get("kid")}
                max_age = 3600
                for part in r.headers.get("cache-control", "").split(","):
                    if part.strip().startswith("max-age="):
                        try:
                            max_age = int(part.strip()[8:])
                        except ValueError:
                            pass
                self._keys_exp = time.monotonic() + min(max_age, 6 * 3600)
            return self._keys.get(kid)

    async def verify(self, credential: str) -> dict:
        """-> {"sub", "email", "name"}; raises AuthError."""
        if not self.client_id:
            raise AuthError("sign-in is not set up on this server")
        try:
            kid = jwt.get_unverified_header(credential).get("kid", "")
        except jwt.PyJWTError as e:
            raise AuthError("bad token") from e
        try:
            key = await self._key(kid)
        except httpx.HTTPError as e:
            raise AuthError("can't reach Google to check the sign-in") from e
        if key is None:
            raise AuthError("bad token")
        try:
            c = jwt.decode(credential, key, algorithms=["RS256"], audience=self.client_id,
                           options={"require": ["exp", "iat", "iss", "sub", "aud"]}, leeway=30)
        except jwt.PyJWTError as e:
            raise AuthError("bad token") from e
        if c.get("iss") not in GOOGLE_ISSUERS:
            raise AuthError("bad token")
        if not c.get("email") or c.get("email_verified") not in (True, "true"):
            raise AuthError("this Google account has no verified email")
        return {"sub": str(c["sub"]), "email": clean_text(c["email"], 200),
                "name": clean_text(c.get("name") or c["email"].split("@")[0], 80)}


def uid_for(sub: str) -> str:
    return hashlib.sha256(f"google:{sub}".encode()).hexdigest()[:32]


# ---------------- our login token ----------------
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Tokens:
    def __init__(self, secret: str):
        if not secret:
            secret = secrets.token_hex(32)
            if settings.google_client_id:
                log.warning("AUTH_SECRET is not set: login tokens end when the server restarts")
        self.secret = secret.encode()

    def make(self, uid: str, email: str, name: str) -> str:
        body = _b64(json.dumps({"uid": uid, "email": email, "name": name,
                                "exp": int(time.time() + settings.auth_days * 86400)}, separators=(",", ":")).encode())
        sig = _b64(hmac.new(self.secret, body.encode(), hashlib.sha256).digest())
        return f"{body}.{sig}"

    def read(self, token: str) -> dict | None:
        try:
            body, sig = token.split(".")
            good = _b64(hmac.new(self.secret, body.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(sig, good):
                return None
            p = json.loads(_unb64(body))
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(p, dict) or not isinstance(p.get("uid"), str) or float(p.get("exp", 0)) < time.time():
            return None
        return p

    def from_header(self, auth: str | None) -> dict | None:
        if not auth or not auth.startswith("Bearer "):
            return None
        return self.read(auth[7:].strip())


# ---------------- library ----------------
def user_key(uid: str) -> str:
    return f"users/{uid}.json"


class Library:
    """Replay metadata (cached), owners, user meeting lists, delete, expiry sweep."""

    def __init__(self, store):
        self.store = store
        self._meta: OrderedDict[str, tuple[float, dict | None]] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, name: str) -> asyncio.Lock:
        if name not in self._locks:
            self._locks[name] = asyncio.Lock()
        return self._locks[name]

    # ---- replay meta ----
    async def meta(self, rid: str) -> dict | None:
        hit = self._meta.get(rid)
        if hit and time.monotonic() - hit[0] < 120:
            return hit[1]
        raw = await self.store.get(meta_key(rid))
        m = json.loads(raw) if raw else None
        self._meta[rid] = (time.monotonic(), m)
        self._meta.move_to_end(rid)
        while len(self._meta) > 2000:
            self._meta.popitem(last=False)
        return m

    async def put_meta(self, m: dict) -> None:
        await self.store.put(meta_key(m["rid"]), json.dumps(m, separators=(",", ":")).encode(), "application/json")
        self._meta[m["rid"]] = (time.monotonic(), m)

    async def readable(self, rid: str) -> bool:
        """False = deleted, expired or never existed (a replay without meta.json is never served)."""
        m = await self.meta(rid)
        if m is None or m.get("deleted"):
            return False
        if expired(m):
            asyncio.create_task(self._remove_quietly(rid, m))
            return False
        return True

    async def _remove_quietly(self, rid: str, m: dict) -> None:
        try:
            await self.delete(rid, m)
        except Exception:  # noqa: BLE001
            pass

    async def saved(self, rid: str, owner_key: str, info: dict, owner: dict | None) -> None:
        """A meeting's replay was just saved."""
        m = {"v": 1, "rid": rid, "created": round(time.time()), "key_sha": key_sha(owner_key),
             "owner": owner["uid"] if owner else None, **info}
        await self.put_meta(m)
        if owner:
            await self._add_to_user(owner, [rid])

    # ---- users ----
    async def _user(self, uid: str) -> dict | None:
        raw = await self.store.get(user_key(uid))
        return json.loads(raw) if raw else None

    async def _add_to_user(self, user: dict, rids: list[str]) -> None:
        async with self._lock(user["uid"]):
            u = await self._user(user["uid"]) or {"v": 1, "meetings": []}
            u["email"], u["name"] = user["email"], user["name"]
            for rid in rids:
                if rid not in u["meetings"]:
                    u["meetings"].append(rid)
            u["meetings"] = u["meetings"][-MAX_MEETINGS:]
            await self.store.put(user_key(user["uid"]), json.dumps(u, separators=(",", ":")).encode(),
                                 "application/json")

    async def _drop_from_user(self, uid: str, rid: str) -> None:
        async with self._lock(uid):
            u = await self._user(uid)
            if u and rid in u.get("meetings", []):
                u["meetings"].remove(rid)
                await self.store.put(user_key(uid), json.dumps(u, separators=(",", ":")).encode(), "application/json")

    async def meetings(self, user: dict) -> list[dict]:
        """Newest first; deleted / expired ones are left out (and dropped from the list)."""
        u = await self._user(user["uid"])
        if not u:
            return []
        out, gone = [], []
        for rid in u.get("meetings", []):
            m = await self.meta(rid)
            if not m or m.get("deleted") or m.get("owner") != user["uid"] or expired(m):
                gone.append(rid)
                continue
            out.append(summary(m))
        for rid in gone:
            await self._drop_from_user(user["uid"], rid)
        out.sort(key=lambda r: r.get("created") or 0, reverse=True)
        return out

    async def claim(self, user: dict, items: list[tuple[str, str]]) -> int:
        """Meetings made on this browser before signing in -> this account (the owner key proves it)."""
        got = []
        for rid, key in items:
            if not valid_rid(rid) or not key:
                continue
            async with self._lock(f"r:{rid}"):
                m = await self.meta(rid)
                if not m or m.get("deleted") or expired(m) or not hmac.compare_digest(m.get("key_sha", ""), key_sha(key)):
                    continue
                if m.get("owner") not in (None, user["uid"]):
                    continue           # already someone else's account
                if m.get("owner") is None:
                    m = {**m, "owner": user["uid"]}
                    await self.put_meta(m)
            got.append(rid)
        if got:
            await self._add_to_user(user, got)
        return len(got)

    # ---- delete ----
    async def can_delete(self, rid: str, key: str | None, user: dict | None) -> dict | None:
        m = await self.meta(rid)
        if not m or m.get("deleted"):
            return None
        if key and hmac.compare_digest(m.get("key_sha", ""), key_sha(key)):
            return m
        if user and m.get("owner") == user["uid"]:
            return m
        return None

    async def delete(self, rid: str, m: dict) -> None:
        async with self._lock(f"r:{rid}"):
            await self._remove(rid, m)

    async def _remove(self, rid: str, m: dict) -> None:
        # the tombstone first: if deleting the files fails half way, nothing is served any more
        self._meta[rid] = (time.monotonic(), {**m, "deleted": True})
        try:
            await self.store.put(meta_key(rid), json.dumps({"rid": rid, "deleted": True}).encode(), "application/json")
            await self.store.delete_prefix(f"replays/{rid}/")
        except Exception as e:  # noqa: BLE001
            log.warning("replay %s delete failed: %s", rid, e)
            raise
        self._meta[rid] = (time.monotonic(), None)
        if m.get("owner"):
            await self._drop_from_user(m["owner"], rid)
        log.info("replay %s deleted", rid)

    async def delete_user(self, user: dict) -> list[str]:
        """"Delete all my data": every replay this account owns + the account's list. Returns the deleted ids."""
        gone = []
        u = await self._user(user["uid"])
        for rid in list((u or {}).get("meetings", [])):
            m = await self.meta(rid)
            if m and m.get("owner") == user["uid"] and not m.get("deleted"):
                await self.delete(rid, m)
                gone.append(rid)
        await self.store.delete_prefix(user_key(user["uid"]))
        return gone

    async def sweep(self) -> int:
        """Removes replays past REPLAY_KEEP_DAYS (pinned ones stay)."""
        n = 0
        for k in await self.store.list_meta():
            rid = k.split("/")[1]
            raw = await self.store.get(k)
            m = json.loads(raw) if raw else None
            if not m:
                continue
            if m.get("deleted") or expired(m):
                try:
                    await self.delete(rid, {**m, "rid": rid})
                    n += 1
                except Exception:  # noqa: BLE001
                    pass
        if n:
            log.info("sweep removed %d old replays", n)
        return n
