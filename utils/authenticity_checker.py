"""
Trip Authenticity Checker
Analyzes trip data for signs of manipulation, missing data, or suspicious patterns.
"""
from __future__ import annotations

import hashlib
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _parse_timestamp_naive_utc(ts: Any) -> datetime | None:
    """Parse any timestamp format to a naive UTC datetime."""
    if ts is None:
        return None
    try:
        if isinstance(ts, str):
            text = ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        elif isinstance(ts, (int, float)):
            return datetime.utcfromtimestamp(ts)
    except Exception:
        pass
    return None


def compute_trip_authenticity(
    metadata: dict[str, Any],
    actual_files: list[Path],
    gps_points: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Compute trip authenticity score (0-100%) and warnings.
    
    Returns:
        {
            "score": 94,
            "warnings": ["2 unexplained gaps", "metadata mismatch"],
            "details": {...}
        }
    """
    score = 100
    warnings = []
    details = {}

    # 1. Metadata integrity
    metadata_score, metadata_warnings, metadata_details = _check_metadata_integrity(
        metadata, actual_files
    )
    score += metadata_score
    warnings.extend(metadata_warnings)
    details["metadata"] = metadata_details

    # 2. GPS consistency
    gps_score, gps_warnings, gps_details = _check_gps_consistency(gps_points)
    score += gps_score
    warnings.extend(gps_warnings)
    details["gps"] = gps_details

    # 3. Temporal consistency
    temporal_score, temporal_warnings, temporal_details = _check_temporal_consistency(
        metadata, gps_points
    )
    score += temporal_score
    warnings.extend(temporal_warnings)
    details["temporal"] = temporal_details

    # 4. File integrity
    file_score, file_warnings, file_details = _check_file_integrity(actual_files, metadata)
    score += file_score
    warnings.extend(file_warnings)
    details["files"] = file_details

    # Clamp to 0-100
    score = max(0, min(100, score))

    return {
        "score": score,
        "warnings": warnings,
        "details": details,
    }


def _check_metadata_integrity(
    metadata: dict[str, Any], actual_files: list[Path]
) -> tuple[int, list[str], dict[str, Any]]:
    """Check if metadata matches actual files."""
    score = 0
    warnings = []
    details = {}

    claimed_total = metadata.get("total_images") or metadata.get("image_count")
    actual_count = len(actual_files)

    if claimed_total is not None:
        try:
            claimed_total = int(claimed_total)
            if claimed_total == actual_count:
                score += 25
                details["image_count_match"] = True
            else:
                diff = abs(claimed_total - actual_count)
                penalty = min(25, diff * 2)
                score += 25 - penalty
                warnings.append(
                    f"Image count mismatch: metadata claims {claimed_total}, found {actual_count}"
                )
                details["image_count_match"] = False
                details["claimed_images"] = claimed_total
                details["actual_images"] = actual_count
        except (ValueError, TypeError):
            warnings.append("Metadata total_images is not a valid number")
            details["image_count_match"] = False
    else:
        score += 15
        details["image_count_claimed"] = False

    # Check required fields
    required_fields = ["start_time", "end_time", "gps_available"]
    missing_fields = [f for f in required_fields if f not in metadata or metadata[f] is None]

    if not missing_fields:
        score += 10
        details["metadata_complete"] = True
    else:
        penalty = len(missing_fields) * 3
        score += max(0, 10 - penalty)
        warnings.append(f"Metadata missing fields: {', '.join(missing_fields)}")
        details["metadata_complete"] = False
        details["missing_fields"] = missing_fields

    return score, warnings, details


def _check_gps_consistency(
    gps_points: list[dict[str, Any]]
) -> tuple[int, list[str], dict[str, Any]]:
    """Check GPS data for suspicious patterns."""
    score = 0
    warnings = []
    details = {}

    if len(gps_points) < 2:
        score += 10
        details["insufficient_gps"] = True
        return score, warnings, details

    # Check for unexplained gaps
    median_interval = _compute_median_interval(gps_points)
    gaps = _find_unexplained_gaps(gps_points, median_interval)

    if len(gaps) == 0:
        score += 25
        details["unexplained_gaps"] = 0
    else:
        penalty = min(25, len(gaps) * 5)
        score += 25 - penalty
        warnings.append(f"{len(gaps)} unexplained GPS gap(s) detected")
        details["unexplained_gaps"] = len(gaps)
        details["gap_locations"] = [
            {
                "from": f"{g['from']['filename']}",
                "to": f"{g['to']['filename']}",
                "distance_km": round(g["distance_km"], 2),
                "time_hours": round(g["time_hours"], 2),
            }
            for g in gaps[:3]
        ]

    # Check for speed anomalies (> 200 km/h = impossible)
    speed_anomalies = _find_speed_anomalies(gps_points)
    if len(speed_anomalies) == 0:
        score += 15
        details["speed_anomalies"] = 0
    else:
        penalty = min(15, len(speed_anomalies) * 3)
        score += 15 - penalty
        warnings.append(f"{len(speed_anomalies)} physically impossible speed detected")
        details["speed_anomalies"] = len(speed_anomalies)

    # Check coordinate diversity
    unique_coords = len(set((p["latitude"], p["longitude"]) for p in gps_points))
    if unique_coords > len(gps_points) * 0.5:
        score += 10
        details["coordinate_diversity"] = True
    else:
        score += 5
        warnings.append("Low GPS coordinate diversity (possible stationary or synthetic data)")
        details["coordinate_diversity"] = False

    return score, warnings, details


def _check_temporal_consistency(
    metadata: dict[str, Any], gps_points: list[dict[str, Any]]
) -> tuple[int, list[str], dict[str, Any]]:
    """Check timestamp patterns."""
    score = 0
    warnings = []
    details = {}

    if len(gps_points) < 3:
        score += 10
        details["insufficient_temporal"] = True
        return score, warnings, details

    # Extract timestamps — normalize all to naive UTC
    timestamps = []
    for p in gps_points:
        dt = _parse_timestamp_naive_utc(p.get("timestamp"))
        if dt:
            timestamps.append(dt)

    if len(timestamps) < 3:
        score += 10
        details["parseable_timestamps"] = len(timestamps)
        return score, warnings, details

    # Check for future timestamps — all timestamps are naive UTC
    now = datetime.utcnow()
    future_ts = [t for t in timestamps if t > now]
    if len(future_ts) == 0:
        score += 15
        details["future_timestamps"] = 0
    else:
        score += 5
        warnings.append(f"{len(future_ts)} timestamp(s) in the future")
        details["future_timestamps"] = len(future_ts)

    # Check interval regularity (too regular = synthetic)
    intervals = [(timestamps[i+1] - timestamps[i]).total_seconds() for i in range(len(timestamps)-1)]
    avg_interval = sum(intervals) / len(intervals) if intervals else 0
    std_interval = (sum((x - avg_interval) ** 2 for x in intervals) / len(intervals)) ** 0.5 if intervals else 0

    if avg_interval > 0:
        cv = std_interval / avg_interval
        if cv < 0.05:
            score += 5
            warnings.append("Timestamps are suspiciously regular (possible synthetic data)")
            details["interval_regularity"] = "suspicious"
        elif cv < 0.3:
            score += 10
            details["interval_regularity"] = "moderate"
        else:
            score += 15
            details["interval_regularity"] = "natural"
    else:
        score += 5

    details["avg_interval_sec"] = round(avg_interval, 1)

    return score, warnings, details


def _check_file_integrity(
    actual_files: list[Path], metadata: dict[str, Any]
) -> tuple[int, list[str], dict[str, Any]]:
    """Check for duplicate files and naming consistency."""
    score = 0
    warnings = []
    details = {}

    # Check for duplicates
    file_hashes = []
    for f in actual_files[:50]:
        try:
            h = hashlib.md5(f.read_bytes()).hexdigest()
            file_hashes.append(h)
        except Exception:
            pass

    hash_counts = Counter(file_hashes)
    duplicates = sum(1 for count in hash_counts.values() if count > 1)

    if duplicates == 0:
        score += 20
        details["duplicate_files"] = 0
    else:
        penalty = min(20, duplicates * 5)
        score += 20 - penalty
        warnings.append(f"{duplicates} duplicate file(s) detected")
        details["duplicate_files"] = duplicates

    # Check file naming consistency
    if actual_files:
        extensions = [f.suffix.lower() for f in actual_files]
        ext_counts = Counter(extensions)
        most_common_ext, most_common_count = ext_counts.most_common(1)[0]
        consistency = most_common_count / len(actual_files)

        if consistency > 0.9:
            score += 10
            details["file_naming_consistent"] = True
        else:
            score += 5
            warnings.append(f"Mixed file formats ({', '.join(f'{k}: {v}' for k, v in ext_counts.items())})")
            details["file_naming_consistent"] = False

    return score, warnings, details


def _compute_median_interval(gps_points: list[dict[str, Any]]) -> float:
    """Compute median time interval between GPS points."""
    timestamps = []
    for p in gps_points:
        dt = _parse_timestamp_naive_utc(p.get("timestamp"))
        if dt:
            timestamps.append(dt)

    if len(timestamps) < 2:
        return 0

    timestamps.sort()
    intervals = [(timestamps[i+1] - timestamps[i]).total_seconds() for i in range(len(timestamps)-1)]
    intervals.sort()
    return intervals[len(intervals) // 2] if intervals else 0


def _find_unexplained_gaps(
    gps_points: list[dict[str, Any]], median_interval: float
) -> list[dict[str, Any]]:
    """Find gaps where GPS jumps without corresponding time gaps."""
    gaps = []
    distance_threshold_km = 0.1
    time_threshold_factor = 3.0

    for i in range(len(gps_points) - 1):
        p1 = gps_points[i]
        p2 = gps_points[i + 1]

        dist_km = _haversine_km(
            float(p1["latitude"]),
            float(p1["longitude"]),
            float(p2["latitude"]),
            float(p2["longitude"]),
        )

        dt1 = _parse_timestamp_naive_utc(p1.get("timestamp"))
        dt2 = _parse_timestamp_naive_utc(p2.get("timestamp"))
        if dt1 and dt2:
            time_diff_hours = abs((dt2 - dt1).total_seconds()) / 3600
        else:
            continue

        if (dist_km > distance_threshold_km and
            time_diff_hours < (median_interval * time_threshold_factor / 3600)):
            gaps.append({
                "from": p1,
                "to": p2,
                "distance_km": dist_km,
                "time_hours": time_diff_hours,
            })

    return gaps


def _find_speed_anomalies(gps_points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find segments where speed exceeds 200 km/h."""
    anomalies = []
    max_speed_kmh = 200

    for i in range(len(gps_points) - 1):
        p1 = gps_points[i]
        p2 = gps_points[i + 1]

        dist_km = _haversine_km(
            float(p1["latitude"]),
            float(p1["longitude"]),
            float(p2["latitude"]),
            float(p2["longitude"]),
        )

        ts1 = p1.get("timestamp")
        ts2 = p2.get("timestamp")
        if ts1 and ts2:
            try:
                if isinstance(ts1, str):
                    ts1 = ts1.replace("Z", "+00:00")
                    dt1 = datetime.fromisoformat(ts1)
                else:
                    dt1 = datetime.fromtimestamp(float(ts1))

                if isinstance(ts2, str):
                    ts2 = ts2.replace("Z", "+00:00")
                    dt2 = datetime.fromisoformat(ts2)
                else:
                    dt2 = datetime.fromtimestamp(float(ts2))

                time_diff_hours = abs((dt2 - dt1).total_seconds()) / 3600

                if time_diff_hours > 0:
                    speed_kmh = dist_km / time_diff_hours
                    if speed_kmh > max_speed_kmh:
                        anomalies.append({
                            "from": p1,
                            "to": p2,
                            "speed_kmh": speed_kmh,
                        })
            except Exception:
                pass

    return anomalies


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in km between two GPS coordinates."""
    radius_km = 6371.0
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)

    delta_lat = lat2_rad - lat1_rad
    delta_lon = lon2_rad - lon1_rad

    a = math.sin(delta_lat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    return 2 * radius_km * math.atan2(math.sqrt(a), math.sqrt(1 - a))
