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
from .live import run_voice
from .tools import FollowTeam

WEB = Path(__file__).parent.parent / "web"

logfire.configure(send_to_logfire="if-token-present", service_name="olympiad-commentator")
logfire.instrument_pydantic()
logfire.instrument_pydantic_ai()

hub: Hub


@asynccontextmanager
async def lifespan(app: FastAPI):
    global hub
    hub = Hub(speed=float(os.environ.get("REPLAY_SPEED", "20")))
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
    return hub.rnd.teams


@app.post("/api/follow")
async def follow(profile: ViewerProfile) -> dict:
    """Start the replay without voice (text fallback, and handy for testing)."""
    checked = FollowTeam.model_validate(profile.model_dump(), context={"hub": hub})
    await hub.follow(checked)
    return hub.describe_match()


@app.websocket("/ws/events")
async def ws_events(ws: WebSocket) -> None:
    await ws.accept()
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


@app.websocket("/ws/voice")
async def ws_voice(ws: WebSocket, team: str = "Botswana", level: str = "club", language: str = "English") -> None:
    await ws.accept()
    try:
        profile = ViewerProfile(team=team, level=level, language=language)
        await run_voice(ws, hub, profile)
    except (ValidationError, Exception) as e:  # tell the page why the voice dropped, then close
        logfire.exception("voice session failed")
        try:
            await ws.send_json({"type": "error", "message": f"{type(e).__name__}: {e}"})
            await ws.close()
        except RuntimeError:
            pass
