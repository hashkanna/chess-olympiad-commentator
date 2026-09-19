# Submission text

**Project:** Olympiad Commentator
**Live:** https://deshkanna--olympiad-commentator.modal.run · **Video:** https://hashkanna.github.io/chess-olympiad-commentator/
**Repo:** https://github.com/hashkanna/chess-olympiad-commentator
**Track:** Open Innovation · **Partner tech:** Google DeepMind, Modal, Pydantic

## What are you building?

A personal AI commentator for any team at the Chess Olympiad. About 190 countries play;
human commentators cover the top five boards. If you follow Botswana, nobody explains your
match. You talk to this one by voice, in your language, at your level. It follows every game
in the round at once and interrupts you only when something important happens to your team.

It runs on a replay of real round-1 games from this month's Olympiad (Lichess broadcasts, real
clock timings, 20× speed, always labelled as a replay). In the demo it breaks off
mid-sentence: "Hold on, board four. Brazil has just blundered," with the blunder and the
engine's refutation drawn on the board and Botswana's chance of at least drawing the match
jumping from 17% to 64%. The real match ended 2–2.

What makes it an agent: a director with a rules gate decides, for every swing in the round,
whether to cut in, wait for a pause, or stay silent, and can explain each decision. The voice
(Gemini Live) only puts engine-verified facts into words.

- **Gemini Live:** outside events interrupt a live conversation. The model's `follow_team`
  call is kept open and each cue is sent as a continuing tool response with its own
  scheduling (INTERRUPT / WHEN_IDLE / SILENT). Measured: 0.9 s from cue to first audio.
- **Modal:** Stockfish on CPU containers. Live "what if" analysis at depth 20 in about 1 s,
  and the whole round (30,189 positions) analysed in 61 s across 99 containers with `.map()`.
- **Pydantic:** every tool the voice can call is a Pydantic model validated against live
  state (is that team in this round? is that move legal right now?); errors go back to the
  model, which asks the viewer again. Logfire traces move → cue → voice. On-screen captions
  are written by Gemma on our own Modal GPU through the Pydantic AI Gateway, with house
  style set by a custom Gateway rule instead of code.
