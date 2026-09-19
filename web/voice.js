// Voice: streams the mic to /ws/voice, plays the commentator's speech, shows the transcript.
// Barge-in works both ways: the viewer can talk over the commentator, and an engine cue
// can cut the commentator off ("interrupted" clears whatever audio is still queued).

const $ = (id) => document.getElementById(id);

let ws = null;
let media = null;
let micCtx = null;
let playCtx = null;
let playHead = 0;
let muted = false;
let lastBubble = null;
const playing = new Set();

function setStatus(text, tone = "idle") {
  $("status").textContent = text;
  $("status").dataset.tone = tone;
}

function playPcm(buffer) {
  const samples = new Int16Array(buffer, 0, Math.floor(buffer.byteLength / 2));
  const audio = playCtx.createBuffer(1, samples.length, 24000);
  const channel = audio.getChannelData(0);
  for (let i = 0; i < samples.length; i++) channel[i] = samples[i] / 32768;
  const src = playCtx.createBufferSource();
  src.buffer = audio;
  src.connect(playCtx.destination);
  const at = Math.max(playCtx.currentTime + 0.03, playHead);
  src.start(at);
  playHead = at + audio.duration;
  playing.add(src);
  src.onended = () => playing.delete(src);
}

function stopPlayback() {
  for (const src of playing) src.stop();
  playing.clear();
  playHead = 0;
}

function say(who, text, extraClass) {
  if (!lastBubble || lastBubble.dataset.who !== who || who === "tool") {
    lastBubble = document.createElement("p");
    lastBubble.className = "bubble" + (extraClass ? ` ${extraClass}` : "");
    lastBubble.dataset.who = who;
    $("transcript").append(lastBubble);
  }
  lastBubble.textContent += text;
  $("transcript").scrollTop = $("transcript").scrollHeight;
}

function onMessage(event) {
  if (event.data instanceof ArrayBuffer) return playPcm(event.data);
  const msg = JSON.parse(event.data);
  switch (msg.type) {
    case "ready":
      setStatus(`Commentary on · ${msg.model}`, "live");
      break;
    case "transcript":
      say(msg.who, msg.text);
      break;
    case "turn_complete":
      lastBubble = null;
      break;
    case "interrupted":
      stopPlayback();
      lastBubble = null;
      break;
    case "tool_call":
      say("tool", `→ ${msg.name}(${Object.values(msg.args || {}).join(", ")})`);
      lastBubble = null;
      break;
    case "tool_result":
      if (!msg.ok) {
        say("tool", `✗ rejected: ${msg.response.errors.map((e) => e.problem).join("; ")}`, "bad");
        lastBubble = null;
      }
      break;
    case "go_away":
      setStatus(`Session ending in ${msg.time_left}`, "warn");
      break;
    case "error":
      setStatus(msg.message, "error");
      break;
  }
}

export function startVoice({ profile }) {
  async function start() {
    $("call").disabled = true;
    setStatus("Asking for the microphone…");
    try {
      media = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 } });
    } catch (err) {
      media = null; // carry on without a mic: the text box still works
      setStatus(`No microphone (${err.message}). You can still type.`, "warn");
    }
    playCtx = new AudioContext({ sampleRate: 24000 });
    if (media) {
      micCtx = new AudioContext({ sampleRate: 16000 });
      await micCtx.audioWorklet.addModule("/web/mic-worklet.js");
      const mic = new AudioWorkletNode(micCtx, "mic-processor");
      mic.port.onmessage = (e) => {
        if (!muted && ws?.readyState === WebSocket.OPEN) ws.send(e.data);
      };
      micCtx.createMediaStreamSource(media).connect(mic);
    }
    const q = new URLSearchParams(profile());
    ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/voice?${q}`);
    ws.binaryType = "arraybuffer";
    ws.onmessage = onMessage;
    ws.onclose = end;
    ws.onopen = () => {
      $("call").textContent = "Stop commentary";
      $("call").disabled = false;
      $("call").onclick = () => ws.close();
      $("mute").disabled = !media;
      $("say").disabled = false;
    };
  }

  function end() {
    stopPlayback();
    media?.getTracks().forEach((t) => t.stop());
    micCtx?.close();
    playCtx?.close();
    ws = media = micCtx = playCtx = null;
    if ($("status").dataset.tone !== "error") setStatus("Commentary stopped");
    $("call").textContent = "Start commentary";
    $("call").disabled = false;
    $("call").onclick = start;
    $("mute").disabled = true;
    $("say").disabled = true;
  }

  $("call").onclick = start;

  $("mute").onclick = () => {
    muted = !muted;
    $("mute").textContent = muted ? "Unmute" : "Mute";
    $("mute").setAttribute("aria-pressed", muted);
    if (muted) ws?.send(JSON.stringify({ type: "mic_off" }));
  };

  $("say-form").onsubmit = (e) => {
    e.preventDefault();
    const text = $("say").value.trim();
    if (!text || !ws) return;
    ws.send(JSON.stringify({ type: "text", text }));
    say("viewer", text);
    lastBubble = null;
    $("say").value = "";
  };
}
