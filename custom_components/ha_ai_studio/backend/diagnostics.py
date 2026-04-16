"""Diagnostics collection for HA AI Studio."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service import async_get_all_descriptions
from homeassistant.helpers.template import Template

from .util import clip_text, generate_id, summarize_text, utc_now_iso

_LOGGER = logging.getLogger(__name__)

CONFIG_FILENAMES = (
    "configuration.yaml",
    "automations.yaml",
    "scripts.yaml",
    "scenes.yaml",
    "templates.yaml",
)

DISCOVERY_SUFFIXES = {
    ".yaml",
    ".yml",
    ".jinja",
    ".jinja2",
    ".j2",
    ".json",
    ".toml",
    ".conf",
    ".cfg",
    ".ini",
    ".txt",
    ".md",
    ".py",
    ".js",
    ".css",
    ".html",
    ".xml",
    ".service",
    ".env",
    ".properties",
}


class DiagnosticsCollector:
    """Collect read-only Home Assistant diagnostics context."""

    def __init__(self, hass: HomeAssistant, config_dir: Path) -> None:
        self.hass = hass
        self.config_dir = Path(config_dir)
        self._config_check_cache: tuple[float, dict[str, Any]] | None = None

    async def collect_snapshot(
        self,
        query: str | None,
        *,
        template: str | None = None,
        file_hints: list[str] | None = None,
    ) -> dict[str, Any]:
        """Collect diagnostics context for a chat message."""
        files_task = self.hass.async_add_executor_job(self._read_config_files, query or "", file_hints or [])
        logs_task = self.hass.async_add_executor_job(self._read_recent_logs)
        config_check_task = self.async_run_config_check()
        entities_task = self._collect_related_entities(query or "")
        services_task = self._collect_related_services(query or "")
        devices_task = self._collect_related_devices(query or "")
        areas_task = self._collect_related_areas(query or "")
        template_task = self._render_template(template)

        (
            config_files,
            recent_logs,
            config_check,
            related_entities,
            related_services,
            related_devices,
            related_areas,
            rendered_template,
        ) = await asyncio.gather(
            files_task,
            logs_task,
            config_check_task,
            entities_task,
            services_task,
            devices_task,
            areas_task,
            template_task,
        )

        snapshot = {
            "id": generate_id("diag"),
            "created_at": utc_now_iso(),
            "query": query or "",
            "config_files": config_files,
            "config_check": config_check,
            "recent_logs": recent_logs,
            "related_entities": related_entities,
            "related_services": related_services,
            "related_devices": related_devices,
            "related_areas": related_areas,
            "template_render": rendered_template,
        }
        snapshot["summary"] = self._summarize_snapshot(snapshot)
        return snapshot

    async def async_run_config_check(self) -> dict[str, Any]:
        """Run or reuse a cached configuration check."""
        now = time.monotonic()
        if self._config_check_cache and now - self._config_check_cache[0] < 30:
            return self._config_check_cache[1]
        result = await self.hass.async_add_executor_job(self._run_config_check)
        self._config_check_cache = (now, result)
        return result

    def _discover_config_files(self, query: str, file_hints: list[str]) -> list[Path]:
        """Build a capped list of likely relevant config files."""
        candidates: list[Path] = []
        seen: set[Path] = set()

        def add_candidate(path: Path) -> None:
            safe_path = self._safe_config_path(path)
            if safe_path is None or not safe_path.exists() or not safe_path.is_file():
                return
            if safe_path in seen:
                return
            seen.add(safe_path)
            candidates.append(safe_path)

        for name in CONFIG_FILENAMES:
            add_candidate(self.config_dir / name)

        packages_dir = self.config_dir / "packages"
        if packages_dir.exists():
            for path in sorted(packages_dir.rglob("*.yaml"))[:20]:
                add_candidate(path)

        blueprints_dir = self.config_dir / "blueprints"
        if blueprints_dir.exists():
            for path in sorted(blueprints_dir.rglob("*.yaml"))[:40]:
                add_candidate(path)

        for hint in file_hints:
            hint_path = self.config_dir / hint
            add_candidate(hint_path)

        lowered_query = query.lower()
        if "automation" in lowered_query:
            add_candidate(self.config_dir / "automations.yaml")
        if "script" in lowered_query:
            add_candidate(self.config_dir / "scripts.yaml")
        if "scene" in lowered_query:
            add_candidate(self.config_dir / "scenes.yaml")
        if "template" in lowered_query or "jinja" in lowered_query:
            add_candidate(self.config_dir / "templates.yaml")
        if "blueprint" in lowered_query or "蓝图" in lowered_query:
            if blueprints_dir.exists():
                for path in sorted(blueprints_dir.rglob("*.yaml"))[:40]:
                    add_candidate(path)

        if lowered_query:
            for path in self._discover_query_matched_files(lowered_query):
                add_candidate(path)

        return candidates[:20]

    def _discover_query_matched_files(self, lowered_query: str) -> list[Path]:
        """Return config files whose names or relative paths match query terms."""
        tokens = self._query_tokens(lowered_query)
        if not tokens:
            return []

        matches: list[Path] = []
        try:
            for path in self.config_dir.rglob("*"):
                safe_path = self._safe_config_path(path)
                if safe_path is None or not safe_path.is_file():
                    continue
                if safe_path.suffix.lower() not in DISCOVERY_SUFFIXES:
                    continue
                relative = str(safe_path.relative_to(self.config_dir)).replace("\\", "/").lower()
                filename = safe_path.name.lower()
                haystack = f"{relative} {filename}"
                if any(token in haystack for token in tokens):
                    matches.append(safe_path)
                if len(matches) >= 30:
                    break
        except Exception as err:
            _LOGGER.debug("Query matched file discovery failed: %s", err)
        return matches

    def _read_config_files(self, query: str, file_hints: list[str]) -> list[dict[str, Any]]:
        """Read a small excerpt from likely relevant config files."""
        files = []
        for path in self._discover_config_files(query, file_hints):
            try:
                content = path.read_text(encoding="utf-8")
                relative_path = str(path.relative_to(self.config_dir)).replace("\\", "/")
                files.append(
                    {
                        "path": relative_path,
                        "exists": True,
                        "size": len(content.encode("utf-8")),
                        "excerpt": clip_text(content, 5000),
                        "editable_content": clip_text(content, 12000),
                        "line_count": content.count("\n") + 1,
                        "summary": summarize_text(content, 160),
                    }
                )
            except Exception as err:
                _LOGGER.debug("Failed to read config file %s: %s", path, err)
        return files

    def _read_recent_logs(self) -> list[dict[str, Any]]:
        """Read recent warning/error log entries from the HA core log."""
        log_path = Path(self.hass.config.path("home-assistant.log"))
        if not log_path.exists():
            return []

        try:
            with log_path.open("r", encoding="utf-8", errors="ignore") as handle:
                lines = handle.readlines()[-400:]
        except Exception as err:
            _LOGGER.debug("Failed to read logs: %s", err)
            return []

        entries: list[dict[str, Any]] = []
        current: list[str] = []
        level = ""
        start_re = re.compile(r"\b(ERROR|WARNING|CRITICAL|INFO)\b", re.IGNORECASE)

        def flush_current() -> None:
            if not current:
                return
            raw = "".join(current).strip()
            if raw and level in {"ERROR", "WARNING", "CRITICAL"}:
                entries.append(
                    {
                        "level": level,
                        "message": summarize_text(raw, 220),
                        "raw": clip_text(raw, 1000),
                    }
                )

        for line in lines:
            match = start_re.search(line)
            if match:
                flush_current()
                current = [line]
                level = match.group(1).upper()
            elif current:
                current.append(line)

        flush_current()
        return entries[-20:]

    def _run_config_check(self) -> dict[str, Any]:
        """Run Home Assistant config check using the available CLI."""
        config_dir = self.hass.config.config_dir

        hass_bin = shutil.which("hass")
        if hass_bin:
            try:
                result = subprocess.run(
                    [hass_bin, "--script", "check_config", "--config", config_dir],
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
                output = (result.stdout or "") + (result.stderr or "")
                return self._parse_check_output(output, result.returncode)
            except Exception as err:
                _LOGGER.debug("hass check_config failed: %s", err)

        ha_bin = shutil.which("ha")
        if ha_bin:
            try:
                result = subprocess.run(
                    [ha_bin, "core", "check"],
                    capture_output=True,
                    text=True,
                    timeout=45,
                )
                output = (result.stdout or "") + (result.stderr or "")
                return self._parse_check_output(output, result.returncode)
            except Exception as err:
                _LOGGER.debug("ha core check failed: %s", err)

        return {
            "success": False,
            "output": "Config check is not available in this environment.",
            "errors": [],
        }

    def _parse_check_output(self, output: str, returncode: int) -> dict[str, Any]:
        """Parse raw config check output into structured errors."""
        lines = output.splitlines()
        config_dir = self.hass.config.config_dir.rstrip("/\\")
        errors: list[dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()
        inline_re = re.compile(r"\(([^,)]+),\s*line\s*(\d+)\)")
        location_re = re.compile(r'^\s*in\s+"([^"]+)",\s*line\s+(\d+)', re.IGNORECASE)

        def relative_path(path: str) -> str:
            normalized = path.replace("\\", "/")
            config_prefix = config_dir.replace("\\", "/")
            if normalized.startswith(config_prefix + "/"):
                return normalized[len(config_prefix) + 1 :]
            return normalized

        for index, line in enumerate(lines):
            inline = inline_re.search(line)
            if inline:
                key = (relative_path(inline.group(1).strip()), int(inline.group(2)))
                if key in seen:
                    continue
                seen.add(key)
                errors.append({"file": key[0], "line": key[1], "message": line.strip()})
                continue

            location = location_re.match(line)
            if not location:
                continue
            key = (relative_path(location.group(1).strip()), int(location.group(2)))
            if key in seen:
                continue
            seen.add(key)

            message = line.strip()
            for cursor in range(index - 1, max(index - 5, -1), -1):
                candidate = lines[cursor].strip()
                if candidate and not location_re.match(candidate):
                    message = candidate
                    break
            errors.append({"file": key[0], "line": key[1], "message": message})

        passed = returncode == 0 and not errors
        if "no errors found" in output.lower():
            passed = True
            errors = []

        return {
            "success": passed,
            "output": clip_text(output, 8000),
            "errors": errors,
        }

    async def _collect_related_entities(self, query: str) -> list[dict[str, Any]]:
        """Return entities that are likely relevant to the query."""
        entity_registry = er.async_get(self.hass)
        platform_map = {entry.entity_id: entry.platform for entry in entity_registry.entities.values()}
        tokens = self._query_tokens(query)
        matches: list[tuple[int, dict[str, Any]]] = []
        for state in self.hass.states.async_all():
            haystack = " ".join(
                [
                    state.entity_id.lower(),
                    str(state.attributes.get("friendly_name") or "").lower(),
                    str(state.attributes.get("device_class") or "").lower(),
                ]
            )
            score = self._token_score(tokens, haystack)
            if score <= 0 and tokens:
                continue
            matches.append(
                (
                    score,
                    {
                        "entity_id": state.entity_id,
                        "friendly_name": state.attributes.get("friendly_name"),
                        "state": state.state,
                        "device_class": state.attributes.get("device_class"),
                        "integration": platform_map.get(state.entity_id),
                    },
                )
            )

        if not tokens:
            matches = matches[:10]
        matches.sort(key=lambda item: (item[0], item[1]["entity_id"]), reverse=True)
        return [entry for _, entry in matches[:15]]

    async def _collect_related_services(self, query: str) -> list[dict[str, Any]]:
        """Return services that match the query terms."""
        descriptions = await async_get_all_descriptions(self.hass)
        tokens = self._query_tokens(query)
        matches: list[tuple[int, dict[str, Any]]] = []
        for domain, domain_services in descriptions.items():
            for service_name, meta in domain_services.items():
                haystack = " ".join(
                    [
                        domain.lower(),
                        service_name.lower(),
                        str((meta or {}).get("description") or "").lower(),
                        str((meta or {}).get("name") or "").lower(),
                    ]
                )
                score = self._token_score(tokens, haystack)
                if score <= 0 and tokens:
                    continue
                matches.append(
                    (
                        score,
                        {
                            "service": f"{domain}.{service_name}",
                            "name": (meta or {}).get("name") or service_name,
                            "description": (meta or {}).get("description") or "",
                        },
                    )
                )
        matches.sort(key=lambda item: (item[0], item[1]["service"]), reverse=True)
        return [entry for _, entry in matches[:15]]

    async def _collect_related_devices(self, query: str) -> list[dict[str, Any]]:
        """Return devices relevant to the query."""
        device_registry = dr.async_get(self.hass)
        tokens = self._query_tokens(query)
        matches: list[tuple[int, dict[str, Any]]] = []
        for device in device_registry.devices.values():
            haystack = " ".join(
                [
                    str(device.name or "").lower(),
                    str(device.name_by_user or "").lower(),
                    str(device.manufacturer or "").lower(),
                    str(device.model or "").lower(),
                ]
            )
            score = self._token_score(tokens, haystack)
            if score <= 0 and tokens:
                continue
            matches.append(
                (
                    score,
                    {
                        "id": device.id,
                        "name": device.name_by_user or device.name or device.id,
                        "manufacturer": device.manufacturer,
                        "model": device.model,
                    },
                )
            )
        matches.sort(key=lambda item: (item[0], item[1]["name"]), reverse=True)
        return [entry for _, entry in matches[:10]]

    async def _collect_related_areas(self, query: str) -> list[dict[str, Any]]:
        """Return areas relevant to the query."""
        area_registry = ar.async_get(self.hass)
        tokens = self._query_tokens(query)
        matches: list[tuple[int, dict[str, Any]]] = []
        for area in area_registry.areas.values():
            haystack = area.name.lower()
            score = self._token_score(tokens, haystack)
            if score <= 0 and tokens:
                continue
            matches.append((score, {"id": area.id, "name": area.name}))
        matches.sort(key=lambda item: (item[0], item[1]["name"]), reverse=True)
        return [entry for _, entry in matches[:10]]

    async def _render_template(self, template_str: str | None) -> dict[str, Any] | None:
        """Render a template string when the caller explicitly requests it."""
        if not template_str:
            return None
        try:
            template = Template(template_str, self.hass)
            result = template.async_render(parse_result=False)
            if asyncio.iscoroutine(result):
                result = await result
            return {
                "template": template_str,
                "result": clip_text(str(result), 3000),
            }
        except Exception as err:
            return {
                "template": template_str,
                "error": str(err),
            }

    def _summarize_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Return a compact summary for UI and model prompts."""
        config_check = snapshot.get("config_check") or {}
        logs = snapshot.get("recent_logs") or []
        return {
            "config_error_count": len(config_check.get("errors") or []),
            "log_error_count": len(logs),
            "config_files": [item["path"] for item in snapshot.get("config_files") or []],
            "related_entities": [item["entity_id"] for item in snapshot.get("related_entities") or []][:8],
            "related_services": [item["service"] for item in snapshot.get("related_services") or []][:8],
        }

    def _query_tokens(self, query: str) -> set[str]:
        """Tokenize a query string for lightweight matching."""
        return {token for token in re.findall(r"[a-zA-Z0-9_\\.:-]+", query.lower()) if len(token) > 2}

    def _token_score(self, tokens: set[str], haystack: str) -> int:
        """Compute a simple term match score."""
        if not tokens:
            return 1
        return sum(3 if token in haystack else 0 for token in tokens)

    def _safe_config_path(self, path: Path) -> Path | None:
        """Restrict reads to the Home Assistant config directory."""
        try:
            resolved = path.resolve()
            resolved.relative_to(self.config_dir.resolve())
            return resolved
        except Exception:
            return None
