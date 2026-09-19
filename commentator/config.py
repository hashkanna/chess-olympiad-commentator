"""Settings, read once from the environment (.env is loaded for local runs)."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    live_model: str


def get_settings() -> Settings:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set. Copy .env.example to .env and fill it in.")
    return Settings(
        gemini_api_key=key,
        live_model=os.environ.get("GEMINI_LIVE_MODEL", "gemini-3.8-live"),
    )
