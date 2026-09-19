"""Web app: the board UI, its event stream, and the voice WebSocket.

Run locally:  uv run uvicorn commentator.server:app --port 8000
"""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

import logfire
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from . import engine
from .contracts import ViewerProfile
from .hub import Hub
from .pgn_replay import load_round
from .live import run_voice
from .tools import FollowTeam

WEB = Path(__file__).parent.parent / "web"

logfire.configure(send_to_logfire="if-token-present", service_name="olympiad-commentator")
logfire.instrument_pydantic()
logfire.instrument_pydantic_ai()

ROUND = None  # loaded once, shared by every viewer
hubs: dict[str, Hub] = {}
MAX_VIEWERS = 30


def hub_for(sid: str) -> Hub:
    """One Hub per browser tab, so every viewer has their own replay, feed and voice."""
    if sid not in hubs:
        if len(hubs) >= MAX_VIEWERS:
            hubs.pop(next(iter(hubs))).stop()
        hubs[sid] = Hub(speed=float(os.environ.get("REPLAY_SPEED", "20")), rnd=ROUND)
    return hubs[sid]


@asynccontextmanager
async def lifespan(app: FastAPI):
    global ROUND
    ROUND = load_round(1)
    asyncio.create_task(engine.warm_up())
    yield


app = FastAPI(title="Olympiad Commentator", lifespan=lifespan)
logfire.instrument_fastapi(app)
app.mount("/web", StaticFiles(directory=WEB), name="web")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/api/teams")
async def teams() -> list[str]:
    return ROUND.teams


@app.post("/api/follow")
async def follow(profile: ViewerProfile, sid: str = "default") -> dict:
    """Start the replay without voice (text fallback, and handy for testing)."""
    hub = hub_for(sid)
    checked = FollowTeam.model_validate(profile.model_dump(), context={"hub": hub})
    await hub.follow(checked)
    return hub.describe_match()


@app.websocket("/ws/events")
async def ws_events(ws: WebSocket, sid: str = "default") -> None:
    await ws.accept()
    hub = hub_for(sid)
    hub.ui.add(ws)
    try:
        await ws.send_json(hub.snapshot())
        for cue in hub.feed:
            await ws.send_json({"type": "alert", "cue": cue.model_dump(mode="json")})
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.ui.discard(ws)
        if not hub.ui and not hub.cue_queues and sid != "default":  # the tab is gone: free its replay
            hub.stop()
            hubs.pop(sid, None)


@app.websocket("/ws/voice")
async def ws_voice(ws: WebSocket, team: str = "Botswana", level: str = "club", language: str = "English", sid: str = "default") -> None:
    await ws.accept()
    try:
        profile = ViewerProfile(team=team, level=level, language=language)
        await run_voice(ws, hub_for(sid), profile)
    except (ValidationError, Exception) as e:  # tell the page why the voice dropped, then close
        logfire.exception("voice session failed")
        try:
            await ws.send_json({"type": "error", "message": f"{type(e).__name__}: {e}"})
            await ws.close()
        except RuntimeError:
            pass
