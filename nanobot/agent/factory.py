"""Factory for creating AgentLoop instances from profiles."""

from pathlib import Path

import yaml
from loguru import logger

from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider
from nanobot.config.schema import AgentProfile, Config


def discover_agents(workspace: Path) -> list[AgentProfile]:
    """Discover agent profiles from workspace/agents/*/agent.yaml."""
    agents_dir = workspace / "agents"
    if not agents_dir.exists():
        return []
    profiles = []
    for agent_dir in sorted(agents_dir.iterdir()):
        yaml_file = agent_dir / "agent.yaml"
        if not yaml_file.exists():
            continue
        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
        name = agent_dir.name
        profiles.append(AgentProfile(
            name=name,
            entity=name,
            tools=data.get("tools", []),
            skills=data.get("skills", []),
            channels=data.get("channels", []),
            session_backend=data.get("session_backend", "file"),
        ))
        logger.info(f"Discovered agent: {name}")
    return profiles


def create_agent_from_profile(
    profile: AgentProfile,
    bus: MessageBus,
    provider: LLMProvider,
    config: Config,
    cron_service=None,
):
    """Create an AgentLoop configured according to an AgentProfile.

    This is the recommended way to instantiate agents when using profiles.
    Without profiles, the existing AgentLoop constructor works as before.
    """
    from nanobot.agent.loop import AgentLoop

    defaults = config.agents.defaults
    workspace = (
        Path(profile.workspace).expanduser()
        if profile.workspace
        else config.workspace_path
    )

    return AgentLoop(
        bus=bus,
        provider=provider,
        workspace=workspace,
        model=profile.model or defaults.model,
        max_iterations=defaults.max_tool_iterations,
        brave_api_key=config.tools.web.search.api_key or None,
        exec_config=config.tools.exec,
        cron_service=cron_service,
        entity=profile.entity,
        temperature=defaults.temperature,
        max_tokens=defaults.max_tokens,
        thinking=defaults.thinking,
        session_backend=profile.session_backend,
        channels=profile.channels or None,
        allowed_tools=profile.tools or None,
        allowed_skills=profile.skills or None,
    )
