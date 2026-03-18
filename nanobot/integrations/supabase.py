"""Lightweight Supabase client for updating crm_mensajes."""

from datetime import datetime, timezone

import httpx
from loguru import logger


class SupabaseCRMClient:
    """Updates crm_mensajes table via Supabase REST API.

    Uses httpx to avoid adding supabase-py as a heavy dependency.
    All operations are fire-and-forget (errors logged, not raised).
    """

    def __init__(self, url: str, service_key: str):
        self.enabled = bool(url and service_key)
        if self.enabled:
            self._client = httpx.AsyncClient(
                base_url=f"{url.rstrip('/')}/rest/v1",
                headers={
                    "apikey": service_key,
                    "Authorization": f"Bearer {service_key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                },
                timeout=15.0,
            )
        else:
            self._client = None
            logger.info("SupabaseCRMClient disabled (no SUPABASE_URL configured)")

    async def mark_sent(
        self,
        crm_mensaje_id: str,
        evolution_msg_id: str,
        mensaje_generado: str,
    ) -> None:
        """Mark a crm_mensajes record as successfully sent."""
        if not self.enabled:
            return
        now = datetime.now(timezone.utc).isoformat()
        await self._update(crm_mensaje_id, {
            "estado_envio": "enviado_api",
            "mensaje_renderizado": mensaje_generado,
            "metadata": {
                "source": "nanobot",
                "agent": "lavanderia",
                "generation_mode": "llm",
                "evolution_msg_id": evolution_msg_id,
                "sent_at": now,
            },
        })

    async def mark_failed(
        self,
        crm_mensaje_id: str,
        error: str,
        retry_count: int = 0,
    ) -> None:
        """Mark a crm_mensajes record as failed."""
        if not self.enabled:
            return
        now = datetime.now(timezone.utc).isoformat()
        await self._update(crm_mensaje_id, {
            "estado_envio": "fallido",
            "detalle_error": error,
            "metadata": {
                "source": "nanobot",
                "error_at": now,
                "retry_count": retry_count,
            },
        })

    async def _update(self, crm_mensaje_id: str, data: dict) -> None:
        """Update a crm_mensajes record by ID."""
        try:
            resp = await self._client.patch(
                "/crm_mensajes",
                params={"id": f"eq.{crm_mensaje_id}"},
                json=data,
            )
            if resp.status_code not in (200, 204):
                logger.error(
                    "Supabase update failed for {}: {} {}",
                    crm_mensaje_id, resp.status_code, resp.text[:200],
                )
        except Exception as e:
            logger.error("Supabase request failed for {}: {}", crm_mensaje_id, e)

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
