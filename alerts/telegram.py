"""Telegram notifier — minimal direct HTTP client (no python-telegram-bot polling).

Sending messages via the Bot API directly is enough for outbound alerts and
keeps the dependency surface small. Falls back to a no-op if disabled.
"""

from __future__ import annotations

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from core.logger import logger
from core.settings import settings

API_URL = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.token = token or settings.telegram_bot_token
        self.chat_id = chat_id or settings.telegram_chat_id
        self.enabled = settings.telegram_enabled if enabled is None else enabled
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15.0)
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def is_configured(self) -> bool:
        return bool(self.enabled and self.token and self.chat_id)

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((httpx.HTTPError,)),
    )
    async def send(self, text: str, *, parse_mode: str = "Markdown") -> bool:
        if not self.is_configured:
            logger.debug("Telegram disabled or unconfigured — skipping send.")
            return False
        client = await self._get_client()
        url = API_URL.format(token=self.token)
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        resp = await client.post(url, json=payload)
        if resp.status_code >= 400:
            logger.error("Telegram error {}: {}", resp.status_code, resp.text[:300])
            resp.raise_for_status()
        return True
