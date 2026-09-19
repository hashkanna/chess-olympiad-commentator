# Pydantic challenge: change the caption writer without touching its code

**The agent.** `commentator/captions.py` is a plain Pydantic AI agent. For every cue the
commentator speaks, it asks Gemma (`google/gemma-4-26B-A4B-it`) running on our own Modal GPU
to write the caption that stays on screen under the board. The call goes through the Pydantic
AI Gateway route `modal` (BYOK). The agent has no style rules and no length limit.

**The problem.** Left alone, the model writes a paragraph. A caption under a chess board has
about one line: it must be readable in two seconds while someone is talking. Longer output
also means the caption lands after the moment has passed.

**The change, made in the Gateway, not in the code.** A custom optimization rule on the
`modal` route:

> **Name:** Broadcast caption house style
>
> **Instruction injected on every request to this route:**
> You are writing a lower-third caption for a live chess broadcast. Output exactly one
> sentence of at most 16 words, in the language requested. Lead with the team or player
> the viewer follows. Present tense. Say what happened and what it means for the match.
> No engine numbers, no centipawns, no move lists, no preamble, no quotation marks, no
> markdown.

When installing the rule, tick the `modal` endpoint in step 2 ("Choose endpoints"), or it
silently does nothing.

**Before / after.** `scripts/gateway_before_after.py` sends the same five real cues from
Botswana v Brazil through the unchanged agent and prints output tokens, latency and the
captions. Run it once with the rule off and once with it on; each run prints its Logfire
trace link.

| | Rule off | Rule on |
|---|---|---|
| Output tokens per caption (mean) | _fill in_ | _fill in_ |
| Latency per caption (mean) | _fill in_ | _fill in_ |
| Fits one line under the board | _fill in_ | _fill in_ |
| Logfire trace | _link_ | _link_ |

**Guardrail (bonus).** Viewers type to the commentator, and their words can end up in a
caption request. A custom pattern for UK phone numbers (`(?:\+44\s?7\d{3}|\b07\d{3})\s?\d{3}\s?\d{3}\b`)
with action **Redact**, scoped to the `modal` endpoint, keeps them off our GPU. The echo test
in the script asks the model to repeat a message containing `07700 900123` character for
character; with the guardrail on, the model repeats the placeholder instead.
