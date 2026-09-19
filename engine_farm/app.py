"""Stockfish on Modal: the engine room for the commentator.

`analyse` evaluates one position; `.map()` fans it out across every board in a round.
CPU only, so it runs on plain Modal containers and scales to hundreds in parallel.

Try it:   modal run engine_farm/app.py
Deploy:   modal deploy engine_farm/app.py
"""

import modal

app = modal.App("olympiad-engine")

image = modal.Image.debian_slim(python_version="3.13").apt_install("stockfish").pip_install("chess==1.11.2")


@app.function(image=image, cpu=1.0, timeout=120, scaledown_window=600, max_containers=100)
def analyse(fen: str, depth: int = 18, multipv: int = 1) -> dict:
    """Best lines for a position. Scores are centipawns from White's point of view."""
    import time

    import chess
    import chess.engine

    board = chess.Board(fen)
    started = time.perf_counter()
    engine = chess.engine.SimpleEngine.popen_uci("/usr/games/stockfish")
    try:
        infos = engine.analyse(board, chess.engine.Limit(depth=depth), multipv=multipv)
    finally:
        engine.quit()
    lines = []
    for info in infos:
        score = info["score"].white()
        pv = info.get("pv", [])
        lines.append(
            {
                "cp": score.score(),
                "mate": score.mate(),
                "pv_uci": [m.uci() for m in pv[:8]],
                "pv_san": board.variation_san(pv[:8]) if pv else "",
            }
        )
    return {"fen": fen, "depth": depth, "lines": lines, "seconds": round(time.perf_counter() - started, 3)}


@app.function(image=image, cpu=1.0, timeout=900, max_containers=100)
def analyse_game(fens: list[str], depth: int = 14) -> list[dict]:
    """Every position of one game with a single engine process: best line and runner-up.
    The gap between them tells the director when a player has only one move that holds."""
    import chess
    import chess.engine

    engine = chess.engine.SimpleEngine.popen_uci("/usr/games/stockfish")
    out = []
    try:
        for fen in fens:
            board = chess.Board(fen)
            if board.is_game_over():
                out.append({"cp": None, "mate": None, "best": None, "cp2": None, "mate2": None})
                continue
            infos = engine.analyse(board, chess.engine.Limit(depth=depth), multipv=2)
            score = infos[0]["score"].white()
            pv = infos[0].get("pv", [])
            second = infos[1]["score"].white() if len(infos) > 1 else None
            out.append({
                "cp": score.score(), "mate": score.mate(), "best": pv[0].uci() if pv else None,
                "cp2": second.score() if second else None, "mate2": second.mate() if second else None,
            })
    finally:
        engine.quit()
    return out


@app.local_entrypoint()
def analyse_round(round_number: int = 1, depth: int = 14):
    """Fan a whole round out across Modal: one call per game, up to 100 containers at once.
    Writes data/analysis/round{N}.json: for every game, our own evaluation and best reply per ply."""
    import json
    import pathlib
    import time

    from commentator.pgn_replay import load_round

    rnd = load_round(round_number)
    games = list(rnd.games.values())
    positions = sum(len(g.moves) for g in games)
    print(f"round {round_number}: {len(games)} games, {positions} positions, depth {depth}")
    started = time.perf_counter()
    results = list(analyse_game.map([[m.fen for m in g.moves] for g in games], kwargs={"depth": depth}))
    took = time.perf_counter() - started
    out = pathlib.Path("data/analysis")
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "round": round_number, "depth": depth, "games": len(games), "positions": positions, "wall_seconds": round(took, 1),
        "analysis": {g.game_id: [[r["cp"], r["mate"], r["best"], r["cp2"], r["mate2"]] for r in res] for g, res in zip(games, results)},
    }
    (out / f"round{round_number}.json").write_text(json.dumps(payload, separators=(",", ":")))
    print(f"{positions} positions in {took:.1f} s wall = {positions / took:.0f} positions/s across the farm")


@app.local_entrypoint()
def main():
    # Position after 1.e4 e5 2.Qh5 Nc6 3.Bc4 Nf6?? -- White mates with Qxf7.
    fen = "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4"
    print(analyse.remote(fen, depth=12))
