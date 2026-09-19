"""Smoke test of the whole system through the voice WebSocket, typed input only.

Needs the server running on :8000. Follows Botswana, asks a question while the replay
runs, and waits for the engine cue to cut the commentator off.
Run:  uv run python scripts/smoke_voice.py [language] [level]
"""

import asyncio
import json
import sys
import time
import urllib.parse

import websockets

LANGUAGE = sys.argv[1] if len(sys.argv) > 1 else "English"
LEVEL = sys.argv[2] if len(sys.argv) > 2 else "beginner"
QUESTIONS = [
    (25, "Why is board 3 going badly for us? Explain it simply."),
    (50, "Tell me about the history of chess in Botswana, take your time."),
    (95, "Why did you cut in just then?"),
    (115, "On board 4, what if Black plays Qh1 now?"),
]


async def main() -> None:
    q = urllib.parse.urlencode({"team": "Botswana", "level": LEVEL, "language": LANGUAGE})
    t0 = time.perf_counter()
    stamp = lambda: f"[{time.perf_counter() - t0:5.1f}s]"
    said: list[str] = []
    async with websockets.connect(f"ws://127.0.0.1:8000/ws/voice?{q}", max_size=None) as ws:
        async def ask() -> None:
            for at, text in QUESTIONS:
                await asyncio.sleep(max(0, at - (time.perf_counter() - t0)))
                print(f"{stamp()} VIEWER: {text}")
                await ws.send(json.dumps({"type": "text", "text": text}))

        asker = asyncio.create_task(ask())
        try:
            async with asyncio.timeout(150):
                async for frame in ws:
                    if isinstance(frame, bytes):
                        continue
                    ev = json.loads(frame)
                    kind = ev.pop("type")
                    if kind == "transcript":
                        if ev["who"] == "commentator":
                            said.append(ev["text"])
                    elif kind in ("turn_complete", "interrupted"):
                        if said:
                            print(f"{stamp()} {'COMMENTATOR (cut off)' if kind == 'interrupted' else 'COMMENTATOR'}: {''.join(said).strip()}")
                            said.clear()
                        if kind == "interrupted":
                            print(f"{stamp()} *** interrupted")
                    elif kind == "tool_call":
                        print(f"{stamp()} tool_call {ev['name']}({ev['args']})")
                    elif kind == "tool_result":
                        print(f"{stamp()} tool_result ok={ev['ok']} {json.dumps(ev['response'])[:260]}")
                    else:
                        print(f"{stamp()} {kind} {ev}")
        except TimeoutError:
            pass
        asker.cancel()


asyncio.run(main())
