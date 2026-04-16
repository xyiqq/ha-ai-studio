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
from .storage import SessionManager, SettingsManager
from .util import json_message, json_response, summarize_text

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class BackendRuntime:
    """Shared backend services for request handlers."""

    hass: HomeAssistant
    config_dir: Path
    settings: SettingsManager
    sessions: SessionManager
    diagnostics: DiagnosticsCollector
    ai: HAStudioAIManager


def create_backend_runtime(hass: HomeAssistant) -> BackendRuntime:
    """Create or return a cached backend runtime."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    runtime = domain_data.get("backend_runtime")
    if runtime:
        return runtime

    config_dir = Path(hass.config.config_dir)
    settings_manager = SettingsManager(hass)
    session_manager = SessionManager(hass)
    diagnostics = DiagnosticsCollector(hass, config_dir)
    ai_manager = HAStudioAIManager(hass, settings_manager)
    runtime = BackendRuntime(
        hass=hass,
        config_dir=config_dir,
        settings=settings_manager,
        sessions=session_manager,
        diagnostics=diagnostics,
        ai=ai_manager,
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
        session = await self.runtime.sessions.async_create_session(payload.get("title"))
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
        assistant_message = await self.runtime.sessions.async_append_message(
            session_id,
            role="assistant",
            content=ai_result["answer"],
            citations=ai_result.get("citations") or [],
            repair_draft=ai_result.get("repair_draft") or "",
            suggested_checks=ai_result.get("suggested_checks") or [],
            diagnostics_snapshot_id=saved_snapshot["id"],
        )

        updated_session = await self.runtime.sessions.async_get_session(session_id)
        return json_response(
            {
                "success": True,
                "session": updated_session,
                "user_message": user_message,
                "assistant_message": assistant_message,
                "diagnostics_snapshot": saved_snapshot,
            }
        )


async def async_register_views(hass: HomeAssistant) -> None:
    """Register all HTTP views for the integration."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get("api_view_registered"):
        return
    runtime = create_backend_runtime(hass)
    hass.http.register_view(HAAIStudioApiView(runtime))
    domain_data["api_view_registered"] = True

