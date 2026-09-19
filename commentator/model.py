"""The numbers behind the commentary: win chance, swings and the match forecast. Plain code.

Nothing here sees a game's final result. Inputs are the engine evaluation of the
current position and the two players' ratings.
"""

import math

import chess

from .contracts import BoardState, MatchPrediction, MoveEvent, SwingEvent

DRAW_SHARE = 0.5  # share of a dead-level position that ends drawn
NOTABLE_DROP = 0.10  # win-chance drop worth a mention
BLUNDER_DROP = 0.20  # win-chance drop worth cutting in for


def win_chance_white(cp: int | None, mate: int | None) -> float:
    """Expected score for White from the position alone (Lichess's centipawn-to-win% curve)."""
    if mate is not None:
        return 1.0 if mate > 0 else 0.0
    if cp is None:
        return 0.5
    return 1 / (1 + math.exp(-0.00368208 * max(-1500, min(1500, cp))))


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def expected_score_white(board: BoardState) -> float:
    """Position plus ratings. A 500-point favourite in a level position is not a coin flip;
    the rating edge counts for less as the game goes on and the position takes over."""
    if board.result:
        return {"1-0": 1.0, "0-1": 0.0, "1/2-1/2": 0.5}[board.result]
    position = win_chance_white(board.eval_cp, board.mate)
    if board.white.elo is None or board.black.elo is None:
        return position
    rating = 1 / (1 + 10 ** (-(board.white.elo - board.black.elo) / 400))
    rating_weight = max(0.3, 1 - board.ply / 100)
    z = _logit(position) + rating_weight * _logit(rating)
    return 1 / (1 + math.exp(-z))


def outcome_chances(expected: float, decided: bool = False) -> tuple[float, float, float]:
    """Split an expected score into (win, draw, loss). Level games are the ones that get drawn."""
    if decided:
        return (1.0, 0.0, 0.0) if expected == 1 else (0.0, 1.0, 0.0) if expected == 0.5 else (0.0, 0.0, 1.0)
    draw = DRAW_SHARE * (1 - abs(2 * expected - 1))
    return expected - draw / 2, draw, 1 - expected - draw / 2


def predict_match(boards: list[BoardState], team: str) -> MatchPrediction:
    """Combine the boards into P(win / draw / loss) for the match: distribution of total points."""
    opponent = next(p.team for b in boards for p in (b.white, b.black) if p.team != team)
    points = {0.0: 1.0}  # half-point totals -> probability
    score = opponent_score = 0.0
    for b in boards:
        e = expected_score_white(b)
        if b.white.team != team:
            e = 1 - e
        if b.result:
            score += e
            opponent_score += 1 - e
        win, draw, loss = outcome_chances(e, decided=bool(b.result))
        nxt: dict[float, float] = {}
        for total, p in points.items():
            for gain, q in ((1.0, win), (0.5, draw), (0.0, loss)):
                nxt[total + gain] = nxt.get(total + gain, 0.0) + p * q
        points = nxt
    half = len(boards) / 2
    return MatchPrediction(
        team=team,
        opponent=opponent,
        p_win=sum(p for t, p in points.items() if t > half),
        p_draw=sum(p for t, p in points.items() if t == half),
        p_loss=sum(p for t, p in points.items() if t < half),
        expected_points=sum(t * p for t, p in points.items()),
        score=score,
        opponent_score=opponent_score,
    )


def detect_swing(before: BoardState, move: MoveEvent) -> SwingEvent | None:
    """A swing is a move that cost the mover a notable share of their winning chances."""
    if move.eval_cp is None and move.mate is None:
        return None
    if before.eval_cp is None and before.mate is None:
        return None
    white_moved = move.ply % 2 == 1
    chance_before = win_chance_white(before.eval_cp, before.mate)
    chance_after = win_chance_white(move.eval_cp, move.mate)
    if not white_moved:
        chance_before, chance_after = 1 - chance_before, 1 - chance_after
    if chance_before - chance_after < NOTABLE_DROP:
        return None
    mover = before.white if white_moved else before.black
    return SwingEvent(
        game_id=move.game_id,
        match_id=before.match_id,
        board=before.board,
        ply=move.ply,
        move_number=(move.ply + 1) // 2,
        mover=mover.name,
        mover_team=mover.team,
        san=move.san,
        uci=move.uci,
        eval_before=before.eval_cp,
        eval_after=move.eval_cp,
        mate_after=move.mate,
        mover_chance_before=chance_before,
        mover_chance_after=chance_after,
    )


def material(fen: str) -> dict[str, int]:
    """Point count per side (pawn 1, knight/bishop 3, rook 5, queen 9), for plain-language explanations."""
    values = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}
    board = chess.Board(fen)
    return {
        name: sum(v * len(board.pieces(piece, colour)) for piece, v in values.items())
        for name, colour in (("white", chess.WHITE), ("black", chess.BLACK))
    }
