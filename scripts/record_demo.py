"""Record the two-minute demo video, hands-free.

A headless Chromium opens the app with a fake microphone. The "viewer" is a text-to-speech
voice (macOS `say`) whose questions are laid out on a timeline and fed to the page as mic
audio, so the whole voice path is exercised: speech detection, transcription, barge-in.
Playwright records the screen; the server records the commentator's speech as the browser
plays it (VOICE_TEE_DIR); ffmpeg muxes screen + both voices into demo_out/demo.mp4.

Needs: macOS, ffmpeg, `uv run --with playwright playwright install chromium`, and the server
running with the recorder on:
    VOICE_TEE_DIR=demo_out LEAD_IN_MINUTES=19 uv run uvicorn commentator.server:app --port 8002
Run:  uv run --with playwright python scripts/record_demo.py [http://127.0.0.1:8002]
"""

import asyncio
import json
import subprocess
import sys
import time
import wave
from pathlib import Path

from playwright.async_api import async_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8002"
OUT = Path("demo_out")
VOICE = "Daniel"
RATE = 48000
LENGTH = 118  # seconds of call to record
SIZE = {"width": 1460, "height": 820}
QUESTIONS = [  # seconds after pressing Start -> what the viewer says
    (17, "Why is board three going badly for us?"),
    (51, "Tell me the history of the Chess Olympiad, in detail."),
    (78, "What if Black plays bishop to c8 on board four?"),
    (100, "Why did you cut in earlier?"),
]


def build_viewer_track() -> Path:
    """One long WAV: silence, with each spoken question at its time. Chrome plays it as the mic."""
    pcm = bytearray(RATE * 2 * (LENGTH + 30))
    for i, (at, text) in enumerate(QUESTIONS):
        aiff, wav = OUT / f"q{i}.aiff", OUT / f"q{i}.wav"
        subprocess.run(["say", "-v", VOICE, "-o", str(aiff), text], check=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(aiff), "-ar", str(RATE), "-ac", "1", "-sample_fmt", "s16", str(wav)], check=True)
        with wave.open(str(wav)) as w:
            frames = w.readframes(w.getnframes())
        start = at * RATE * 2
        pcm[start:start + len(frames)] = frames
    track = OUT / "viewer.wav"
    with wave.open(str(track), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(pcm))
    return track.resolve()


async def main() -> None:
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("*.webm"):
        old.unlink()
    viewer = build_viewer_track()
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=[
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
            f"--use-file-for-fake-audio-capture={viewer}%noloop",
            "--autoplay-policy=no-user-gesture-required",
        ])
        context = await browser.new_context(viewport=SIZE, record_video_dir=str(OUT), record_video_size=SIZE, permissions=["microphone"])
        page = await context.new_page()
        video_started = time.time()
        page.on("console", lambda m: print("console:", m.text) if m.type == "error" else None)
        await page.goto(URL)
        await page.wait_for_selector("#team")
        await page.fill("#team", "Botswana")
        await page.select_option("#level", "club")
        await page.fill("#language", "English")
        await asyncio.sleep(1.5)
        await page.click("#call")
        clicked = time.time()
        print("call started; recording", LENGTH, "s")
        await asyncio.sleep(LENGTH)
        print("status:", await page.inner_text("#status"))
        print("transcript:\n" + await page.inner_text("#transcript"))
        await page.click("#call")  # hang up: the server writes commentator.wav
        await asyncio.sleep(2)
        await context.close()
        await browser.close()

    video = next(OUT.glob("*.webm"))
    speech_started = json.loads((OUT / "commentator.json").read_text())["started_wall"]
    offsets = {"commentator": speech_started - video_started, "viewer": clicked - video_started + 0.25}
    print("offsets:", offsets)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
        "-itsoffset", f"{offsets['commentator']:.3f}", "-i", str(OUT / "commentator.wav"),
        "-itsoffset", f"{offsets['viewer']:.3f}", "-i", str(viewer),
        "-filter_complex", "[1:a]aresample=48000[c];[2:a]volume=0.9[v];[c][v]amix=inputs=2:normalize=0:duration=longest[a]",
        "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-t", str(LENGTH + 4), "-movflags", "+faststart", str(OUT / "demo.mp4"),
    ], check=True)
    print("wrote", OUT / "demo.mp4")


asyncio.run(main())
