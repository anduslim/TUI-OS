"""Pick the LLM client for ``Settings.llm_api``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .ollama import OllamaClient
from .openai_chat import OpenAIChatClient

if TYPE_CHECKING:
    from ..config import Settings


def make_llm_client(settings: "Settings") -> OllamaClient | OpenAIChatClient:
    if settings.resolved_llm_api == "openai":
        return OpenAIChatClient(
            settings.resolved_llm_url, settings.resolved_llm_model, api_key=settings.llm_api_key
        )
    return OllamaClient(settings.resolved_llm_url, settings.resolved_llm_model)
