"""Real-API test of the backend without the frontend.

Streams a WAV file at real-time speed to a running backend (as the browser would),
prints live transcript + cards, then stops and prints the report.

  cd backend && source .venv/bin/activate
  uvicorn app.main:app --port 8000 --ws-ping-interval 20      # terminal 1
  python scripts/live_test.py meeting.wav --seconds 120         # terminal 2
"""
from __future__ import annotations

import argparse
import array
import asyncio
import json
import time
import wave

import httpx
import websockets

RATE = 24_000


def load_wav_24k_mono(path: str) -> bytes:
    with wave.open(path, "rb") as w:
        ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw != 2:
        raise SystemExit("WAV must be 16-bit PCM")
    a = array.array("h")
    a.frombytes(raw)
    if ch > 1:
        a = array.array("h", (sum(a[i:i + ch]) // ch for i in range(0, len(a), ch)))
    if sr != RATE:
        ratio = sr / RATE
        out = array.array("h")
        for i in range(int(len(a) / ratio)):
            x = i * ratio
            j = int(x)
            f = x - j
            nxt = a[j + 1] if j + 1 < len(a) else a[j]
            out.append(int(a[j] * (1 - f) + nxt * f))
        a = out
    return a.tobytes()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--backend", default="http://localhost:8000")
    ap.add_argument("--origin", default="http://localhost:3000")
    ap.add_argument("--seconds", type=float, default=0, help="stop after N seconds (0 = whole file)")
    ap.add_argument("--keywords", default="", help="comma separated, e.g. Claude,MinuteCanvas")
    ap.add_argument("--access-code", default=None)
    args = ap.parse_args()

    pcm = load_wav_24k_mono(args.wav)
    if args.seconds:
        pcm = pcm[: int(args.seconds * RATE * 2)]
    print(f"audio: {len(pcm) / RATE / 2:.1f} s")

    async with httpx.AsyncClient() as c:
        r = await c.post(f"{args.backend}/api/sessions", headers={"Origin": args.origin},
                         json={"keywords": [k for k in args.keywords.split(",") if k], "access_code": args.access_code})
        r.raise_for_status()
        s = r.json()

    ws_url = args.backend.replace("http", "ws", 1) + f"/ws/{s['session_id']}"
    async with websockets.connect(ws_url, origin=args.origin, max_size=2**24) as ws:
        await ws.send(json.dumps({"type": "auth", "token": s["token"]}))
        print("hello:", json.loads(await ws.recv())["state"])
        await ws.send(json.dumps({"type": "start"}))
        done = asyncio.Event()

        async def reader():
            async for m in ws:
                e = json.loads(m)
                t = e["type"]
                if t == "transcript_final":
                    print(f"  [{e['t']:6.1f}] {e['speaker']}: {e['text']}")
                elif t in ("cards_added", "cards_updated"):
                    for cd in e["cards"]:
                        extra = " ".join(f"{k}={cd[k]}" for k in ("owner", "due", "status") if cd.get(k))
                        print(f"  {'+' if t == 'cards_added' else '~'} {cd['column']:<9} {cd['title']} {extra} [img:{cd['image_status']}]")
                elif t in ("voice_status", "spark_status", "state", "notice", "error"):
                    print(f"  ({t}: {json.dumps({k: v for k, v in e.items() if k != 'type'})})")
                elif t == "report_ready":
                    print("\nREPORT:\n" + json.dumps(e["report"], indent=2)[:4000])
                    done.set()
                    return

        rt = asyncio.create_task(reader())
        chunk = RATE * 2 // 10
        t0 = time.monotonic()
        for i, o in enumerate(range(0, len(pcm), chunk)):
            await ws.send(o.to_bytes(8, "little") + pcm[o:o + chunk])
            await asyncio.sleep(max(0, t0 + (i + 1) * 0.1 - time.monotonic()))
        print("audio done, stopping…")
        await ws.send(json.dumps({"type": "stop"}))
        try:
            await asyncio.wait_for(done.wait(), timeout=180)
        finally:
            rt.cancel()


if __name__ == "__main__":
    asyncio.run(main())
