"""The HA AI Studio integration."""
from __future__ import annotations

import asyncio
import importlib
import logging
from pathlib import Path
from typing import Any

from aiohttp import web

from homeassistant.components import frontend
try:
    from homeassistant.components.http import HomeAssistantView, StaticPathConfig
except ImportError:
    from homeassistant.components.http import HomeAssistantView

    class StaticPathConfig:
        """Shim for StaticPathConfig for older Home Assistant versions."""

        def __init__(self, url_path: str, path: str, cache_headers: bool) -> None:
            """Initialize the shim."""
            self.url_path = url_path
            self.path = path
            self.cache_headers = cache_headers

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .const import (
    API_BASE_PATH,
    DOMAIN,
    FALLBACK_PANEL_SUBTITLE,
    FALLBACK_PANEL_TITLE,
    NAME,
    PANEL_COMPONENT,
    PANEL_ICON,
    PANEL_URL_PATH,
    PANEL_VIEW_PATH,
    STATIC_BASE_PATH,
    VERSION,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def _panel_html_path(hass: HomeAssistant) -> Path:
    """Return the expected panel HTML path."""
    return Path(hass.config.path("custom_components", DOMAIN, "www", "panels", "panel.html"))


def _fallback_panel_html() -> str:
    """Return a minimal fallback UI until the frontend bundle exists."""
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{FALLBACK_PANEL_TITLE}</title>
    <style>
      :root {{
        color-scheme: light dark;
        font-family: "Segoe UI", "SF Pro Display", system-ui, sans-serif;
      }}
      body {{
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        background:
          radial-gradient(circle at top, rgba(74, 144, 226, 0.15), transparent 40%),
          linear-gradient(180deg, #101520, #0b0e14 65%);
        color: #f5f7fb;
      }}
      main {{
        width: min(680px, calc(100vw - 32px));
        padding: 32px;
        border: 1px solid rgba(255, 255, 255, 0.12);
        border-radius: 24px;
        background: rgba(9, 14, 24, 0.8);
        box-shadow: 0 24px 60px rgba(0, 0, 0, 0.35);
      }}
      h1 {{
        margin: 0 0 12px;
        font-size: clamp(2rem, 5vw, 3rem);
      }}
      p {{
        margin: 0;
        line-height: 1.6;
        color: rgba(245, 247, 251, 0.82);
      }}
      code {{
        font-family: "Cascadia Code", "SF Mono", ui-monospace, monospace;
      }}
    </style>
  </head>
  <body>
    <main>
      <h1>{FALLBACK_PANEL_TITLE}</h1>
      <p>{FALLBACK_PANEL_SUBTITLE}</p>
      <p style="margin-top: 12px;">Version: <code>{VERSION}</code></p>
      <p style="margin-top: 12px;">Expected frontend entry: <code>/local/{DOMAIN}/panels/panel.html</code></p>
    </main>
  </body>
</html>
"""


class HAAIStudioPanelView(HomeAssistantView):
    """Serve the HA AI Studio panel HTML."""

    url = PANEL_VIEW_PATH
    name = f"{DOMAIN}:panel"
    requires_auth = False

    def __init__(self, html_path: Path) -> None:
        """Initialize the panel view."""
        self._html_path = html_path

    async def get(self, request: web.Request) -> web.Response:
        """Serve the built panel or a fallback shell."""
        if self._html_path.exists():
            content = await asyncio.to_thread(self._html_path.read_text, encoding="utf-8")
            content = content.replace("{{VERSION}}", VERSION)
        else:
            content = _fallback_panel_html()

        return web.Response(
            text=content,
            content_type="text/html",
            headers={"Cache-Control": "no-cache"},
        )


class HAAIStudioPlaceholderApiView(HomeAssistantView):
    """Fallback API surface until backend business views are available."""

    url = API_BASE_PATH
    name = f"api:{DOMAIN}"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        """Return a stable placeholder response."""
        return web.json_response(
            {
                "success": False,
                "error": "backend_not_ready",
                "message": "HA AI Studio backend API has not been registered yet.",
            },
            status=501,
        )

    async def get(self, request: web.Request) -> web.Response:
        """Return API surface metadata."""
        return web.json_response(
            {
                "success": True,
                "domain": DOMAIN,
                "status": "placeholder",
                "message": "HA AI Studio API mount point is available.",
            }
        )


async def _register_static_path(hass: HomeAssistant) -> None:
    """Register the static asset path for frontend resources."""
    path_on_disk = str(hass.config.path("custom_components", DOMAIN, "www"))

    if hasattr(hass.http, "async_register_static_paths"):
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    url_path=STATIC_BASE_PATH,
                    path=path_on_disk,
                    cache_headers=False,
                )
            ]
        )
        return

    if hasattr(hass.http, "register_static_path"):
        hass.http.register_static_path(STATIC_BASE_PATH, path_on_disk, False)
        return

    _LOGGER.error("Unable to register static path for %s", DOMAIN)


async def _try_register_backend_views(hass: HomeAssistant) -> bool:
    """Register backend-provided views when they become available."""
    try:
        backend_api = importlib.import_module(f".backend.api", __package__)
    except ModuleNotFoundError as err:
        if err.name in {f"{__package__}.backend", f"{__package__}.backend.api"}:
            return False
        raise

    register_fn = getattr(backend_api, "async_register_views", None)
    if callable(register_fn):
        result = register_fn(hass)
        if hasattr(result, "__await__"):
            await result
        return True

    view_cls = getattr(backend_api, "HAAIStudioApiView", None)
    if view_cls is not None:
        hass.http.register_view(view_cls())
        return True

    return False


async def _register_shared_views(hass: HomeAssistant) -> None:
    """Register global static and HTTP views once per Home Assistant instance."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get("views_registered"):
        return

    await _register_static_path(hass)
    hass.http.register_view(HAAIStudioPanelView(_panel_html_path(hass)))

    if not await _try_register_backend_views(hass):
        hass.http.register_view(HAAIStudioPlaceholderApiView())

    domain_data["views_registered"] = True


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the HA AI Studio integration."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HA AI Studio from a config entry."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data[entry.entry_id] = {}
    await _register_shared_views(hass)

    frontend.async_register_built_in_panel(
        hass,
        component_name=PANEL_COMPONENT,
        sidebar_title=NAME,
        sidebar_icon=PANEL_ICON,
        frontend_url_path=PANEL_URL_PATH,
        config={"url": PANEL_VIEW_PATH},
        require_admin=True,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    frontend.async_remove_panel(hass, PANEL_URL_PATH)
    hass.data.setdefault(DOMAIN, {}).pop(entry.entry_id, None)
    return True
