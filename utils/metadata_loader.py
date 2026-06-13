from __future__ import annotations

import json
import logging
from pathlib import Path


logger = logging.getLogger(__name__)


def _first_present(data: dict, keys: list[str]) -> object | None:
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return None


def _normalize_trip_metadata(data: dict) -> dict:
    return {
        "trip_id": _first_present(data, ["trip_id", "tripId", "id", "trip"]),
        "start_time": _first_present(data, ["start_time", "startTime", "start", "trip_start_time"]),
        "end_time": _first_present(data, ["end_time", "endTime", "end", "trip_end_time"]),
        "total_images": _first_present(data, ["total_images", "totalImages", "image_count", "images", "count"]),
        "first_timestamp": _first_present(data, ["first_timestamp", "firstTimestamp", "photo_first_timestamp"]),
        "last_timestamp": _first_present(data, ["last_timestamp", "lastTimestamp", "photo_last_timestamp"]),
        "gps_available": _first_present(data, ["gps_available", "gpsAvailable", "gps", "has_gps"]),
    }


def load_trip_metadata(path: str | Path) -> dict:
    trip_path = Path(path)
    metadata_path = trip_path / "metadata.json"

    logger.debug("Loading trip metadata from %s", metadata_path)

    if not metadata_path.exists():
        logger.debug("Metadata missing for trip folder: %s", trip_path)
        return {
            "status": "missing",
            "error": "No metadata found",
            "metadata_path": str(metadata_path),
            "metadata": None,
        }

    try:
        with metadata_path.open("r", encoding="utf-8") as file_handle:
            raw_metadata = json.load(file_handle)
    except json.JSONDecodeError:
        logger.exception("Invalid JSON in metadata file: %s", metadata_path)
        return {
            "status": "invalid_json",
            "error": "Metadata corrupted",
            "metadata_path": str(metadata_path),
            "metadata": None,
        }

    if not isinstance(raw_metadata, dict):
        logger.debug("Metadata file is not an object: %s", metadata_path)
        raw_metadata = {}

    normalized_metadata = _normalize_trip_metadata(raw_metadata)
    logger.debug("Loaded trip metadata for %s", trip_path.name)

    return {
        "status": "ok",
        "error": None,
        "metadata_path": str(metadata_path),
        "metadata": raw_metadata,
        **normalized_metadata,
    }