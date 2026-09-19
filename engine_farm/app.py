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


@app.local_entrypoint()
def main():
    # Position after 1.e4 e5 2.Qh5 Nc6 3.Bc4 Nf6?? -- White mates with Qxf7.
    fen = "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4"
    print(analyse.remote(fen, depth=12))
