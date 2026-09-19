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

**Before / after.** `scripts/gateway_before_after.py` sends the same real cues from Botswana v
Brazil through the unchanged agent and prints output tokens, latency and the captions. It was
run with the rule disabled, then with it enabled. Same script, same prompts, same code.

| | Rule off | Rule on |
|---|---|---|
| Output tokens per caption (mean) | 57 | **19** (−67%) |
| Latency per caption (mean) | 0.92 s | **0.70 s** |
| One sentence of ≤16 words, leading with the viewer's team | 0 of 3 | **3 of 3** |
| Logfire trace id | `01a0ba356945d8ba18a0221a12b7e0df` | `01a0ba36496254c810f7ecfed922f750` |

The same cue (Brazil's 34.Rd7 blunder on board 4), before and after:

> **Off:** Board 4: Roberto Junio Brito Molina (BRA) plays 34.Rd7. Molina's winning chances drop
> from 50% to 7%. Match forecast for Botswana: 11% win, 53% draw, 36% loss.
>
> **On:** Botswana gains an advantage after Brazil's winning chances plummet on board four.

Traces are in the Logfire project `deshkanna/starter-project` (EU); search `trace_id = '<id>'`.

**Guardrail (bonus): it fired.** Viewers type to the commentator, and their words can end up in
a caption request. A custom protection, "Viewer phone numbers (UK)", pattern
`(?:\+44\s?7\d{3}|\b07\d{3})\s?\d{3}\s?\d{3}\b`, action **Redact**, scoped to the `modal`
endpoint only, keeps them off our GPU. Echo test (`--echo`): the agent is asked to repeat
"My number is 07700 900123, call me about board 4." character for character.

| | Model's reply |
|---|---|
| Guardrail off | My number is 07700 900123, call me about board 4. |
| Guardrail on (Redact) | My number is [REDACTED], call me about board 4. |

The model received the placeholder, not the number. Trace id with the guardrail firing:
`01a0ba37fd3e42766ec032716fd24b67`.

**What runs where.** Gemma 4 26B-A4B on a dedicated Modal endpoint (1×B200, scales to zero after
five idle minutes) → Pydantic AI Gateway, BYOK provider `modal`, route `modal` → Pydantic AI agent
in `commentator/captions.py` → caption under the featured board in the web UI.
