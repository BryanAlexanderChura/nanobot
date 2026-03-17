"""WhatsApp channel implementation using Evolution API."""

import asyncio

import httpx
from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import WhatsAppConfig


class EvolutionChannel(BaseChannel):
    """
    WhatsApp channel using Evolution API.

    Receives messages via the webhook server (webhook/routes.py calls _handle_message).
    Sends messages via REST to Evolution API.
    Falls back to mock mode if evolution_api_url is empty.
    """

    name = "whatsapp"

    def __init__(self, config: WhatsAppConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: WhatsAppConfig = config
        self._stop_event = asyncio.Event()
        self._mock_mode = not config.evolution_api_url

        if not self._mock_mode:
            self._client = httpx.AsyncClient(
                base_url=config.evolution_api_url,
                headers={"apikey": config.evolution_api_key},
                timeout=30.0,
            )
        else:
            self._client = None
            logger.info("EvolutionChannel running in MOCK mode (no evolution_api_url)")

    async def start(self) -> None:
        """Wait until stopped. Messages arrive via webhook server, not polling."""
        self._running = True
        logger.info("EvolutionChannel started (provider=evolution)")
        await self._stop_event.wait()

    async def send(self, msg: OutboundMessage) -> None:
        """Send a text message via Evolution API."""
        number = self._jid_to_number(msg.chat_id)

        if self._mock_mode:
            logger.info("[EvolutionChannel MOCK] → {}: {}", number, msg.content[:200])
            return

        try:
            resp = await self._client.post(
                f"/message/sendText/{self.config.evolution_instance}",
                json={"number": number, "text": msg.content},
            )
            if resp.status_code != 200:
                logger.error(
                    "Evolution API error {}: {}", resp.status_code, resp.text[:200]
                )
        except Exception as e:
            logger.error("Failed to send via Evolution API: {}", e)

    async def stop(self) -> None:
        """Stop the channel and close HTTP client."""
        self._running = False
        self._stop_event.set()
        if self._client:
            await self._client.aclose()

    def _jid_to_number(self, jid: str) -> str:
        """Convert WhatsApp JID to plain number.

        '51987654321@s.whatsapp.net' → '51987654321'
        """
        return jid.split("@")[0]
