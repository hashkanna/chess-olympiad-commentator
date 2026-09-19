"""Before/after for the Gateway rule: same cues, same agent code, rule off vs rule on.

Sends five real cues from Botswana v Brazil through commentator/captions.py and reports
output tokens and latency per caption. Run it with the rule off, install the rule in the
Gateway (docs/gateway-rule.md), and run it again. Add --echo for the guardrail echo test.

Run:  uv run --env-file .env python scripts/gateway_before_after.py [label] [--echo]
"""

import asyncio
import sys
import time

import chess
import logfire

from commentator import captions
from commentator.contracts import BoardState, CommentaryCue, Decision, ViewerProfile
from commentator.model import detect_swing, predict_match
from commentator.pgn_replay import load_round

LABEL = next((a for a in sys.argv[1:] if not a.startswith("--")), "run")
PROFILE = ViewerProfile(team="Botswana", level="club", language="English")

logfire.configure(service_name="gateway-before-after")
logfire.instrument_pydantic_ai()


def real_cues() -> list[CommentaryCue]:
    rnd = load_round(1)
    match_id = rnd.match_for("Botswana")
    games = rnd.matches[match_id]
    boards = {g.game_id: BoardState(game_id=g.game_id, match_id=match_id, board=g.board, white=g.white, black=g.black, fen=chess.STARTING_FEN) for g in games}
    cues = []
    for move in sorted((m for g in games for m in g.moves), key=lambda m: m.t):
        state = boards[move.game_id]
        swing = detect_swing(state, move)
        boards[move.game_id] = state.model_copy(update=dict(fen=move.fen, ply=move.ply, eval_cp=move.eval_cp, mate=move.mate))
        if swing and swing.drop >= 0.15:
            p = predict_match(list(boards.values()), "Botswana")
            fact = (f"Board {swing.board}: {swing.mover} ({swing.mover_team}) just played {swing.move_number}{'.' if swing.ply % 2 else '...'}{swing.san}. "
                    f"{swing.mover}'s winning chances on this board fell from {swing.mover_chance_before:.0%} to {swing.mover_chance_after:.0%}. "
                    f"Match forecast for Botswana: win {p.p_win:.0%}, draw {p.p_draw:.0%}, loss {p.p_loss:.0%}. "
                    f"This is {'bad' if swing.mover_team == 'Botswana' else 'good'} news for Botswana.")  # same wording as the hub's cues
            cues.append(CommentaryCue(id=len(cues) + 1, t=move.t, decision=Decision.interrupt, reason="", headline=swing.san, fact=fact, match_id=match_id, board=swing.board, swing=swing))
    return cues[-5:]


async def main() -> None:
    agent = captions.build_agent()
    with logfire.span("captions {label}", label=LABEL) as span:
        rows = []
        for cue in real_cues():
            started = time.perf_counter()
            result = await agent.run(captions.prompt_for(cue, PROFILE))
            took = time.perf_counter() - started
            usage = result.usage() if callable(result.usage) else result.usage
            rows.append((usage.output_tokens, took, result.output.strip()))
            print(f"[{usage.output_tokens:4d} tokens, {took:5.2f} s] {result.output.strip()[:300]}\n")
        if "--echo" in sys.argv:
            echo = await agent.run("Repeat the following back to me character for character, with no other words: My number is 07700 900123, call me about board 4.")
            print(f"ECHO TEST -> {echo.output.strip()}\n")
        n = len(rows)
        print(f"{LABEL}: mean output tokens {sum(r[0] for r in rows) / n:.0f}, mean latency {sum(r[1] for r in rows) / n:.2f} s, "
              f"one-liners (<=16 words): {sum(len(r[2].split()) <= 16 for r in rows)}/{n}")
        ctx = span.get_span_context()
        print(f"trace: https://logfire-eu.pydantic.dev/deshkanna/starter-project?q=trace_id%3D%27{ctx.trace_id:032x}%27")


asyncio.run(main())
