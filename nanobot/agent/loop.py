"""Agent loop: the core processing engine."""

import asyncio
import json
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider
from nanobot.agent.context import ContextBuilder
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.filesystem import ReadFileTool, WriteFileTool, EditFileTool, ListDirTool
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.web import WebSearchTool, WebFetchTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.handoff import HandoffTool
from nanobot.agent.subagent import SubagentManager
from nanobot.session.manager import SessionManager


class AgentLoop:
    """
    The agent loop is the core processing engine.
    
    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """
    
    # Tool groups for declarative profile configuration
    TOOL_GROUPS = {
        "safe":   ["consulta", "consulta_cuidado"],
        "files":  ["read_file", "write_file", "edit_file", "list_dir"],
        "web":    ["web_search", "web_fetch"],
        "comms":  ["message", "handoff"],
        "system": ["exec", "spawn", "cron"],
    }

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 20,
        brave_api_key: str | None = None,
        exec_config: "ExecToolConfig | None" = None,
        cron_service: "CronService | None" = None,
        entity: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        thinking: bool = True,
        session_backend: str = "file",
        channels: list[str] | None = None,
        allowed_tools: list[str] | None = None,
    ):
        from nanobot.config.schema import ExecToolConfig
        from nanobot.cron.service import CronService
        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.entity = entity
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.thinking = thinking

        self.channels = set(channels) if channels else None  # None = accept all
        self.allowed_tools = self._resolve_tools(allowed_tools) if allowed_tools else None
        self.context = ContextBuilder(workspace, entity=entity)
        self.sessions = SessionManager(workspace, backend=session_backend)
        self.tools = ToolRegistry()
        self._supabase_tool = None
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            brave_api_key=brave_api_key,
            exec_config=self.exec_config,
        )

        self._running = False
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._instance_id = uuid.uuid4().hex[:8]
        self._scratch_dir = Path(tempfile.gettempdir()) / "nanobot" / self._instance_id
        self._scratch_dir.mkdir(parents=True, exist_ok=True)
        self._register_default_tools()
    
    @classmethod
    def _resolve_tools(cls, names: list[str]) -> set[str]:
        """Expand tool group names into individual tool names."""
        result = set()
        for n in names:
            result.update(cls.TOOL_GROUPS.get(n, [n]))
        return result

    def _register_default_tools(self) -> None:
        """Register tools, filtered by allowed_tools (groups or individual names)."""
        from nanobot.agent.tools.supabase import SupabaseTool
        from nanobot.agent.tools.cuidado_textil import CuidadoTextilTool

        all_tools = [
            ReadFileTool(),
            WriteFileTool(),
            EditFileTool(),
            ListDirTool(),
            ExecTool(
                working_dir=str(self.workspace),
                timeout=self.exec_config.timeout,
                restrict_to_workspace=self.exec_config.restrict_to_workspace,
            ),
            WebSearchTool(api_key=self.brave_api_key),
            WebFetchTool(),
            MessageTool(send_callback=self.bus.publish_outbound),
            SpawnTool(manager=self.subagents),
            HandoffTool(bus=self.bus),
        ]
        if self.cron_service:
            all_tools.append(CronTool(self.cron_service))

        # Domain-specific tools
        self._supabase_tool = SupabaseTool()
        all_tools.append(self._supabase_tool)
        entity_name = self.entity or "general"
        refs_dir = self.workspace / "agents" / entity_name / "skills" / "cuidado-textil" / "references"
        if refs_dir.exists():
            all_tools.append(CuidadoTextilTool(references_dir=str(refs_dir)))

        for tool in all_tools:
            if self.allowed_tools is None or tool.name in self.allowed_tools:
                self.tools.register(tool)
    
    async def run(self) -> None:
        """Run the agent loop, processing messages from the bus.

        Messages from different sessions are processed concurrently.
        Messages within the same session are serialized via per-session locks.
        """
        self._running = True
        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(
                    self.bus.consume_inbound(),
                    timeout=1.0
                )
                # Process each message concurrently; lock per session inside
                asyncio.create_task(self._handle_message(msg))
            except asyncio.TimeoutError:
                continue

    async def _handle_message(self, msg: InboundMessage) -> None:
        """Handle a single message with per-session serialization."""
        # Accept handoff messages targeted at this agent's channels
        effective_channel = msg.channel
        if msg.channel.startswith("handoff:") and self.channels:
            target = msg.channel.split(":", 1)[1]
            if target in self.channels or target == getattr(self, 'entity', None):
                effective_channel = msg.metadata.get("origin_channel", msg.channel)
                msg = InboundMessage(
                    channel=effective_channel, sender_id=msg.sender_id,
                    chat_id=msg.chat_id, content=msg.content,
                    media=msg.media, metadata=msg.metadata,
                )

        # Skip messages from channels this agent doesn't serve
        if self.channels and msg.channel not in self.channels and msg.channel != "system":
            # Re-queue so another agent can pick it up
            await self.bus.publish_inbound(msg)
            return

        lock = self._session_locks.setdefault(msg.session_key, asyncio.Lock())
        async with lock:
            try:
                response = await self._process_message(msg)
                if response:
                    chunks = _split_chunks(response.content)
                    for i, chunk in enumerate(chunks):
                        if i > 0:
                            await asyncio.sleep(0.8)
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=response.channel,
                            chat_id=response.chat_id,
                            content=chunk,
                        ))
            except Exception as e:
                logger.error(f"Error processing message: {e}")
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=f"Sorry, I encountered an error: {str(e)}"
                ))
    
    def stop(self) -> None:
        """Stop the agent loop and clean up scratch directory."""
        self._running = False
        shutil.rmtree(self._scratch_dir, ignore_errors=True)
        logger.info("Agent loop stopping")
    
    async def _process_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        Process a single inbound message.
        
        Args:
            msg: The inbound message to process.
        
        Returns:
            The response message, or None if no response needed.
        """
        # Handle system messages (subagent announces)
        # The chat_id contains the original "channel:chat_id" to route back to
        if msg.channel == "system":
            return await self._process_system_message(msg)
        
        logger.info(f"Processing message from {msg.channel}:{msg.sender_id}")
        
        # Get or create session
        session = await self.sessions.get_or_create(msg.session_key)

        # Request-scoped context: isolated per message, no shared mutable state
        request_ctx = {"channel": msg.channel, "chat_id": msg.chat_id}

        # Resolve customer context if supabase tool is active
        customer_context = ""
        if self._supabase_tool and self.tools.get("consulta"):
            try:
                customer_context = await self._supabase_tool.build_customer_context(
                    msg.chat_id
                )
            except Exception as e:
                logger.warning(f"Customer lookup failed: {e}")

        # Legacy: also set_context for backwards compat (CLI, single-user)
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(msg.channel, msg.chat_id)

        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(msg.channel, msg.chat_id)

        cron_tool = self.tools.get("cron")
        if isinstance(cron_tool, CronTool):
            cron_tool.set_context(msg.channel, msg.chat_id)

        handoff_tool = self.tools.get("handoff")
        if isinstance(handoff_tool, HandoffTool):
            handoff_tool.set_context(msg.channel, msg.chat_id)

        # Build initial messages (use get_history for LLM-formatted messages)
        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
            customer_context=customer_context,
        )
        
        # Agent loop
        iteration = 0
        final_content = None
        
        while iteration < self.max_iterations:
            iteration += 1
            
            # Call LLM
            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                thinking=self.thinking,
            )
            
            # Handle tool calls
            if response.has_tool_calls:
                # Add assistant message with tool calls
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)  # Must be JSON string
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts
                )
                
                # Execute tools (pass request_ctx for session-aware tools)
                for tool_call in response.tool_calls:
                    args_str = json.dumps(tool_call.arguments)
                    logger.debug(f"Executing tool: {tool_call.name} with arguments: {args_str}")
                    result = await self.tools.execute(
                        tool_call.name, tool_call.arguments, ctx=request_ctx
                    )
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
            else:
                # No tool calls, we're done
                final_content = response.content
                break

        if final_content is None:
            final_content = "I've completed processing but have no response to give."
        
        # Save to session
        session.add_message("user", msg.content)
        session.add_message("assistant", final_content)
        await self.sessions.save(session)
        
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content
        )
    
    async def _process_system_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        Process a system message (e.g., subagent announce).
        
        The chat_id field contains "original_channel:original_chat_id" to route
        the response back to the correct destination.
        """
        logger.info(f"Processing system message from {msg.sender_id}")
        
        # Parse origin from chat_id (format: "channel:chat_id")
        if ":" in msg.chat_id:
            parts = msg.chat_id.split(":", 1)
            origin_channel = parts[0]
            origin_chat_id = parts[1]
        else:
            # Fallback
            origin_channel = "cli"
            origin_chat_id = msg.chat_id
        
        # Use the origin session for context
        session_key = f"{origin_channel}:{origin_chat_id}"
        session = await self.sessions.get_or_create(session_key)

        # Request-scoped context for this system message
        request_ctx = {"channel": origin_channel, "chat_id": origin_chat_id}

        # Legacy: also set_context for backwards compat
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(origin_channel, origin_chat_id)

        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(origin_channel, origin_chat_id)

        cron_tool = self.tools.get("cron")
        if isinstance(cron_tool, CronTool):
            cron_tool.set_context(origin_channel, origin_chat_id)
        
        # Build messages with the announce content
        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=msg.content,
            channel=origin_channel,
            chat_id=origin_chat_id,
        )
        
        # Agent loop (limited for announce handling)
        iteration = 0
        final_content = None
        
        while iteration < self.max_iterations:
            iteration += 1
            
            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                thinking=self.thinking,
            )
            
            if response.has_tool_calls:
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts
                )
                
                for tool_call in response.tool_calls:
                    args_str = json.dumps(tool_call.arguments)
                    logger.debug(f"Executing tool: {tool_call.name} with arguments: {args_str}")
                    result = await self.tools.execute(
                        tool_call.name, tool_call.arguments, ctx=request_ctx
                    )
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
            else:
                final_content = response.content
                break

        if final_content is None:
            final_content = "Background task completed."
        
        # Save to session (mark as system message in history)
        session.add_message("user", f"[System: {msg.sender_id}] {msg.content}")
        session.add_message("assistant", final_content)
        await self.sessions.save(session)
        
        return OutboundMessage(
            channel=origin_channel,
            chat_id=origin_chat_id,
            content=final_content
        )
    
    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        media: list[str] | None = None,
    ) -> str:
        """
        Process a message directly (for CLI or cron usage).

        Returns:
            The agent's response (raw, may contain ||| delimiters).
        """
        msg = InboundMessage(
            channel=channel,
            sender_id="user",
            chat_id=chat_id,
            content=content,
            media=media or [],
        )

        response = await self._process_message(msg)
        return response.content if response else ""


def _split_chunks(text: str) -> list[str]:
    """Split response by ||| delimiter, return non-empty stripped chunks."""
    if "|||" not in text:
        return [text]
    return [c.strip() for c in text.split("|||") if c.strip()]
