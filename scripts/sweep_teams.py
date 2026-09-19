"""Robustness sweep: follow every team in the round and push its whole match through the hub.

A judge can pick any country, so every match must load, start somewhere sensible and
produce cues without an exception: three-board matches, games without clocks, games
without evaluations, matches with no big swing.
Run:  uv run python scripts/sweep_teams.py
"""

import asyncio
import os
from collections import Counter

os.environ.pop("PYDANTIC_AI_GATEWAY_API_KEY", None)  # no caption calls during the sweep

from commentator.contracts import ViewerProfile  # noqa: E402
from commentator.hub import Hub  # noqa: E402


async def main() -> None:
    hub = Hub(speed=1e9)
    failures, decisions, quiet = [], Counter(), []
    for team in hub.rnd.teams:
        try:
            await hub.follow(ViewerProfile(team=team), start_t=0.0)
            hub._replay_task.cancel()
            hub.describe_match()
            games = hub.rnd.matches[hub.match_id]
            for move in sorted((m for g in games for m in g.moves), key=lambda m: m.t):
                await hub._on_move(move)
            hub.describe_match()
            mine = [c for c in hub.feed if c.about_viewer_match]
            decisions.update(c.decision.value for c in mine)
            if not any(c.decision.value == "interrupt" for c in mine):
                quiet.append(team)
            start = hub._biggest_swing_t(hub.match_id)
            assert start >= 0
            hub.profile = None  # so the next follow() starts fresh
        except Exception as e:  # keep sweeping, report at the end
            failures.append((team, f"{type(e).__name__}: {e}"))
    print(f"{len(hub.rnd.teams)} teams swept, {len(failures)} failures")
    for team, err in failures[:20]:
        print("  FAIL", team, err)
    print("cues in followed matches:", dict(decisions))
    print(f"{len(quiet)} teams whose match never triggers a cut-in (heads-ups and results only):", ", ".join(quiet[:12]), "…" if len(quiet) > 12 else "")


asyncio.run(main())
