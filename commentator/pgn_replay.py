"""Load a round from Lichess broadcast PGNs and replay it on its real move timings.

Each game becomes a list of MoveEvents stamped with game time, rebuilt from the clock
tags (90 minutes + 30 seconds a move, +30 minutes after move 40). Games without clock
tags fall back to a fixed pace. The final result of a game is kept out of every event
and is only released when the replay reaches the game's last move.

Inspect a round:  uv run python -m commentator.pgn_replay info
"""

import asyncio
import re
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import chess
import chess.pgn

from .contracts import MoveEvent, Player

DATA = Path(__file__).parent.parent / "data" / "pgn"
BASE_CLOCK = 90 * 60
INCREMENT = 30
BONUS_AT_MOVE = 40
BONUS = 30 * 60
DEFAULT_PACE = 75  # seconds a move when a game has no clock tags


@dataclass
class Game:
    game_id: str
    match_id: str
    board: int
    white: Player
    black: Player
    moves: list[MoveEvent]
    result: str  # hidden from consumers until `end_t`
    order: float  # Round tag sub-number, used to put boards in order
    has_clocks: bool = True

    @property
    def end_t(self) -> float:
        return self.moves[-1].t if self.moves else 0.0

    def team_side(self, team: str) -> chess.Color:
        return chess.WHITE if self.white.team == team else chess.BLACK


@dataclass
class Round:
    games: dict[str, Game] = field(default_factory=dict)
    matches: dict[str, list[Game]] = field(default_factory=dict)  # match_id -> boards 1-4

    @property
    def teams(self) -> list[str]:
        return sorted({t for m in self.matches for t in m.split(" v ")})

    def match_for(self, team: str) -> str | None:
        return next((m for m in self.matches if team in m.split(" v ")), None)


def _player(headers: chess.pgn.Headers, colour: str) -> Player:
    elo = headers.get(f"{colour}Elo", "")
    return Player(
        name=_readable(headers.get(colour, "?")),
        team=headers.get(f"{colour}Team", "?"),
        title=headers.get(f"{colour}Title") or None,
        elo=int(elo) if elo.isdigit() else None,
    )


def _readable(name: str) -> str:
    """'Chigaev, Maksim' -> 'Maksim Chigaev'."""
    last, _, first = name.partition(", ")
    return f"{first} {last}".strip() if first else name


def _load_game(pgn: chess.pgn.Game) -> Game | None:
    h = pgn.headers
    result = h.get("Result", "*")
    if result not in ("1-0", "0-1", "1/2-1/2"):
        return None
    white, black = _player(h, "White"), _player(h, "Black")
    game_id = h.get("GameURL", h.get("Site", "")).rstrip("/").rsplit("/", 1)[-1]
    sub = re.search(r"\.(\d+)$", h.get("Round", ""))

    moves: list[MoveEvent] = []
    clocks = {chess.WHITE: BASE_CLOCK, chess.BLACK: BASE_CLOCK}
    seen_clock = False
    t = 0.0
    board = pgn.board()
    for node in pgn.mainline():
        mover = board.turn
        san = board.san(node.move)
        move_number = board.fullmove_number
        board.push(node.move)

        clk = node.clock()
        if clk is not None:
            seen_clock = True
            bonus = BONUS if move_number == BONUS_AT_MOVE else 0
            spent = clocks[mover] - clk + INCREMENT + bonus
            clocks[mover] = int(clk)
            t += min(max(spent, 1), 3600)
        else:
            t += DEFAULT_PACE

        score = node.eval()
        white_score = score.white() if score else None
        moves.append(
            MoveEvent(
                game_id=game_id,
                ply=board.ply(),
                san=san,
                uci=node.move.uci(),
                fen=board.fen(),
                t=t,
                clock_white=clocks[chess.WHITE] if seen_clock else None,
                clock_black=clocks[chess.BLACK] if seen_clock else None,
                eval_cp=white_score.score() if white_score else None,
                mate=white_score.mate() if white_score else None,
            )
        )
    if not moves:
        return None  # forfeit
    return Game(
        game_id=game_id,
        match_id="",
        board=0,
        white=white,
        black=black,
        moves=moves,
        result=result,
        order=float(sub.group(1)) if sub else 0.0,
        has_clocks=seen_clock,
    )


def load_round(round_number: int = 1, data_dir: Path = DATA) -> Round:
    rnd = Round()
    by_pairing: dict[frozenset, list[Game]] = defaultdict(list)
    for path in sorted(data_dir.glob(f"open-round{round_number}-*.pgn")):
        with open(path, encoding="utf-8") as f:
            while (pgn := chess.pgn.read_game(f)) is not None:
                if game := _load_game(pgn):
                    by_pairing[frozenset((game.white.team, game.black.team))].append(game)
    for games in by_pairing.values():
        games.sort(key=lambda g: g.order)
        first = games[0]
        match_id = f"{first.white.team} v {first.black.team}"  # board 1's White team is named first
        for i, game in enumerate(games, start=1):
            game.match_id, game.board = match_id, i
            rnd.games[game.game_id] = game
        rnd.matches[match_id] = games
    return rnd


class ReplayClock:
    """Maps wall time to game time at a chosen speed."""

    def __init__(self, start_t: float = 0.0, speed: float = 20.0):
        self.start_t, self.speed = start_t, speed
        self._wall0 = time.monotonic()

    def now(self) -> float:
        return self.start_t + (time.monotonic() - self._wall0) * self.speed

    def wall_seconds_until(self, t: float) -> float:
        return max(0.0, (t - self.now()) / self.speed)


async def replay(rnd: Round, clock: ReplayClock) -> AsyncIterator[MoveEvent]:
    """Yield every move in the round from `clock.start_t` onwards, each at its due time."""
    timeline = sorted(
        (m for g in rnd.games.values() for m in g.moves if m.t > clock.start_t), key=lambda m: m.t
    )
    for move in timeline:
        if (wait := clock.wall_seconds_until(move.t)) > 0:
            await asyncio.sleep(wait)
        yield move


if __name__ == "__main__":
    rnd = load_round(1)
    games = list(rnd.games.values())
    print(f"round 1: {len(rnd.matches)} matches, {len(games)} games, {len(rnd.teams)} teams")
    print(f"with clocks: {sum(g.has_clocks for g in games)}, with evals: {sum(any(m.eval_cp is not None for m in g.moves) for g in games)}")
    print(f"boards per match: {sorted({len(m) for m in rnd.matches.values()})}")
    longest = max(games, key=lambda g: g.end_t)
    print(f"longest game: {longest.end_t / 3600:.1f} h ({longest.white.name} v {longest.black.name})")
    sample = rnd.matches[rnd.match_for("England") or next(iter(rnd.matches))]
    for g in sample:
        print(f"  board {g.board}: {g.white.name} ({g.white.team}, {g.white.elo}) v {g.black.name} ({g.black.team}, {g.black.elo})  {len(g.moves)} plies, ends {g.end_t / 60:.0f} min")
