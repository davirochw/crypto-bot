"""AI layer: provider-agnostic chat client + analyst that interprets signals."""

from ai.base import AIClient
from ai.analyst import AIAnalyst
from ai.openrouter_client import OpenRouterClient
from ai.groq_client import GroqClient

__all__ = ["AIClient", "AIAnalyst", "OpenRouterClient", "GroqClient", "build_default_client"]


def build_default_client() -> AIClient:
    """Build the AI client based on `settings.ai_provider`."""
    from core.settings import settings

    if settings.ai_provider == "openrouter":
        return OpenRouterClient()
    return GroqClient()
