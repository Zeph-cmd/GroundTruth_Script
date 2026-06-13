from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from pathlib import Path
from typing import Any

import folium

from utils.image_loader import get_trip_images


@dataclass(frozen=True)
class GPSPoint:
    filename: str
    latitude: float
    longitude: float
    timestamp: object | None
    path: Path | None = None


def _first_present(data: dict[str, Any], keys: list[str]) -> Any | None:
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return None


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None

    return number


def _coerce_timestamp(value: Any) -> Any | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float)):
        return value
    return str(value)


def _timestamp_sort_key(value: Any) -> tuple[int, str]:
    if isinstance(value, datetime):
        return 0, value.isoformat()

    if isinstance(value, (int, float)):
        return 0, datetime.fromtimestamp(float(value)).isoformat()

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 1, ""

        normalized = text.replace("Z", "+00:00")
        try:
            return 0, datetime.fromisoformat(normalized).isoformat()
        except ValueError:
            try:
                return 0, datetime.fromtimestamp(float(text)).isoformat()
            except ValueError:
                return 1, text

    return 1, str(value)


def _parse_image_lookup(trip_path: str | Path | None) -> dict[str, Path]:
    if trip_path is None:
        return {}

    image_lookup: dict[str, Path] = {}
    for image_path in get_trip_images(trip_path):
        image_lookup[image_path.name.lower()] = image_path
    return image_lookup


def _extract_points_from_object(data: Any, image_lookup: dict[str, Path], points: list[GPSPoint], seen: set[tuple[str, float, float, object | None]]) -> None:
    if isinstance(data, dict):
        latitude = _coerce_float(_first_present(data, ["latitude", "lat", "gps_latitude", "y"]))
        longitude = _coerce_float(_first_present(data, ["longitude", "lon", "lng", "gps_longitude", "x"]))
        filename = _first_present(data, ["filename", "file_name", "name", "image", "path"])

        if filename is not None:
            filename_text = str(filename).strip()
            if latitude is not None and longitude is not None and filename_text:
                if -90 <= latitude <= 90 and -180 <= longitude <= 180:
                    timestamp = _coerce_timestamp(_first_present(data, ["timestamp", "time", "datetime", "captured_at"]))
                    key = (filename_text.lower(), latitude, longitude, timestamp)
                    if key not in seen:
                        seen.add(key)
                        points.append(
                            GPSPoint(
                                filename=filename_text,
                                latitude=latitude,
                                longitude=longitude,
                                timestamp=timestamp,
                                path=image_lookup.get(filename_text.lower()),
                            )
                        )

        for value in data.values():
            _extract_points_from_object(value, image_lookup, points, seen)
        return

    if isinstance(data, list):
        for item in data:
            _extract_points_from_object(item, image_lookup, points, seen)


def extract_gps_points(metadata: dict[str, Any] | None, trip_path: str | Path | None = None) -> list[dict[str, Any]]:
    if not isinstance(metadata, dict):
        return []

    image_lookup = _parse_image_lookup(trip_path)
    points: list[GPSPoint] = []
    seen: set[tuple[str, float, float, object | None]] = set()
    _extract_points_from_object(metadata, image_lookup, points, seen)

    points.sort(key=lambda point: (_timestamp_sort_key(point.timestamp), point.filename.lower()))
    return [point.__dict__ for point in points]


def _sample_points(points: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if len(points) <= limit:
        return points

    step = max(1, math.ceil(len(points) / limit))
    return points[::step][:limit]


def _haversine_km(first_lat: float, first_lon: float, second_lat: float, second_lon: float) -> float:
    radius_km = 6371.0
    lat1 = math.radians(first_lat)
    lon1 = math.radians(first_lon)
    lat2 = math.radians(second_lat)
    lon2 = math.radians(second_lon)

    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    a = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    return 2 * radius_km * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _estimate_distance_km(points: list[dict[str, Any]]) -> float:
    distance = 0.0
    for previous, current in zip(points, points[1:]):
        distance += _haversine_km(
            float(previous["latitude"]),
            float(previous["longitude"]),
            float(current["latitude"]),
            float(current["longitude"]),
        )
    return distance


def build_trip_map(
    points: list[dict[str, Any]],
    *,
    show_markers: bool = True,
    show_route: bool = True,
    marker_sample_limit: int = 2000,
    route_sample_limit: int = 5000,
) -> dict[str, Any]:
    valid_points = [
        point
        for point in points
        if _coerce_float(point.get("latitude")) is not None and _coerce_float(point.get("longitude")) is not None
    ]

    if not valid_points:
        return {
            "map": None,
            "summary": {
                "total_points": 0,
                "estimated_distance_km": 0.0,
                "start_coordinates": None,
                "end_coordinates": None,
            },
            "rendered_points": [],
            "sampled": False,
        }

    ordered_points = sorted(
        valid_points,
        key=lambda point: (_timestamp_sort_key(point.get("timestamp")), str(point.get("filename") or "").lower()),
    )

    average_latitude = sum(float(point["latitude"]) for point in ordered_points) / len(ordered_points)
    average_longitude = sum(float(point["longitude"]) for point in ordered_points) / len(ordered_points)

    map_object = folium.Map(
        location=[average_latitude, average_longitude],
        zoom_start=13,
        control_scale=True,
        prefer_canvas=True,
    )

    if show_route:
        route_points = _sample_points(ordered_points, route_sample_limit)
        folium.PolyLine(
            locations=[[float(point["latitude"]), float(point["longitude"])] for point in route_points],
            color="#1f77b4",
            weight=4,
            opacity=0.8,
        ).add_to(map_object)

    rendered_points = _sample_points(ordered_points, marker_sample_limit) if show_markers else []
    sampled = len(rendered_points) < len(ordered_points)

    if show_markers:
        marker_group = folium.FeatureGroup(name="Image Markers", show=True)
        for point in rendered_points:
            latitude = float(point["latitude"])
            longitude = float(point["longitude"])
            filename = str(point["filename"])
            timestamp = point.get("timestamp")
            popup_html = (
                f"<strong>Filename:</strong> {filename}<br>"
                f"<strong>Timestamp:</strong> {timestamp if timestamp is not None else 'Not available'}<br>"
                f"<strong>Latitude:</strong> {latitude}<br>"
                f"<strong>Longitude:</strong> {longitude}"
            )
            folium.Marker(
                location=[latitude, longitude],
                tooltip=filename,
                popup=folium.Popup(popup_html, max_width=320),
                icon=folium.Icon(color="blue", icon="camera", prefix="fa"),
            ).add_to(marker_group)
        marker_group.add_to(map_object)

    bounds = [
        [min(float(point["latitude"]) for point in ordered_points), min(float(point["longitude"]) for point in ordered_points)],
        [max(float(point["latitude"]) for point in ordered_points), max(float(point["longitude"]) for point in ordered_points)],
    ]
    map_object.fit_bounds(bounds)

    summary = {
        "total_points": len(ordered_points),
        "estimated_distance_km": round(_estimate_distance_km(ordered_points), 2),
        "start_coordinates": (float(ordered_points[0]["latitude"]), float(ordered_points[0]["longitude"])),
        "end_coordinates": (float(ordered_points[-1]["latitude"]), float(ordered_points[-1]["longitude"])),
    }

    return {
        "map": map_object,
        "summary": summary,
        "rendered_points": rendered_points,
        "sampled": sampled,
    }