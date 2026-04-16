"""Safe file edit and backup restore helpers for HA AI Studio."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from homeassistant.core import HomeAssistant

from .storage import BackupManager
from .util import clip_text, utc_now_iso

SAFE_TEXT_SUFFIXES = {
    ".yaml",
    ".yml",
    ".jinja",
    ".jinja2",
    ".j2",
    ".txt",
    ".conf",
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".csv",
    ".log",
    ".template",
    ".tmpl",
}
BLOCKED_PATH_PARTS = {
    ".storage",
    "__pycache__",
    ".git",
    ".github",
    ".venv",
    "deps",
}


class SafeConfigEditor:
    """Apply and restore text edits inside the Home Assistant config directory."""

    def __init__(self, hass: HomeAssistant, config_dir: Path, backups: BackupManager) -> None:
        self.hass = hass
        self.config_dir = Path(config_dir).resolve()
        self.backups = backups

    async def async_apply_edits(
        self,
        *,
        session_id: str,
        message_id: str,
        proposed_edits: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Apply a list of proposed edits after creating per-file backups."""
        results: list[dict[str, Any]] = []
        for edit in proposed_edits:
            normalized = self._normalize_proposed_edit(edit)
            target_path, relative_path = self._resolve_safe_path(normalized["path"])
            original_state = await self.hass.async_add_executor_job(self._read_original_state, target_path)
            backup = await self.backups.async_create_backup(
                path=relative_path,
                reason=normalized["reason"],
                session_id=session_id,
                message_id=message_id,
                original_content=original_state["content"],
                file_existed=original_state["file_existed"],
            )
            await self.hass.async_add_executor_job(
                self._write_text_file,
                target_path,
                normalized["content"],
            )
            results.append(
                {
                    "path": relative_path,
                    "reason": normalized["reason"],
                    "backup_id": backup["id"],
                    "status": "applied",
                    "can_restore": True,
                    "restored": False,
                    "applied_at": utc_now_iso(),
                    "content_preview": clip_text(normalized["content"], 240),
                }
            )
        return results

    async def async_restore_backup(self, backup_id: str) -> dict[str, Any]:
        """Restore a backup by id."""
        backup = await self.backups.async_get_backup(backup_id)
        if not backup:
            raise ValueError("Backup not found")

        target_path, relative_path = self._resolve_safe_path(str(backup.get("path") or ""))
        await self.hass.async_add_executor_job(self._restore_backup_sync, target_path, backup)
        updated = await self.backups.async_mark_restored(backup_id)
        return {
            "backup": updated or backup,
            "result": {
                "backup_id": backup_id,
                "path": relative_path,
                "status": "restored",
                "restored": True,
                "can_restore": False,
                "restored_at": (updated or backup).get("restored_at") or utc_now_iso(),
            },
        }

    def _normalize_proposed_edit(self, edit: dict[str, Any]) -> dict[str, str]:
        """Validate one proposed edit payload."""
        if not isinstance(edit, dict):
            raise ValueError("Each proposed edit must be an object")

        path = str(edit.get("path") or "").strip()
        reason = str(edit.get("reason") or "").strip()
        content = edit.get("content")

        if not path:
            raise ValueError("Each proposed edit requires a path")
        if not reason:
            raise ValueError(f"Edit {path} is missing a reason")
        if not isinstance(content, str):
            raise ValueError(f"Edit {path} content must be a string")

        return {
            "path": path,
            "reason": reason,
            "content": content,
        }

    def _resolve_safe_path(self, raw_path: str) -> tuple[Path, str]:
        """Resolve and validate a safe config-relative path."""
        normalized = str(raw_path or "").strip().replace("\\", "/")
        if not normalized:
            raise ValueError("Path is required")
        if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
            raise ValueError("Only config-relative paths are allowed")

        target_path = (self.config_dir / normalized).resolve(strict=False)
        try:
            relative = target_path.relative_to(self.config_dir)
        except ValueError as err:
            raise ValueError("Path must stay inside the Home Assistant config directory") from err

        for part in relative.parts:
            if part in BLOCKED_PATH_PARTS or part.startswith("."):
                raise ValueError(f"Path is not allowed: {relative.as_posix()}")

        suffix = target_path.suffix.lower()
        if suffix not in SAFE_TEXT_SUFFIXES:
            raise ValueError(
                f"Unsupported file type for edits: {target_path.name}. Allowed types: {', '.join(sorted(SAFE_TEXT_SUFFIXES))}"
            )

        if not target_path.exists() and not target_path.parent.exists():
            raise ValueError(f"Target parent directory does not exist: {relative.parent.as_posix()}")

        return target_path, relative.as_posix()

    def _read_original_state(self, target_path: Path) -> dict[str, Any]:
        """Read the current file state before overwriting."""
        if not target_path.exists():
            return {"file_existed": False, "content": ""}
        return {
            "file_existed": True,
            "content": target_path.read_text(encoding="utf-8"),
        }

    def _write_text_file(self, target_path: Path, content: str) -> None:
        """Write a UTF-8 text file."""
        target_path.write_text(content, encoding="utf-8")

    def _restore_backup_sync(self, target_path: Path, backup: dict[str, Any]) -> None:
        """Restore a file from persisted backup data."""
        if backup.get("file_existed"):
            target_path.write_text(str(backup.get("original_content") or ""), encoding="utf-8")
            return
        if target_path.exists():
            target_path.unlink()
