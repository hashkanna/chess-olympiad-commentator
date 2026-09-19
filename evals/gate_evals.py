"""Pydantic Evals suite for the director's judgement: when to cut in, wait, or stay silent.

The gate is rules, and rules drift when thresholds get tuned at 3 a.m. Each case states a
situation in chess terms and the decision a good broadcast director would make. Evaluations
are centipawns from White's point of view; the model converts them to winning chances.
Run:  uv run python evals/gate_evals.py
"""

from dataclasses import dataclass

from pydantic import BaseModel
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EqualsExpected, Evaluator, EvaluatorContext

from commentator.contracts import MatchPrediction, SwingEvent
from commentator.gate import Gate
from commentator.model import win_chance_white


class Situation(BaseModel):
    eval_before: int  # centipawns, White's view
    eval_after: int
    white_moved: bool = True
    in_viewer_match: bool = True
    match_win_before: float = 0.30  # viewer's team, chance of winning the match
    match_win_after: float = 0.30
    just_cut_in: bool = False  # the director interrupted a moment ago


def decide(s: Situation) -> str:
    before, after = win_chance_white(s.eval_before, None), win_chance_white(s.eval_after, None)
    if not s.white_moved:
        before, after = 1 - before, 1 - after
    if before - after < 0.10:
        return "not_a_swing"  # the swing detector never raises it
    swing = SwingEvent(
        game_id="g", match_id="A v B", board=1, ply=41 if s.white_moved else 42, move_number=21, mover="Mover", mover_team="A",
        san="Rd7", uci="c7d7", eval_before=s.eval_before, eval_after=s.eval_after, mover_chance_before=before, mover_chance_after=after,
    )
    gate = Gate()
    if s.just_cut_in:
        gate.decide(swing.model_copy(update={"mover_chance_before": 0.6, "mover_chance_after": 0.1}), True)  # a first interrupt
    forecast = lambda p: MatchPrediction(team="A", opponent="B", p_win=p, p_draw=0.3, p_loss=max(0.0, 0.7 - p), expected_points=2.0)
    verdict = gate.decide(swing, s.in_viewer_match, forecast(s.match_win_before), forecast(s.match_win_after))
    return verdict[0].value if verdict else "not_logged"


@dataclass
class NeverTalksOverOtherMatches(Evaluator[Situation, str]):
    """Whatever happens elsewhere in the hall, the viewer is never interrupted for it."""

    def evaluate(self, ctx: EvaluatorContext[Situation, str]) -> bool:
        return ctx.inputs.in_viewer_match or ctx.output not in ("interrupt", "when_idle")


dataset = Dataset[Situation, str](
    name="director-gate",
    cases=[
        Case(name="level game thrown away", inputs=Situation(eval_before=0, eval_after=-695), expected_output="interrupt"),
        Case(name="black blunders a level game", inputs=Situation(eval_before=10, eval_after=520, white_moved=False), expected_output="interrupt"),
        Case(name="+9 to +6 is not drama", inputs=Situation(eval_before=900, eval_after=600), expected_output="not_a_swing"),
        Case(name="already lost, now more lost", inputs=Situation(eval_before=-350, eval_after=-900), expected_output="silent"),
        Case(name="small slip, keep quiet", inputs=Situation(eval_before=60, eval_after=-10), expected_output="not_a_swing"),
        Case(name="11-point slip is logged, not spoken", inputs=Situation(eval_before=181, eval_after=54), expected_output="silent"),
        Case(name="18-point slip gets a mention at a pause", inputs=Situation(eval_before=-276, eval_after=-640, match_win_before=0.03, match_win_after=0.03), expected_output="when_idle"),
        Case(name="clear mistake, not decisive", inputs=Situation(eval_before=40, eval_after=-150), expected_output="when_idle"),
        Case(name="modest slip that swings the whole match", inputs=Situation(eval_before=30, eval_after=-120, match_win_before=0.45, match_win_after=0.25), expected_output="interrupt"),
        Case(name="second blunder seconds after a cut-in waits", inputs=Situation(eval_before=0, eval_after=-600, just_cut_in=True), expected_output="when_idle"),
        Case(name="huge blunder in another match", inputs=Situation(eval_before=0, eval_after=-800, in_viewer_match=False), expected_output="silent"),
        Case(name="ordinary mistake in another match", inputs=Situation(eval_before=50, eval_after=-100, in_viewer_match=False), expected_output="not_logged"),
    ],
    evaluators=[EqualsExpected(), NeverTalksOverOtherMatches()],
)

if __name__ == "__main__":
    report = dataset.evaluate_sync(decide)
    report.print(include_input=False, include_output=True, include_expected_output=True, include_durations=False)
