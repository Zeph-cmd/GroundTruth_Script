from pathlib import Path


TRIPS_DIR = Path("trips")


def get_trip_folders() -> list[str]:
    if not TRIPS_DIR.exists():
        return []

    return sorted(item.name for item in TRIPS_DIR.iterdir() if item.is_dir())