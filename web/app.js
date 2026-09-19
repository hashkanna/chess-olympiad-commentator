// Board UI: renders hub state (featured board, four mini boards, forecast, director's feed)
// from /ws/events. The voice lives in voice.js.

import { Chessground } from "https://cdn.jsdelivr.net/npm/chessground@9.2.1/+esm";
import { startVoice } from "/web/voice.js";

const $ = (id) => document.getElementById(id);
const pct = (x) => `${Math.round(x * 100)}%`;
const START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

let snap = null; // latest state from the hub
let snapAt = 0; // when it arrived, for ticking clocks
let pinned = null; // board the viewer clicked, until the director moves on
let lastServerFeatured = null;
let matchShown = null;
const minis = new Map(); // board number -> { cg, el }

const big = Chessground($("big"), { fen: START_FEN, viewOnly: true, coordinates: true, animation: { duration: 350 } });
window.__big = big; // handy in the console

function viewerIsWhite(b) {
  return snap.profile && b.white.team === snap.profile.team;
}

function lastMove(b) {
  return b.last_uci ? [b.last_uci.slice(0, 2), b.last_uci.slice(2, 4)] : undefined;
}

function clockText(seconds) {
  if (seconds == null) return "";
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${h}:${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

function buildMinis(boards) {
  $("minis").replaceChildren();
  minis.clear();
  for (const b of boards) {
    const el = document.createElement("div");
    el.className = "mini";
    el.innerHTML = `<header><b></b><span class="res"></span></header><div class="wrap"><div class="cg"></div></div><div class="winbar"><div></div></div><div class="names"></div>`;
    el.onclick = () => {
      pinned = b.board;
      render();
    };
    $("minis").append(el);
    const cg = Chessground(el.querySelector(".cg"), { fen: b.fen, viewOnly: true, coordinates: false, animation: { duration: 300 } });
    minis.set(b.board, { cg, el });
  }
}

function playerStrip(el, player, clock, ticking) {
  el.innerHTML = `<span class="name"></span><span class="meta"></span><span class="clock"></span>`;
  el.querySelector(".name").textContent = `${player.title ? player.title + " " : ""}${player.name}`;
  el.querySelector(".meta").textContent = `${player.team}${player.elo ? " · " + player.elo : ""}`;
  const c = el.querySelector(".clock");
  c.textContent = clockText(clock);
  c.classList.toggle("ticking", ticking);
  c.hidden = clock == null;
}

function render() {
  if (!snap || !snap.match_id) return;
  const boards = snap.boards;
  if (matchShown !== snap.match_id) {
    matchShown = snap.match_id;
    pinned = null;
    buildMinis(boards);
    $("feed").replaceChildren();
    $("caption").textContent = "";
  }
  if (snap.featured !== lastServerFeatured) {
    lastServerFeatured = snap.featured;
    pinned = null; // the director changed subject: follow it
  }
  const featuredNo = pinned ?? snap.featured;
  const elapsed = ((performance.now() - snapAt) / 1000) * snap.speed;

  for (const b of boards) {
    const { cg, el } = minis.get(b.board);
    const white = viewerIsWhite(b);
    cg.set({ fen: b.fen, lastMove: lastMove(b), orientation: white ? "white" : "black" });
    const ours = white ? b.win_chance_white : 1 - b.win_chance_white;
    el.querySelector(".winbar div").style.width = pct(b.result ? { "1-0": white ? 1 : 0, "0-1": white ? 0 : 1, "1/2-1/2": 0.5 }[b.result] : ours);
    el.querySelector("header b").textContent = `Board ${b.board}`;
    el.querySelector(".res").textContent = b.result ? `finished ${b.result}` : `${snap.profile.team}: ${pct(ours)}`;
    el.querySelector(".names").textContent = `${b.white.name} – ${b.black.name}`;
    el.classList.toggle("on", b.board === featuredNo);
    el.classList.toggle("alerted", b.arrows.length > 0 && b.arrows[0].brush === "red");
  }

  const f = boards.find((b) => b.board === featuredNo) ?? boards[0];
  const white = viewerIsWhite(f);
  // Shapes go in through set(): in view-only mode setAutoShapes() alone does not repaint.
  big.set({ fen: f.fen, lastMove: lastMove(f), orientation: white ? "white" : "black", drawable: { autoShapes: f.arrows } });
  const whiteToMove = f.fen.split(" ")[1] === "w";
  const tick = (isWhite) => !f.result && whiteToMove === isWhite;
  const clock = (isWhite) => {
    const base = isWhite ? f.clock_white : f.clock_black;
    return base == null ? null : tick(isWhite) ? base - elapsed : base;
  };
  playerStrip($("top-player"), white ? f.black : f.white, clock(!white), tick(!white));
  playerStrip($("bottom-player"), white ? f.white : f.black, clock(white), tick(white));
  $("evalfill").style.height = pct(f.win_chance_white);
  $("evalfill").parentElement.classList.toggle("flipped", !white);

  const p = snap.prediction;
  if (p) {
    $("match").hidden = false;
    $("team-a").textContent = p.team;
    $("team-b").textContent = p.opponent;
    $("score").textContent = `${p.score} – ${p.opponent_score}`;
    $("f-win").style.width = pct(p.p_win);
    $("f-draw").style.width = pct(p.p_draw);
    $("f-loss").style.width = pct(p.p_loss);
    $("l-win").textContent = `Our forecast · ${p.team} win ${pct(p.p_win)}`;
    $("l-draw").textContent = `draw ${pct(p.p_draw)}`;
    $("l-loss").textContent = `loss ${pct(p.p_loss)}`;
  }
  if (snap.game_minutes != null) {
    const m = snap.game_minutes + Math.floor(elapsed / 60);
    $("game-clock").textContent = `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m into the round · ${snap.speed}× speed`;
  }
  $("engine").textContent = snap.engine === "modal" ? "Engine room: Stockfish on Modal" : "Engine room: closed";
  $("engine").classList.toggle("live", snap.engine === "modal");
}

const CHIP = { interrupt: "CUT IN", when_idle: "AT A PAUSE", silent: "IGNORED" };

function addCue({ cue }) {
  const el = document.createElement("div");
  el.className = `cue ${cue.decision}`;
  el.innerHTML = `<div class="head"><span class="chip"></span><span class="text"></span></div><div class="why"></div>`;
  el.querySelector(".chip").textContent = CHIP[cue.decision];
  el.querySelector(".text").textContent = cue.headline;
  el.querySelector(".why").textContent = cue.reason;
  $("feed").prepend(el);
  while ($("feed").children.length > 40) $("feed").lastChild.remove();
  if (cue.decision === "interrupt") {
    $("stage").classList.remove("flash");
    void $("stage").offsetWidth; // restart the animation
    $("stage").classList.add("flash");
    $("caption").textContent = cue.headline + " — " + cue.reason;
  }
}

function showLatency(ev) {
  $("latency").hidden = false;
  $("latency").classList.add("live");
  $("latency").textContent = `move → voice ${((ev.move_to_cue_ms + ev.cue_to_audio_ms) / 1000).toFixed(1)} s (gate + engine ${ev.move_to_cue_ms} ms, Live ${ev.cue_to_audio_ms} ms)`;
}

function connectEvents() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/events`);
  ws.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    if (ev.type === "state") {
      snap = ev;
      snapAt = performance.now();
      if (!$("teams").children.length) for (const t of ev.teams) $("teams").append(new Option(t));
      render();
    } else if (ev.type === "reset") {
      $("feed").replaceChildren();
      $("caption").textContent = "";
      $("latency").hidden = true;
      $("stage").classList.remove("flash");
    } else if (ev.type === "alert") addCue(ev);
    else if (ev.type === "latency") showLatency(ev);
  };
  ws.onclose = () => setTimeout(connectEvents, 1000);
}

connectEvents();
setInterval(render, 1000); // keeps the clocks ticking between moves

const profile = () => ({ team: $("team").value.trim(), level: $("level").value, language: $("language").value.trim() || "English" });

$("watch").onclick = async () => {
  const r = await fetch("/api/follow", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(profile()) });
  if (!r.ok) $("status").textContent = `Could not follow that team (${r.status}). Check the spelling.`;
};

startVoice({ profile });
