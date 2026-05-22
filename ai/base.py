"""Provider-agnostic chat client interface.

Both OpenRouter and Groq expose an OpenAI-compatible API, so we can share
implementation. Subclasses just supply api_key, base_url, default model and
provider-specific headers.
"""

from __future__ import annotations

from abc import ABC
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from core.logger import logger


class AIClient(ABC):
    name: str = "abstract"
    base_url: str
    api_key: str
    default_model: str
    extra_headers: dict[str, str] = {}

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                **self.extra_headers,
            }
            self._client = httpx.AsyncClient(
                base_url=self.base_url, headers=headers, timeout=self._timeout
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((httpx.HTTPError,)),
    )
    async def chat(
        self,
        system: str,
        user: str,
        *,
        model: str | None = None,
        temperature: float = 0.4,
        max_tokens: int = 600,
    ) -> str:
        if not self.api_key:
            logger.warning("[{}] No API key set, skipping AI call.", self.name)
            return ""
        client = await self._get_client()
        payload: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        resp = await client.post("/chat/completions", json=payload)
        if resp.status_code >= 400:
            logger.error("[{}] {} {}", self.name, resp.status_code, resp.text[:300])
            resp.raise_for_status()
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            logger.error("[{}] Unexpected response shape: {}", self.name, exc)
            return ""
