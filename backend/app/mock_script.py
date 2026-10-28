"""MOCK_META=1: a scripted client meeting + a scripted Flow agent (0 credits).

The scripted agent goes through the same quote check as the real one. One call has a
slightly wrong quote on purpose, so the reject -> fix retry path is visible in mock mode too.
"""
from __future__ import annotations

import json
import re

# (speaker, text) — A = client, B = developer
MOCK_LINES: list[tuple[str, str]] = [
    ("A", "We want a mobile app for our sofa shop so customers can order online."),
    ("B", "Okay, so the customer opens the app and lands on the home screen with categories."),
    ("A", "Yes, from the home screen they pick a sofa and see the product page."),
    ("B", "On the product page they choose the colour and add it to the cart."),
    ("A", "Then they go to checkout and pay by card."),
    ("B", "After the payment succeeds we show an order confirmation screen."),
    ("A", "Delivery should take five working days inside Colombo."),
    ("B", "Right now the add to cart button crashes on older Android phones."),
    ("A", "Also customers must log in with an OTP before checkout."),
    ("B", "The OTP expires in five minutes."),
    ("A", "Let's keep it simple, the code is valid for ten minutes."),
    ("B", "Let's move on to the admin panel for stock updates."),
    ("B", "Good news, we fixed the add to cart crash in yesterday's build."),
    ("A", "If the payment fails we just show an error and let them try again."),
]


def _calls_for_line(text: str, flow: dict) -> list[tuple[str, dict]]:
    t = text.lower()
    bugs = {b["text"].lower(): b["id"] for b in flow.get("open_bugs", [])}
    gaps = {g["question"].lower(): g["id"] for g in flow.get("gaps", []) if not g.get("resolved")}
    out: list[tuple[str, dict]] = []
    if "home screen with categories" in t:
        out.append(("add_step", {"label": "Home screen", "kind": "start",
                                 "quote": "lands on the home screen with categories"}))
    elif "see the product page" in t:
        out.append(("add_step", {"label": "Product page", "kind": "step", "after": "Home screen",
                                 "link_label": "pick a sofa", "quote": "they pick a sofa and see the product page"}))
    elif "add it to the cart" in t:
        out.append(("add_step", {"label": "Cart", "kind": "step", "after": "Product page",
                                 "quote": "choose the colour and add it to the cart"}))
    elif "checkout and pay by card" in t:
        out.append(("add_step", {"label": "Checkout & pay", "kind": "decision", "after": "Cart",
                                 "quote": "they go to checkout and pay by card"}))
    elif "payment succeeds" in t:
        # wrong on purpose: paraphrased quote -> rejected -> fixed on the retry turn
        out.append(("add_step", {"label": "Order confirmed", "kind": "end", "after": "Checkout & pay",
                                 "link_label": "success", "quote": "when payment works we show a confirmation page"}))
    elif "crashes on older android" in t:
        out.append(("report_bug", {"step": "Cart", "text": "Add to cart crashes on older Android",
                                   "quote": "the add to cart button crashes on older Android phones"}))
    elif "log in with an otp" in t:
        out.append(("add_step", {"label": "OTP login", "kind": "step", "after": "Cart",
                                 "quote": "customers must log in with an OTP before checkout"}))
        out.append(("link_steps", {"from_step": "OTP login", "to_step": "Checkout & pay",
                                   "label": "verified", "quote": "customers must log in with an OTP before checkout"}))
    elif "valid for ten minutes" in t:
        out.append(("flag_contradiction", {"quote_a": "The OTP expires in five minutes",
                                           "quote_b": "the code is valid for ten minutes",
                                           "note": "OTP expiry: five minutes or ten minutes?", "step": "OTP login"}))
    elif "admin panel" in t:
        out.append(("flag_gap", {"step": "Checkout & pay", "missing_branch": "payment fails",
                                 "question": "What happens if the card payment fails?",
                                 "quote": "Then they go to checkout and pay by card"}))
    elif "fixed the add to cart crash" in t:
        bid = next((v for k, v in bugs.items() if "add to cart" in k), None)
        if bid:
            out.append(("resolve", {"item_id": bid, "quote": "we fixed the add to cart crash in yesterday's build"}))
    elif "if the payment fails" in t:
        gid = next((v for k, v in gaps.items() if "payment fails" in k), None)
        if gid:
            out.append(("resolve", {"item_id": gid, "answer": "Show an error and let them try again",
                                    "quote": "If the payment fails we just show an error and let them try again"}))
            out.append(("add_step", {"label": "Payment error", "kind": "step", "after": "Checkout & pay",
                                     "link_label": "fails", "link_type": "fail",
                                     "quote": "we just show an error and let them try again"}))
    return out


_FIXES = {"when payment works we show a confirmation page": "After the payment succeeds we show an order confirmation screen"}


def _plan(payload: dict) -> list[tuple[str, dict]]:
    flow = payload.get("current_flow") or {}
    calls: list[tuple[str, dict]] = []
    for line in payload.get("new_transcript", []):
        text = re.sub(r"^\[[^\]]*\]\s*[^:]*:\s*", "", line)
        calls += _calls_for_line(text, flow)
    if payload.get("mode") in ("gap_review", "final"):
        labels = {s["label"] for s in flow.get("steps", [])}
        asked = " ".join(g["question"].lower() for g in flow.get("gaps", []))
        if "OTP login" in labels and "otp is wrong" not in asked:
            calls.append(("flag_gap", {"step": "OTP login", "missing_branch": "wrong OTP",
                                       "question": "What happens if the OTP is wrong?",
                                       "quote": "customers must log in with an OTP before checkout"}))
    return calls


def _payload(user: str) -> dict:
    try:
        return json.loads(user)
    except ValueError:
        return {}


def _retry(results: list[tuple[str, dict, dict]]) -> list[tuple[str, dict]]:
    """Second turn: fix rejected quotes we know the right words for."""
    out = []
    for name, args, res in results:
        if not res.get("ok") and args.get("quote") in _FIXES:
            out.append((name, {**args, "quote": _FIXES[args["quote"]]}))
    return out


def mock_flow(messages: list[dict]) -> dict:
    """Tool-calling shape: {"role":"assistant","content":...,"tool_calls":[...]}."""
    turn = sum(1 for m in messages if m.get("role") == "assistant")
    if turn == 0:
        calls = _plan(_payload(messages[1].get("content", "")))
    else:
        last = next(m for m in reversed(messages) if m.get("role") == "assistant")
        by_id = {c["id"]: c for c in last.get("tool_calls") or []}
        results = []
        for m in messages:
            if m.get("role") == "tool" and m.get("tool_call_id") in by_id:
                c = by_id[m["tool_call_id"]]
                results.append((c["function"]["name"], json.loads(c["function"]["arguments"]), json.loads(m["content"])))
        calls = _retry(results)
    if not calls:
        return {"role": "assistant", "content": "done"}
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": f"call_{turn}_{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
        for i, (n, a) in enumerate(calls)]}


def mock_flow_json(user: str, history: list[dict] | None) -> dict:
    """JSON-fallback shape: {"calls":[{"name":..., "arguments":"<json>"}]}."""
    if not history:
        calls = _plan(_payload(user))
    else:
        prev = json.loads(history[-2]["content"]).get("calls", [])
        res = json.loads(history[-1]["content"].split("\n", 1)[1]) if "\n" in history[-1]["content"] else []
        calls = _retry([(c["name"], json.loads(c["arguments"]), r) for c, r in zip(prev, res)])
    return {"calls": [{"name": n, "arguments": json.dumps(a)} for n, a in calls]}
