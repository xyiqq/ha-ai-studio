"""Constants for the HA AI Studio integration."""
from __future__ import annotations

DOMAIN = "ha_ai_studio"
NAME = "HA AI Studio"
VERSION = "0.2.0"

PANEL_ICON = "mdi:robot-outline"
PANEL_COMPONENT = "iframe"
PANEL_URL_PATH = DOMAIN

API_BASE_PATH = f"/api/{DOMAIN}"
PANEL_VIEW_PATH = f"/{DOMAIN}/panel"
STATIC_BASE_PATH = f"/local/{DOMAIN}"

FALLBACK_PANEL_TITLE = "HA AI Studio"
FALLBACK_PANEL_SUBTITLE = "Frontend workspace is not ready yet."
