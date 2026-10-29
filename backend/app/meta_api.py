"""Muse Spark (chat completions, json_schema) and Muse Image (/v1/responses) clients.

Request shapes follow the Oct 27 API tests. If a field name differs from ~/mc-test,
change it ONLY here (build_spark_request / build_image_request / extract_image).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
import re

import httpx

from .config import settings
from .prompts import IMAGE_STYLE

log = logging.getLogger("minutecanvas.meta")


class MetaAPIError(RuntimeError):
    pass


class ToolsUnsupported(MetaAPIError):
    """The API rejected `tools`; the Flow agent switches to the JSON fallback."""


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.meta_api_key}", "Content-Type": "application/json"}


def build_spark_request(system: str, user: str, schema_name: str, schema: dict, extras: bool = True,
                        history: list[dict] | None = None) -> dict:
    """extras=False is exactly the body that passed the Oct 27 test.
    extras=True adds reasoning_effort (faster) + strict schema; dropped automatically on HTTP 400.
    history: extra messages after the first user message (Flow agent JSON fallback retry turn)."""
    js = {"name": schema_name, "schema": schema if extras else plain_schema(schema)}
    body = {
        "model": settings.spark_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}, *(history or [])],
        "response_format": {"type": "json_schema", "json_schema": js},
    }
    if extras:
        js["strict"] = True
        if settings.spark_reasoning:
            body["reasoning_effort"] = settings.spark_reasoning
    return body


def build_tools_request(messages: list[dict], tools: list[dict], extras: bool = True) -> dict:
    """Flow agent: OpenAI-style function calling (tools / tool_calls / role "tool")."""
    body = {"model": settings.spark_model, "messages": messages, "tools": tools, "tool_choice": "auto"}
    if extras:
        body["parallel_tool_calls"] = True
        if settings.spark_reasoning:
            body["reasoning_effort"] = settings.spark_reasoning
    return body


def build_image_request(prompt: str) -> dict:
    """Same body as the passing Oct 27 test (mc_api_test.py image)."""
    return {
        "model": settings.image_model,
        "input": f"{IMAGE_STYLE} {prompt}",
        "store": False,
        "tools": [{
            "type": "image_generation",
            "enable_image_search": False,
            "enable_web_search": False,
            "enable_shell": False,
            "reasoning_strength": settings.image_reasoning,
            "output_format": "webp",
            "size": "1024x1024",
        }],
    }


def plain_schema(node):
    """Tested-style schema: no nullable types (["string","null"] -> "string", null enum -> "none"),
    no additionalProperties. The board treats "" / "none" as empty."""
    if isinstance(node, list):
        return [plain_schema(x) for x in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for k, v in node.items():
        if k == "additionalProperties":
            continue
        if k == "type" and isinstance(v, list):
            v = next((x for x in v if x != "null"), "string")
        elif k == "enum" and isinstance(v, list) and None in v:
            v = [x for x in v if x is not None] + ["none"]
        out[k] = plain_schema(v)
    return out


_B64_KEYS = ("b64_json", "result", "image_base64", "base64", "data", "image")


def extract_image(payload) -> bytes | None:
    """Tested shape: output[] item {"type":"image_generation_call","result":<base64>}.
    Falls back to searching the whole JSON."""
    if isinstance(payload, dict):
        for o in payload.get("output") or []:
            if isinstance(o, dict) and o.get("type") == "image_generation_call" and isinstance(o.get("result"), str):
                img = _decode_b64(o["result"])
                if img:
                    return img
    stack = [payload]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k in _B64_KEYS:
                v = cur.get(k)
                if isinstance(v, str) and len(v) > 200:
                    img = _decode_b64(v)
                    if img:
                        return img
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
        elif isinstance(cur, str) and cur.startswith("data:image/"):
            img = _decode_b64(cur)
            if img:
                return img
    return None


def _decode_b64(s: str) -> bytes | None:
    if s.startswith("data:"):
        s = s.split(",", 1)[-1]
    try:
        raw = base64.b64decode(s, validate=False)
    except Exception:
        return None
    return raw if image_mime(raw) else None


def image_mime(raw: bytes) -> str | None:
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if raw[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return None


class MetaClient:
    def __init__(self, http: httpx.AsyncClient | None = None):
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0))
        self.spark_extras = True     # becomes False if the API rejects reasoning_effort/strict
        self.tools_extras = True     # becomes False if the API rejects parallel_tool_calls/reasoning_effort
        self.tools_ok = True         # becomes False if the API rejects `tools` -> Flow agent uses JSON calls

    async def aclose(self):
        await self.http.aclose()

    async def spark_json(self, system: str, user: str, schema_name: str, schema: dict,
                         history: list[dict] | None = None) -> dict:
        if settings.mock_meta:
            await _mock_wait()
            if schema_name == "flow_calls":
                return _mock_flow_json(user, history)
            if schema_name == "verify_conflict":
                return {"same_fact": True, "a_is_about": "mock", "b_is_about": "mock"}
            return _mock_spark(user, schema_name)
        last = None
        for attempt in range(3):
            body = build_spark_request(system, user, schema_name, schema, self.spark_extras, history)
            try:
                r = await self.http.post(f"{settings.meta_api_base}/chat/completions", headers=_headers(), json=body)
                if r.status_code == 400 and self.spark_extras:
                    log.warning("spark rejected reasoning_effort/strict, using the tested body: %s", r.text[:300])
                    self.spark_extras = False
                    continue
                if r.status_code >= 400:
                    raise MetaAPIError(f"spark HTTP {r.status_code}: {r.text[:300]}")
                data = r.json()
                content = data["choices"][0]["message"]["content"]
                if isinstance(content, list):  # some APIs return content parts
                    content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
                return json.loads(_strip_fence(content))
            except (MetaAPIError, httpx.HTTPError, KeyError, ValueError) as e:
                last = e
                log.warning("spark attempt %d failed: %s", attempt + 1, e)
        raise MetaAPIError(str(last))

    async def spark_tools(self, messages: list[dict], tools: list[dict]) -> dict:
        """One agent turn. Returns the assistant message ({"content":..., "tool_calls":[...]})."""
        if settings.mock_meta:
            await _mock_wait()
            return _mock_flow(messages)
        last = None
        for attempt in range(3):
            body = build_tools_request(messages, tools, self.tools_extras)
            try:
                r = await self.http.post(f"{settings.meta_api_base}/chat/completions", headers=_headers(), json=body)
                if r.status_code == 400:
                    if self.tools_extras:
                        log.warning("tools call rejected, retrying without parallel_tool_calls/reasoning_effort: %s", r.text[:300])
                        self.tools_extras = False
                        continue
                    raise ToolsUnsupported(f"tools HTTP 400: {r.text[:300]}")
                if r.status_code >= 400:
                    raise MetaAPIError(f"tools HTTP {r.status_code}: {r.text[:300]}")
                msg = r.json()["choices"][0]["message"]
                if not isinstance(msg, dict):
                    raise ValueError("no message")
                return msg
            except ToolsUnsupported:
                raise
            except (MetaAPIError, httpx.HTTPError, KeyError, IndexError, ValueError) as e:
                last = e
                log.warning("tools attempt %d failed: %s", attempt + 1, e)
        raise MetaAPIError(str(last))

    async def image(self, prompt: str) -> bytes:
        if settings.mock_meta:
            return _mock_image()
        r = await self.http.post(f"{settings.meta_api_base}/responses", headers=_headers(), json=build_image_request(prompt))
        if r.status_code >= 400:
            raise MetaAPIError(f"image HTTP {r.status_code}: {r.text[:300]}")
        img = extract_image(r.json())
        if not img:
            raise MetaAPIError("image: no base64 image in response")
        return img


def _strip_fence(s: str) -> str:
    s = s.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.S)
    return m.group(1) if m else s


# ---------------- mock mode (MOCK_META=1): no credits used, for UI work ----------------
_MOCK_CARDS = [
    ("goals", "Launch MVP before December", None, None, None),
    ("decisions", "Use Stripe for payments", None, None, None),
    ("actions", "Send API docs to client", "Speaker A", "Friday", None),
    ("updates", "Login API integration", "Speaker B", None, "in_progress"),
    ("questions", "Who hosts the staging server?", None, None, None),
]


def _mock_spark(user: str, schema_name: str) -> dict:
    if schema_name == "summary":
        return {"title": "Mock Client Sync", "summary": "Mock summary of the meeting.",
                "takeaways": ["Stripe is the payment provider", "Login API is blocked on the client's keys",
                              "API docs go to the client by Friday"],
                "image_prompt": "team around a table"}
    # scripted: the OTP time becomes a decision card, so the "Conflict · not decided" tag shows in mock mode
    try:
        lines = json.loads(user).get("new_transcript", [])
    except ValueError:
        lines = []
    for line in lines:
        m = re.match(r"\[([\d:]+)\][^:]*:\s*(.*)", line)
        if m and "valid for ten minutes" in m.group(2):
            return {"new_cards": [{"column": "decisions", "title": "OTP valid for ten minutes",
                                   "detail": "Login code expires after ten minutes.", "owner": None, "due": None,
                                   "status": None, "said_at": m.group(1), "image_prompt": "a phone with a timer"}],
                    "updates": [], "resolved_questions": [], "meeting_title": "Mock Client Sync"}
    col, title, owner, due, status = random.choice(_MOCK_CARDS)
    return {
        "new_cards": [{"column": col, "title": f"{title} #{random.randint(1, 999)}", "detail": "Mock card from MOCK_META mode.",
                       "owner": owner, "due": due, "status": status, "image_prompt": title}],
        "updates": [], "resolved_questions": [], "meeting_title": "Mock Client Sync",
    }


def _mock_image() -> bytes:
    # 1x1 PNG
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="
    )


async def _mock_wait() -> None:
    """MOCK_DELAY_S=4: mock calls take as long as real ones, so the "thinking" UI states can be checked."""
    delay = float(os.environ.get("MOCK_DELAY_S") or 0)
    if delay > 0:
        await asyncio.sleep(delay)


def _mock_flow(messages: list[dict]) -> dict:
    from .mock_script import mock_flow
    return mock_flow(messages)


def _mock_flow_json(user: str, history: list[dict] | None) -> dict:
    from .mock_script import mock_flow_json
    return mock_flow_json(user, history)
