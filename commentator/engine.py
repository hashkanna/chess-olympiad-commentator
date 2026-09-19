"""Client for the Stockfish farm on Modal (engine_farm/app.py).

The commentary never depends on it: if the app is not deployed or a call is slow, cues
go out without a refutation and "what if" reports that the engine room is closed.
"""

import json
import time
from functools import cache
from pathlib import Path

import chess
import logfire

APP, FUNCTION = "olympiad-engine", "analyse"
RETRY_AFTER = 30.0

_fn = None
_failed_at = 0.0
_last_error = "not tried yet"


class EngineUnavailable(Exception):
    pass


def status() -> str:
    return "modal" if _fn is not None else f"off ({_last_error})"


def _function():
    global _fn, _failed_at, _last_error
    if _fn is not None:
        return _fn
    if time.monotonic() - _failed_at < RETRY_AFTER and _failed_at:
        raise EngineUnavailable(_last_error)
    try:
        import modal

        fn = modal.Function.from_name(APP, FUNCTION)
        fn.hydrate()
        _fn = fn
        return fn
    except Exception as e:  # not deployed, no credentials, no network
        _failed_at, _last_error = time.monotonic(), type(e).__name__
        raise EngineUnavailable(_last_error) from e


async def analyse(fen: str, depth: int = 16, multipv: int = 1) -> dict:
    fn = _function()
    with logfire.span("modal stockfish depth {depth}", depth=depth, fen=fen):
        return await fn.remote.aio(fen, depth, multipv)


async def best_line(fen: str, depth: int = 14) -> dict | None:
    """The engine's best reply in a position: used for the green refutation arrow."""
    result = await analyse(fen, depth=depth)
    if not result["lines"] or not result["lines"][0]["pv_uci"]:
        return None
    line = result["lines"][0]
    uci = line["pv_uci"][0]
    return {"uci": uci, "san": chess.Board(fen).san(chess.Move.from_uci(uci)), "cp": line["cp"], "mate": line["mate"], "pv_san": line["pv_san"]}


@cache
def _round_analysis() -> dict[str, list]:
    """Whole-round analysis produced on Modal by `modal run engine_farm/app.py::analyse_round`."""
    path = Path(__file__).parent.parent / "data" / "analysis" / "round1.json"
    return json.loads(path.read_text())["analysis"] if path.exists() else {}


def cached_reply(game_id: str, ply: int, fen: str) -> dict | None:
    """Best reply to the move at `ply`, from the batch analysis: instant, no network."""
    rows = _round_analysis().get(game_id)
    if not rows or ply > len(rows) or not rows[ply - 1][2]:
        return None
    uci = rows[ply - 1][2]
    return {"uci": uci, "san": chess.Board(fen).san(chess.Move.from_uci(uci)), "cp": rows[ply - 1][0], "mate": rows[ply - 1][1]}


async def warm_up() -> None:
    try:
        await analyse(chess.STARTING_FEN, depth=8)
    except EngineUnavailable:
        pass
