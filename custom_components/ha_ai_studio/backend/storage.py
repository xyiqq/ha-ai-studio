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
BACKUPS_STORE_KEY = f"{DOMAIN}.file_backups"
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
            self._sessions_data["sessions"] = {
                session_id: self._normalize_session_payload(session)
                for session_id, session in self._sessions_data["sessions"].items()
                if isinstance(session, dict)
            }
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
                    "auto_approve_edits": bool(session.get("auto_approve_edits", False)),
                }
            )
        sessions.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        return sessions

    async def async_create_session(
        self,
        title: str | None = None,
        *,
        auto_approve_edits: bool = False,
    ) -> dict[str, Any]:
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
                "auto_approve_edits": bool(auto_approve_edits),
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
        auto_approve_edits: bool | None = None,
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
            if auto_approve_edits is not None:
                session["auto_approve_edits"] = bool(auto_approve_edits)
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
        proposed_edits: list[dict[str, Any]] | None = None,
        applied_edits: list[dict[str, Any]] | None = None,
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
                "proposed_edits": deepcopy(proposed_edits or []),
                "applied_edits": deepcopy(applied_edits or []),
            }
            session.setdefault("messages", []).append(message)
            session["updated_at"] = message["created_at"]
            if role == "user" and not session.get("last_summary"):
                session["last_summary"] = summarize_text(content, 220)
            if diagnostics_snapshot_id is not None:
                session["diagnostics_snapshot_id"] = diagnostics_snapshot_id
            await self.sessions_store.async_save(self._sessions_data)
            return deepcopy(message)

    async def async_get_message(self, session_id: str, message_id: str) -> dict[str, Any] | None:
        """Return one message from a session."""
        await self.async_load()
        session = self._sessions_data["sessions"].get(session_id)
        if not session:
            return None
        for message in session.get("messages", []):
            if message.get("id") == message_id:
                return deepcopy(message)
        return None

    async def async_store_applied_edits(
        self,
        session_id: str,
        message_id: str,
        applied_edits: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Persist applied edit results on an assistant message."""
        await self.async_load()
        async with self._lock:
            session = self._sessions_data["sessions"].get(session_id)
            if not session:
                return None
            for message in session.get("messages", []):
                if message.get("id") != message_id:
                    continue
                message["applied_edits"] = deepcopy(applied_edits)
                session["updated_at"] = utc_now_iso()
                await self.sessions_store.async_save(self._sessions_data)
                return deepcopy(message)
        return None

    async def async_mark_backup_restored(
        self,
        session_id: str,
        message_id: str,
        backup_id: str,
    ) -> dict[str, Any] | None:
        """Mark one applied edit as restored on the source message."""
        await self.async_load()
        async with self._lock:
            session = self._sessions_data["sessions"].get(session_id)
            if not session:
                return None
            restored_at = utc_now_iso()
            for message in session.get("messages", []):
                if message.get("id") != message_id:
                    continue
                updated = False
                for applied_edit in message.get("applied_edits", []):
                    if applied_edit.get("backup_id") != backup_id:
                        continue
                    applied_edit["status"] = "restored"
                    applied_edit["restored"] = True
                    applied_edit["can_restore"] = False
                    applied_edit["restored_at"] = restored_at
                    updated = True
                if not updated:
                    return deepcopy(message)
                session["updated_at"] = restored_at
                await self.sessions_store.async_save(self._sessions_data)
                return deepcopy(message)
        return None

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

    def _normalize_session_payload(self, session: dict[str, Any]) -> dict[str, Any]:
        """Backfill newly added session and message fields."""
        normalized = deepcopy(session)
        normalized.setdefault("last_summary", "")
        normalized.setdefault("diagnostics_snapshot_id", None)
        normalized["auto_approve_edits"] = bool(normalized.get("auto_approve_edits", False))
        messages = []
        for message in normalized.get("messages", []):
            if not isinstance(message, dict):
                continue
            item = deepcopy(message)
            item.setdefault("citations", [])
            item.setdefault("repair_draft", "")
            item.setdefault("suggested_checks", [])
            item.setdefault("diagnostics_snapshot_id", None)
            item.setdefault("proposed_edits", [])
            item.setdefault("applied_edits", [])
            messages.append(item)
        normalized["messages"] = messages
        return normalized


class BackupManager:
    """Persist backup records separately from sessions and settings."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store = Store(hass, STORE_VERSION, BACKUPS_STORE_KEY)
        self._data: dict[str, Any] = {"backups": {}}
        self._loaded = False
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        """Load backups from storage once."""
        if self._loaded:
            return
        async with self._lock:
            if self._loaded:
                return
            loaded = await self.store.async_load() or {"backups": {}}
            loaded.setdefault("backups", {})
            self._data = {
                "backups": {
                    backup_id: self._normalize_backup_payload(backup)
                    for backup_id, backup in loaded["backups"].items()
                    if isinstance(backup, dict)
                }
            }
            self._loaded = True

    async def async_create_backup(
        self,
        *,
        path: str,
        reason: str,
        session_id: str,
        message_id: str,
        original_content: str,
        file_existed: bool,
    ) -> dict[str, Any]:
        """Persist one file backup."""
        await self.async_load()
        async with self._lock:
            backup_id = generate_id("backup")
            backup = {
                "id": backup_id,
                "created_at": utc_now_iso(),
                "restored_at": None,
                "path": path,
                "reason": reason,
                "session_id": session_id,
                "message_id": message_id,
                "file_existed": bool(file_existed),
                "original_content": original_content,
                "status": "available",
                "can_restore": True,
            }
            self._data["backups"][backup_id] = backup
            await self.store.async_save(self._data)
            return deepcopy(backup)

    async def async_get_backup(self, backup_id: str) -> dict[str, Any] | None:
        """Return one backup by id."""
        await self.async_load()
        backup = self._data["backups"].get(backup_id)
        return deepcopy(backup) if backup else None

    async def async_mark_restored(self, backup_id: str) -> dict[str, Any] | None:
        """Mark a backup as restored."""
        await self.async_load()
        async with self._lock:
            backup = self._data["backups"].get(backup_id)
            if not backup:
                return None
            backup["restored_at"] = utc_now_iso()
            backup["status"] = "restored"
            backup["can_restore"] = False
            await self.store.async_save(self._data)
            return deepcopy(backup)

    def _normalize_backup_payload(self, backup: dict[str, Any]) -> dict[str, Any]:
        """Backfill newly added backup fields."""
        normalized = deepcopy(backup)
        normalized.setdefault("restored_at", None)
        normalized.setdefault("reason", "")
        normalized.setdefault("session_id", "")
        normalized.setdefault("message_id", "")
        normalized.setdefault("file_existed", True)
        normalized.setdefault("original_content", "")
        normalized.setdefault("status", "available")
        normalized["can_restore"] = bool(normalized.get("can_restore", normalized["status"] != "restored"))
        return normalized
