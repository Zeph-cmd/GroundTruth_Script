from __future__ import annotations

from pathlib import Path


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


def get_trip_images(path: str | Path) -> list[Path]:
    trip_path = Path(path)
    if not trip_path.exists():
        return []

    return sorted(
        item for item in trip_path.rglob("*") if item.is_file() and item.suffix.lower() in IMAGE_EXTENSIONS
    )