"""The whole commentator, hosted on Modal: board UI, event stream and the Gemini Live voice bridge.

One container holds every viewer's hub (each browser tab has its own replay and voice), so
WebSockets and state stay in one process. It calls the Stockfish function in `app.py` for
"what if" analysis and the Gemma endpoint through the Pydantic AI Gateway for captions.
HTTPS comes with the URL, so the microphone works from any phone or laptop.

Deploy (from the repository root, with a filled-in .env):  modal deploy engine_farm/web.py
"""

import modal

app = modal.App("olympiad-commentator")

image = (
    modal.Image.debian_slim(python_version="3.13")
    .pip_install(
        "chess==1.11.2", "fastapi>=0.116", "google-genai>=2.24", "logfire[fastapi]>=5.1", "modal",
        "pydantic>=2.11", "pydantic-ai-slim[openai]>=2.46", "python-dotenv>=1.0", "uvicorn[standard]>=0.30",
    )
    .add_local_python_source("commentator")
    .add_local_dir("web", "/root/web")
    .add_local_dir("data/pgn", "/root/data/pgn")
    .add_local_dir("data/analysis", "/root/data/analysis")
)


@app.function(image=image, secrets=[modal.Secret.from_dotenv()], max_containers=1, scaledown_window=1200, timeout=3600, cpu=2.0, memory=2048)
@modal.concurrent(max_inputs=200)
@modal.asgi_app(label="olympiad-commentator")
def web():
    from commentator.server import app as fastapi_app

    return fastapi_app
