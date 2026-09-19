"""The hub: holds the state of the round, runs the replay, and fans events out.

Every move in the round flows through here: board update -> swing detector -> match
forecast -> gate -> CommentaryCue. Cues go to the web UI (alert feed, arrows) and to
the voice session (as continuing tool responses with the gate's scheduling).
One viewer at a time: this is a demo server, not a multi-tenant one.
"""

import asyncio
import difflib
import time

import chess
import logfire
from fastapi import WebSocket

from . import engine
from .contracts import BoardState, CommentaryCue, Decision, MatchPrediction, MoveEvent, SwingEvent, ViewerProfile
from .gate import Gate
from .model import detect_swing, material, predict_match, win_chance_white
from .pgn_replay import Game, ReplayClock, Round, load_round, replay

LEAD_IN_MINUTES = 24  # start the replay this much game time before the match's biggest swing
ENGINE_WAIT_SECONDS = 1.5  # how long a cue may wait for the engine's refutation
HOLD_AFTER_CUT_IN = 30.0  # the director stays on the moment this long after cutting in
ARROW_SECONDS = 25.0  # arrows stay on a board at least this long, even if play moves on


def _pawns(cp: int | None, mate: int | None) -> str:
    if mate is not None:
        return f"mate in {abs(mate)} for {'White' if mate > 0 else 'Black'}"
    return "unknown" if cp is None else f"{cp / 100:+.1f}"


class Hub:
    def __init__(self, speed: float = 20.0):
        self.rnd: Round = load_round(1)
        self.speed = speed
        self.boards: dict[str, BoardState] = {}
        self.profile: ViewerProfile | None = None
        self.match_id: str | None = None
        self.featured = 1
        self.feed: list[CommentaryCue] = []
        self.arrows: dict[int, list[dict]] = {}  # board -> [{orig, dest, brush}]
        self._arrows_at: dict[int, float] = {}
        self.clock: ReplayClock | None = None
        self.ui: set[WebSocket] = set()
        self.cue_queues: set[asyncio.Queue[tuple[CommentaryCue, float]]] = set()
        self.gate = Gate()
        self._replay_task: asyncio.Task | None = None
        self._cue_id = 0

    # ---- viewer ---------------------------------------------------------

    def closest_teams(self, name: str) -> list[str]:
        return difflib.get_close_matches(name.title(), self.rnd.teams, n=3, cutoff=0.5)

    async def follow(self, profile: ViewerProfile, start_t: float | None = None) -> None:
        self.profile = profile
        self.match_id = self.rnd.match_for(profile.team)
        self.feed.clear()
        self.arrows.clear()
        self.featured = 1
        self.gate = Gate()
        if start_t is None:
            start_t = max(0.0, self._biggest_swing_t(self.match_id) - LEAD_IN_MINUTES * 60)
        self._rewind_to(start_t)
        if self._replay_task:
            self._replay_task.cancel()
        self.clock = ReplayClock(start_t=start_t, speed=self.speed)
        self._replay_task = asyncio.create_task(self._run_replay())
        await self.broadcast({"type": "reset"})
        await self.broadcast_state()

    def _biggest_swing_t(self, match_id: str) -> float:
        """Where to start the replay. Scheduling only: the forecast never sees what comes next."""
        best_t, best_drop = 0.0, 0.0
        for game in self.rnd.matches[match_id]:
            state = self._fresh(game)
            for move in game.moves:
                swing = detect_swing(state, move)
                if swing and swing.mover_chance_before > 0.3 and swing.drop > best_drop:
                    best_t, best_drop = move.t, swing.drop
                state = self._apply(state, move, game)
        return best_t

    # ---- board state ----------------------------------------------------

    def _fresh(self, game: Game) -> BoardState:
        return BoardState(
            game_id=game.game_id, match_id=game.match_id, board=game.board, white=game.white, black=game.black,
            fen=chess.STARTING_FEN, clock_white=5400 if game.has_clocks else None, clock_black=5400 if game.has_clocks else None,
        )

    def _apply(self, state: BoardState, move: MoveEvent, game: Game) -> BoardState:
        return state.model_copy(update=dict(
            fen=move.fen, ply=move.ply, last_san=move.san, last_uci=move.uci, eval_cp=move.eval_cp, mate=move.mate,
            win_chance_white=win_chance_white(move.eval_cp, move.mate) if (move.eval_cp is not None or move.mate is not None) else state.win_chance_white,
            clock_white=move.clock_white, clock_black=move.clock_black,
            result=game.result if move.ply == game.moves[-1].ply else None,  # released only at the last move
        ))

    def _rewind_to(self, t: float) -> None:
        for game in self.rnd.games.values():
            state = self._fresh(game)
            for move in game.moves:
                if move.t > t:
                    break
                state = self._apply(state, move, game)
            self.boards[game.game_id] = state

    def match_boards(self, match_id: str | None = None) -> list[BoardState]:
        games = self.rnd.matches.get(match_id or self.match_id or "", [])
        return [self.boards[g.game_id] for g in games]

    def prediction(self) -> MatchPrediction | None:
        if not (self.profile and self.match_id):
            return None
        return predict_match(self.match_boards(), self.profile.team)

    # ---- replay ---------------------------------------------------------

    async def _run_replay(self) -> None:
        assert self.clock
        async for move in replay(self.rnd, self.clock):
            try:
                await self._on_move(move)
            except Exception:
                logfire.exception("move handling failed")

    async def _on_move(self, move: MoveEvent) -> None:
        received = time.monotonic()
        game = self.rnd.games[move.game_id]
        before_state = self.boards[move.game_id]
        ours = game.match_id == self.match_id
        before = self.prediction() if ours else None
        swing = detect_swing(before_state, move)
        self.boards[move.game_id] = after_state = self._apply(before_state, move, game)
        after = self.prediction() if ours else None

        if ours and time.monotonic() - self._arrows_at.get(game.board, 0.0) > ARROW_SECONDS:
            self.arrows.pop(game.board, None)
        if swing:
            verdict = self.gate.decide(swing, ours, before, after)
            if verdict:
                await self._cue_for_swing(swing, verdict, after_state, after, received)
        if ours and after_state.result:
            await self._cue_for_result(after_state, after, received)
        if ours:
            await self.broadcast_state()

    async def _cue_for_swing(self, swing, verdict, state: BoardState, prediction, received: float) -> None:
        decision, reason = verdict
        ours = state.match_id == self.match_id
        if ours and decision is not Decision.silent:
            best = engine.cached_reply(swing.game_id, swing.ply, state.fen)  # from the Modal batch run
            if best is None:
                with logfire.span("engine refutation", board=swing.board):
                    try:
                        best = await asyncio.wait_for(engine.best_line(state.fen), ENGINE_WAIT_SECONDS)
                    except (TimeoutError, engine.EngineUnavailable):
                        best = None
            if best:
                swing.refutation_san, swing.refutation_uci = best["san"], best["uci"]

        team = self.profile.team if self.profile else swing.mover_team
        ours_moved = swing.mover_team == team
        fact = (
            f"Board {swing.board}: {swing.mover} ({swing.mover_team}) just played {swing.move_number}"
            f"{'.' if swing.ply % 2 else '...'}{swing.san}. Engine evaluation went from {_pawns(swing.eval_before, None)} "
            f"to {_pawns(swing.eval_after, swing.mate_after)} (White's point of view). {swing.mover}'s winning chances on this "
            f"board fell from {swing.mover_chance_before:.0%} to {swing.mover_chance_after:.0%}."
        )
        if swing.refutation_san:
            fact += f" The engine's punishing reply is {swing.refutation_san}."
        if prediction and ours:
            fact += (
                f" Match forecast for {team}: win {prediction.p_win:.0%}, draw {prediction.p_draw:.0%}, "
                f"loss {prediction.p_loss:.0%}. This is {'bad' if ours_moved else 'good'} news for {team}."
            )
        headline = f"Bd {swing.board} · {swing.move_number}{'.' if swing.ply % 2 else '…'}{swing.san} · {swing.mover_team} {swing.mover_chance_before:.0%} → {swing.mover_chance_after:.0%}"
        if not ours:
            headline = f"{swing.match_id} · bd {swing.board} · {swing.san}"
        if ours and decision is not Decision.silent:
            self.featured = swing.board
            shapes = [{"orig": swing.uci[:2], "dest": swing.uci[2:4], "brush": "red"}]
            if swing.refutation_uci:
                shapes.append({"orig": swing.refutation_uci[:2], "dest": swing.refutation_uci[2:4], "brush": "green"})
            self.arrows[swing.board] = shapes
            self._arrows_at[swing.board] = time.monotonic()
        await self._emit(CommentaryCue(
            id=self._next_id(), t=self.clock.now() if self.clock else 0, decision=decision, reason=reason, headline=headline,
            fact=fact, match_id=swing.match_id, board=swing.board, about_viewer_match=ours, swing=swing, prediction=prediction,
        ), received)

    async def _cue_for_result(self, state: BoardState, prediction: MatchPrediction | None, received: float) -> None:
        winner = {"1-0": state.white, "0-1": state.black}.get(state.result or "")
        outcome = f"{winner.name} ({winner.team}) won" if winner else "the game was drawn"
        over = all(b.result for b in self.match_boards())
        decided = over or (prediction and max(prediction.score, prediction.opponent_score) > 2)
        fact = f"Board {state.board} has finished: {outcome}. Match score: {prediction.team} {prediction.score:g}, {prediction.opponent} {prediction.opponent_score:g}." if prediction else f"Board {state.board} has finished: {outcome}."
        if over and prediction:
            fact += f" That is the final score of the match."
        if prediction and not decided:
            fact += f" Forecast for {prediction.team}: win {prediction.p_win:.0%}, draw {prediction.p_draw:.0%}, loss {prediction.p_loss:.0%}."
        await self._emit(CommentaryCue(
            id=self._next_id(), t=self.clock.now() if self.clock else 0,
            decision=Decision.interrupt if decided else Decision.when_idle,
            reason=("The match is over." if over else "The match is decided.") if decided else "A board finished: worth a mention at the next pause.",
            headline=f"Bd {state.board} finished · {state.result}", fact=fact, match_id=state.match_id, board=state.board, prediction=prediction,
        ), received)

    def _next_id(self) -> int:
        self._cue_id += 1
        return self._cue_id

    async def _emit(self, cue: CommentaryCue, received: float) -> None:
        self.feed.append(cue)
        if cue.decision is Decision.interrupt and cue.swing and self.clock:
            self.clock.hold(HOLD_AFTER_CUT_IN)
        logfire.info("cue {decision}: {headline}", decision=cue.decision.value, headline=cue.headline, reason=cue.reason)
        await self.broadcast({"type": "alert", "cue": cue.model_dump(mode="json"), "gate_ms": round((time.monotonic() - received) * 1000)})
        if cue.about_viewer_match:
            for q in self.cue_queues:
                q.put_nowait((cue, received))

    # ---- what the voice tools read ----------------------------------------

    def describe_board(self, b: BoardState) -> dict:
        team = self.profile.team if self.profile else b.white.team
        ours_white = b.white.team == team
        chance = b.win_chance_white if ours_white else 1 - b.win_chance_white
        mat = material(b.fen)
        return {
            "board": b.board,
            "white": f"{b.white.name} ({b.white.team}, rated {b.white.elo})",
            "black": f"{b.black.name} ({b.black.team}, rated {b.black.elo})",
            f"{team}_plays": "White" if ours_white else "Black",
            "status": f"finished, result {b.result}" if b.result else f"in progress, move {(b.ply + 1) // 2}",
            "last_move": b.last_san,
            "engine_eval_white_view": _pawns(b.eval_cp, b.mate),
            f"{team}_winning_chances_from_position": f"{chance:.0%}",
            "material_points": mat,
            "clock_seconds": {"white": b.clock_white, "black": b.clock_black},
        }

    def describe_match(self) -> dict:
        p = self.prediction()
        return {
            "match": self.match_id,
            "note": "Replay of a real round-1 match from the 46th Chess Olympiad. Not live.",
            "boards": [self.describe_board(b) for b in self.match_boards()],
            "forecast": p and {
                "team": p.team, "win": f"{p.p_win:.0%}", "draw": f"{p.p_draw:.0%}", "loss": f"{p.p_loss:.0%}",
                "expected_points_of_4": round(p.expected_points, 2), "score_so_far": f"{p.score:g}-{p.opponent_score:g}",
            },
        }

    def recent_decisions(self, n: int = 6) -> list[dict]:
        return [{"headline": c.headline, "decision": c.decision.value, "reason": c.reason} for c in self.feed[-n:]]

    # ---- web UI -------------------------------------------------------------

    def snapshot(self) -> dict:
        p = self.prediction()
        return {
            "type": "state",
            "profile": self.profile and self.profile.model_dump(),
            "match_id": self.match_id,
            "teams": self.rnd.teams,
            "featured": self.featured,
            "speed": self.speed,
            "game_minutes": round(self.clock.now() / 60) if self.clock else None,
            "held_seconds": round(self.clock.held_for()) if self.clock else 0,
            "boards": [b.model_dump(mode="json") | {"arrows": self.arrows.get(b.board, [])} for b in self.match_boards()],
            "prediction": p and p.model_dump(),
            "engine": engine.status(),
        }

    async def broadcast_state(self) -> None:
        await self.broadcast(self.snapshot())

    async def broadcast(self, event: dict) -> None:
        for ws in list(self.ui):
            try:
                await ws.send_json(event)
            except Exception:
                self.ui.discard(ws)

    async def feature(self, board: int) -> None:
        self.featured = board
        await self.broadcast_state()
