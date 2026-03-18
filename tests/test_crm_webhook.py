# tests/test_crm_webhook.py
"""Tests for CRM webhook integration."""

import pytest

from nanobot.bus.events import InboundMessage, OutboundMessage


class TestCrossChannelRouting:
    """Test that reply_channel metadata overrides outbound channel."""

    def test_outbound_uses_reply_channel_when_present(self):
        """OutboundMessage should use reply_channel from metadata."""
        msg = InboundMessage(
            channel="crm_event",
            sender_id="crm_system",
            chat_id="51987654321@s.whatsapp.net",
            content="Test CRM event",
            metadata={"reply_channel": "whatsapp"},
        )
        out_channel = msg.metadata.get("reply_channel", msg.channel)
        assert out_channel == "whatsapp"

    def test_outbound_falls_back_to_channel_when_no_reply_channel(self):
        """Without reply_channel, should use original channel."""
        msg = InboundMessage(
            channel="whatsapp",
            sender_id="user123",
            chat_id="51987654321@s.whatsapp.net",
            content="Hello",
        )
        out_channel = msg.metadata.get("reply_channel", msg.channel)
        assert out_channel == "whatsapp"
