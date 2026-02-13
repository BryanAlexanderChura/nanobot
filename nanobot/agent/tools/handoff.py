"""Handoff tool for routing messages between agent profiles."""

from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus


class HandoffTool(Tool):
    """Route a message to another agent profile via the bus."""

    def __init__(self, bus: MessageBus):
        self._bus = bus
        self._channel = ""
        self._chat_id = ""

    def set_context(self, channel: str, chat_id: str) -> None:
        self._channel = channel
        self._chat_id = chat_id

    @property
    def name(self) -> str:
        return "handoff"

    @property
    def description(self) -> str:
        return "Transfer the current conversation to another agent profile."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Name of the target agent profile",
                },
                "message": {
                    "type": "string",
                    "description": "Context/summary to pass to the target agent",
                },
            },
            "required": ["target", "message"],
        }

    async def execute(self, **kwargs: Any) -> str:
        target = kwargs["target"]
        message = kwargs["message"]
        await self._bus.publish_inbound(InboundMessage(
            channel=f"handoff:{target}",
            sender_id="agent",
            chat_id=self._chat_id,
            content=message,
            metadata={"origin_channel": self._channel},
        ))
        return f"Handed off to '{target}'."
