"""Rank round-1 matches by drama, to choose what to replay in the demo.

Runs every match through the same swing detector and match model the live system uses
and lists the single moves that moved a team's match forecast the most.
Run:  uv run python scripts/find_drama.py
"""

import chess

from commentator.contracts import BoardState
from commentator.model import detect_swing, predict_match, win_chance_white
from commentator.pgn_replay import load_round

rnd = load_round(1)
rows = []
for match_id, games in rnd.matches.items():
    if len(games) != 4 or not all(g.has_clocks for g in games):
        continue
    team = games[0].white.team
    boards = {
        g.game_id: BoardState(game_id=g.game_id, match_id=match_id, board=g.board, white=g.white, black=g.black, fen=chess.STARTING_FEN)
        for g in games
    }
    for move in sorted((m for g in games for m in g.moves), key=lambda m: m.t):
        b = boards[move.game_id]
        before = predict_match(list(boards.values()), team)
        swing = detect_swing(b, move)
        boards[move.game_id] = b.model_copy(update=dict(
            fen=move.fen, ply=move.ply, eval_cp=move.eval_cp, mate=move.mate, last_san=move.san,
            win_chance_white=win_chance_white(move.eval_cp, move.mate)))
        if swing and swing.mover_chance_before > 0.3:
            after = predict_match(list(boards.values()), team)
            shift = abs(after.p_win - before.p_win) + abs(after.p_loss - before.p_loss)
            rows.append((shift, match_id, swing, before, after, move.t))

rows.sort(key=lambda r: -r[0])
print(f"{'shift':>5}  {'match':34} bd  move            mover chance   {team and 'match forecast for first-named team (W/D/L)'}")
for shift, match_id, s, before, after, t in rows[:14]:
    print(f"{shift * 50:5.0f}  {match_id[:34]:34} {s.board}   {s.move_number}{'.' if s.ply % 2 else '...'}{s.san:9} {s.mover_chance_before:4.0%} -> {s.mover_chance_after:4.0%}   "
          f"{before.p_win:.0%}/{before.p_draw:.0%}/{before.p_loss:.0%} -> {after.p_win:.0%}/{after.p_draw:.0%}/{after.p_loss:.0%}   at {t / 60:.0f} min  ({s.mover}, {s.mover_team})")
