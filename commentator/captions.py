"""On-screen captions, written by an open-weight model on our own Modal GPU.

The voice says it; the caption under the board keeps it on screen. Each spoken cue is
turned into a written caption by Gemma running on a Modal endpoint, reached through the
Pydantic AI Gateway route `modal`.

This agent is deliberately plain: no style rules, no length limits. House style for
captions (length, tense, what to leave out) is a Gateway optimization rule installed on
the `modal` route, so an editor can change how every caption reads without touching or
redeploying this code. See docs/gateway-rule.md.

Off unless PYDANTIC_AI_GATEWAY_API_KEY is set.
"""

import os
from typing import Any

import logfire
from pydantic_ai import Agent

from .contracts import CommentaryCue, ViewerProfile

MODEL = os.environ.get("MODAL_MODEL", "google/gemma-4-26B-A4B-it")

_agent: Agent | None = None


def enabled() -> bool:
    return bool(os.environ.get("PYDANTIC_AI_GATEWAY_API_KEY"))


def build_agent() -> Agent:
    from openai.types.chat import ChatCompletion
    from pydantic_ai.models.openai import OpenAIChatModel, _ChatCompletion
    from pydantic_ai.providers.gateway import gateway_provider

    # Modal returns `metadata.weight_versions` as a list, but the OpenAI schema types
    # `metadata` as dict[str, str]. Widen it on both models that see the payload.
    for _model in (ChatCompletion, _ChatCompletion):
        _model.model_fields["metadata"].annotation = dict[str, Any] | None
        _model.model_rebuild(force=True)

    provider = gateway_provider("openai-chat", route="modal")
    return Agent(
        OpenAIChatModel(MODEL, provider=provider),
        instructions="You write the on-screen caption for a chess broadcast. Use only the facts you are given.",
    )


def prompt_for(cue: CommentaryCue, profile: ViewerProfile) -> str:
    return (
        f"Viewer: follows {profile.team}, chess level {profile.level}, reads {profile.language}.\n"
        f"What just happened (engine-verified): {cue.fact}\n"
        f"Write the caption in {profile.language}."
    )


async def write_caption(cue: CommentaryCue, profile: ViewerProfile) -> str | None:
    global _agent
    if not enabled():
        return None
    if _agent is None:
        _agent = build_agent()
    try:
        with logfire.span("caption for cue {cue_id}", cue_id=cue.id, decision=cue.decision.value):
            result = await _agent.run(prompt_for(cue, profile))
        return result.output.strip()
    except Exception:
        logfire.exception("caption failed")
        return None
