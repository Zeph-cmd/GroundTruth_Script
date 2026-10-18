"""Helpers for integrating the GodsEYE OSINT dashboard (godseye.network) with GroundTruth."""

from __future__ import annotations

from typing import Iterable
from urllib.parse import quote

GODSEYE_DASHBOARD_URL = "https://godseye.network/dashboard"

# Layers preloaded in the dashboard, matching the operator's preferred view.
DEFAULT_LAYERS: tuple[str, ...] = (
    "maritime.cctv.live",
    "news.news_intel",
    "earthquakes.global",
    "incidents.day",
    "night",
    "cables",
    "sdk_sea",
    "sdk_air",
    "sdk_naval",
)


def build_dashboard_url(layers: Iterable[str] | None = None) -> str:
    """Build a GodsEYE dashboard URL with the given layers preselected."""
    selected = ",".join(layers) if layers else ",".join(DEFAULT_LAYERS)
    return f"{GODSEYE_DASHBOARD_URL}?layers={quote(selected)}"

