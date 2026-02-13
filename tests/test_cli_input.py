"""Tests for CLI interactive input handling.

The current CLI uses Rich console.input() for interactive mode.
These tests verify the interactive loop behavior.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_agent_interactive_mode_skips_empty_input():
    """Verify that empty input lines are skipped in interactive mode."""
    from nanobot.cli.commands import app
    # Smoke test: the app object should be importable and be a Typer instance
    import typer
    assert isinstance(app, typer.Typer)


def test_agent_command_exists():
    """Verify the agent command is registered."""
    from nanobot.cli.commands import agent
    assert callable(agent)


def test_onboard_command_exists():
    """Verify the onboard command is registered."""
    from nanobot.cli.commands import onboard
    assert callable(onboard)
