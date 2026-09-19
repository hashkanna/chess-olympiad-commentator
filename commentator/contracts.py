"""Shared types. Every component codes against this file.

Evaluations are centipawns from White's point of view. Win chances are expected scores
in [0, 1]. Times (`t`) are seconds of game time since the round started.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Player(BaseModel):
    name: str
    team: str
    title: str | None = None
    elo: int | None = None


class MoveEvent(BaseModel):
    game_id: str
    ply: int
    san: str
    uci: str
    fen: str  # position after the move
    t: float
    clock_white: int | None = None  # seconds left
    clock_black: int | None = None
    eval_cp: int | None = None
    mate: int | None = None  # moves to mate, positive if White mates


class BoardState(BaseModel):
    """One game as the viewer sees it right now. `result` stays None until the replay reaches the end."""

    game_id: str
    match_id: str
    board: int  # 1-4
    white: Player
    black: Player
    fen: str
    ply: int = 0
    last_san: str | None = None
    last_uci: str | None = None
    eval_cp: int | None = None
    mate: int | None = None
    win_chance_white: float = 0.5  # from the position alone
    clock_white: int | None = None
    clock_black: int | None = None
    result: Literal["1-0", "0-1", "1/2-1/2"] | None = None


class MatchPrediction(BaseModel):
    team: str  # the team these numbers are about
    opponent: str
    p_win: float
    p_draw: float
    p_loss: float
    expected_points: float  # out of 4
    score: float = 0.0  # board points already banked
    opponent_score: float = 0.0


class Decision(StrEnum):
    interrupt = "interrupt"  # cut in now
    when_idle = "when_idle"  # mention at the next pause
    silent = "silent"  # log it, say nothing


class SwingEvent(BaseModel):
    """A move that changed who is likely to win."""

    game_id: str
    match_id: str
    board: int
    ply: int
    move_number: int
    mover: str
    mover_team: str
    san: str
    uci: str
    eval_before: int | None
    eval_after: int | None
    mate_after: int | None = None
    mover_chance_before: float
    mover_chance_after: float
    refutation_san: str | None = None  # filled in by the engine when available
    refutation_uci: str | None = None

    @property
    def drop(self) -> float:
        return self.mover_chance_before - self.mover_chance_after


class CommentaryCue(BaseModel):
    """What the gate hands to the voice. `fact` holds only engine-verified statements."""

    id: int
    t: float
    decision: Decision
    reason: str  # why the gate chose this, shown in the feed and used for "why did you cut in?"
    headline: str  # short line for the alert feed
    fact: str
    match_id: str
    board: int | None = None
    about_viewer_match: bool = True
    swing: SwingEvent | None = None
    prediction: MatchPrediction | None = None


class ViewerProfile(BaseModel):
    team: str = Field(description="The national team the viewer is following, e.g. 'England'.")
    level: Literal["beginner", "club", "expert"] = Field(
        default="club", description="How much chess the viewer knows."
    )
    language: str = Field(default="English", description="Language to commentate in, e.g. 'English', 'Portuguese'.")
