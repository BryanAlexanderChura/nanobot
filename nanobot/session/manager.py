"""Session management for conversation history."""

import json
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from cachetools import TTLCache
from loguru import logger

from nanobot.utils.helpers import ensure_dir, safe_filename


@dataclass
class Session:
    """
    A conversation session.

    Stores messages in JSONL format for easy reading and persistence.
    """

    key: str  # channel:chat_id
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a message to the session."""
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **kwargs
        }
        self.messages.append(msg)
        self.updated_at = datetime.now()

    def get_history(self, max_messages: int = 50) -> list[dict[str, Any]]:
        """
        Get message history for LLM context.

        Args:
            max_messages: Maximum messages to return.

        Returns:
            List of messages in LLM format.
        """
        # Get recent messages
        recent = self.messages[-max_messages:] if len(self.messages) > max_messages else self.messages

        # Convert to LLM format (just role and content)
        return [{"role": m["role"], "content": m["content"]} for m in recent]

    def clear(self) -> None:
        """Clear all messages in the session."""
        self.messages = []
        self.updated_at = datetime.now()


class SessionManager:
    """
    Manages conversation sessions.

    Supports two backends:
    - "file" (default): JSONL files in ~/.nanobot/sessions/
    - "supabase": sesiones_chat table in Supabase
    """

    def __init__(self, workspace: Path, backend: str = "file"):
        self.workspace = workspace
        self.backend = backend
        self.sessions_dir = ensure_dir(Path.home() / ".nanobot" / "sessions")
        self._cache: TTLCache = TTLCache(maxsize=100, ttl=7200)

    def _get_session_path(self, key: str) -> Path:
        """Get the file path for a session."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.sessions_dir / f"{safe_key}.jsonl"

    async def get_or_create(self, key: str) -> Session:
        """
        Get an existing session or create a new one.

        Args:
            key: Session key (usually channel:chat_id).

        Returns:
            The session.
        """
        # Check cache first (both backends use it)
        if key in self._cache:
            return self._cache[key]

        # Load from backend
        if self.backend == "supabase":
            session = await self._load_supabase(key)
        else:
            session = self._load_file(key)

        if session is None:
            session = Session(key=key)

        self._cache[key] = session
        return session

    async def save(self, session: Session) -> None:
        """Save a session to the configured backend."""
        if self.backend == "supabase":
            await self._save_supabase(session)
        else:
            self._save_file(session)
        self._cache[session.key] = session

    def delete(self, key: str) -> bool:
        """
        Delete a session.

        Args:
            key: Session key.

        Returns:
            True if deleted, False if not found.
        """
        # Remove from cache
        self._cache.pop(key, None)

        # Remove file (file backend only; supabase deletion not implemented yet)
        path = self._get_session_path(key)
        if path.exists():
            path.unlink()
            return True
        return False

    def list_sessions(self) -> list[dict[str, Any]]:
        """
        List all sessions.

        Returns:
            List of session info dicts.
        """
        sessions = []

        for path in self.sessions_dir.glob("*.jsonl"):
            try:
                # Read just the metadata line
                with open(path) as f:
                    first_line = f.readline().strip()
                    if first_line:
                        data = json.loads(first_line)
                        if data.get("_type") == "metadata":
                            sessions.append({
                                "key": path.stem.replace("_", ":"),
                                "created_at": data.get("created_at"),
                                "updated_at": data.get("updated_at"),
                                "path": str(path)
                            })
            except Exception:
                continue

        return sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)

    # ------------------------------------------------------------------
    # File backend
    # ------------------------------------------------------------------

    def _load_file(self, key: str) -> Session | None:
        """Load a session from JSONL file."""
        path = self._get_session_path(key)

        if not path.exists():
            return None

        try:
            messages = []
            metadata = {}
            created_at = None

            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    data = json.loads(line)

                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        created_at = (
                            datetime.fromisoformat(data["created_at"])
                            if data.get("created_at") else None
                        )
                    else:
                        messages.append(data)

            return Session(
                key=key,
                messages=messages,
                created_at=created_at or datetime.now(),
                metadata=metadata
            )
        except Exception as e:
            logger.warning(f"Failed to load session {key}: {e}")
            return None

    def _save_file(self, session: Session) -> None:
        """Save a session to JSONL file (atomic write via tmp + replace)."""
        path = self._get_session_path(session.key)
        tmp = path.with_suffix(".tmp")

        with open(tmp, "w") as f:
            metadata_line = {
                "_type": "metadata",
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
                "metadata": session.metadata
            }
            f.write(json.dumps(metadata_line) + "\n")

            for msg in session.messages:
                f.write(json.dumps(msg) + "\n")

        tmp.replace(path)

    # ------------------------------------------------------------------
    # Supabase backend
    # ------------------------------------------------------------------

    async def _get_supabase(self):
        """Get cached Supabase client (reuses the one from supabase tool)."""
        import os
        from supabase import acreate_client

        # Reuse global cache if available
        from nanobot.agent.tools.supabase import _client_cache, _get_client
        return await _get_client()

    async def _load_supabase(self, key: str) -> Session | None:
        """Load a session from Supabase."""
        try:
            db = await self._get_supabase()
            res = await (
                db.table("sesiones_chat")
                .select("messages, metadata, created_at, updated_at")
                .eq("key", key)
                .limit(1)
                .execute()
            )
            if not res.data:
                return None

            row = res.data[0]
            return Session(
                key=key,
                messages=row.get("messages") or [],
                created_at=datetime.fromisoformat(row["created_at"]) if row.get("created_at") else datetime.now(),
                updated_at=datetime.fromisoformat(row["updated_at"]) if row.get("updated_at") else datetime.now(),
                metadata=row.get("metadata") or {},
            )
        except Exception as e:
            logger.warning(f"Supabase session load failed for {key}: {e}")
            return None

    async def _save_supabase(self, session: Session) -> None:
        """Save a session to Supabase (upsert)."""
        try:
            db = await self._get_supabase()
            await (
                db.table("sesiones_chat")
                .upsert({
                    "key": session.key,
                    "messages": session.messages,
                    "metadata": session.metadata,
                    "created_at": session.created_at.isoformat(),
                    "updated_at": session.updated_at.isoformat(),
                })
                .execute()
            )
        except Exception as e:
            logger.error(f"Supabase session save failed for {session.key}: {e}")
            # Fallback: also save to file so data isn't lost
            self._save_file(session)
