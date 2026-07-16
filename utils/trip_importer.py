from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path
from typing import Any


TRIPS_DIR = Path("trips")
IMPORTED_TRIPS_DIR = TRIPS_DIR / "_imports"
IMPORTED_ZIPS_DIR = TRIPS_DIR / "_incoming"


class TripImportError(RuntimeError):
    pass


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return slug or "Imported_Trip"


def _unique_path(base_dir: Path, name: str, suffix: str = "") -> Path:
    candidate = base_dir / f"{name}{suffix}"
    counter = 1
    while candidate.exists():
        candidate = base_dir / f"{name}_{counter}{suffix}"
        counter += 1
    return candidate


def _uploaded_bytes(uploaded_file: Any) -> bytes:
    if hasattr(uploaded_file, "getbuffer"):
        return bytes(uploaded_file.getbuffer())
    if hasattr(uploaded_file, "read"):
        return uploaded_file.read()
    raise TripImportError("unsupported uploaded file")


def _is_allowed_image_path(path: Path) -> bool:
    return path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


def _clear_existing_imports() -> None:
    """Clear all existing imported zips and extracted trips"""
    if IMPORTED_ZIPS_DIR.exists():
        shutil.rmtree(IMPORTED_ZIPS_DIR)
    if IMPORTED_TRIPS_DIR.exists():
        shutil.rmtree(IMPORTED_TRIPS_DIR)
    EXTRACTED_TRIPS_DIR = TRIPS_DIR / ".extracted"
    if EXTRACTED_TRIPS_DIR.exists():
        shutil.rmtree(EXTRACTED_TRIPS_DIR)


def import_trip_zip(uploaded_file: Any, trip_name: str | None = None, clear_old: bool = False) -> Path:
    if uploaded_file is None:
        raise TripImportError("choose a ZIP file first")

    if clear_old:
        _clear_existing_imports()

    IMPORTED_ZIPS_DIR.mkdir(parents=True, exist_ok=True)
    base_name = trip_name.strip() if isinstance(trip_name, str) and trip_name.strip() else Path(str(getattr(uploaded_file, "name", "Imported_Trip"))).stem
    safe_name = _slugify(base_name)
    target_path = _unique_path(IMPORTED_ZIPS_DIR, safe_name, ".zip")
    target_path.write_bytes(_uploaded_bytes(uploaded_file))
    return target_path


def import_trip_folder_files(uploaded_files: list[Any], trip_name: str | None = None) -> Path:
    if not uploaded_files:
        raise TripImportError("choose one or more files first")

    IMPORTED_TRIPS_DIR.mkdir(parents=True, exist_ok=True)
    base_name = trip_name.strip() if isinstance(trip_name, str) and trip_name.strip() else Path(str(getattr(uploaded_files[0], "name", "Imported_Trip"))).stem
    safe_name = _slugify(base_name)
    target_root = _unique_path(IMPORTED_TRIPS_DIR, safe_name)
    target_root.mkdir(parents=True, exist_ok=False)

    for uploaded_file in uploaded_files:
        relative_name = str(getattr(uploaded_file, "name", "")).replace("\\", "/").strip("/")
        if not relative_name:
            continue

        relative_path = Path(relative_name)
        safe_parts = [part for part in relative_path.parts if part not in {"", ".", ".."}]
        if not safe_parts:
            continue

        if len(safe_parts) == 1 and not _is_allowed_image_path(Path(safe_parts[0])) and Path(safe_parts[0]).suffix.lower() == ".json":
            destination = target_root.joinpath(*safe_parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(_uploaded_bytes(uploaded_file))
            continue

        destination = target_root.joinpath(*safe_parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(_uploaded_bytes(uploaded_file))

    return target_root