"""Persistent settings, session, and diagnostics storage."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .util import generate_id, summarize_text, utc_now_iso

DOMAIN = "ha_ai_studio"
SETTINGS_STORE_KEY = f"{DOMAIN}.settings"
SESSIONS_STORE_KEY = f"{DOMAIN}.sessions"
SNAPSHOTS_STORE_KEY = f"{DOMAIN}.diagnostics_snapshots"
STORE_VERSION = 1

DEFAULT_SETTINGS: dict[str, Any] = {
    "aiType": "cloud",
    "cloudProvider": "openai",
    "aiModel": "",
    "openaiApiKey": "",
    "openaiBaseUrl": "",
    "localAiProvider": "ollama",
    "ollamaUrl": "http://localhost:11434",
    "ollamaModel": "",
    "lmStudioUrl": "http://localhost:1234",
    "lmStudioModel": "",
    "customAiUrl": "",
    "customAiModel": "",
    "customAiApiKey": "",
    "uiLanguage": "auto",
}


class SettingsManager:
    """Persist AI settings separately from sessions."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store = Store(hass, STORE_VERSION, SETTINGS_STORE_KEY)
        self._data: dict[str, Any] = {"settings": deepcopy(DEFAULT_SETTINGS)}
        self._loaded = False
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        """Load settings from storage once."""
        if self._loaded:
            return
        async with self._lock:
            if self._loaded:
                return
            loaded = await self.store.async_load() or {}
            settings = deepcopy(DEFAULT_SETTINGS)
            settings.update(loaded.get("settings") or {})
            self._data = {"settings": settings}
            self._loaded = True

    async def async_get_settings(self) -> dict[str, Any]:
        """Return persisted settings merged with defaults."""
        await self.async_load()
        return deepcopy(self._data["settings"])

    async def async_save_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        """Replace persisted settings."""
        await self.async_load()
        async with self._lock:
            merged = deepcopy(DEFAULT_SETTINGS)
            merged.update({key: value for key, value in settings.items() if value is not None})
            self._data = {"settings": merged}
            await self.store.async_save(self._data)
            return deepcopy(merged)


class SessionManager:
    """Manage chat sessions and diagnostics snapshots."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.sessions_store = Store(hass, STORE_VERSION, SESSIONS_STORE_KEY)
        self.snapshots_store = Store(hass, STORE_VERSION, SNAPSHOTS_STORE_KEY)
        self._sessions_data: dict[str, Any] = {"sessions": {}}
        self._snapshots_data: dict[str, Any] = {"snapshots": {}}
        self._loaded = False
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        """Load sessions and snapshots once."""
        if self._loaded:
            return
        async with self._lock:
            if self._loaded:
                return
            self._sessions_data = await self.sessions_store.async_load() or {"sessions": {}}
            self._snapshots_data = await self.snapshots_store.async_load() or {"snapshots": {}}
            self._sessions_data.setdefault("sessions", {})
            self._snapshots_data.setdefault("snapshots", {})
            self._loaded = True

    async def async_list_sessions(self) -> list[dict[str, Any]]:
        """Return session metadata sorted by updated_at descending."""
        await self.async_load()
        sessions = []
        for session in self._sessions_data["sessions"].values():
            sessions.append(
                {
                    "id": session["id"],
                    "title": session["title"],
                    "created_at": session["created_at"],
                    "updated_at": session["updated_at"],
                    "last_summary": session.get("last_summary", ""),
                    "diagnostics_snapshot_id": session.get("diagnostics_snapshot_id"),
                    "message_count": len(session.get("messages", [])),
                }
            )
        sessions.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        return sessions

    async def async_create_session(self, title: str | None = None) -> dict[str, Any]:
        """Create and persist a new session."""
        await self.async_load()
        async with self._lock:
            session_id = generate_id("session")
            now = utc_now_iso()
            session = {
                "id": session_id,
                "title": (title or "New chat").strip() or "New chat",
                "created_at": now,
                "updated_at": now,
                "last_summary": "",
                "diagnostics_snapshot_id": None,
                "messages": [],
            }
            self._sessions_data["sessions"][session_id] = session
            await self.sessions_store.async_save(self._sessions_data)
            return deepcopy(session)

    async def async_get_session(self, session_id: str) -> dict[str, Any] | None:
        """Return a full session payload."""
        await self.async_load()
        session = self._sessions_data["sessions"].get(session_id)
        return deepcopy(session) if session else None

    async def async_delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        await self.async_load()
        async with self._lock:
            if session_id not in self._sessions_data["sessions"]:
                return False
            self._sessions_data["sessions"].pop(session_id, None)
            await self.sessions_store.async_save(self._sessions_data)
            return True

    async def async_update_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        last_summary: str | None = None,
        diagnostics_snapshot_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Update session metadata."""
        await self.async_load()
        async with self._lock:
            session = self._sessions_data["sessions"].get(session_id)
            if not session:
                return None

            if title is not None:
                session["title"] = title.strip() or session["title"]
            if last_summary is not None:
                session["last_summary"] = summarize_text(last_summary, 220)
            if diagnostics_snapshot_id is not None:
                session["diagnostics_snapshot_id"] = diagnostics_snapshot_id
            session["updated_at"] = utc_now_iso()
            await self.sessions_store.async_save(self._sessions_data)
            return deepcopy(session)

    async def async_append_message(
        self,
        session_id: str,
        *,
        role: str,
        content: str,
        citations: list[dict[str, Any]] | None = None,
        repair_draft: str | None = None,
        suggested_checks: list[str] | None = None,
        diagnostics_snapshot_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Append a message to a session."""
        await self.async_load()
        async with self._lock:
            session = self._sessions_data["sessions"].get(session_id)
            if not session:
                return None

            message = {
                "id": generate_id("message"),
                "role": role,
                "content": content,
                "created_at": utc_now_iso(),
                "citations": citations or [],
                "repair_draft": repair_draft or "",
                "suggested_checks": suggested_checks or [],
                "diagnostics_snapshot_id": diagnostics_snapshot_id,
            }
            session.setdefault("messages", []).append(message)
            session["updated_at"] = message["created_at"]
            if role == "user" and not session.get("last_summary"):
                session["last_summary"] = summarize_text(content, 220)
            if diagnostics_snapshot_id is not None:
                session["diagnostics_snapshot_id"] = diagnostics_snapshot_id
            await self.sessions_store.async_save(self._sessions_data)
            return deepcopy(message)

    async def async_save_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Persist a diagnostics snapshot."""
        await self.async_load()
        async with self._lock:
            snapshot_id = snapshot.get("id") or generate_id("diag")
            normalized = deepcopy(snapshot)
            normalized["id"] = snapshot_id
            self._snapshots_data["snapshots"][snapshot_id] = normalized
            await self.snapshots_store.async_save(self._snapshots_data)
            return deepcopy(normalized)

    async def async_get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        """Return a persisted diagnostics snapshot."""
        await self.async_load()
        snapshot = self._snapshots_data["snapshots"].get(snapshot_id)
        return deepcopy(snapshot) if snapshot else None
