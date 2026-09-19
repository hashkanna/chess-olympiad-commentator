"""Tools the voice commentator can call.

Gemini Live has no structured output, so typed data comes in as function-call arguments.
Each tool is a Pydantic model: its JSON schema is the declaration Gemini sees, and the
arguments Gemini sends are validated against it, with the hub's live state as context
(is that a team in this round? is that move legal on that board right now?). When
validation fails the error text goes back to the model, which asks the viewer again.
"""

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import chess
from google.genai import types
from pydantic import BaseModel, Field, ValidationError, ValidationInfo, field_validator, model_validator

from . import engine
from .contracts import ViewerProfile
from .hub import Hub, _pawns

Scheduling = types.FunctionResponseScheduling
Handler = Callable[[Any, Hub], Awaitable[dict]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Handler
    scheduling: Scheduling


@dataclass(frozen=True)
class ToolOutcome:
    ok: bool
    response: dict
    scheduling: Scheduling


TOOLS: dict[str, Tool] = {}
FOLLOW = "follow_team"  # the one tool whose call stays open and streams cues


def tool(args_model: type[BaseModel], *, scheduling: Scheduling = Scheduling.WHEN_IDLE):
    def register(fn: Handler) -> Handler:
        TOOLS[fn.__name__] = Tool(fn.__name__, inspect.getdoc(fn) or "", args_model, fn, scheduling)
        return fn

    return register


def _inline_refs(schema: dict) -> dict:
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(dict(defs[node["$ref"].rsplit("/", 1)[-1]]))
            return {k: walk(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def declarations() -> list[types.FunctionDeclaration]:
    return [
        types.FunctionDeclaration(name=t.name, description=t.description, parameters_json_schema=_inline_refs(t.args_model.model_json_schema()))
        for t in TOOLS.values()
    ]


async def dispatch(name: str, raw_args: dict | None, hub: Hub) -> ToolOutcome:
    t = TOOLS.get(name)
    if t is None:
        return ToolOutcome(False, {"status": "error", "error": f"unknown tool {name!r}"}, Scheduling.WHEN_IDLE)
    try:
        args = t.args_model.model_validate(raw_args or {}, context={"hub": hub})
    except ValidationError as e:
        errors = [
            {"field": ".".join(str(p) for p in err["loc"]) or "(arguments)", "problem": err["msg"].removeprefix("Value error, ")}
            for err in e.errors(include_url=False, include_context=False, include_input=False)
        ]
        return ToolOutcome(False, {"status": "invalid", "errors": errors, "instruction": "Nothing happened. Ask the viewer about this, then call the tool again."}, Scheduling.WHEN_IDLE)
    return ToolOutcome(True, {"status": "ok", **await t.handler(args, hub)}, t.scheduling)


# ---- follow_team --------------------------------------------------------------


class FollowTeam(ViewerProfile):
    @field_validator("team")
    @classmethod
    def team_is_playing(cls, team: str, info: ValidationInfo) -> str:
        hub: Hub = info.context["hub"]
        by_lower = {t.lower(): t for t in hub.rnd.teams}
        if team.strip().lower() in by_lower:
            return by_lower[team.strip().lower()]
        close = hub.closest_teams(team)
        raise ValueError(f"no team called {team!r} played in this round." + (f" Closest names: {', '.join(close)}." if close else ""))


@tool(FollowTeam)
async def follow_team(args: FollowTeam, hub: Hub) -> dict:
    """Start following a national team's match. Call this first, and again if the viewer
    switches team, language or level. The call stays open: every later response to it is
    a live cue from the engine room about that match."""
    await hub.follow(args)
    return {
        "following": args.team,
        "speak_in": args.language,
        "viewer_level": args.level,
        "now": hub.describe_match(),
        "next": "Give a short summary of the four boards and the forecast, in the viewer's language and at their level.",
    }


# ---- questions about the match ---------------------------------------------------


class NoArgs(BaseModel):
    pass


@tool(NoArgs)
async def get_match_state(args: NoArgs, hub: Hub) -> dict:
    """Current state of all four boards and the match forecast. Call before answering any
    question about the score, who is better, or how the match is going."""
    return hub.describe_match()


class BoardNumber(BaseModel):
    board: int = Field(ge=1, le=4, description="Board number in the viewer's match, 1 to 4.")

    @model_validator(mode="after")
    def following_a_match(self, info: ValidationInfo) -> "BoardNumber":
        if not info.context["hub"].match_id:
            raise ValueError("no team is being followed yet; call follow_team first")
        return self


@tool(BoardNumber)
async def explain_board(args: BoardNumber, hub: Hub) -> dict:
    """Engine-checked detail on one board, and show that board on the viewer's main screen.
    Call before explaining why a position is better or worse."""
    await hub.feature(args.board)
    state = hub.match_boards()[args.board - 1]
    detail = hub.describe_board(state)
    try:
        best = await engine.best_line(state.fen, depth=16) if not state.result else None
        if best:
            detail["engine_best_continuation"] = best["pv_san"]
    except engine.EngineUnavailable:
        pass
    return detail


class WhatIf(BoardNumber):
    move: str = Field(description="The move the viewer is asking about, in standard notation, e.g. 'Nf5' or 'Qxd4'.")

    @model_validator(mode="after")
    def move_is_legal(self, info: ValidationInfo) -> "WhatIf":
        state = info.context["hub"].match_boards()[self.board - 1]
        if state.result:
            raise ValueError(f"board {self.board} has already finished")
        position = chess.Board(state.fen)
        try:
            self.move = position.san(position.parse_san(self.move.replace("0", "O")))
        except ValueError:
            side = "White" if position.turn else "Black"
            piece = self.move[0] if self.move[:1] in "NBRQK" else ""
            legal = sorted(position.san(m) for m in position.legal_moves)
            options = [m for m in legal if piece and m.startswith(piece)][:10] or legal[:10]
            raise ValueError(f"{self.move} is not a legal move on board {self.board} right now. It is {side} to move. Legal moves include: {', '.join(options)}.") from None
        return self


@tool(WhatIf, scheduling=Scheduling.INTERRUPT)
async def what_if(args: WhatIf, hub: Hub) -> dict:
    """Deep engine analysis of a move the viewer suggests. It runs in the background for a
    few seconds: tell the viewer you are checking and keep chatting. The answer cuts in."""
    state = hub.match_boards()[args.board - 1]
    position = chess.Board(state.fen)
    move = position.parse_san(args.move)
    position.push(move)
    hub.arrows[args.board] = [{"orig": move.uci()[:2], "dest": move.uci()[2:4], "brush": "blue"}]
    await hub.feature(args.board)
    try:
        result = await engine.analyse(position.fen(), depth=20)
    except engine.EngineUnavailable:
        return {"analysed": False, "reason": "The engine room is closed right now, so this move cannot be checked."}
    line = result["lines"][0]
    if line["pv_uci"]:
        reply = line["pv_uci"][0]
        hub.arrows[args.board].append({"orig": reply[:2], "dest": reply[2:4], "brush": "green"})
        await hub.broadcast_state()
    return {
        "analysed": True,
        "move": args.move,
        "evaluation_now_white_view": _pawns(state.eval_cp, state.mate),
        "evaluation_after_move_white_view": _pawns(line["cp"], line["mate"]),
        "engine_expected_continuation": line["pv_san"],
        "depth": result["depth"],
        "engine_seconds": result["seconds"],
    }


@tool(NoArgs)
async def explain_decisions(args: NoArgs, hub: Hub) -> dict:
    """Why the director cut in, waited, or stayed silent on recent events. Call when the
    viewer asks things like 'why did you cut to board 3?' or 'why didn't you mention that?'."""
    return {"recent": hub.recent_decisions()}
