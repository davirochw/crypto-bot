"""Groq chat client (OpenAI-compatible). Ultra-fast inference for Llama/Mixtral."""

from __future__ import annotations

from ai.base import AIClient
from core.settings import settings


class GroqClient(AIClient):
    name = "groq"

    def __init__(self) -> None:
        super().__init__()
        self.api_key = settings.groq_api_key
        self.base_url = settings.groq_base_url
        self.default_model = settings.groq_model
