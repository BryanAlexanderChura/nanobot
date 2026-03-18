"""Tests for Supabase CRM integration client."""

import pytest
import respx
import httpx


class TestSupabaseCRMClient:
    """Test the Supabase client for crm_mensajes updates."""

    def _make_client(self):
        from nanobot.integrations.supabase import SupabaseCRMClient
        return SupabaseCRMClient(
            url="https://test.supabase.co",
            service_key="test-service-key",
        )

    def test_client_creation(self):
        client = self._make_client()
        assert client is not None

    @respx.mock
    @pytest.mark.asyncio
    async def test_mark_sent_updates_crm_mensaje(self):
        client = self._make_client()
        route = respx.patch(
            "https://test.supabase.co/rest/v1/crm_mensajes",
        ).mock(return_value=httpx.Response(200, json=[{}]))

        await client.mark_sent(
            crm_mensaje_id="uuid-123",
            evolution_msg_id="BAE123",
            mensaje_generado="Hola Marita!",
        )
        await client.close()

        assert route.called
        request = route.calls[0].request
        assert request.headers["apikey"] == "test-service-key"
        assert "uuid-123" in str(request.url)

    @respx.mock
    @pytest.mark.asyncio
    async def test_mark_failed_updates_crm_mensaje(self):
        client = self._make_client()
        route = respx.patch(
            "https://test.supabase.co/rest/v1/crm_mensajes",
        ).mock(return_value=httpx.Response(200, json=[{}]))

        await client.mark_failed(
            crm_mensaje_id="uuid-456",
            error="Evolution API error: 400",
            retry_count=2,
        )
        await client.close()

        assert route.called

    @respx.mock
    @pytest.mark.asyncio
    async def test_mark_sent_handles_api_error_gracefully(self):
        client = self._make_client()
        respx.patch(
            "https://test.supabase.co/rest/v1/crm_mensajes",
        ).mock(return_value=httpx.Response(500, text="Internal error"))

        # Should not raise — errors are logged, not propagated
        await client.mark_sent(
            crm_mensaje_id="uuid-789",
            evolution_msg_id="BAE456",
            mensaje_generado="Test",
        )
        await client.close()

    def test_disabled_when_no_url(self):
        from nanobot.integrations.supabase import SupabaseCRMClient
        client = SupabaseCRMClient(url="", service_key="")
        assert client.enabled is False
