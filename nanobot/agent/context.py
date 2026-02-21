"""Context builder for assembling agent prompts."""

import base64
import mimetypes
import platform
from pathlib import Path
from typing import Any

from nanobot.agent.memory import MemoryStore
from nanobot.agent.skills import SkillsLoader


class ContextBuilder:
    """
    Builds the context (system prompt + messages) for the agent.
    
    Assembles bootstrap files, memory, skills, and conversation history
    into a coherent prompt for the LLM.
    """
    
    BOOTSTRAP_FILES = ["IDENTITY.md", "SOUL.md", "AGENTS.md", "USER.md", "TOOLS.md"]
    
    def __init__(self, workspace: Path, entity: str | None = None,
                 allowed_skills: list[str] | None = None):
        self.workspace = workspace
        self.entity = entity or "general"
        self.entity_dir = workspace / "agents" / self.entity
        self.customer_context: str = ""
        self._entity_prompt_cache: str | None = None
        self.memory = MemoryStore(self.entity_dir)
        self.skills = SkillsLoader(
            workspace, agent_skills_dir=self.entity_dir / "skills",
            allowed_skills=allowed_skills,
        )
    
    def build_system_prompt(
        self, skill_names: list[str] | None = None, customer_context: str | None = None
    ) -> str:
        """
        Build the system prompt from agent directory files, memory, and skills.

        All agents (general and specialized) use the same flow:
        1. Load identity files from workspace/agents/{entity}/
        2. Load memory from workspace/agents/{entity}/memory/
        3. Load skills (agent-specific first, then shared)
        """
        parts = []

        # Identity (from agents/{entity}/ — IDENTITY.md, SOUL.md, + bootstrap files)
        parts.append(self._build_identity_prompt(customer_context=customer_context))

        # Memory context
        memory = self.memory.get_memory_context()
        if memory:
            parts.append(f"# Memory\n\n{memory}")

        # Skills - progressive loading
        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")

        skills_summary = self.skills.build_skills_summary()
        if skills_summary:
            parts.append(f"""# Skills

The following skills extend your capabilities. To use a skill, read its SKILL.md file using the read_file tool.
Skills with available="false" need dependencies installed first - you can try installing them with apt/brew.

{skills_summary}""")

        return "\n\n---\n\n".join(parts)
    
    def _build_identity_prompt(self, customer_context: str | None = None) -> str:
        """Build prompt from agent directory files.

        Loads all .md files from workspace/agents/{entity}/ (IDENTITY.md, SOUL.md,
        AGENTS.md, USER.md, TOOLS.md, etc.) and injects runtime context.
        """
        from datetime import datetime
        import time as _time

        if self._entity_prompt_cache is None:
            parts = []
            for filename in self.BOOTSTRAP_FILES:
                fp = self.entity_dir / filename
                if fp.exists():
                    parts.append(fp.read_text(encoding="utf-8"))
            self._entity_prompt_cache = "\n\n---\n\n".join(parts)

        # Inject runtime variables
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        tz = _time.strftime("%Z") or "UTC"
        agent_dir = str(self.entity_dir.expanduser().resolve())
        base_prompt = self._entity_prompt_cache.replace("{now}", now)
        base_prompt = base_prompt.replace("{tz}", tz)
        base_prompt = base_prompt.replace("{agent_dir}", agent_dir)

        # Request-scoped customer_context; fallback to instance attr for compat
        ctx = customer_context if customer_context is not None else self.customer_context
        if ctx:
            return "\n\n---\n\n".join([base_prompt, ctx]) if base_prompt else ctx
        return base_prompt
    
    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        customer_context: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Build the complete message list for an LLM call.

        Args:
            history: Previous conversation messages.
            current_message: The new user message.
            skill_names: Optional skills to include.
            media: Optional list of local file paths for images/media.
            channel: Current channel (telegram, feishu, etc.).
            chat_id: Current chat/user ID.
            customer_context: Optional customer context (request-scoped).
                Falls back to self.customer_context for backwards compat.

        Returns:
            List of messages including system prompt.
        """
        messages = []

        # System prompt (use request-scoped customer_context if provided)
        effective_customer = customer_context if customer_context is not None else self.customer_context
        system_prompt = self.build_system_prompt(skill_names, customer_context=effective_customer)
        if channel and chat_id:
            system_prompt += f"\n\n## Current Session\nChannel: {channel}\nChat ID: {chat_id}"
        messages.append({"role": "system", "content": system_prompt})

        # History
        messages.extend(history)

        # Current message (with optional image attachments)
        user_content = self._build_user_content(current_message, media)
        messages.append({"role": "user", "content": user_content})

        return messages

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """Build user message content with optional base64-encoded images."""
        if not media:
            return text
        
        images = []
        for path in media:
            p = Path(path)
            mime, _ = mimetypes.guess_type(path)
            if not p.is_file() or not mime or not mime.startswith("image/"):
                continue
            b64 = base64.b64encode(p.read_bytes()).decode()
            images.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        
        if not images:
            return text
        return images + [{"type": "text", "text": text}]
    
    def add_tool_result(
        self,
        messages: list[dict[str, Any]],
        tool_call_id: str,
        tool_name: str,
        result: str
    ) -> list[dict[str, Any]]:
        """
        Add a tool result to the message list.
        
        Args:
            messages: Current message list.
            tool_call_id: ID of the tool call.
            tool_name: Name of the tool.
            result: Tool execution result.
        
        Returns:
            Updated message list.
        """
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": result
        })
        return messages
    
    def add_assistant_message(
        self,
        messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Add an assistant message to the message list.
        
        Args:
            messages: Current message list.
            content: Message content.
            tool_calls: Optional tool calls.
            reasoning_content: Thinking output (Kimi, DeepSeek-R1, etc.).
        
        Returns:
            Updated message list.
        """
        msg: dict[str, Any] = {"role": "assistant", "content": content or ""}
        
        if tool_calls:
            msg["tool_calls"] = tool_calls
        
        # Thinking models reject history without this
        if reasoning_content:
            msg["reasoning_content"] = reasoning_content
        
        messages.append(msg)
        return messages
