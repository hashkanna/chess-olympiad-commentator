"""Score the match forecast against what really happened in round 1.

Every 15 minutes of game time, in every four-board match, ask the model for
P(win / draw / loss) and compare with the real match result. Reports the Brier score
(lower is better; 0 is perfect, 0.667 is a uniform guess) next to two baselines, and a
reliability table: when we said "70%", how often did it happen?

The model's parameters were set by hand and never fitted to these results.
Run:  uv run python scripts/score_forecast.py
"""

import chess

from commentator.contracts import BoardState
from commentator.model import predict_match, win_chance_white
from commentator.pgn_replay import load_round

STEP = 15 * 60
rnd = load_round(1)
rows = []  # (minute, forecast triple, ratings-only triple, outcome index)
for match_id, games in rnd.matches.items():
    if len(games) != 4:
        continue
    team = games[0].white.team
    points = sum({"1-0": 1.0, "0-1": 0.0, "1/2-1/2": 0.5}[g.result] if g.white.team == team else {"1-0": 0.0, "0-1": 1.0, "1/2-1/2": 0.5}[g.result] for g in games)
    outcome = 0 if points > 2 else 1 if points == 2 else 2
    fresh = [BoardState(game_id=g.game_id, match_id=match_id, board=g.board, white=g.white, black=g.black, fen=chess.STARTING_FEN) for g in games]
    prior = predict_match(fresh, team)
    end = max(g.end_t for g in games)
    t = 0.0
    while t < end:
        boards = []
        for g, state in zip(games, fresh):
            for m in g.moves:
                if m.t > t:
                    break
                state = state.model_copy(update=dict(ply=m.ply, eval_cp=m.eval_cp, mate=m.mate, fen=m.fen,
                                                     result=g.result if m.ply == g.moves[-1].ply else None,
                                                     win_chance_white=win_chance_white(m.eval_cp, m.mate)))
            boards.append(state)
        p = predict_match(boards, team)
        rows.append((t / 60, (p.p_win, p.p_draw, p.p_loss), (prior.p_win, prior.p_draw, prior.p_loss), outcome))
        t += STEP


def brier(triples):
    return sum(sum((p[i] - (1.0 if i == o else 0.0)) ** 2 for i in range(3)) for p, o in triples) / len(triples)


print(f"{len(rows)} forecasts across {len({r for r in rnd.matches if len(rnd.matches[r]) == 4})} matches, one every 15 minutes of game time")
print(f"Brier score  our forecast: {brier([(f, o) for _, f, _, o in rows]):.3f}   ratings only: {brier([(r, o) for _, _, r, o in rows]):.3f}   uniform guess: {brier([((1/3, 1/3, 1/3), o) for *_, o in rows]):.3f}")
for label, lo, hi in (("first hour", 0, 60), ("hours 2-3", 60, 180), ("after 3 h", 180, 1e9)):
    part = [(f, r, o) for m, f, r, o in rows if lo <= m < hi]
    print(f"  {label:10} ours {brier([(f, o) for f, _, o in part]):.3f}   ratings only {brier([(r, o) for _, r, o in part]):.3f}   (n={len(part)})")
print("reliability of P(first-named team wins):")
for lo in (0.0, 0.2, 0.4, 0.6, 0.8):
    part = [(f[0], o == 0) for _, f, _, o in rows if lo <= f[0] < lo + 0.2 + (1e-9 if lo == 0.8 else 0)]
    if part:
        print(f"  said {lo:.0%}-{lo + 0.2:.0%}: mean forecast {sum(p for p, _ in part) / len(part):.0%}, happened {sum(w for _, w in part) / len(part):.0%}  (n={len(part)})")
