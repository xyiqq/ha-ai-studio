"""AI provider routing and response normalization for HA AI Studio."""
from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp
from aiohttp import web
from homeassistant.core import HomeAssistant

from .storage import SettingsManager
from .util import clip_text, json_message, normalize_citation, parse_json_object, summarize_text

_LOGGER = logging.getLogger(__name__)


class HAStudioAIManager:
    """Call AI providers and normalize HA-specific responses."""

    def __init__(self, hass: HomeAssistant, settings_manager: SettingsManager) -> None:
        self.hass = hass
        self.settings_manager = settings_manager

    async def async_get_effective_settings(
        self,
        settings_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Merge request overrides on top of persisted settings."""
        settings = await self.settings_manager.async_get_settings()
        if not isinstance(settings_override, dict):
            return settings

        alias_map = {
            "ai_type": "aiType",
            "cloud_provider": "cloudProvider",
            "ai_model": "aiModel",
            "openai_api_key": "openaiApiKey",
            "openai_base_url": "openaiBaseUrl",
            "local_ai_provider": "localAiProvider",
            "ollama_url": "ollamaUrl",
            "ollama_model": "ollamaModel",
            "lm_studio_url": "lmStudioUrl",
            "lm_studio_model": "lmStudioModel",
            "custom_ai_url": "customAiUrl",
            "custom_ai_model": "customAiModel",
            "custom_ai_api_key": "customAiApiKey",
        }
        merged = dict(settings)
        sanitized = {key: value for key, value in settings_override.items() if value is not None}
        for key, alias in alias_map.items():
            if key in sanitized and alias not in sanitized:
                sanitized[alias] = sanitized[key]
        merged.update(sanitized)
        return merged

    def _resolve_selection(
        self,
        settings: dict[str, Any],
        *,
        ai_type: str | None = None,
        cloud_provider: str | None = None,
        ai_model: str | None = None,
    ) -> tuple[str, str, str | None]:
        """Resolve the effective AI mode, provider, and model."""
        resolved_ai_type = str(ai_type or settings.get("aiType") or "cloud")
        resolved_provider = str(cloud_provider or settings.get("cloudProvider") or "openai")
        resolved_model = ai_model or settings.get("aiModel")

        if resolved_ai_type == "local-ai":
            resolved_provider = str(settings.get("localAiProvider") or "ollama")
            if resolved_provider == "ollama":
                resolved_model = settings.get("ollamaModel") or resolved_model
            elif resolved_provider == "lm-studio":
                resolved_model = settings.get("lmStudioModel") or resolved_model
            elif resolved_provider == "custom":
                resolved_model = settings.get("customAiModel") or resolved_model

        return resolved_ai_type, resolved_provider, resolved_model

    def _build_openai_chat_url(self, base_url: str | None, default_base: str) -> str:
        """Normalize an OpenAI-compatible chat completions URL."""
        raw = (base_url or default_base or "").strip().rstrip("/")
        if not raw:
            return ""
        lower = raw.lower()
        if lower.endswith("/v1/chat/completions") or lower.endswith("/chat/completions"):
            return raw
        if lower.endswith("/v1"):
            return f"{raw}/chat/completions"
        return f"{raw}/v1/chat/completions"

    def _build_openai_models_url(self, base_url: str | None, default_base: str) -> str:
        """Normalize an OpenAI-compatible models URL."""
        raw = (base_url or default_base or "").strip().rstrip("/")
        if not raw:
            return ""
        lower = raw.lower()
        if lower.endswith("/v1/models") or lower.endswith("/models"):
            return raw
        if lower.endswith("/v1/chat/completions"):
            return raw[: -len("/chat/completions")] + "/models"
        if lower.endswith("/chat/completions"):
            return raw[: -len("/chat/completions")] + "/models"
        if lower.endswith("/v1"):
            return f"{raw}/models"
        return f"{raw}/v1/models"

    def _build_ollama_chat_url(self, base_url: str | None) -> str:
        """Normalize an Ollama chat endpoint."""
        raw = (base_url or "http://localhost:11434").strip().rstrip("/")
        if raw.lower().endswith("/api/chat"):
            return raw
        return f"{raw}/api/chat"

    def _build_ollama_models_url(self, base_url: str | None) -> str:
        """Normalize an Ollama model tags endpoint."""
        raw = (base_url or "http://localhost:11434").strip().rstrip("/")
        lower = raw.lower()
        if lower.endswith("/api/tags"):
            return raw
        if lower.endswith("/api/chat"):
            return raw[: -len("/api/chat")] + "/api/tags"
        return f"{raw}/api/tags"

    async def get_models(
        self,
        *,
        ai_type: str | None = None,
        cloud_provider: str | None = None,
        ai_model: str | None = None,
        settings_override: dict[str, Any] | None = None,
    ) -> web.Response:
        """Return remote model options for the active provider."""
        settings = await self.async_get_effective_settings(settings_override)
        resolved_ai_type, resolved_provider, resolved_model = self._resolve_selection(
            settings,
            ai_type=ai_type,
            cloud_provider=cloud_provider,
            ai_model=ai_model,
        )

        endpoint = ""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        provider_label = "OpenAI-compatible"
        parse_fn = self._parse_openai_models

        if resolved_ai_type == "cloud":
            if resolved_provider != "openai":
                return json_message(f"Unsupported cloud provider: {resolved_provider}", status_code=400)
            api_key = settings.get("openaiApiKey")
            if not api_key:
                return json_message("No API key configured for OpenAI-compatible provider", status_code=400)
            endpoint = self._build_openai_models_url(settings.get("openaiBaseUrl"), "https://api.openai.com")
            headers["Authorization"] = f"Bearer {api_key}"
        elif resolved_provider == "ollama":
            endpoint = self._build_ollama_models_url(settings.get("ollamaUrl"))
            provider_label = "Ollama"
            parse_fn = self._parse_ollama_models
        elif resolved_provider == "lm-studio":
            endpoint = self._build_openai_models_url(settings.get("lmStudioUrl"), "http://localhost:1234")
            provider_label = "LM Studio"
        elif resolved_provider == "custom":
            custom_url = settings.get("customAiUrl")
            if not custom_url:
                return json_message("Custom AI endpoint URL is required", status_code=400)
            endpoint = self._build_openai_models_url(custom_url, custom_url)
            provider_label = "Custom AI"
            api_key = settings.get("customAiApiKey") or settings.get("openaiApiKey")
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
        else:
            return json_message(f"Unsupported local AI provider: {resolved_provider}", status_code=400)

        payload, error_response = await self._http_get_json(provider_label, endpoint, headers)
        if error_response:
            return error_response

        raw_models = parse_fn(payload)
        models, configured_available = self._normalize_models(raw_models, resolved_model)
        return web.json_response(
            {
                "success": True,
                "ai_type": resolved_ai_type,
                "provider": resolved_provider,
                "models": models,
                "selected_model": resolved_model or "",
                "configured_model": resolved_model or "",
                "configured_model_available": configured_available,
                "supports_custom_model": True,
                "source": "remote",
                "endpoint": endpoint,
            }
        )

    async def generate_reply(
        self,
        *,
        user_message: str,
        session: dict[str, Any],
        snapshot: dict[str, Any],
        settings_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a chat request to the configured provider and normalize the response."""
        settings = await self.async_get_effective_settings(settings_override)
        ai_type, provider, ai_model = self._resolve_selection(settings)
        system_prompt = self._build_system_prompt(settings)
        diagnostics_context = self._build_diagnostics_context(snapshot)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": diagnostics_context},
        ]

        for message in session.get("messages", [])[-10:]:
            role = message.get("role")
            if role not in {"user", "assistant"}:
                continue
            messages.append({"role": role, "content": str(message.get("content") or "")})
        messages.append({"role": "user", "content": user_message})

        raw_text = await self._call_provider(
            settings=settings,
            ai_type=ai_type,
            provider=provider,
            ai_model=ai_model,
            messages=messages,
        )
        normalized = self._normalize_model_reply(raw_text, snapshot)
        normalized["model"] = ai_model or ""
        normalized["provider"] = provider
        return normalized

    def _build_system_prompt(self, settings: dict[str, Any]) -> str:
        """Create the HA-specific system prompt."""
        language = settings.get("uiLanguage") or "auto"
        preferred_language = "Chinese" if language in {"auto", "zh", "zh-Hans", "zh-Hant"} else "the user's UI language"
        return (
            "You are Home Assistant Configuration & Diagnostics Copilot.\n"
            "Stay focused on Home Assistant configuration, automations, scripts, templates, entities, services, devices, "
            "areas, integrations, and runtime errors. If the user asks something unrelated, briefly redirect back to the "
            "Home Assistant context.\n"
            "Use the provided diagnostics snapshot as evidence. Do not claim you changed files. Do not suggest destructive "
            "actions without clearly marking them as needing manual confirmation.\n"
            "Prefer concise, technical answers. Reply in "
            f"{preferred_language}.\n"
            "Return JSON only with this shape:\n"
            "{\n"
            '  "answer": "Markdown answer with sections Diagnosis, Why, Evidence, Repair Draft, How to Verify",\n'
            '  "citations": [{"type": "config_file|config_check|log|entity|service|device|area|template", "title": "", "path": "", "line": 0, "snippet": ""}],\n'
            '  "repair_draft": "Markdown snippet with code fences when applicable",\n'
            '  "suggested_checks": ["short next step", "short next step"]\n'
            "}\n"
        )

    def _build_diagnostics_context(self, snapshot: dict[str, Any]) -> str:
        """Serialize a bounded diagnostics context for the model."""
        config_files = [
            {
                "path": item.get("path"),
                "summary": item.get("summary"),
                "excerpt": clip_text(item.get("excerpt"), 1600),
            }
            for item in (snapshot.get("config_files") or [])[:4]
        ]
        context = {
            "snapshot_id": snapshot.get("id"),
            "query": snapshot.get("query"),
            "config_check": snapshot.get("config_check"),
            "recent_logs": (snapshot.get("recent_logs") or [])[:10],
            "config_files": config_files,
            "related_entities": (snapshot.get("related_entities") or [])[:10],
            "related_services": (snapshot.get("related_services") or [])[:10],
            "related_devices": (snapshot.get("related_devices") or [])[:8],
            "related_areas": (snapshot.get("related_areas") or [])[:8],
            "template_render": snapshot.get("template_render"),
        }
        return "Diagnostics snapshot:\n" + json.dumps(context, ensure_ascii=False, indent=2)

    async def _call_provider(
        self,
        *,
        settings: dict[str, Any],
        ai_type: str,
        provider: str,
        ai_model: str | None,
        messages: list[dict[str, str]],
    ) -> str:
        """Route the request to the correct AI provider."""
        if ai_type == "cloud":
            if provider != "openai":
                raise ValueError(f"Unsupported cloud provider: {provider}")
            api_key = settings.get("openaiApiKey")
            if not api_key:
                raise ValueError("No API key configured for OpenAI-compatible provider")
            url = self._build_openai_chat_url(settings.get("openaiBaseUrl"), "https://api.openai.com")
            payload: dict[str, Any] = {"messages": messages}
            if ai_model:
                payload["model"] = ai_model
            return await self._http_post_openai_like("OpenAI-compatible", url, payload, api_key)

        if provider == "ollama":
            model = ai_model or settings.get("ollamaModel")
            if not model:
                raise ValueError("No Ollama model configured")
            url = self._build_ollama_chat_url(settings.get("ollamaUrl"))
            payload = {"model": model, "messages": messages, "stream": False}
            return await self._http_post_ollama(url, payload)

        if provider == "lm-studio":
            url = self._build_openai_chat_url(settings.get("lmStudioUrl"), "http://localhost:1234")
            payload = {"messages": messages}
            if ai_model:
                payload["model"] = ai_model
            return await self._http_post_openai_like("LM Studio", url, payload, None)

        if provider == "custom":
            custom_url = settings.get("customAiUrl")
            if not custom_url:
                raise ValueError("Custom AI endpoint URL is required")
            url = self._build_openai_chat_url(custom_url, custom_url)
            payload = {"messages": messages}
            if ai_model:
                payload["model"] = ai_model
            api_key = settings.get("customAiApiKey") or settings.get("openaiApiKey")
            return await self._http_post_openai_like("Custom AI", url, payload, api_key)

        raise ValueError(f"Unsupported AI provider: {provider}")

    async def _http_post_openai_like(
        self,
        provider_label: str,
        url: str,
        payload: dict[str, Any],
        api_key: str | None,
    ) -> str:
        """Call an OpenAI-compatible chat endpoint."""
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        data, error_response = await self._http_post_json(provider_label, url, headers, payload)
        if error_response:
            raise RuntimeError(self._response_message(error_response))

        if isinstance(data, dict):
            choices = data.get("choices") or []
            if choices and isinstance(choices[0], dict):
                message = choices[0].get("message") or {}
                content = message.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    parts = []
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            parts.append(str(item.get("text") or ""))
                    return "\n".join(parts).strip()
        raise RuntimeError(f"{provider_label} returned an empty response")

    async def _http_post_ollama(self, url: str, payload: dict[str, Any]) -> str:
        """Call an Ollama chat endpoint."""
        headers = {"Content-Type": "application/json"}
        data, error_response = await self._http_post_json("Ollama", url, headers, payload)
        if error_response:
            raise RuntimeError(self._response_message(error_response))

        if isinstance(data, dict):
            message = data.get("message") or {}
            content = message.get("content")
            if isinstance(content, str):
                return content
        raise RuntimeError("Ollama returned an empty response")

    async def _http_post_json(
        self,
        provider_label: str,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> tuple[Any | None, web.Response | None]:
        """POST JSON and return decoded payload or an error response."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload) as response:
                    text = await response.text()
                    data = self._decode_json(text)
                    if response.status >= 400:
                        return None, json_message(
                            self._build_error_message(provider_label, response.status, data, text),
                            status_code=response.status,
                        )
                    return data, None
        except Exception as err:
            _LOGGER.error("%s request failed: %s", provider_label, err)
            return None, json_message(f"{provider_label} request failed: {err}", status_code=500)

    async def _http_get_json(
        self,
        provider_label: str,
        url: str,
        headers: dict[str, str],
    ) -> tuple[Any | None, web.Response | None]:
        """GET JSON and return decoded payload or an error response."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    text = await response.text()
                    data = self._decode_json(text)
                    if response.status >= 400:
                        return None, json_message(
                            self._build_error_message(provider_label, response.status, data, text),
                            status_code=response.status,
                        )
                    return data, None
        except Exception as err:
            _LOGGER.error("%s request failed: %s", provider_label, err)
            return None, json_message(f"{provider_label} request failed: {err}", status_code=500)

    def _decode_json(self, raw_text: str) -> Any:
        """Decode JSON text safely."""
        if not raw_text:
            return {}
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError:
            return {}

    def _response_message(self, response: web.Response) -> str:
        """Extract a readable message from an aiohttp response object."""
        return response.text or "Request failed"

    def _build_error_message(self, provider_label: str, status: int, data: Any, raw_text: str) -> str:
        """Build a provider error message from the response payload."""
        detail = ""
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict):
                detail = str(error.get("message") or "")
            elif error:
                detail = str(error)
            elif data.get("message"):
                detail = str(data.get("message"))
        if not detail:
            detail = summarize_text(raw_text, 220)
        return f"{provider_label} error {status}: {detail}".strip()

    def _parse_openai_models(self, payload: Any) -> list[dict[str, Any]]:
        """Parse an OpenAI-compatible /models response."""
        models: list[dict[str, Any]] = []
        if not isinstance(payload, dict):
            return models
        for item in payload.get("data") or []:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "").strip()
            if not model_id:
                continue
            models.append({"id": model_id, "label": model_id, "owned_by": item.get("owned_by")})
        return models

    def _parse_ollama_models(self, payload: Any) -> list[dict[str, Any]]:
        """Parse an Ollama /api/tags response."""
        models: list[dict[str, Any]] = []
        if not isinstance(payload, dict):
            return models
        for item in payload.get("models") or []:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("model") or item.get("name") or "").strip()
            if not model_id:
                continue
            models.append({"id": model_id, "label": model_id, "size": item.get("size")})
        return models

    def _normalize_models(
        self,
        raw_models: list[dict[str, Any]],
        configured_model: str | None,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Normalize model data and preserve a manually configured model."""
        models: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw_models:
            model_id = str(item.get("id") or "").strip()
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            normalized = dict(item)
            normalized.setdefault("label", model_id)
            normalized["id"] = model_id
            models.append(normalized)

        configured = (configured_model or "").strip()
        configured_available = configured in seen if configured else False
        if configured and not configured_available:
            models.insert(
                0,
                {
                    "id": configured,
                    "label": configured,
                    "is_custom": True,
                    "is_configured": True,
                },
            )
        elif configured:
            for model in models:
                if model["id"] == configured:
                    model["is_configured"] = True
                    break
        return models, configured_available

    def _normalize_model_reply(self, raw_text: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Normalize a model response into the API contract."""
        parsed = parse_json_object(raw_text)
        if isinstance(parsed, dict):
            answer = str(parsed.get("answer") or "").strip()
            if not answer:
                answer = self._format_answer_from_fields(parsed)
            citations = [
                normalize_citation(item)
                for item in parsed.get("citations") or []
                if isinstance(item, dict)
            ]
            repair_draft = str(parsed.get("repair_draft") or "").strip()
            suggested_checks = [
                str(item).strip()
                for item in parsed.get("suggested_checks") or []
                if str(item).strip()
            ]
        else:
            answer = raw_text.strip()
            citations = []
            repair_draft = ""
            suggested_checks = []

        if not citations:
            citations = self._fallback_citations(snapshot)
        if not suggested_checks:
            suggested_checks = self._fallback_suggested_checks(snapshot)
        if not answer:
            answer = self._fallback_answer(snapshot)

        return {
            "answer": answer,
            "citations": citations,
            "repair_draft": repair_draft,
            "suggested_checks": suggested_checks,
        }

    def _format_answer_from_fields(self, payload: dict[str, Any]) -> str:
        """Build a markdown answer if the model returned split fields."""
        diagnosis = str(payload.get("diagnosis") or "").strip()
        why = str(payload.get("why") or "").strip()
        evidence = str(payload.get("evidence") or "").strip()
        repair = str(payload.get("repair_draft") or "").strip()
        verify = "\n".join(str(item).strip() for item in payload.get("suggested_checks") or [] if str(item).strip())
        parts = []
        if diagnosis:
            parts.append(f"## Diagnosis\n{diagnosis}")
        if why:
            parts.append(f"## Why\n{why}")
        if evidence:
            parts.append(f"## Evidence\n{evidence}")
        if repair:
            parts.append(f"## Repair Draft\n{repair}")
        if verify:
            parts.append(f"## How to Verify\n{verify}")
        return "\n\n".join(parts).strip()

    def _fallback_citations(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        """Build citations from the diagnostics snapshot when the model omits them."""
        citations: list[dict[str, Any]] = []
        config_check = snapshot.get("config_check") or {}
        for error in (config_check.get("errors") or [])[:4]:
            citations.append(
                normalize_citation(
                    {
                        "type": "config_check",
                        "title": error.get("file") or "Configuration check",
                        "path": error.get("file") or "",
                        "line": error.get("line") or 0,
                        "snippet": error.get("message") or "",
                    }
                )
            )
        for item in (snapshot.get("recent_logs") or [])[:4]:
            citations.append(
                normalize_citation(
                    {
                        "type": "log",
                        "title": item.get("level") or "Log entry",
                        "snippet": item.get("raw") or item.get("message") or "",
                    }
                )
            )
        for item in (snapshot.get("config_files") or [])[:4]:
            citations.append(
                normalize_citation(
                    {
                        "type": "config_file",
                        "title": item.get("path") or "Config file",
                        "path": item.get("path") or "",
                        "snippet": item.get("summary") or item.get("excerpt") or "",
                    }
                )
            )
        for item in (snapshot.get("related_entities") or [])[:2]:
            citations.append(
                normalize_citation(
                    {
                        "type": "entity",
                        "title": item.get("entity_id") or "Entity",
                        "snippet": f"State: {item.get('state')} | Friendly name: {item.get('friendly_name')}",
                    }
                )
            )
        return citations[:8]

    def _fallback_suggested_checks(self, snapshot: dict[str, Any]) -> list[str]:
        """Build suggested next steps when the model omits them."""
        checks: list[str] = []
        config_check = snapshot.get("config_check") or {}
        if config_check.get("errors"):
            checks.append("Run Home Assistant config check again after applying the draft fix.")
        if snapshot.get("recent_logs"):
            checks.append("Reproduce the issue once and compare the newest error entries in the core log.")
        if snapshot.get("template_render"):
            checks.append("Re-render the affected template after any edits and confirm the output is stable.")
        if not checks:
            checks.append("Validate the affected automation, script, or integration from the Home Assistant UI.")
        return checks[:4]

    def _fallback_answer(self, snapshot: dict[str, Any]) -> str:
        """Return a compact fallback answer if parsing fails."""
        config_check = snapshot.get("config_check") or {}
        recent_logs = snapshot.get("recent_logs") or []
        diagnosis = "No structured AI answer was returned. Use the attached diagnostics snapshot as the source of truth."
        why = f"Configuration check errors: {len(config_check.get('errors') or [])}. Recent log entries: {len(recent_logs)}."
        evidence = "See the citations sourced from configuration check output, logs, and relevant config files."
        verify = "Re-run config check and confirm the newest log entries stop reproducing."
        return "\n\n".join(
            [
                f"## Diagnosis\n{diagnosis}",
                f"## Why\n{why}",
                f"## Evidence\n{evidence}",
                f"## How to Verify\n{verify}",
            ]
        )
