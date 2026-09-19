# Olympiad Commentator

**A personal AI commentator for any team at the Chess Olympiad.** You talk with it in real
time, in your language. It follows every game in the round at once and interrupts you only
when something important happens in your team's match.

Built solo at the {Tech: Europe} Agentic AI Hack, London, 19 September 2026.

**[▶ Watch the two-minute demo](docs/demo.mp4)** ([download](https://github.com/hashkanna/chess-olympiad-commentator/releases/download/demo-v1/olympiad-commentator-demo.mp4)) ·
[Pydantic Gateway challenge write-up](docs/gateway-rule.md). The demo was recorded hands-free by
`scripts/record_demo.py`: the viewer's questions are a text-to-speech voice fed in as the microphone.

About 190 countries play the Olympiad across hundreds of boards. Human commentators cover
perhaps the top five. If your team is Botswana, nobody explains its match to you. In round 1
Botswana drew 2–2 with Brazil: a 2230 beat a grandmaster and a 1914 beat a 2390. This
project is the commentator that would have been with you for it.

> **This is a replay.** The games are real: round 1 of the 46th Chess Olympiad (Samarkand,
> 2026), downloaded from Lichess broadcasts and played back on their real move timings at
> 20× speed. Nothing here is live, and the app says so on screen and in the commentary.

## What you experience

1. Pick a team, a level (beginner / club / expert) and a language. Press **Start commentary**.
2. The commentator summarises your team's four boards and gives a match forecast.
3. You chat with it by voice: "Why is board 3 worse?", "What if Black plays Bc8?"
4. Mid-conversation it cuts in: *"Hold on — board four. Brazil has just blundered."*
   The featured board switches, a red arrow shows the blunder and a green arrow shows the
   engine's refutation.
5. After cutting in, the director holds the replay on that position for half a minute, so you
   can ask about it: "what if Black plays Bc8?" runs a depth-20 search on Modal in the
   background and the answer cuts in when it lands.
6. Ask "why did you cut in?" and it tells you what the director weighed. The feed on the
   right also shows what it chose to ignore, greyed out, with the reason.

## How it works

```
Lichess PGN ─> Replayer ─MoveEvent─> Swing detector ─SwingEvent─> Gate ─CommentaryCue─┐
  (393 games)   real clock            win chance, not              interrupt /         │
                timings, 20×          centipawns                   at a pause / silent │
                                           │                                           ▼
                                  Match model (W/D/L)        Hub ──events (WebSocket)──> Web UI
                                                              │
     Stockfish on Modal <── refutation, "what if" ────────────┤
                                                              ▼
                     browser mic/speaker <── audio ──> Gemini Live session
                                                       (cues arrive as continuing
                                                        tool responses)
```

| Part | What it does | Where |
|---|---|---|
| Replayer | Loads the round, rebuilds each game's timeline from clock tags (90 min + 30 s/move, +30 min at move 40), emits `MoveEvent`s at N× speed. A game's result is withheld until the replay reaches its last move. | `commentator/pgn_replay.py` |
| Swing detector | Converts evaluations to win chance and flags moves that cost the mover 10+ points of it. | `commentator/model.py` |
| Match model | Blends position and ratings into an expected score per board, splits it into win/draw/loss, and convolves four boards into P(win / draw / loss) for the match. Plain code. | `commentator/model.py` |
| Gate | Decides **interrupt / mention at a pause / stay silent**, with a reason. Judges change in win chance, never raw centipawns (+9 to +6 is not drama). Rules, no model. | `commentator/gate.py` |
| Hub | Holds state for every game in the round, runs the replay, fans cues out to the UI and the voice. | `commentator/hub.py` |
| Voice | Gemini Live session bridged to the browser: audio both ways, barge-in, transcripts, tool dispatch, cue injection, measured latency. | `commentator/live.py` |
| Tools | `follow_team`, `get_match_state`, `explain_board`, `what_if`, `explain_decisions`: each a Pydantic model. | `commentator/tools.py` |
| Engine farm | Stockfish on Modal CPU containers: refutations (≈0.3 s warm), depth-20 "what if" (≈0.9 s), `.map()` over a whole round. | `engine_farm/app.py`, `commentator/engine.py` |
| Web UI | Featured board with arrows and eval bar, four mini boards with win bars, match score and forecast, director's feed, voice controls with a text fallback. No build step. | `web/` |
| Contracts | Every shared type. | `commentator/contracts.py` |

### The part that makes it an agent: outside events interrupt a live conversation

Gemini Live's scheduling options (`INTERRUPT`, `WHEN_IDLE`, `SILENT`) exist only on function
responses. So the commentator calls `follow_team` once and **that call is kept open**: every
cue from the gate is sent as a further response to it (`will_continue=True`) with the gate's
scheduling. An `INTERRUPT` cue cuts the model off mid-sentence and it starts speaking the
cue about 0.9 s later (measured; `scripts/spike_cues.py` is the experiment that proved it).
`SILENT` cues update what the model knows without making it speak.

### Rules that keep it honest

- **The engine supplies the facts; the voice only words them.** Cues carry engine-verified
  statements. The model is told it cannot see the boards and must call a tool before stating
  any chess fact.
- **Typed all the way.** Live has no structured output, so typed data arrives as function-call
  arguments validated by Pydantic *against live state*: is that a team in this round, is that
  move legal on that board right now. On failure the error text (with the closest team names,
  or the legal moves) goes back to the model, which asks the viewer again.
- **The forecast never sees a result** until the replay reaches the end of that game.
  (Choosing where to start the replay does look ahead for the biggest swing; that is
  scheduling, not prediction.)
- **Evaluations for swing detection are Lichess's**, embedded in the broadcast PGNs. Our own
  Stockfish on Modal provides what those lack: best replies, "what if" lines, deeper search.
- **Latency shown is latency measured**: move received → cue sent → first audio.

## Partner technologies

- **Google DeepMind — Gemini Live API** (`gemini-3.8-live`, `google-genai` SDK): two-way
  voice, barge-in, any language, non-blocking tools, and event-driven interruption through
  continuing tool responses with per-cue scheduling.
- **Modal**: Stockfish as a Modal function on CPU containers (`engine_farm/app.py`), called
  live for refutations and "what if" analysis and fanned out with `.map()` for whole-round
  analysis. Scales to zero; no GPU needed.
- **Pydantic**: Pydantic models as the contract between every component and as the tool
  interface to Gemini Live (schema out, validation with live context in, errors back to the
  model). **Logfire** spans from move to cue to voice (`LOGFIRE_TOKEN` optional).

Also used: FastAPI, uvicorn, python-chess, chessground (GPL-3.0, loaded from a CDN), uv.
Game data: Lichess broadcast API.

## Setup

Requires [uv](https://docs.astral.sh/uv/), a Gemini API key with Live API access, and
(optional, for arrows and "what if") a Modal account.

```bash
cp .env.example .env                      # fill in GEMINI_API_KEY
uv sync
uv run python scripts/fetch_pgn.py        # optional: round 1 PGNs are already in data/pgn/
modal deploy engine_farm/app.py           # optional: Stockfish on Modal
modal run engine_farm/app.py::analyse_round   # optional: re-run the whole-round fan-out
uv run uvicorn commentator.server:app --port 8000
```

Open http://localhost:8000, choose **Botswana**, press **Start commentary** and allow the
microphone (browsers only allow it on `localhost` or HTTPS). Use a headset. The blunder
arrives about 70 seconds in. **Watch silently** runs the replay and the director's feed
without the voice.

Without Modal the app still runs: cues go out without a refutation and "what if" reports
that the engine room is closed.

### Scripts

| Script | What it does |
|---|---|
| `scripts/fetch_pgn.py` | Download finished rounds from Lichess broadcasts |
| `scripts/find_drama.py` | Rank a round's matches by the single moves that moved a match forecast most |
| `scripts/score_forecast.py` | Brier score and reliability table for the match forecast against real results |
| `scripts/spike_cues.py` | The experiment: can an outside event interrupt a Live conversation through a tool? |
| `scripts/smoke_voice.py` | Whole system through the voice WebSocket with typed input (server must be running) |
| `python -m commentator.pgn_replay info` | Summary of the loaded round |

## Measured on the day

- Interrupt cue → first audio from Gemini Live: **0.9 s** (spike, mid-sentence cut-off).
- Stockfish on Modal, warm: **0.28 s** for a refutation, **0.9 s** for a depth-20 "what if".
- Whole round on Modal with `.map()`, one call per game: **30,189 positions at depth 14 in 61 s**
  across 99 containers (≈490 positions/s). The result, `data/analysis/round1.json`, makes every
  refutation arrow instant; live calls are kept for "what if" and as the fallback.
- Round 1 loaded and timelined: 393 games, 100 matches, 200 teams, in under 2 s.
- Match forecast scored against the real results (`scripts/score_forecast.py`, 1,756 forecasts
  over 95 matches): Brier score **0.016**, against 0.021 for ratings alone and 0.667 for a uniform
  guess. Round 1 pairs the top half against the bottom half, so ratings alone call almost every
  match; the live position mainly helps late on (0.007 against 0.022 after three hours). The
  model's parameters were set by hand and never fitted to these results.

## Limits

- One viewer at a time: the hub holds a single viewer's state.
- The match model is a hand-set blend of evaluation and rating. It is scored (see above) but
  not fitted, and round 1 is too lopsided to say much about calibration in close matches.
- The gate is rules. A learned gate could sit behind the same interface.
