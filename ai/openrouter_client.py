"""OpenRouter chat client. Uses OpenAI-compatible /chat/completions."""

from __future__ import annotations

from ai.base import AIClient
from core.settings import settings


class OpenRouterClient(AIClient):
    name = "openrouter"

    def __init__(self) -> None:
        super().__init__()
        self.api_key = settings.openrouter_api_key
        self.base_url = settings.openrouter_base_url
        self.default_model = settings.openrouter_model
        self.extra_headers = {
            "HTTP-Referer": "https://github.com/local/crypto-copilot",
            "X-Title": "Crypto Copilot",
        }
