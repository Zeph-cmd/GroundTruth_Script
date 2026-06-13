from __future__ import annotations

import csv
import json
import logging
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)

EXPORTS_DIR = Path("exports")


class ExportError(RuntimeError):
    pass


def _ensure_exports_dir() -> Path:
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return EXPORTS_DIR


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return slug or "Unknown"


def _timestamp_suffix() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _build_export_stem(trip_id: str) -> str:
    return f"RoadVision_Trip_{_slugify(trip_id)}_{_timestamp_suffix()}"


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize_value(item) for item in value]
    return value


def _validate_trip_inputs(trip_path: str | Path, metadata: dict[str, Any] | None, image_records: list[dict[str, Any]]) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    selected_trip_path = Path(trip_path)
    if not selected_trip_path.exists():
        raise ExportError("missing files")

    if metadata is None or not isinstance(metadata, dict):
        raise ExportError("corrupted metadata")

    if not image_records:
        raise ExportError("empty trips")

    return selected_trip_path, metadata, image_records


def build_csv_export_rows(trip_id: str, image_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in image_records:
        rows.append(
            {
                "filename": record.get("name"),
                "timestamp": _serialize_value(record.get("timestamp")),
                "latitude": record.get("latitude"),
                "longitude": record.get("longitude"),
                "trip_id": trip_id,
            }
        )
    return rows


def build_json_export_payload(trip_id: str, trip_path: str | Path, metadata: dict[str, Any], image_records: list[dict[str, Any]], analytics_bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "trip_id": trip_id,
        "trip_path": Path(trip_path).as_posix(),
        "metadata": _serialize_value(metadata),
        "images": _serialize_value(image_records),
        "analytics": _serialize_value(analytics_bundle),
    }


def export_trip_csv(trip_path: str | Path, trip_id: str, metadata: dict[str, Any] | None, image_records: list[dict[str, Any]], analytics_bundle: dict[str, Any] | None = None) -> Path:
    selected_trip_path, metadata_dict, image_records = _validate_trip_inputs(trip_path, metadata, image_records)
    _ensure_exports_dir()

    csv_path = EXPORTS_DIR / f"{_build_export_stem(trip_id)}.csv"
    rows = build_csv_export_rows(trip_id, image_records)

    if not rows:
        raise ExportError("empty trips")

    with csv_path.open("w", newline="", encoding="utf-8") as file_handle:
        writer = csv.DictWriter(file_handle, fieldnames=["filename", "timestamp", "latitude", "longitude", "trip_id"])
        writer.writeheader()
        writer.writerows(rows)

    logger.debug("CSV export saved to %s for %s", csv_path, selected_trip_path)
    return csv_path


def export_trip_json(trip_path: str | Path, trip_id: str, metadata: dict[str, Any] | None, image_records: list[dict[str, Any]], analytics_bundle: dict[str, Any] | None = None) -> Path:
    selected_trip_path, metadata_dict, image_records = _validate_trip_inputs(trip_path, metadata, image_records)
    _ensure_exports_dir()

    json_path = EXPORTS_DIR / f"{_build_export_stem(trip_id)}.json"
    payload = build_json_export_payload(trip_id, selected_trip_path, metadata_dict, image_records, analytics_bundle or {})

    with json_path.open("w", encoding="utf-8") as file_handle:
        json.dump(payload, file_handle, indent=2, ensure_ascii=False)

    logger.debug("JSON export saved to %s for %s", json_path, selected_trip_path)
    return json_path


def export_trip_zip(trip_path: str | Path, trip_id: str, metadata: dict[str, Any] | None, image_records: list[dict[str, Any]], analytics_bundle: dict[str, Any] | None = None) -> Path:
    selected_trip_path, metadata_dict, image_records = _validate_trip_inputs(trip_path, metadata, image_records)
    _ensure_exports_dir()

    metadata_path = selected_trip_path / "metadata.json"
    if not metadata_path.exists():
        raise ExportError("missing files")

    zip_path = EXPORTS_DIR / f"{_build_export_stem(trip_id)}.zip"
    analytics_json = json.dumps(_serialize_value(analytics_bundle or {}), indent=2, ensure_ascii=False).encode("utf-8")

    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("analytics.json", analytics_json)
        archive.write(metadata_path, arcname="metadata.json")

        for record in image_records:
            image_path = Path(record["path"])
            if not image_path.exists():
                raise ExportError(f"missing files: {image_path.name}")
            archive.write(image_path, arcname=f"images/{image_path.name}")

    logger.debug("ZIP export saved to %s for %s", zip_path, selected_trip_path)
    return zip_path