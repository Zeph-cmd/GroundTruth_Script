from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import radians, sin, cos, sqrt, atan2
from pathlib import Path
from typing import Any


def _first_present(data: dict[str, Any], keys: list[str]) -> Any | None:
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return None


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None

    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        normalized = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                return datetime.fromtimestamp(float(text), tz=timezone.utc)
            except ValueError:
                return None
    return None


def _collect_image_records(data: Any, records: list[dict[str, Any]]) -> None:
    if isinstance(data, dict):
        filename = _first_present(data, ["filename", "file_name", "name", "image"])
        latitude = _coerce_float(_first_present(data, ["latitude", "lat", "gps_latitude"]))
        longitude = _coerce_float(_first_present(data, ["longitude", "lon", "lng", "gps_longitude"]))
        timestamp = _first_present(data, ["timestamp", "time", "datetime", "captured_at"])

        if filename is not None:
            records.append(
                {
                    "filename": str(filename),
                    "timestamp": timestamp,
                    "latitude": latitude,
                    "longitude": longitude,
                }
            )

        for value in data.values():
            _collect_image_records(value, records)
        return

    if isinstance(data, list):
        for item in data:
            _collect_image_records(item, records)


def _dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, Any, Any, Any]] = set()
    deduped: list[dict[str, Any]] = []

    for record in records:
        key = (
            record["filename"].lower(),
            record.get("timestamp"),
            record.get("latitude"),
            record.get("longitude"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)

    return deduped


def _valid_timestamps(records: list[dict[str, Any]]) -> list[datetime]:
    timestamps = []
    for record in records:
        parsed = _parse_timestamp(record.get("timestamp"))
        if parsed is not None:
            timestamps.append(parsed)
    return sorted(timestamps)


def _valid_gps_points(records: list[dict[str, Any]]) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for record in records:
        latitude = _coerce_float(record.get("latitude"))
        longitude = _coerce_float(record.get("longitude"))
        if latitude is None or longitude is None:
            continue
        if -90 <= latitude <= 90 and -180 <= longitude <= 180:
            points.append((latitude, longitude))
    return points


def _image_count(metadata: dict[str, Any], records: list[dict[str, Any]]) -> int:
    count = metadata.get("total_images")
    if isinstance(count, int) and count >= 0:
        return count
    return len(records)


def calculate_trip_duration(metadata: dict[str, Any]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    _collect_image_records(metadata, records)
    records = _dedupe_records(records)
    timestamps = _valid_timestamps(records)

    if not timestamps:
        return {"start_time": None, "end_time": None, "duration_minutes": 0.0, "image_count": len(records)}

    start_time = timestamps[0]
    end_time = timestamps[-1]
    duration_minutes = max(0.0, (end_time - start_time).total_seconds() / 60.0)
    return {
        "start_time": start_time,
        "end_time": end_time,
        "duration_minutes": round(duration_minutes, 2),
        "image_count": len(records),
    }


def calculate_image_intervals(metadata: dict[str, Any]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    _collect_image_records(metadata, records)
    records = _dedupe_records(records)
    timestamps = _valid_timestamps(records)

    if len(timestamps) < 2:
        return {
            "average_seconds": 0.0,
            "min_seconds": 0.0,
            "max_seconds": 0.0,
            "intervals": [],
        }

    intervals = [max(0.0, (current - previous).total_seconds()) for previous, current in zip(timestamps, timestamps[1:])]
    average_seconds = sum(intervals) / len(intervals)
    return {
        "average_seconds": round(average_seconds, 2),
        "min_seconds": round(min(intervals), 2),
        "max_seconds": round(max(intervals), 2),
        "intervals": [round(interval, 2) for interval in intervals],
    }


def estimate_distance_from_gps(metadata: dict[str, Any]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    _collect_image_records(metadata, records)
    records = _dedupe_records(records)
    gps_points = _valid_gps_points(records)

    if len(gps_points) < 2:
        return {"distance_km": 0.0, "avg_speed_kmh": 0.0, "gps_points": len(gps_points)}

    distance_km = 0.0
    for (first_lat, first_lon), (second_lat, second_lon) in zip(gps_points, gps_points[1:]):
        radius_km = 6371.0
        lat1 = radians(first_lat)
        lon1 = radians(first_lon)
        lat2 = radians(second_lat)
        lon2 = radians(second_lon)
        delta_lat = lat2 - lat1
        delta_lon = lon2 - lon1
        a = sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
        distance_km += 2 * radius_km * atan2(sqrt(a), sqrt(1 - a))

    duration = calculate_trip_duration(metadata)
    duration_hours = max(duration["duration_minutes"] / 60.0, 1e-6)
    avg_speed_kmh = distance_km / duration_hours

    return {
        "distance_km": round(distance_km, 2),
        "avg_speed_kmh": round(avg_speed_kmh, 2),
        "gps_points": len(gps_points),
    }


def detect_data_gaps(metadata: dict[str, Any]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    _collect_image_records(metadata, records)
    records = _dedupe_records(records)

    total_images = _image_count(metadata, records)
    timestamps = _valid_timestamps(records)
    gps_points = _valid_gps_points(records)

    missing_timestamps = max(0, total_images - len(timestamps))
    missing_gps_points = max(0, total_images - len(gps_points))

    irregular_intervals = 0
    if len(timestamps) >= 3:
        intervals = [(current - previous).total_seconds() for previous, current in zip(timestamps, timestamps[1:]) if current >= previous]
        if intervals:
            median_interval = sorted(intervals)[len(intervals) // 2]
            irregular_intervals = sum(1 for interval in intervals if median_interval > 0 and abs(interval - median_interval) / median_interval > 0.75)

    coverage_penalty = 0
    if total_images:
        coverage_penalty += int(40 * (missing_gps_points / total_images))
        coverage_penalty += int(35 * (missing_timestamps / total_images))
        coverage_penalty += min(25, irregular_intervals * 3)

    score = max(0, min(100, 100 - coverage_penalty))

    return {
        "missing_gps_points": missing_gps_points,
        "missing_timestamps": missing_timestamps,
        "irregular_intervals": irregular_intervals,
        "quality_score": score,
        "total_images": total_images,
    }