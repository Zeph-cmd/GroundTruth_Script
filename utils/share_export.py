from __future__ import annotations

import json
import logging
import re
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image


logger = logging.getLogger(__name__)

EXPORTS_DIR = Path("exports")


ProgressCallback = Any


class ShareExportError(RuntimeError):
    pass


def _ensure_exports_dir() -> Path:
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return EXPORTS_DIR


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return slug or "Unknown"


def _timestamp_suffix() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


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


def _estimate_total_bytes(paths: list[Path]) -> int:
    total_bytes = 0
    for item_path in paths:
        if item_path.exists() and item_path.is_file():
            total_bytes += item_path.stat().st_size
    return total_bytes


def _zip_bytes_to_mb(value: int) -> float:
    return round(value / (1024 * 1024), 2)


def _build_trip_payload(trip_id: str, trip_path: Path, metadata: dict[str, Any], image_records: list[dict[str, Any]], analytics_bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "trip_id": trip_id,
        "trip_path": trip_path.as_posix(),
        "metadata": _serialize_value(metadata),
        "images": _serialize_value(image_records),
        "analytics": _serialize_value(analytics_bundle),
    }


def _write_json_file(file_path: Path, payload: dict[str, Any]) -> None:
    with file_path.open("w", encoding="utf-8") as file_handle:
        json.dump(payload, file_handle, indent=2, ensure_ascii=False)


def _safe_zip_write(archive: zipfile.ZipFile, source: Path, arcname: str) -> None:
    if not source.exists():
        raise ShareExportError(f"missing files: {source.name}")
    archive.write(source, arcname=arcname)


def _ensure_trip_ready(trip_path: str | Path, metadata: dict[str, Any] | None, image_records: list[dict[str, Any]] | None) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    selected_trip_path = Path(trip_path)
    if not selected_trip_path.exists():
        raise ShareExportError("missing files")

    if not isinstance(metadata, dict):
        raise ShareExportError("corrupted metadata")

    if not image_records:
        raise ShareExportError("empty trips")

    return selected_trip_path, metadata, image_records


def _trip_export_name(trip_id: str) -> str:
    return f"RoadVision_{_slugify(trip_id)}.zip"


def _emit_progress(progress_callback: ProgressCallback | None, message: str) -> None:
    if progress_callback is not None:
        progress_callback(message)


def _compress_directory(root_path: Path, zip_path: Path, root_arcname: str, *, include_size_stats: bool = False) -> dict[str, float] | None:
    source_files = [item for item in root_path.rglob("*") if item.is_file()]
    before_mb = _zip_bytes_to_mb(_estimate_total_bytes(source_files))

    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for item_path in source_files:
            relative_path = item_path.relative_to(root_path)
            archive.write(item_path, arcname=str(Path(root_arcname) / relative_path))

    after_mb = _zip_bytes_to_mb(zip_path.stat().st_size)
    if include_size_stats:
        return {"before_mb": before_mb, "after_mb": after_mb}
    return None


def _write_trip_bundle(trip_path: Path, temp_root: Path, metadata: dict[str, Any], image_records: list[dict[str, Any]], analytics_bundle: dict[str, Any], *, lightweight: bool) -> Path:
    trip_root = temp_root / trip_path.name
    images_root = trip_root / "images"
    images_root.mkdir(parents=True, exist_ok=True)

    filtered_metadata = metadata
    if lightweight:
        filtered_metadata = {
            key: value
            for key, value in metadata.items()
            if key in {"trip_id", "tripId", "start_time", "startTime", "end_time", "endTime", "total_images", "totalImages", "images", "frames", "photos", "records"}
        }

    for record in image_records:
        source_image = Path(record["path"])
        if not source_image.exists():
            raise ShareExportError(f"missing files: {source_image.name}")

        target_image = images_root / source_image.name
        if lightweight:
            try:
                with Image.open(source_image) as image_handle:
                    image_handle = image_handle.convert("RGB")
                    image_handle.thumbnail((1600, 1600))
                    image_handle.save(target_image, format="JPEG", quality=80, optimize=True)
            except Exception as exc:
                raise ShareExportError(f"unable to prepare image: {source_image.name}") from exc
        else:
            target_image.write_bytes(source_image.read_bytes())

    _write_json_file(trip_root / "metadata.json", _serialize_value(filtered_metadata))
    _write_json_file(trip_root / "analytics.json", _serialize_value(analytics_bundle))
    return trip_root


def export_selected_trip(
    trip_path: str | Path,
    trip_id: str,
    metadata: dict[str, Any] | None,
    image_records: list[dict[str, Any]] | None,
    analytics_bundle: dict[str, Any] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    selected_trip_path, metadata_dict, image_records = _ensure_trip_ready(trip_path, metadata, image_records)
    _ensure_exports_dir()

    zip_path = EXPORTS_DIR / _trip_export_name(trip_id)
    bundle_payload = _build_trip_payload(trip_id, selected_trip_path, metadata_dict, image_records, analytics_bundle or {})

    with tempfile.TemporaryDirectory(prefix="roadvision_export_") as temp_dir:
        _emit_progress(progress_callback, "Preparing files...")
        temp_root = Path(temp_dir)
        trip_root = _write_trip_bundle(selected_trip_path, temp_root, metadata_dict, image_records, analytics_bundle or {}, lightweight=False)

        _emit_progress(progress_callback, "Compressing...")
        temp_trip_files = [item for item in trip_root.rglob("*") if item.is_file()]
        before_mb = _zip_bytes_to_mb(
            _estimate_total_bytes([Path(record["path"]) for record in image_records] + [selected_trip_path / "metadata.json"])
            + len(json.dumps(_serialize_value(analytics_bundle or {}), indent=2, ensure_ascii=False).encode("utf-8"))
        )
        with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for item_path in temp_trip_files:
                archive.write(item_path, arcname=str(Path(trip_root.name) / item_path.relative_to(trip_root)))

        _emit_progress(progress_callback, "Finalizing...")

    return {
        "export_path": zip_path,
        "before_mb": before_mb,
        "after_mb": _zip_bytes_to_mb(zip_path.stat().st_size),
        "temp_cleanup": True,
        "payload": bundle_payload,
    }


def export_lightweight_package(
    trip_path: str | Path,
    trip_id: str,
    metadata: dict[str, Any] | None,
    image_records: list[dict[str, Any]] | None,
    analytics_bundle: dict[str, Any] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    selected_trip_path, metadata_dict, image_records = _ensure_trip_ready(trip_path, metadata, image_records)
    _ensure_exports_dir()

    zip_path = EXPORTS_DIR / _trip_export_name(trip_id)

    with tempfile.TemporaryDirectory(prefix="roadvision_lightweight_") as temp_dir:
        _emit_progress(progress_callback, "Preparing files...")
        temp_root = Path(temp_dir)
        trip_root = _write_trip_bundle(selected_trip_path, temp_root, metadata_dict, image_records, analytics_bundle or {}, lightweight=True)
        temp_trip_files = [item for item in trip_root.rglob("*") if item.is_file()]
        before_mb = _zip_bytes_to_mb(
            _estimate_total_bytes([Path(record["path"]) for record in image_records] + [selected_trip_path / "metadata.json"])
            + len(json.dumps(_serialize_value(analytics_bundle or {}), indent=2, ensure_ascii=False).encode("utf-8"))
        )

        _emit_progress(progress_callback, "Compressing...")
        with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for item_path in temp_trip_files:
                archive.write(item_path, arcname=str(Path(trip_root.name) / item_path.relative_to(trip_root)))

        _emit_progress(progress_callback, "Finalizing...")

    return {
        "export_path": zip_path,
        "before_mb": before_mb,
        "after_mb": _zip_bytes_to_mb(zip_path.stat().st_size),
        "temp_cleanup": True,
    }


def _trip_metadata_export(trip_path: Path) -> tuple[dict[str, Any], list[Path]]:
    from utils.image_loader import get_trip_images
    from utils.metadata_loader import load_trip_metadata

    metadata_result = load_trip_metadata(trip_path)
    if metadata_result["status"] != "ok":
        raise ShareExportError(metadata_result["error"])

    image_paths = get_trip_images(trip_path)
    if not image_paths:
        raise ShareExportError("empty trips")

    return metadata_result["metadata"], image_paths


def _trip_analytics_export(trip_id: str, trip_path: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    from utils.analytics import (
        calculate_image_intervals,
        calculate_trip_duration,
        detect_data_gaps,
        estimate_distance_from_gps,
    )

    return {
        "trip_id": trip_id,
        "trip_path": trip_path.as_posix(),
        "duration": calculate_trip_duration(metadata),
        "intervals": calculate_image_intervals(metadata),
        "movement": estimate_distance_from_gps(metadata),
        "data_gaps": detect_data_gaps(metadata),
    }


def export_full_dataset(trips_root: str | Path, dataset_name: str = "RoadVision_Dataset", progress_callback: ProgressCallback | None = None) -> dict[str, Any]:
    root_path = Path(trips_root)
    if not root_path.exists():
        raise ShareExportError("missing files")

    trip_paths = sorted(item for item in root_path.iterdir() if item.is_dir())
    if not trip_paths:
        raise ShareExportError("empty trips")

    _ensure_exports_dir()
    zip_path = EXPORTS_DIR / f"{_slugify(dataset_name)}.zip"

    before_bytes = 0
    _emit_progress(progress_callback, "Preparing files...")
    with tempfile.TemporaryDirectory(prefix="roadvision_full_dataset_") as temp_dir:
        temp_root = Path(temp_dir)
        dataset_root = temp_root / _slugify(dataset_name)
        dataset_root.mkdir(parents=True, exist_ok=True)

        _emit_progress(progress_callback, "Compressing...")
        for trip_path in trip_paths:
            metadata, image_paths = _trip_metadata_export(trip_path)
            analytics_payload = _trip_analytics_export(trip_path.name, trip_path, metadata)
            before_bytes += _estimate_total_bytes([trip_path / "metadata.json", *image_paths])
            before_bytes += len(json.dumps(_serialize_value(analytics_payload), indent=2, ensure_ascii=False).encode("utf-8"))

            trip_root = dataset_root / trip_path.name
            images_root = trip_root / "images"
            images_root.mkdir(parents=True, exist_ok=True)

            (trip_root / "metadata.json").write_text(json.dumps(_serialize_value(metadata), indent=2, ensure_ascii=False), encoding="utf-8")
            (trip_root / "analytics.json").write_text(json.dumps(_serialize_value(analytics_payload), indent=2, ensure_ascii=False), encoding="utf-8")

            for image_path in image_paths:
                if not image_path.exists():
                    raise ShareExportError(f"missing files: {image_path.name}")
                (images_root / image_path.name).write_bytes(image_path.read_bytes())

        with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for item_path in dataset_root.rglob("*"):
                if item_path.is_file():
                    archive.write(item_path, arcname=str(item_path.relative_to(dataset_root)))

        _emit_progress(progress_callback, "Finalizing...")

    return {
        "export_path": zip_path,
        "before_mb": _zip_bytes_to_mb(before_bytes),
        "after_mb": _zip_bytes_to_mb(zip_path.stat().st_size),
        "temp_cleanup": True,
    }


def share_file(file_path: str | Path) -> Path:
    share_path = Path(file_path)
    if not share_path.exists():
        raise ShareExportError("missing files")
    logger.debug("Prepared share file: %s", share_path)
    return share_path