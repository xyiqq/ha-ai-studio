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
    ".conf",
    ".cfg",
    ".ini",
    ".json",
    ".xml",
    ".toml",
    ".env",
    ".properties",
}
AUTOMATION_RELOAD_EXACT_PATHS = {
    "automations.yaml",
}
AUTOMATION_RELOAD_PREFIXES = (
    "automations/",
    "blueprints/automation/",
)
SCENE_RELOAD_EXACT_PATHS = {
    "scenes.yaml",
}
BLOCKED_PATH_PARTS = {
    ".storage",
    "__pycache__",
    ".git",
    ".github",
    ".venv",
    "deps",
    "custom_components",
    "node_modules",
    "pyscript",
    "python_scripts",
    "www",
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
        normalized_edits: list[dict[str, str]] = []
        seen_paths: set[str] = set()
        for edit in proposed_edits:
            normalized = self._normalize_proposed_edit(edit)
            dedupe_key = normalized["path"].casefold()
            if dedupe_key in seen_paths:
                raise ValueError(f"Duplicate edit target is not allowed: {normalized['path']}")
            seen_paths.add(dedupe_key)
            normalized_edits.append(normalized)

        results: list[dict[str, Any]] = []
        try:
            for normalized in normalized_edits:
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
        except Exception:
            await self.async_rollback_applied_edits(results)
            raise
        return results

    async def async_rollback_applied_edits(
        self,
        applied_edits: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Restore a batch of applied edits in reverse order."""
        rollback_results: list[dict[str, Any]] = []
        for applied_edit in reversed(applied_edits):
            backup_id = str(applied_edit.get("backup_id") or "").strip()
            if not backup_id:
                continue
            restored = await self.async_restore_backup(backup_id)
            rollback_results.append(restored["result"])
        return rollback_results

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

    async def async_reload_after_edits(self, applied_edits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Reload runtime YAML integrations when edited paths support live reload."""
        edited_paths = [
            str(item.get("path") or "").strip()
            for item in applied_edits
            if str(item.get("path") or "").strip()
        ]
        return await self.async_reload_paths(edited_paths)

    async def async_reload_paths(self, relative_paths: list[str]) -> list[dict[str, Any]]:
        """Reload runtime YAML integrations for supported edited paths."""
        normalized_paths = sorted(
            {
                str(path or "").strip().replace("\\", "/")
                for path in relative_paths
                if str(path or "").strip()
            }
        )
        automation_paths = [path for path in normalized_paths if self._requires_automation_reload(path)]
        scene_paths = [path for path in normalized_paths if self._requires_scene_reload(path)]
        reload_results: list[dict[str, Any]] = []
        if not self.hass.services.has_service("automation", "reload"):
            if automation_paths:
                raise RuntimeError("Home Assistant 当前无法调用 automation.reload，所以自动化还不能立即生效。")
        elif automation_paths:
            await self.hass.services.async_call("automation", "reload", blocking=True)
            reload_results.append(
                {
                    "domain": "automation",
                    "service": "reload",
                    "label": "automations",
                    "paths": automation_paths,
                    "reloaded_at": utc_now_iso(),
                }
            )

        if not self.hass.services.has_service("scene", "reload"):
            if scene_paths:
                raise RuntimeError("Home Assistant 当前无法调用 scene.reload，所以场景还不能立即生效。")
        elif scene_paths:
            await self.hass.services.async_call("scene", "reload", blocking=True)
            reload_results.append(
                {
                    "domain": "scene",
                    "service": "reload",
                    "label": "scenes",
                    "paths": scene_paths,
                    "reloaded_at": utc_now_iso(),
                }
            )

        return reload_results

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

    def _requires_automation_reload(self, relative_path: str) -> bool:
        """Return whether a relative config path needs automation.reload."""
        normalized = str(relative_path or "").strip().replace("\\", "/").casefold()
        if not normalized:
            return False
        if normalized in AUTOMATION_RELOAD_EXACT_PATHS:
            return True
        return any(normalized.startswith(prefix) for prefix in AUTOMATION_RELOAD_PREFIXES)

    def _requires_scene_reload(self, relative_path: str) -> bool:
        """Return whether a relative config path needs scene.reload."""
        normalized = str(relative_path or "").strip().replace("\\", "/").casefold()
        if not normalized:
            return False
        return normalized in SCENE_RELOAD_EXACT_PATHS

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
