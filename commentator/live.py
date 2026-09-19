"""The voice: bridges one browser WebSocket to one Gemini Live session.

Browser -> server: binary frames are mic audio (PCM16 mono 16 kHz); JSON text frames are control.
Server -> browser: binary frames are speech (PCM16 24 kHz); JSON text frames are events.

How outside events reach the conversation: the model calls `follow_team` once, and that
call is kept open. Each CommentaryCue from the hub is sent as a further response to it
(will_continue=True) with the gate's scheduling: INTERRUPT cuts the model off mid-sentence,
WHEN_IDLE waits for a pause, SILENT only updates what the model knows.
"""

import asyncio
import json
import os
import time
import wave
from pathlib import Path

import logfire
from fastapi import WebSocket, WebSocketDisconnect
from google import genai
from google.genai import types

from . import tools
from .config import get_settings
from .contracts import CommentaryCue, Decision, ViewerProfile
from .hub import Hub

class SpeechRecorder:
    """Optional: writes the commentator's speech to a WAV exactly as the browser plays it
    (chunks queued back to back, queue dropped on interruption). Used to make the demo video.
    Enabled by setting VOICE_TEE_DIR."""

    RATE = 24000

    def __init__(self, directory: str):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.started_wall = time.time()
        self.t0 = time.monotonic()
        self.pcm = bytearray()
        self.play_head = 0.0

    def chunk(self, data: bytes) -> None:
        now = time.monotonic() - self.t0
        start = max(now + 0.03, self.play_head)
        offset = int(start * self.RATE) * 2
        if len(self.pcm) < offset:
            self.pcm.extend(bytes(offset - len(self.pcm)))
        self.pcm[offset:offset + len(data)] = data
        self.play_head = start + len(data) / 2 / self.RATE

    def interrupted(self) -> None:
        cut = int((time.monotonic() - self.t0) * self.RATE) * 2
        del self.pcm[cut:]
        self.play_head = 0.0

    def close(self) -> None:
        with wave.open(str(self.dir / "commentator.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.RATE)
            w.writeframes(bytes(self.pcm))
        (self.dir / "commentator.json").write_text(json.dumps({"started_wall": self.started_wall}))


SCHEDULING = {
    Decision.interrupt: types.FunctionResponseScheduling.INTERRUPT,
    Decision.when_idle: types.FunctionResponseScheduling.WHEN_IDLE,
    Decision.silent: types.FunctionResponseScheduling.SILENT,
}

SYSTEM_PROMPT = """\
You are a personal chess commentator for one viewer of the 46th Chess Olympiad (Samarkand, 2026), round 1.

Honesty rule: this is a REPLAY of real games from the event, played back at {speed:g}x speed.
Never say or imply the games are being played right now.

The viewer: follows {team}. Chess level: {level}. Speak {language} at all times, including cues.

How you work:
- Start by calling follow_team with the viewer's team, level and language. Do it again if
  they change any of these. If it returns errors, ask the viewer and call it again.
- You cannot see the boards. Every chess fact you say must come from a tool result or a cue.
  Never invent moves, evaluations or plans. If you lack a fact, call a tool first.
- Later responses to follow_team are live cues from the engine room. Deliver each in one or
  two sentences. A cue marked interrupt: break off and say it at once, like a commentator
  cutting in ("Hold on..."). Then, if it still matters, return to what you were discussing.
- For "what if she plays X?" call what_if. It takes a few seconds: say you are checking and
  carry on talking. The answer will cut in.
- You follow every game in the round, not only the viewer's match. When asked what else is
  happening, call round_highlights and pick the one or two best stories.
- Forecasts are from our own model. Say "our forecast" and give the percentage.
- Level: beginner = name pieces and squares in plain words, explain why a move matters, no
  jargon. club = normal chess talk. expert = notation and evaluations are welcome.
- Keep turns short, two or three sentences. This is a conversation, and the viewer may interrupt.
"""


def build_config(profile: ViewerProfile, speed: float) -> types.LiveConnectConfig:
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=SYSTEM_PROMPT.format(speed=speed, **profile.model_dump()),
        tools=[types.Tool(function_declarations=tools.declarations())],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        context_window_compression=types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow()),
    )


async def run_voice(ws: WebSocket, hub: Hub, profile: ViewerProfile) -> None:
    settings = get_settings()
    client = genai.Client(api_key=settings.gemini_api_key)
    pending: dict[str, asyncio.Task] = {}
    cues: asyncio.Queue[tuple[CommentaryCue, float]] = asyncio.Queue()
    pump: asyncio.Task | None = None
    awaiting_audio: dict | None = None  # set when an interrupt cue goes out, cleared at first audio
    recorder = SpeechRecorder(os.environ["VOICE_TEE_DIR"]) if os.environ.get("VOICE_TEE_DIR") else None

    async def emit(event: str, **data) -> None:
        await ws.send_text(json.dumps({"type": event, **data}, default=str))

    with logfire.span("voice session", team=profile.team, language=profile.language, level=profile.level):
        async with client.aio.live.connect(model=settings.live_model, config=build_config(profile, hub.speed)) as session:
            await emit("ready", model=settings.live_model)
            # Nudge the model to open the conversation itself.
            await session.send_realtime_input(text=f"(The viewer has just joined. They follow {profile.team}. Begin.)")

            async def pump_cues(fc: types.FunctionCall) -> None:
                nonlocal awaiting_audio
                while True:
                    cue, received = await cues.get()
                    payload = {
                        "cue": cue.decision.value,
                        "fact": cue.fact,
                        "instruction": "Cut in now. Say who slipped and with which move, the engine's punishing reply if given, and what it does to the match forecast. The replay is held on this position for half a minute, so invite a question." if cue.decision is Decision.interrupt else "Mention briefly at a pause." if cue.decision is Decision.when_idle else "Do not speak about this unless asked.",
                    }
                    sent = time.monotonic()
                    if cue.decision is Decision.interrupt:
                        awaiting_audio = {"cue_id": cue.id, "sent": sent, "move_to_cue_ms": round((sent - received) * 1000)}
                    await session.send_tool_response(function_responses=types.FunctionResponse(
                        id=fc.id, name=fc.name, response=payload, will_continue=True, scheduling=SCHEDULING[cue.decision]))
                    logfire.info("cue sent to voice: {decision}", decision=cue.decision.value, cue_id=cue.id)

            async def handle_tool_call(fc: types.FunctionCall) -> None:
                nonlocal pump
                with logfire.span("tool {name}", name=fc.name, args=fc.args):
                    await emit("tool_call", id=fc.id, name=fc.name, args=fc.args)
                    outcome = await tools.dispatch(fc.name, fc.args, hub)
                    await emit("tool_result", id=fc.id, name=fc.name, ok=outcome.ok, response=outcome.response)
                    keep_open = outcome.ok and fc.name == tools.FOLLOW
                    await session.send_tool_response(function_responses=types.FunctionResponse(
                        id=fc.id, name=fc.name, response=outcome.response, scheduling=outcome.scheduling,
                        will_continue=True if keep_open else None))
                    if keep_open:
                        if pump:
                            pump.cancel()
                        pump = asyncio.create_task(pump_cues(fc))

            async def uplink() -> None:
                while True:
                    msg = await ws.receive()
                    if msg["type"] == "websocket.disconnect":
                        return
                    if data := msg.get("bytes"):
                        await session.send_realtime_input(audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000"))
                    elif text := msg.get("text"):
                        ctl = json.loads(text)
                        if ctl.get("type") == "text":
                            await session.send_realtime_input(text=ctl["text"])
                        elif ctl.get("type") == "mic_off":
                            await session.send_realtime_input(audio_stream_end=True)

            async def downlink() -> None:
                nonlocal awaiting_audio
                while True:  # receive() ends at each turn boundary; keep listening
                    async for m in session.receive():
                        if m.tool_call:
                            for fc in m.tool_call.function_calls or []:
                                task = asyncio.create_task(handle_tool_call(fc))
                                pending[fc.id] = task
                                task.add_done_callback(lambda _, i=fc.id: pending.pop(i, None))
                        if m.tool_call_cancellation:
                            for call_id in m.tool_call_cancellation.ids or []:
                                if task := pending.get(call_id):
                                    task.cancel()
                        if m.go_away:
                            await emit("go_away", time_left=m.go_away.time_left)
                        sc = m.server_content
                        if not sc:
                            continue
                        if sc.interrupted:
                            if recorder:
                                recorder.interrupted()
                            await emit("interrupted")
                        if sc.model_turn:
                            for part in sc.model_turn.parts or []:
                                if part.inline_data and part.inline_data.data:
                                    if awaiting_audio:
                                        lat = awaiting_audio | {"cue_to_audio_ms": round((time.monotonic() - awaiting_audio.pop("sent")) * 1000)}
                                        awaiting_audio = None
                                        logfire.info("interrupt latency", **lat)
                                        await hub.broadcast({"type": "latency", **lat})
                                    if recorder:
                                        recorder.chunk(part.inline_data.data)
                                    await ws.send_bytes(part.inline_data.data)
                        if sc.input_transcription and sc.input_transcription.text:
                            await emit("transcript", who="viewer", text=sc.input_transcription.text)
                        if sc.output_transcription and sc.output_transcription.text:
                            await emit("transcript", who="commentator", text=sc.output_transcription.text)
                        if sc.turn_complete:
                            await emit("turn_complete")

            hub.cue_queues.add(cues)
            jobs = [asyncio.create_task(uplink()), asyncio.create_task(downlink())]
            try:
                done, _ = await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
                for job in done:
                    job.result()
            except WebSocketDisconnect:
                pass
            finally:
                if recorder:
                    recorder.close()
                hub.cue_queues.discard(cues)
                for job in [*jobs, *pending.values(), *([pump] if pump else [])]:
                    job.cancel()
