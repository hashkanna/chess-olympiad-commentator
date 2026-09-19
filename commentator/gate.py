"""The gate: for each swing decide whether to cut in, mention it at the next pause, or stay silent.

It judges the change in winning chances, never raw centipawns: +9 to +6 is not drama.
Every decision carries its reason, which goes to the alert feed (ignored swings are shown
greyed out) and lets the commentator answer "why did you cut to board 3?".
"""

import time
from dataclasses import dataclass

from .contracts import Decision, MatchPrediction, SwingEvent
from .model import BLUNDER_DROP

MENTION_DROP = 0.15  # below this a slip is logged but not spoken

ALREADY_DECIDED = 0.25  # mover was below this before the move: the game was going anyway
MATCH_SHIFT = 0.15  # change in the team's chance of winning the match that matters by itself
OTHER_MATCH_NOTE = 0.35  # swings elsewhere in the hall only reach the feed above this size


@dataclass
class Gate:
    min_gap_seconds: float = 12.0  # never cut in twice in quick succession
    _last_interrupt: float = 0.0

    def decide(
        self,
        swing: SwingEvent,
        in_viewer_match: bool,
        before: MatchPrediction | None = None,
        after: MatchPrediction | None = None,
    ) -> tuple[Decision, str] | None:
        """Returns None for swings not worth logging at all."""
        drop = round(swing.drop * 100)
        if not in_viewer_match:
            if swing.drop >= OTHER_MATCH_NOTE:
                return Decision.silent, f"Not your match ({swing.match_id}), so no interruption."
            return None

        match_shift = abs(after.p_win - before.p_win) + abs(after.p_loss - before.p_loss) if before and after else 0.0
        if swing.mover_chance_before < ALREADY_DECIDED:
            return Decision.silent, f"The game was already going against {swing.mover}; this changes little."
        if swing.drop >= BLUNDER_DROP or match_shift >= MATCH_SHIFT:
            if time.monotonic() - self._last_interrupt < self.min_gap_seconds:
                return Decision.when_idle, "A big swing, but I had only just cut in, so it waits for a pause."
            self._last_interrupt = time.monotonic()
            why = f"{swing.mover}'s winning chances fell {drop} points in one move"
            if match_shift >= MATCH_SHIFT:
                why += f" and the match forecast moved {round(match_shift * 50)} points"
            return Decision.interrupt, why + "."
        if swing.drop < MENTION_DROP:
            return Decision.silent, f"A {drop}-point slip: too small to talk over you for."
        return Decision.when_idle, f"A {drop}-point slip: worth a mention, not worth cutting in for."
