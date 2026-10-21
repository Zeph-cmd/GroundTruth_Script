from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from pathlib import Path
from typing import Any

import json
import urllib.request
import urllib.error
import folium
from functools import lru_cache

from utils.image_loader import get_trip_images


@lru_cache(maxsize=100)
def reverse_geocode(latitude: float, longitude: float) -> str:
    """Get comprehensive area/landmark name from GPS coordinates using Nominatim"""
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?lat={latitude}&lon={longitude}&format=json&zoom=16"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "GroundTruth/1.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            address = data.get("address", {})

            # Build a comprehensive location string with multiple levels
            parts = []

            # Specific location (road, suburb, neighbourhood)
            specific = (
                address.get("road") or
                address.get("suburb") or
                address.get("neighbourhood") or
                address.get("quarter")
            )
            if specific:
                parts.append(specific)

            # City/town level
            city = (
                address.get("city") or
                address.get("town") or
                address.get("village") or
                address.get("hamlet")
            )
            if city:
                parts.append(city)

            # District/municipal/metropolitan level
            district = (
                address.get("district") or
                address.get("municipality") or
                address.get("municipal_district") or
                address.get("metropolitan_area") or
                address.get("county")
            )
            if district:
                parts.append(district)

            # Region/state level
            region = (
                address.get("state") or
                address.get("region") or
                address.get("province")
            )
            if region:
                parts.append(region)

            # Country
            country = address.get("country")
            if country:
                parts.append(country)

            if parts:
                return ", ".join(parts)

            # Fallback to display name (truncated)
            display_name = data.get("display_name", "")
            if display_name:
                # Take the first 4 meaningful parts
                parts = [p.strip() for p in display_name.split(",")[:4]]
                return ", ".join(parts)
    except Exception:
        pass
    return "Unknown location"


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

    # Treat 0 as invalid GPS (coordinates in the Gulf of Guinea)
    if number == 0.0:
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
        filename = _first_present(data, ["filename", "file_name", "file", "name", "image", "path"])

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


STALE_FIX_SPEED_KMH = 150.0


def _point_epoch(point: dict[str, Any]) -> float | None:
    """Timestamp as epoch seconds, or None when unusable for speed math."""
    value = point.get("timestamp")
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        normalized = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized).timestamp()
        except ValueError:
            try:
                return float(normalized)
            except ValueError:
                return None
    return None


def _split_stale_fixes_once(ordered_points: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One pass: flag points whose jump to the NEXT point is impossibly fast.

    A stale GPS fix (leftover lock from a previous location) shows up as a
    large jump that immediately snaps back - implied ground speed far above
    any real travel. Devices often freeze on the stale fix for several
    captures, so the whole identical-coordinate run behind a flagged point is
    flagged with it. Only map rendering is affected; no data is removed.
    """
    if len(ordered_points) < 2:
        return list(ordered_points), []

    route_points: list[dict[str, Any]] = []
    stale_fixes: list[dict[str, Any]] = []
    flagged_flags = [False] * len(ordered_points)

    for index, point in enumerate(ordered_points[:-1]):
        nxt = ordered_points[index + 1]
        distance_km = _haversine_km(
            float(point["latitude"]), float(point["longitude"]),
            float(nxt["latitude"]), float(nxt["longitude"]),
        )
        start_ts = _point_epoch(point)
        end_ts = _point_epoch(nxt)
        delta_hours = ((end_ts - start_ts) / 3600.0) if (start_ts is not None and end_ts is not None) else None
        if delta_hours is not None and delta_hours > 0 and (distance_km / delta_hours) > STALE_FIX_SPEED_KMH:
            flagged_flags[index] = True
            # The frozen-fix run: identical coordinates trailing this point.
            walk = index - 1
            while walk >= 0 and not flagged_flags[walk]:
                prev = ordered_points[walk]
                if _haversine_km(
                    float(prev["latitude"]), float(prev["longitude"]),
                    float(point["latitude"]), float(point["longitude"]),
                ) * 1000.0 > 30.0:
                    break
                flagged_flags[walk] = True
                walk -= 1

    for index, point in enumerate(ordered_points[:-1]):
        if flagged_flags[index]:
            stale_fixes.append(point)
        else:
            route_points.append(point)
    route_points.append(ordered_points[-1])
    return route_points, stale_fixes


def _split_stale_fixes(ordered_points: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Repeat the stale-fix pass until stable (handles chained stale fixes)."""
    route_points = list(ordered_points)
    all_stale: list[dict[str, Any]] = []
    for _ in range(5):
        route_points, stale = _split_stale_fixes_once(route_points)
        if not stale:
            break
        all_stale.extend(stale)
    return route_points, all_stale


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


def _compute_coverage(ordered_points: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute route coverage score: how much of the route was actually recorded."""
    if len(ordered_points) < 2:
        return {
            "route_length_km": 0.0,
            "captured_km": 0.0,
            "coverage_pct": 100.0 if len(ordered_points) == 1 else 0.0,
            "longest_gap_m": 0.0,
        }

    # Compute segment distances between consecutive GPS points
    segment_distances = []
    for previous, current in zip(ordered_points, ordered_points[1:]):
        dist = _haversine_km(
            float(previous["latitude"]),
            float(previous["longitude"]),
            float(current["latitude"]),
            float(current["longitude"]),
        )
        segment_distances.append(dist)

    route_length_km = sum(segment_distances)
    if route_length_km == 0:
        return {
            "route_length_km": 0.0,
            "captured_km": 0.0,
            "coverage_pct": 100.0,
            "longest_gap_m": 0.0,
        }

    # Determine what counts as an "unrecorded gap".
    # Use the median segment distance as baseline for normal photo spacing.
    # Segments significantly larger than the median are considered gaps.
    sorted_dists = sorted(segment_distances)
    median_dist_km = sorted_dists[len(sorted_dists) // 2]
    # A gap is any segment > 3x the median spacing (or > 100m absolute minimum)
    gap_threshold_km = max(median_dist_km * 3, 0.1)

    gap_distances_km = [d for d in segment_distances if d > gap_threshold_km]
    unrecorded_km = sum(gap_distances_km)
    captured_km = route_length_km - unrecorded_km
    coverage_pct = (captured_km / route_length_km) * 100
    longest_gap_m = (max(gap_distances_km) * 1000) if gap_distances_km else 0.0

    return {
        "route_length_km": round(route_length_km, 1),
        "captured_km": round(max(captured_km, 0), 1),
        "coverage_pct": round(coverage_pct, 1),
        "longest_gap_m": round(longest_gap_m, 0),
    }


def build_trip_map(
    points: list[dict[str, Any]],
    *,
    show_markers: bool = True,
    show_route: bool = True,
    imagery: bool = False,
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
    route_points, stale_fixes = _split_stale_fixes(ordered_points)
    if not route_points:
        route_points = list(ordered_points)

    average_latitude = sum(float(point["latitude"]) for point in ordered_points) / len(ordered_points)
    average_longitude = sum(float(point["longitude"]) for point in ordered_points) / len(ordered_points)

    map_object = folium.Map(
        location=[average_latitude, average_longitude],
        zoom_start=13,
        control_scale=True,
        prefer_canvas=True,
        tiles=None if imagery else "OpenStreetMap",
    )

    if imagery:
        folium.TileLayer(
            tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            attr="Esri, Maxar, Earthstar Geographics, and the GIS User Community",
            name="Satellite imagery",
            overlay=False,
            control=True,
        ).add_to(map_object)

    if show_route:
        route_line_points = _sample_points(route_points, route_sample_limit)
        folium.PolyLine(
            locations=[[float(point["latitude"]), float(point["longitude"])] for point in route_line_points],
            color="#1f77b4",
            weight=4,
            opacity=0.8,
        ).add_to(map_object)

    rendered_points = _sample_points(route_points, marker_sample_limit) if show_markers else []
    sampled = len(rendered_points) < len(route_points)

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
            folium.CircleMarker(
                location=[latitude, longitude],
                tooltip=filename,
                popup=folium.Popup(popup_html, max_width=320),
                radius=8,
                color="#ffffff",
                weight=2,
                fill=True,
                fill_color="#007bff",
                fill_opacity=1.0,
            ).add_to(marker_group)
        marker_group.add_to(map_object)

    # Add start and end markers with offset to prevent overlap
    start_point = route_points[0]
    end_point = route_points[-1]
    start_lat, start_lon = float(start_point["latitude"]), float(start_point["longitude"])
    end_lat, end_lon = float(end_point["latitude"]), float(end_point["longitude"])

    if len(ordered_points) > 1:
        # Offset markers slightly to prevent overlap
        offset = 0.0001
        start_marker_lat = start_lat - offset
        start_marker_lon = start_lon - offset

        end_marker_lat = end_lat + offset
        end_marker_lon = end_lon + offset

        folium.Marker(
            location=[start_marker_lat, start_marker_lon],
            tooltip="Start",
            popup=folium.Popup(f"<strong>Start:</strong> {start_point['filename']}", max_width=320),
            icon=folium.Icon(color="green", icon="play", prefix="fa"),
        ).add_to(map_object)

        folium.Marker(
            location=[end_marker_lat, end_marker_lon],
            tooltip="End",
            popup=folium.Popup(f"<strong>End:</strong> {end_point['filename']}", max_width=320),
            icon=folium.Icon(color="red", icon="stop", prefix="fa"),
        ).add_to(map_object)

    # Stale GPS fixes: kept visible (orange) but excluded from route/bounds.
    for stale_point in stale_fixes:
        folium.CircleMarker(
            location=[float(stale_point["latitude"]), float(stale_point["longitude"])],
            tooltip=f"Stale GPS fix (excluded from route): {stale_point.get('filename')}",
            popup=folium.Popup(
                f"<strong>Stale GPS fix</strong><br>"
                f"<strong>Filename:</strong> {stale_point.get('filename')}<br>"
                f"This point implies impossible movement speed and was likely "
                f"recorded before the device acquired a fresh GPS lock. "
                f"Excluded from the route drawing only - the data is kept.",
                max_width=320,
            ),
            radius=8,
            color="#ffffff",
            weight=2,
            fill=True,
            fill_color="#ff8800",
            fill_opacity=1.0,
        ).add_to(map_object)

    bounds = [
        [min(float(point["latitude"]) for point in route_points), min(float(point["longitude"]) for point in route_points)],
        [max(float(point["latitude"]) for point in route_points), max(float(point["longitude"]) for point in route_points)],
    ]
    map_object.fit_bounds(bounds)

    # Get area name from start coordinates
    area_name = reverse_geocode(start_lat, start_lon)

    # Compute coverage metrics (physically plausible points only)
    coverage = _compute_coverage(route_points)

    summary = {
        "total_points": len(ordered_points),
        "stale_fixes": len(stale_fixes),
        "estimated_distance_km": round(_estimate_distance_km(route_points), 2),
        "start_coordinates": (start_lat, start_lon),
        "end_coordinates": (end_lat, end_lon),
        "area_name": area_name,
        "coverage": coverage,
    }

    return {
        "map": map_object,
        "summary": summary,
        "rendered_points": rendered_points,
        "sampled": sampled,
    }