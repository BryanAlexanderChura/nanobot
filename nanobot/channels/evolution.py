"""WhatsApp channel implementation using Evolution API."""

import asyncio
import re

import httpx
from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import WhatsAppConfig

# Max chars per WhatsApp message before splitting
_MAX_CHUNK = 1500


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
        """Send message via Evolution API: text chunks first, then media attachments."""
        number = self._jid_to_number(msg.chat_id)

        # 1. Send text content first
        chunks = self._split_message(msg.content)
        last_msg_id = ""
        for chunk in chunks:
            msg_id = await self._send_text(number, chunk)
            if msg_id:
                last_msg_id = msg_id
            if len(chunks) > 1:
                await asyncio.sleep(0.8)

        # 2. Send media attachments after text (PDFs, images, etc.)
        for media_path in (msg.media or []):
            if chunks:
                await asyncio.sleep(0.8)
            msg_id = await self._send_media(number, media_path)
            if msg_id:
                last_msg_id = msg_id

        # Store Evolution message ID in metadata for CRM tracking
        if last_msg_id:
            msg.metadata["evolution_msg_id"] = last_msg_id

    async def _send_text(self, number: str, text: str) -> str:
        """Send a single text message to a number. Returns Evolution message ID."""
        if not text or not text.strip():
            return ""

        if self._mock_mode:
            logger.info("[EvolutionChannel MOCK] → {}: {}", number, text[:200])
            return ""

        try:
            resp = await self._client.post(
                f"/message/sendText/{self.config.evolution_instance}",
                json={"number": number, "text": text},
            )
            if resp.status_code not in (200, 201):
                logger.error(
                    "Evolution API error {}: {}", resp.status_code, resp.text[:200]
                )
                return ""
            # Extract message ID from Evolution response
            data = resp.json()
            return data.get("key", {}).get("id", "")
        except Exception as e:
            logger.error("Failed to send via Evolution API: {}", e)
            return ""

    async def _send_media(self, number: str, media_path: str) -> str:
        """Send a media file (PDF, image, etc.) via Evolution API.

        Supports both local file paths (read as base64) and public URLs (passed directly).
        """
        if self._mock_mode:
            logger.info("[EvolutionChannel MOCK] media → {}: {}", number, media_path)
            return ""

        import mimetypes
        import os

        is_url = media_path.startswith("http://") or media_path.startswith("https://")

        if is_url:
            mime = "application/pdf"
            filename = media_path.split("/")[-1].split("?")[0]
            media_value = media_path
        else:
            mime, _ = mimetypes.guess_type(media_path)
            mime = mime or "application/octet-stream"
            filename = os.path.basename(media_path)
            try:
                import base64
                with open(media_path, "rb") as f:
                    raw = base64.b64encode(f.read()).decode()
                media_value = f"data:{mime};base64,{raw}"
            except FileNotFoundError:
                logger.error("Media file not found: {}", media_path)
                return ""

        if mime.startswith("image/"):
            media_type = "image"
        elif mime.startswith("audio/"):
            media_type = "audio"
        elif mime.startswith("video/"):
            media_type = "video"
        else:
            media_type = "document"

        try:
            resp = await self._client.post(
                f"/message/sendMedia/{self.config.evolution_instance}",
                json={
                    "number": number,
                    "mediatype": media_type,
                    "mimetype": mime,
                    "fileName": filename,
                    "media": media_value,
                },
            )
            if resp.status_code not in (200, 201):
                logger.error(
                    "Evolution API media error {}: {}", resp.status_code, resp.text[:200]
                )
                return ""
            data = resp.json()
            return data.get("key", {}).get("id", "")
        except Exception as e:
            logger.error("Failed to send media via Evolution API: {}", e)
            return ""

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

    @staticmethod
    def _split_message(text: str) -> list[str]:
        """Split long text into WhatsApp-friendly chunks.

        Splits on double newlines (paragraphs) first, then on single newlines,
        keeping each chunk under _MAX_CHUNK characters.
        """
        if len(text) <= _MAX_CHUNK:
            return [text]

        # Split on double newlines (paragraphs)
        paragraphs = re.split(r"\n\n+", text)
        chunks: list[str] = []
        current = ""

        for para in paragraphs:
            candidate = f"{current}\n\n{para}" if current else para
            if len(candidate) <= _MAX_CHUNK:
                current = candidate
            else:
                if current:
                    chunks.append(current.strip())
                # If single paragraph is too long, split on newlines
                if len(para) > _MAX_CHUNK:
                    lines = para.split("\n")
                    current = ""
                    for line in lines:
                        candidate = f"{current}\n{line}" if current else line
                        if len(candidate) <= _MAX_CHUNK:
                            current = candidate
                        else:
                            if current:
                                chunks.append(current.strip())
                            current = line
                else:
                    current = para

        if current:
            chunks.append(current.strip())

        return chunks if chunks else [text]
