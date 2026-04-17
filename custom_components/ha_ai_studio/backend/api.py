"""HTTP API surface for HA AI Studio."""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from ..const import API_BASE_PATH, DOMAIN
from .ai_manager import HAStudioAIManager
from .diagnostics import DiagnosticsCollector
from .editor import SafeConfigEditor
from .storage import BackupManager, SessionManager, SettingsManager
from .util import json_message, json_response, summarize_text

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class BackendRuntime:
    """Shared backend services for request handlers."""

    hass: HomeAssistant
    config_dir: Path
    settings: SettingsManager
    sessions: SessionManager
    backups: BackupManager
    diagnostics: DiagnosticsCollector
    ai: HAStudioAIManager
    editor: SafeConfigEditor
    cancelled_runs: set[str]


def create_backend_runtime(hass: HomeAssistant) -> BackendRuntime:
    """Create or return a cached backend runtime."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    runtime = domain_data.get("backend_runtime")
    if runtime:
        return runtime

    config_dir = Path(hass.config.config_dir)
    settings_manager = SettingsManager(hass)
    session_manager = SessionManager(hass)
    backup_manager = BackupManager(hass)
    diagnostics = DiagnosticsCollector(hass, config_dir)
    ai_manager = HAStudioAIManager(hass, settings_manager)
    editor = SafeConfigEditor(hass, config_dir, backup_manager)
    runtime = BackendRuntime(
        hass=hass,
        config_dir=config_dir,
        settings=settings_manager,
        sessions=session_manager,
        backups=backup_manager,
        diagnostics=diagnostics,
        ai=ai_manager,
        editor=editor,
        cancelled_runs=set(),
    )
    domain_data["backend_runtime"] = runtime
    return runtime


class HAAIStudioApiView(HomeAssistantView):
    """Main API view for HA AI Studio."""

    url = API_BASE_PATH
    name = f"api:{DOMAIN}"
    requires_auth = True

    def __init__(self, runtime: BackendRuntime) -> None:
        """Initialize the API view."""
        self.runtime = runtime

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET actions."""
        user = request.get("hass_user")
        if not user:
            return web.Response(status=401, text="Unauthorized")

        action = request.query.get("action")
        if not action:
            return json_message("Missing action", status_code=400)

        try:
            if action == "get_settings":
                settings = await self.runtime.settings.async_get_settings()
                return json_response({"success": True, "settings": settings})

            if action == "chat_list_sessions":
                sessions = await self.runtime.sessions.async_list_sessions()
                return json_response({"success": True, "sessions": sessions})

            if action == "chat_get_session":
                session_id = request.query.get("session_id", "")
                return await self._handle_chat_get_session(session_id)

            if action == "diagnostics_get_snapshot":
                snapshot_id = request.query.get("snapshot_id", "")
                session_id = request.query.get("session_id", "")
                return await self._handle_get_snapshot(snapshot_id=snapshot_id, session_id=session_id)

            if action == "health":
                return json_response({"success": True, "status": "ok", "domain": DOMAIN})

            return json_message(f"Unknown action: {action}", status_code=400)
        except Exception as err:
            _LOGGER.error("GET action %s failed: %s", action, err, exc_info=True)
            return json_message(f"Action failed: {err}", status_code=500)

    async def post(self, request: web.Request) -> web.Response:
        """Handle POST actions."""
        user = request.get("hass_user")
        if not user:
            return web.Response(status=401, text="Unauthorized")

        try:
            payload = json.loads(await request.read() or b"{}")
        except Exception as err:
            return json_message(f"Invalid JSON: {err}", status_code=400)

        action = payload.get("action")
        if not action:
            return json_message("Missing action", status_code=400)

        handlers: dict[str, Callable[[dict[str, Any]], Awaitable[web.Response]]] = {
            "save_settings": self._handle_save_settings,
            "ai_get_models": self._handle_ai_get_models,
            "chat_list_sessions": self._handle_chat_list_sessions_post,
            "chat_create_session": self._handle_chat_create_session,
            "chat_update_session": self._handle_chat_update_session,
            "chat_get_session": self._handle_chat_get_session_post,
            "chat_delete_session": self._handle_chat_delete_session,
            "chat_send_message": self._handle_chat_send_message,
            "chat_cancel_run": self._handle_chat_cancel_run,
            "chat_apply_proposed_edits": self._handle_chat_apply_proposed_edits,
            "chat_restore_backup": self._handle_chat_restore_backup,
            "chat_refresh_diagnostics": self._handle_chat_refresh_diagnostics,
            "diagnostics_get_snapshot": self._handle_get_snapshot_post,
        }
        handler = handlers.get(action)
        if not handler:
            return json_message(f"Unknown action: {action}", status_code=400)

        try:
            return await handler(payload)
        except Exception as err:
            _LOGGER.error("POST action %s failed: %s", action, err, exc_info=True)
            return json_message(f"Action failed: {err}", status_code=500)

    async def _handle_save_settings(self, payload: dict[str, Any]) -> web.Response:
        settings = payload.get("settings")
        if not isinstance(settings, dict):
            return json_message("settings payload must be an object", status_code=400)
        saved = await self.runtime.settings.async_save_settings(settings)
        return json_response({"success": True, "settings": saved})

    async def _handle_ai_get_models(self, payload: dict[str, Any]) -> web.Response:
        settings_override = payload.get("settings") if isinstance(payload.get("settings"), dict) else payload
        return await self.runtime.ai.get_models(
            ai_type=payload.get("ai_type"),
            cloud_provider=payload.get("cloud_provider"),
            ai_model=payload.get("ai_model"),
            settings_override=settings_override,
        )

    async def _handle_chat_list_sessions_post(self, payload: dict[str, Any]) -> web.Response:
        del payload
        sessions = await self.runtime.sessions.async_list_sessions()
        return json_response({"success": True, "sessions": sessions})

    async def _handle_chat_create_session(self, payload: dict[str, Any]) -> web.Response:
        session = await self.runtime.sessions.async_create_session(
            payload.get("title"),
            auto_approve_edits=bool(payload.get("auto_approve_edits", False)),
        )
        sessions = await self.runtime.sessions.async_list_sessions()
        return json_response({"success": True, "session": session, "sessions": sessions})

    async def _handle_chat_update_session(self, payload: dict[str, Any]) -> web.Response:
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            return json_message("session_id is required", status_code=400)

        session = await self.runtime.sessions.async_update_session(
            session_id,
            title=payload.get("title"),
            last_summary=payload.get("last_summary"),
            diagnostics_snapshot_id=payload.get("diagnostics_snapshot_id"),
            auto_approve_edits=payload.get("auto_approve_edits")
            if isinstance(payload.get("auto_approve_edits"), bool)
            else None,
        )
        if not session:
            return json_message("Session not found", status_code=404)
        return json_response({"success": True, "session": session})

    async def _handle_chat_get_session(self, session_id: str) -> web.Response:
        session_id = str(session_id or "").strip()
        if not session_id:
            return json_message("session_id is required", status_code=400)

        session = await self.runtime.sessions.async_get_session(session_id)
        if not session:
            return json_message("Session not found", status_code=404)
        snapshot = None
        if session.get("diagnostics_snapshot_id"):
            snapshot = await self.runtime.sessions.async_get_snapshot(session["diagnostics_snapshot_id"])
        return json_response({"success": True, "session": session, "snapshot": snapshot})

    async def _handle_chat_get_session_post(self, payload: dict[str, Any]) -> web.Response:
        return await self._handle_chat_get_session(str(payload.get("session_id") or ""))

    async def _handle_chat_delete_session(self, payload: dict[str, Any]) -> web.Response:
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            return json_message("session_id is required", status_code=400)
        deleted = await self.runtime.sessions.async_delete_session(session_id)
        if not deleted:
            return json_message("Session not found", status_code=404)
        sessions = await self.runtime.sessions.async_list_sessions()
        return json_response({"success": True, "deleted": True, "sessions": sessions})

    async def _handle_chat_refresh_diagnostics(self, payload: dict[str, Any]) -> web.Response:
        query = str(payload.get("query") or "").strip()
        file_hints = payload.get("file_hints") if isinstance(payload.get("file_hints"), list) else []
        template = payload.get("template")
        snapshot = await self.runtime.diagnostics.collect_snapshot(
            query,
            template=template,
            file_hints=[str(item) for item in file_hints],
        )
        saved_snapshot = await self.runtime.sessions.async_save_snapshot(snapshot)

        session_id = str(payload.get("session_id") or "").strip()
        if session_id:
            await self.runtime.sessions.async_update_session(
                session_id,
                diagnostics_snapshot_id=saved_snapshot["id"],
                last_summary=summarize_text(query, 220),
            )

        return json_response({"success": True, "snapshot": saved_snapshot})

    async def _handle_get_snapshot(
        self,
        *,
        snapshot_id: str = "",
        session_id: str = "",
    ) -> web.Response:
        resolved_snapshot_id = str(snapshot_id or "").strip()
        if not resolved_snapshot_id and session_id:
            session = await self.runtime.sessions.async_get_session(session_id)
            if not session:
                return json_message("Session not found", status_code=404)
            resolved_snapshot_id = str(session.get("diagnostics_snapshot_id") or "").strip()

        if not resolved_snapshot_id:
            return json_message("snapshot_id or session_id is required", status_code=400)

        snapshot = await self.runtime.sessions.async_get_snapshot(resolved_snapshot_id)
        if not snapshot:
            return json_message("Diagnostics snapshot not found", status_code=404)
        return json_response({"success": True, "snapshot": snapshot})

    async def _handle_get_snapshot_post(self, payload: dict[str, Any]) -> web.Response:
        return await self._handle_get_snapshot(
            snapshot_id=str(payload.get("snapshot_id") or ""),
            session_id=str(payload.get("session_id") or ""),
        )

    async def _handle_chat_send_message(self, payload: dict[str, Any]) -> web.Response:
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            return json_message("session_id is required", status_code=400)

        session = await self.runtime.sessions.async_get_session(session_id)
        if not session:
            return json_message("Session not found", status_code=404)

        message_text = str(payload.get("message") or payload.get("content") or "").strip()
        if not message_text:
            return json_message("message is required", status_code=400)

        file_hints = payload.get("file_hints") if isinstance(payload.get("file_hints"), list) else []
        template = payload.get("template")
        settings_override = payload.get("settings") if isinstance(payload.get("settings"), dict) else payload
        run_id = str(payload.get("run_id") or "").strip()

        snapshot = await self.runtime.diagnostics.collect_snapshot(
            message_text,
            template=template,
            file_hints=[str(item) for item in file_hints],
        )
        saved_snapshot = await self.runtime.sessions.async_save_snapshot(snapshot)

        title = session.get("title") or "New chat"
        if title == "New chat" and not session.get("messages"):
            await self.runtime.sessions.async_update_session(
                session_id,
                title=summarize_text(message_text, 60) or "New chat",
                diagnostics_snapshot_id=saved_snapshot["id"],
                last_summary=message_text,
            )
        else:
            await self.runtime.sessions.async_update_session(
                session_id,
                diagnostics_snapshot_id=saved_snapshot["id"],
                last_summary=message_text,
            )

        ai_result = await self.runtime.ai.generate_reply(
            user_message=message_text,
            session=session,
            snapshot=saved_snapshot,
            settings_override=settings_override,
        )

        user_message = await self.runtime.sessions.async_append_message(
            session_id,
            role="user",
            content=message_text,
            diagnostics_snapshot_id=saved_snapshot["id"],
        )
        if run_id and run_id in self.runtime.cancelled_runs:
            self.runtime.cancelled_runs.discard(run_id)
            updated_session = await self.runtime.sessions.async_get_session(session_id)
            return json_response(
                {
                    "success": False,
                    "cancelled": True,
                    "run_id": run_id,
                    "session": updated_session,
                    "user_message": user_message,
                    "diagnostics_snapshot": saved_snapshot,
                }
            )
        assistant_message = await self.runtime.sessions.async_append_message(
            session_id,
            role="assistant",
            content=ai_result["answer"],
            citations=ai_result.get("citations") or [],
            repair_draft=ai_result.get("repair_draft") or "",
            suggested_checks=ai_result.get("suggested_checks") or [],
            diagnostics_snapshot_id=saved_snapshot["id"],
            proposed_edits=ai_result.get("proposed_edits") or [],
        )

        updated_session = await self.runtime.sessions.async_get_session(session_id)
        return json_response(
            {
                "success": True,
                "session": updated_session,
                "user_message": user_message,
                "assistant_message": assistant_message,
                "diagnostics_snapshot": saved_snapshot,
                "run_id": run_id,
            }
        )

    async def _handle_chat_cancel_run(self, payload: dict[str, Any]) -> web.Response:
        run_id = str(payload.get("run_id") or "").strip()
        if not run_id:
            return json_message("run_id is required", status_code=400)
        self.runtime.cancelled_runs.add(run_id)
        return json_response({"success": True, "cancelled": True, "run_id": run_id})

    async def _handle_chat_apply_proposed_edits(self, payload: dict[str, Any]) -> web.Response:
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            return json_message("session_id is required", status_code=400)

        session = await self.runtime.sessions.async_get_session(session_id)
        if not session:
            return json_message("Session not found", status_code=404)

        confirmed = bool(payload.get("confirmed", False))
        if not session.get("auto_approve_edits") and not confirmed:
            return json_message(
                "Applying edits requires confirmation for this chat session",
                status_code=409,
                requires_confirmation=True,
            )

        message_id = str(payload.get("message_id") or "").strip()
        proposed_edits = payload.get("proposed_edits") if isinstance(payload.get("proposed_edits"), list) else None

        source_message = None
        if proposed_edits is None:
            if not message_id:
                return json_message("message_id or proposed_edits is required", status_code=400)
            source_message = await self.runtime.sessions.async_get_message(session_id, message_id)
            if not source_message:
                return json_message("Message not found", status_code=404)
            proposed_edits = source_message.get("proposed_edits") or []

        if not proposed_edits:
            return json_message("No proposed edits available to apply", status_code=400)

        if not message_id:
            if not source_message:
                return json_message("message_id is required when applying ad-hoc proposed edits", status_code=400)
            message_id = str(source_message.get("id") or "").strip()

        baseline_check = await self.runtime.diagnostics.async_run_config_check(force_refresh=True)
        try:
            applied_edits = await self.runtime.editor.async_apply_edits(
                session_id=session_id,
                message_id=message_id,
                proposed_edits=proposed_edits,
            )
        except ValueError as err:
            return json_message(str(err), status_code=400)

        config_check = await self.runtime.diagnostics.async_run_config_check(force_refresh=True)
        if self._config_check_regressed(baseline_check, config_check):
            rollback_results = await self.runtime.editor.async_rollback_applied_edits(applied_edits)
            post_rollback_check = await self.runtime.diagnostics.async_run_config_check(force_refresh=True)
            return json_message(
                self._build_failed_config_apply_message(config_check),
                status_code=409,
                rolled_back=True,
                config_check=config_check,
                baseline_config_check=baseline_check,
                rollback_results=rollback_results,
                post_rollback_config_check=post_rollback_check,
            )

        try:
            reload_results = await self.runtime.editor.async_reload_after_edits(applied_edits)
        except RuntimeError as err:
            rollback_results = await self.runtime.editor.async_rollback_applied_edits(applied_edits)
            post_rollback_check = await self.runtime.diagnostics.async_run_config_check(force_refresh=True)
            return json_message(
                self._build_failed_reload_message(err),
                status_code=409,
                rolled_back=True,
                reload_failed=True,
                config_check=config_check,
                baseline_config_check=baseline_check,
                rollback_results=rollback_results,
                post_rollback_config_check=post_rollback_check,
            )

        updated_message = await self.runtime.sessions.async_store_applied_edits(
            session_id,
            message_id,
            applied_edits,
        )
        updated_session = await self.runtime.sessions.async_get_session(session_id)
        return json_response(
            {
                "success": True,
                "session": updated_session,
                "message": updated_message,
                "applied_edits": applied_edits,
                "config_check": config_check,
                "reload_results": reload_results,
            }
        )

    async def _handle_chat_restore_backup(self, payload: dict[str, Any]) -> web.Response:
        backup_id = str(payload.get("backup_id") or "").strip()
        if not backup_id:
            return json_message("backup_id is required", status_code=400)

        try:
            restored = await self.runtime.editor.async_restore_backup(backup_id)
        except ValueError as err:
            return json_message(str(err), status_code=404 if "not found" in str(err).lower() else 400)
        backup = restored["backup"]
        session_id = str(payload.get("session_id") or backup.get("session_id") or "").strip()
        message_id = str(payload.get("message_id") or backup.get("message_id") or "").strip()

        updated_message = None
        updated_session = None
        if session_id and message_id:
            updated_message = await self.runtime.sessions.async_mark_backup_restored(session_id, message_id, backup_id)
            updated_session = await self.runtime.sessions.async_get_session(session_id)

        try:
            reload_results = await self.runtime.editor.async_reload_paths([str(backup.get("path") or "")])
        except RuntimeError as err:
            return json_message(
                f"备份已恢复到配置文件，但未能立即重载相关自动化。{err}",
                status_code=409,
                restored=True,
                reload_failed=True,
                backup=backup,
                restore_result=restored["result"],
                message=updated_message,
                session=updated_session,
            )

        return json_response(
            {
                "success": True,
                "backup": backup,
                "restore_result": restored["result"],
                "message": updated_message,
                "session": updated_session,
                "reload_results": reload_results,
            }
        )

    def _config_error_signatures(self, config_check: dict[str, Any] | None) -> set[tuple[str, int, str]]:
        """Create stable signatures for structured config-check errors."""
        signatures: set[tuple[str, int, str]] = set()
        for item in (config_check or {}).get("errors") or []:
            if not isinstance(item, dict):
                continue
            signatures.add(
                (
                    str(item.get("file") or ""),
                    int(item.get("line") or 0),
                    str(item.get("message") or ""),
                )
            )
        return signatures

    def _config_check_regressed(
        self,
        baseline_check: dict[str, Any],
        updated_check: dict[str, Any],
    ) -> bool:
        """Return true when applied edits introduce new config-check failures."""
        if updated_check.get("success"):
            return False

        if baseline_check.get("success"):
            return True

        baseline_errors = self._config_error_signatures(baseline_check)
        updated_errors = self._config_error_signatures(updated_check)
        if updated_errors - baseline_errors:
            return True

        return False

    def _build_failed_config_apply_message(self, config_check: dict[str, Any]) -> str:
        """Build a readable message for failed edit application."""
        errors = config_check.get("errors") or []
        if errors:
            first_error = errors[0]
            location = str(first_error.get("file") or "").strip()
            line = int(first_error.get("line") or 0)
            if location and line > 0:
                location = f"{location}:{line}"
            detail = str(first_error.get("message") or "").strip()
            summary = "应用修改后 Home Assistant 配置校验失败，已自动回滚。"
            if location:
                return f"{summary} 首个错误位于 {location}。{detail}".strip()
            if detail:
                return f"{summary} {detail}".strip()

        output = summarize_text(str(config_check.get("output") or ""), 260)
        if output:
            return f"应用修改后 Home Assistant 配置校验失败，已自动回滚。{output}"
        return "应用修改后 Home Assistant 配置校验失败，已自动回滚。"

    def _build_failed_reload_message(self, err: Exception) -> str:
        """Build a readable message for failed live reload after edits."""
        detail = str(err).strip()
        summary = "修改已写入，但相关自动化未能立即重载，已自动回滚，所以无需重启前也不会留下半生效状态。"
        if detail:
            return f"{summary} {detail}".strip()
        return summary


async def async_register_views(hass: HomeAssistant) -> None:
    """Register all HTTP views for the integration."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get("api_view_registered"):
        return
    runtime = create_backend_runtime(hass)
    hass.http.register_view(HAAIStudioApiView(runtime))
    domain_data["api_view_registered"] = True
