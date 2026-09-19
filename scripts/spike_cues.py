"""Spike: can outside events interrupt a Gemini Live conversation through a tool?

The model calls follow_match once. We keep that call open and push cues as continuing
function responses (will_continue=True), each with its own scheduling:
INTERRUPT should cut the model off mid-sentence, WHEN_IDLE should wait, SILENT should
produce no speech. One session, typed input, no audio in.

Run:  uv run python scripts/spike_cues.py
"""

import asyncio
import time

from google import genai
from google.genai import types

from commentator.config import get_settings

S = types.FunctionResponseScheduling

SYSTEM = """\
You are a live chess commentator for one viewer. At the start, call follow_match with the
viewer's team. That call stays open: its responses are live cues from the engine room.
Put each cue into one or two spoken sentences using only the facts in it. If a cue says
priority "interrupt", stop what you are saying and deliver it at once.
"""

FOLLOW = types.FunctionDeclaration(
    name="follow_match",
    description="Start following a team's match. Stays open and streams engine-verified cues.",
    parameters_json_schema={"type": "object", "properties": {"team": {"type": "string"}}, "required": ["team"]},
)

CUES = [  # (seconds after the call opens, scheduling, payload)
    (9, S.INTERRUPT, {"priority": "interrupt", "board": 2, "fact": "Lithuania's board 2 just blundered with 29.Qd2. Evaluation went from +1.4 to -7.8. The refutation is 29...Rxe3. Lithuania is now likely to lose the match."}),
    (26, S.SILENT, {"priority": "silent", "board": 4, "fact": "Board 4 evaluation is steady at +0.3."}),
    (32, S.WHEN_IDLE, {"priority": "when_idle", "board": 1, "fact": "Board 1 has been agreed drawn. Match score is 0.5-0.5."}),
]


async def main() -> None:
    settings = get_settings()
    client = genai.Client(api_key=settings.gemini_api_key)
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=SYSTEM,
        tools=[types.Tool(function_declarations=[FOLLOW])],
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )
    t0 = time.perf_counter()
    stamp = lambda: f"[{time.perf_counter() - t0:5.1f}s]"
    said: list[str] = []
    cue_sent_at: float | None = None

    def flush(tag: str) -> None:
        if said:
            print(f"{stamp()} {tag}: {''.join(said).strip()}")
            said.clear()

    async with client.aio.live.connect(model=settings.live_model, config=config) as session:
        await session.send_realtime_input(
            text="I'm following Lithuania. Start following."
        )

        async def push_cues(fc: types.FunctionCall) -> None:
            nonlocal cue_sent_at
            opened = time.perf_counter()
            await session.send_tool_response(function_responses=types.FunctionResponse(
                id=fc.id, name=fc.name, response={"status": "following"}, will_continue=True, scheduling=S.SILENT))
            # Get the model talking, so the INTERRUPT cue lands mid-sentence.
            await asyncio.sleep(1)
            await session.send_realtime_input(
                text="While we wait, tell me the history of the Chess Olympiad in detail. Keep talking for at least a minute.")
            for after, scheduling, payload in CUES:
                await asyncio.sleep(max(0, opened + after - time.perf_counter()))
                flush("SAID BEFORE CUE")
                print(f"{stamp()} >>> cue sent, scheduling={scheduling.value}: {payload['fact'][:60]}…")
                cue_sent_at = time.perf_counter()
                await session.send_tool_response(function_responses=types.FunctionResponse(
                    id=fc.id, name=fc.name, response=payload, will_continue=True, scheduling=scheduling))

        pusher: asyncio.Task | None = None

        async def listen() -> None:
            nonlocal pusher, cue_sent_at
            while True:
                async for m in session.receive():
                    if m.tool_call:
                        for fc in m.tool_call.function_calls or []:
                            print(f"{stamp()} tool_call {fc.name}({fc.args})")
                            pusher = asyncio.create_task(push_cues(fc))
                    sc = m.server_content
                    if not sc:
                        continue
                    if sc.interrupted:
                        flush("CUT OFF")
                        print(f"{stamp()} *** interrupted flag")
                    if sc.model_turn and cue_sent_at is not None:
                        print(f"{stamp()} first audio {time.perf_counter() - cue_sent_at:.2f}s after cue")
                        cue_sent_at = None
                    if sc.output_transcription and sc.output_transcription.text:
                        said.append(sc.output_transcription.text)
                    if sc.turn_complete:
                        flush("TURN")

        try:
            await asyncio.wait_for(listen(), timeout=50)
        except TimeoutError:
            flush("STILL TALKING AT TIMEOUT")
        if pusher:
            pusher.cancel()


if __name__ == "__main__":
    asyncio.run(main())
