from __future__ import annotations

import hashlib
import logging
import shutil
import zipfile
from pathlib import Path

TRIPS_DIR = Path("trips")
EXTRACTED_TRIPS_DIR = TRIPS_DIR / ".extracted"

logger = logging.getLogger(__name__)


def _metadata_trip_directories(root: Path) -> list[Path]:
    if not root.exists():
        return []

    trip_directories: list[Path] = []
    for metadata_path in root.rglob("metadata.json"):
        trip_directory = metadata_path.parent
        if trip_directory.is_dir():
            trip_directories.append(trip_directory)
    return trip_directories


def _archive_signature(archive_path: Path) -> str:
    stat_result = archive_path.stat()
    payload = f"{archive_path.resolve()}::{stat_result.st_mtime_ns}::{stat_result.st_size}"
    return hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()


def _safe_extract_zip(archive_path: Path) -> Path:
    signature = _archive_signature(archive_path)
    extract_root = EXTRACTED_TRIPS_DIR / f"{archive_path.stem}_{signature[:12]}"
    marker_path = extract_root / ".archive_signature"

    if extract_root.exists() and marker_path.exists() and marker_path.read_text(encoding="utf-8") == signature:
        return extract_root

    if extract_root.exists():
        shutil.rmtree(extract_root)

    extract_root.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive_path, "r") as archive:
        for member in archive.infolist():
            member_path = Path(member.filename)
            if member_path.is_absolute():
                continue

            target_path = (extract_root / member_path).resolve()
            if extract_root.resolve() not in target_path.parents and target_path != extract_root.resolve():
                continue

            if member.is_dir():
                target_path.mkdir(parents=True, exist_ok=True)
                continue

            target_path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member, "r") as source_handle, target_path.open("wb") as target_handle:
                shutil.copyfileobj(source_handle, target_handle)

    marker_path.write_text(signature, encoding="utf-8")
    logger.debug("Extracted archive %s to %s", archive_path, extract_root)
    return extract_root


def _discover_trip_directories() -> list[Path]:
    trip_directories = {trip_directory.resolve() for trip_directory in _metadata_trip_directories(TRIPS_DIR)}

    if TRIPS_DIR.exists():
        for archive_path in TRIPS_DIR.rglob("*.zip"):
            if archive_path.is_file():
                extracted_root = _safe_extract_zip(archive_path)
                trip_directories.update(trip_directory.resolve() for trip_directory in _metadata_trip_directories(extracted_root))

    return sorted((Path(path) for path in trip_directories), key=lambda item: item.as_posix().lower())

def get_trip_folders() -> list[Path]:
    return _discover_trip_directories()