"""Replay: every server -> browser message of a meeting, stamped with the meeting time it was sent at.

A share link (/replay?id=<replay id>) plays the meeting from 00:00: transcript, board and Flow Map appear in the same
order and at the same meeting time as they did live. The replay id is a random 128-bit token = the access key
(no login). Audio is never saved; only text, the board, the Flow Map and the card images.

Storage: local folder (REPLAY_DIR, default backend/replays) or a Cloud Storage bucket (REPLAY_BUCKET, Cloud Run:
the service account token comes from the metadata server, so no extra package is needed).
  replays/<rid>/events.json.gz        {"v":1,"title","date","duration_s","speakers","events":[[t, msg], ...]}
  replays/<rid>/images/<img id>       card / cover images
  replays/<rid>/meta.json             {"created", "key_sha", "owner", title, date, duration, gap/conflict counts}
  users/<uid>.json                    a signed-in user's meeting list (see accounts.py)

v6.18: the meeting's own browser gets a secret owner key (only its sha256 is stored) that deletes the replay; a
signed-in owner can delete it too. Replays are kept REPLAY_KEEP_DAYS (30) days: older ones are refused on read and
removed by a sweep; ids in PINNED_REPLAYS (the public demo replay) are kept.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import re
import secrets
import shutil
import time
from pathlib import Path
from urllib.parse import quote

import httpx

from .config import settings

log = logging.getLogger("minutecanvas.replay")

RID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
IMG_RE = re.compile(r"^[a-f0-9]{32}$")

# messages a replay needs (pings, hello, notices, errors and voice status are live-only)
KEEP = {
    "state", "transcript_partial", "transcript_partial_clear", "transcript_final", "cards_added", "cards_updated",
    "card_deleted", "board_title", "speakers", "spark_status", "flow", "flow_status", "report_ready",
}


def new_replay_id() -> str:
    return secrets.token_urlsafe(16)


def valid_rid(rid: str) -> bool:
    return bool(RID_RE.match(rid or ""))


def valid_img(img: str) -> bool:
    return bool(IMG_RE.match(img or ""))


class Recorder:
    """Collects the messages of one session (in memory, until Stop)."""

    def __init__(self) -> None:
        self.events: list[list] = []
        self.bytes = 0
        self.full = False

    def add(self, t: float, msg: dict) -> None:
        if self.full or msg.get("type") not in KEEP:
            return
        raw = json.dumps(msg, separators=(",", ":"))
        self.bytes += len(raw)
        if self.bytes > settings.replay_max_bytes:      # a 3-hour meeting stays far below this; protects memory
            self.full = True
            log.warning("replay log full (%d bytes), later messages are not kept", self.bytes)
            return
        self.events.append([round(t, 2), json.loads(raw)])   # a copy: later changes to the dicts don't leak in

    def payload(self, sid: str, rid: str, title: str | None, date: str, duration_s: float,
                speakers: dict[str, str]) -> bytes:
        body = json.dumps({"v": 1, "title": title, "date": date, "duration_s": round(duration_s, 1),
                           "speakers": speakers, "events": self.events}, separators=(",", ":"))
        # images: the session URL needs the session token; the replay URL needs only the replay id
        body = body.replace(f"/api/sessions/{sid}/images/", f"/api/replays/{rid}/images/")
        return gzip.compress(body.encode(), compresslevel=6)


# ---------------- storage ----------------
class LocalStore:
    def __init__(self, root: str):
        self.root = Path(root)
        if not self.root.is_absolute():
            self.root = Path(__file__).resolve().parent.parent / self.root

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        p = self.root / key
        await asyncio.to_thread(self._write, p, data)

    @staticmethod
    def _write(p: Path, data: bytes) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)

    async def get(self, key: str) -> bytes | None:
        p = self.root / key
        try:
            return await asyncio.to_thread(p.read_bytes)
        except (FileNotFoundError, NotADirectoryError):
            return None

    async def delete_prefix(self, prefix: str) -> None:
        """Deletes every object under `prefix` (a "folder", ends with "/") or the one object named `prefix`."""
        p = self.root / prefix
        await asyncio.to_thread(lambda: shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True))

    async def list_meta(self) -> list[str]:
        """Keys of all replays/<rid>/meta.json."""
        base = self.root / "replays"
        found = await asyncio.to_thread(lambda: sorted(base.glob("*/meta.json")) if base.exists() else [])
        return [f"replays/{f.parent.name}/meta.json" for f in found]


class GcsStore:
    """Cloud Storage JSON API with the Cloud Run service account (metadata server token)."""

    TOKEN_URL = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"

    def __init__(self, bucket: str, http: httpx.AsyncClient | None = None):
        self.bucket = bucket
        self.http = http or httpx.AsyncClient(timeout=30)
        self._token: str | None = None
        self._token_exp = 0.0
        self._lock = asyncio.Lock()

    async def _auth(self) -> dict:
        async with self._lock:
            if not self._token or time.monotonic() > self._token_exp:
                r = await self.http.get(self.TOKEN_URL, headers={"Metadata-Flavor": "Google"})
                r.raise_for_status()
                j = r.json()
                self._token = j["access_token"]
                self._token_exp = time.monotonic() + max(60, int(j.get("expires_in", 300)) - 120)
            return {"Authorization": f"Bearer {self._token}"}

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        r = await self.http.post(
            f"https://storage.googleapis.com/upload/storage/v1/b/{self.bucket}/o",
            params={"uploadType": "media", "name": key}, content=data,
            headers={**await self._auth(), "Content-Type": content_type})
        r.raise_for_status()

    async def get(self, key: str) -> bytes | None:
        r = await self.http.get(
            f"https://storage.googleapis.com/storage/v1/b/{self.bucket}/o/{quote(key, safe='')}",
            params={"alt": "media"}, headers=await self._auth())
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content

    async def _list(self, **params) -> list[str]:
        names: list[str] = []
        token = None
        while True:
            q = {**params, "fields": "items(name),nextPageToken"}
            if token:
                q["pageToken"] = token
            r = await self.http.get(f"https://storage.googleapis.com/storage/v1/b/{self.bucket}/o",
                                    params=q, headers=await self._auth())
            r.raise_for_status()
            j = r.json()
            names += [i["name"] for i in j.get("items", [])]
            token = j.get("nextPageToken")
            if not token:
                return names

    async def _delete(self, key: str) -> None:
        r = await self.http.delete(f"https://storage.googleapis.com/storage/v1/b/{self.bucket}/o/{quote(key, safe='')}",
                                   headers=await self._auth())
        if r.status_code not in (200, 204, 404):
            r.raise_for_status()

    async def delete_prefix(self, prefix: str) -> None:
        keys = await self._list(prefix=prefix) if prefix.endswith("/") else [prefix]
        sem = asyncio.Semaphore(8)

        async def one(k: str):
            async with sem:
                await self._delete(k)
        await asyncio.gather(*(one(k) for k in keys))

    async def list_meta(self) -> list[str]:
        return await self._list(prefix="replays/", matchGlob="replays/*/meta.json")


def make_store():
    if settings.replay_bucket:
        return GcsStore(settings.replay_bucket)
    return LocalStore(settings.replay_dir)


def events_key(rid: str) -> str:
    return f"replays/{rid}/events.json.gz"


def image_key(rid: str, img: str) -> str:
    return f"replays/{rid}/images/{img}"


def meta_key(rid: str) -> str:
    return f"replays/{rid}/meta.json"


def new_owner_key() -> str:
    return secrets.token_urlsafe(24)


def key_sha(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def pinned(rid: str) -> bool:
    return rid in settings.pinned_replays


def expired(meta: dict, now: float | None = None) -> bool:
    if pinned(meta.get("rid", "")):
        return False
    created = float(meta.get("created") or 0)
    return (now or time.time()) - created > settings.replay_keep_days * 86400


def summary(meta: dict) -> dict:
    """The dashboard row of a replay (no secrets)."""
    return {k: meta.get(k) for k in ("rid", "title", "date", "duration_s", "gaps", "conflicts", "created")} | {
        "expires": None if pinned(meta.get("rid", "")) else
        round(float(meta.get("created") or 0) + settings.replay_keep_days * 86400)}
