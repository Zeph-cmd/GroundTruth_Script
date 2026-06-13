import logging
from datetime import datetime
from pathlib import Path

import streamlit as st
from PIL import Image
import plotly.graph_objects as go
from streamlit_folium import st_folium

from utils.analytics import (
    calculate_image_intervals,
    calculate_trip_duration,
    detect_data_gaps,
    estimate_distance_from_gps,
)
from utils.share_export import ShareExportError, export_full_dataset, export_lightweight_package, export_selected_trip, share_file
from utils.metadata_loader import load_trip_metadata
from utils.map_loader import build_trip_map, extract_gps_points
from utils.image_loader import get_trip_images
from utils.trip_loader import get_trip_folders
from utils.yolo_detector import YOLODetectorError, detect_objects


TRIPS_DIR = Path("trips")
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value)

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return datetime.min

        normalized = text.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            try:
                return datetime.fromtimestamp(float(text))
            except ValueError:
                return datetime.min

    return datetime.min


def _extract_timestamp_from_metadata(metadata: dict | None, image_name: str) -> object | None:
    if not isinstance(metadata, dict):
        return None

    candidates = [
        metadata.get("images"),
        metadata.get("frames"),
        metadata.get("photos"),
        metadata.get("items"),
        metadata.get("records"),
    ]

    normalized_name = image_name.lower()

    for candidate in candidates:
        if isinstance(candidate, dict):
            for key, value in candidate.items():
                if str(key).lower() == normalized_name and isinstance(value, dict):
                    return value.get("timestamp") or value.get("time") or value.get("datetime")

        if isinstance(candidate, list):
            for entry in candidate:
                if not isinstance(entry, dict):
                    continue

                entry_name = str(
                    entry.get("filename")
                    or entry.get("file_name")
                    or entry.get("name")
                    or entry.get("image")
                    or ""
                ).lower()
                if entry_name == normalized_name:
                    return entry.get("timestamp") or entry.get("time") or entry.get("datetime")

    for key in ("timestamp", "time", "datetime"):
        value = metadata.get(key)
        if isinstance(value, dict):
            entry = value.get(image_name) or value.get(image_name.lower())
            if isinstance(entry, dict):
                return entry.get("timestamp") or entry.get("time") or entry.get("datetime")

    return None


def _extract_lat_lon(metadata: dict | None, image_name: str) -> tuple[object | None, object | None]:
    if not isinstance(metadata, dict):
        return None, None

    candidates = [
        metadata.get("images"),
        metadata.get("frames"),
        metadata.get("photos"),
        metadata.get("items"),
        metadata.get("records"),
    ]

    normalized_name = image_name.lower()

    for candidate in candidates:
        if isinstance(candidate, dict):
            for key, value in candidate.items():
                if str(key).lower() == normalized_name and isinstance(value, dict):
                    return value.get("latitude") or value.get("lat"), value.get("longitude") or value.get("lon")

        if isinstance(candidate, list):
            for entry in candidate:
                if not isinstance(entry, dict):
                    continue

                entry_name = str(
                    entry.get("filename")
                    or entry.get("file_name")
                    or entry.get("name")
                    or entry.get("image")
                    or ""
                ).lower()
                if entry_name == normalized_name:
                    return entry.get("latitude") or entry.get("lat"), entry.get("longitude") or entry.get("lon")

    return None, None


def _build_image_records(trip_path: Path, metadata: dict | None) -> list[dict]:
    records = []
    for image_path in get_trip_images(trip_path):
        timestamp = _extract_timestamp_from_metadata(metadata, image_path.name)
        records.append(
            {
                "path": image_path,
                "name": image_path.name,
                "timestamp": timestamp,
                "timestamp_sort": _parse_datetime(timestamp),
            }
        )
    return records


def _format_value(value: object | None) -> str:
    if value is None:
        return "Not available"
    return str(value)


def _format_confidence(value: object | None) -> str:
    if value is None:
        return "0%"
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return "0%"
    return f"{confidence * 100:.0f}%"


def _confidence_percent(value: float) -> str:
    return f"{value * 100:.0f}%"


def _image_signature(image_path: Path) -> tuple[int, int]:
    stat_result = image_path.stat()
    return stat_result.st_mtime_ns, stat_result.st_size


@st.cache_data(show_spinner=False)
def _cached_yolo_detections(image_path: str, signature: tuple[int, int]) -> dict[str, object]:
    detections = detect_objects(image_path)
    return {
        "status": "ok",
        "detections": detections,
        "count": len(detections),
        "signature": signature,
    }


def _annotate_image(image_path: Path, detections: list[dict[str, object]]) -> Image.Image:
    try:
        import cv2
    except Exception as exc:
        raise YOLODetectorError(
            "opencv-python is not available. Install dependencies from requirements.txt to enable bounding-box overlays."
        ) from exc

    image_bgr = cv2.imread(str(image_path))
    if image_bgr is None:
        raise YOLODetectorError("Unable to load image for annotation.")

    for detection in detections:
        x1, y1, x2, y2 = [int(value) for value in detection["bbox"]]
        label = str(detection["label"])
        confidence = float(detection["confidence"])
        caption = f"{label} {_confidence_percent(confidence)}"

        cv2.rectangle(image_bgr, (x1, y1), (x2, y2), (0, 255, 0), 2)
        ((text_width, text_height), baseline) = cv2.getTextSize(caption, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
        top = max(0, y1 - text_height - baseline - 6)
        cv2.rectangle(image_bgr, (x1, top), (x1 + text_width + 10, y1), (0, 255, 0), -1)
        cv2.putText(image_bgr, caption, (x1 + 5, y1 - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 2)

    image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(image_rgb)


def _render_ai_detection_results(image_path: Path) -> None:
    try:
        image_signature = _image_signature(image_path)
    except Exception:
        st.warning("AI Detection Results")
        st.info("Unable to load image for detection.")
        return

    try:
        detection_state = _cached_yolo_detections(str(image_path), image_signature)
    except YOLODetectorError as exc:
        st.warning("AI Detection Results")
        st.info(str(exc))
        return
    except Exception:
        st.warning("AI Detection Results")
        st.info("Detection unavailable due to an unexpected error.")
        logger.exception("Unexpected YOLO detection failure for %s", image_path)
        return

    detections = detection_state.get("detections", [])
    st.markdown("**AI Detection Results**")
    st.metric("Number of objects detected", str(detection_state.get("count", 0)))

    if not detections:
        st.info("No objects detected")
        return

    for detection in detections:
        st.write(
            f"- {detection['label']} | confidence: {_confidence_percent(float(detection['confidence']))}"
        )


def _as_utc_datetime(value: object | None) -> datetime | None:
    parsed = _parse_datetime(value)
    if parsed == datetime.min:
        return None
    return parsed


def _build_analytics_chart_data(image_records: list[dict], gps_points: list[dict]) -> tuple[list[datetime], list[int], list[float]]:
    timestamps = [record["timestamp_sort"] for record in image_records if record.get("timestamp_sort") != datetime.min]
    timestamps = sorted(timestamp for timestamp in timestamps if isinstance(timestamp, datetime))

    running_counts = list(range(1, len(timestamps) + 1))

    gps_timestamps = [point.get("timestamp") for point in gps_points]
    gps_datetimes = [parsed for parsed in (_as_utc_datetime(timestamp) for timestamp in gps_timestamps) if parsed is not None]

    return timestamps, running_counts, gps_datetimes


def _build_time_chart(timestamps: list[datetime], running_counts: list[int]):
    figure = go.Figure()
    if timestamps:
        figure.add_trace(go.Scatter(x=timestamps, y=running_counts, mode="lines+markers", name="Image Count"))
    figure.update_layout(
        title="Time vs Image Count",
        xaxis_title="Time",
        yaxis_title="Cumulative Images",
        height=320,
        margin=dict(l=10, r=10, t=40, b=10),
    )
    return figure


def _build_gps_density_chart(gps_datetimes: list[datetime]):
    figure = go.Figure()
    if gps_datetimes:
        bucket_counts: dict[str, int] = {}
        for timestamp in gps_datetimes:
            bucket = timestamp.strftime("%Y-%m-%d %H:00")
            bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

        ordered_buckets = sorted(bucket_counts.items())
        figure.add_trace(
            go.Bar(x=[bucket for bucket, _ in ordered_buckets], y=[count for _, count in ordered_buckets], name="GPS Density")
        )
    figure.update_layout(
        title="GPS Density Over Time",
        xaxis_title="Time Bucket",
        yaxis_title="GPS Points",
        height=320,
        margin=dict(l=10, r=10, t=40, b=10),
    )
    return figure


@st.cache_data(show_spinner=False)
def _compute_analytics_bundle(metadata: dict, total_images: int) -> dict:
    duration = calculate_trip_duration(metadata)
    intervals = calculate_image_intervals(metadata)
    movement = estimate_distance_from_gps(metadata)
    gaps = detect_data_gaps(metadata)
    return {
        "duration": duration,
        "intervals": intervals,
        "movement": movement,
        "gaps": gaps,
        "total_images": total_images,
    }


def _show_export_result(result: dict[str, object]) -> None:
    export_path = Path(str(result["export_path"]))
    before_mb = float(result.get("before_mb", 0.0) or 0.0)
    after_mb = float(result.get("after_mb", 0.0) or 0.0)
    st.success("Completed")
    st.caption(f"Saved to folder: {export_path.as_posix()}")
    st.caption(f"Total MB before export: {before_mb:.2f}")
    st.caption(f"Total MB after compression: {after_mb:.2f}")
    try:
        share_file(export_path)
    except ShareExportError:
        logger.exception("Share hook preparation failed for %s", export_path)


def _run_share_export(action: str, selected_trip_path: Path, selected_trip: str, metadata: dict, image_records: list[dict], analytics_bundle: dict) -> None:
    status_placeholder = st.empty()

    def _update_status(message: str) -> None:
        status_placeholder.info(message)

    try:
        if action == "full":
            result = export_full_dataset(TRIPS_DIR, progress_callback=_update_status)
        elif action == "selected":
            result = export_selected_trip(selected_trip_path, selected_trip, metadata, image_records, analytics_bundle, progress_callback=_update_status)
        elif action == "lightweight":
            result = export_lightweight_package(selected_trip_path, selected_trip, metadata, image_records, analytics_bundle, progress_callback=_update_status)
        else:
            raise ShareExportError("unsupported export format")

        _show_export_result(result)
    except ShareExportError as exc:
        status_placeholder.error("Export failed")
        st.error(str(exc))
    except Exception:
        status_placeholder.error("Export failed")
        st.error("Export failed")
        logger.exception("Unexpected share/export failure for %s", selected_trip_path)


@st.cache_data(show_spinner=False)
def _load_preview_bytes(image_path: str) -> bytes:
    return Path(image_path).read_bytes()


st.set_page_config(page_title="RoadVision Dataset Explorer", layout="wide")
st.title("RoadVision Dataset Explorer")

with st.sidebar:
    trips = get_trip_folders()
    selected_trip = st.selectbox(
        "Select Trip",
        options=trips if trips else ["No trips found"],
        disabled=not trips,
    )

    if st.button("Refresh"):
        st.rerun()


st.write("Welcome to RoadVision Dataset Explorer.")

if not trips:
    st.info("No trips found.\nPlace exported trip folders inside ./trips/")
else:
    selected_trip_path = TRIPS_DIR / selected_trip
    logger.debug("Selected trip folder: %s", selected_trip_path)
    metadata_result = load_trip_metadata(selected_trip_path)

    if metadata_result["status"] == "missing":
        st.info(metadata_result["error"])
    elif metadata_result["status"] == "invalid_json":
        st.error(metadata_result["error"])
    else:
        image_extensions = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
        fallback_total_images = sum(
            1
            for item in selected_trip_path.rglob("*")
            if item.is_file() and item.suffix.lower() in image_extensions
        )

        trip_id = metadata_result["trip_id"] or selected_trip
        start_time = metadata_result["start_time"] or "Not available"
        end_time = metadata_result["end_time"] or "Not available"
        total_images = metadata_result["total_images"]
        if total_images is None:
            total_images = fallback_total_images

        first_timestamp = metadata_result["first_timestamp"] or "Not available"
        last_timestamp = metadata_result["last_timestamp"] or "Not available"
        gps_available = metadata_result["gps_available"]
        if isinstance(gps_available, str):
            gps_available = gps_available.strip().lower() in {"yes", "true", "1", "available"}

        image_records = _build_image_records(selected_trip_path, metadata_result.get("metadata"))
        image_path_lookup = {record["name"].lower(): record["path"] for record in image_records}

        st.markdown(f"**Selected Trip:** {selected_trip}")
        st.markdown(f"**Path:** {TRIPS_DIR.as_posix()}/{selected_trip}")

        st.subheader("Trip Information")
        cards = st.columns(4)
        cards[0].metric("Trip ID", str(trip_id))
        cards[1].metric("Start Time", str(start_time))
        cards[2].metric("End Time", str(end_time))
        cards[3].metric("Total Images", str(total_images))

        st.divider()
        st.subheader("Photo Summary")
        summary_columns = st.columns(3)
        summary_columns[0].metric("First Timestamp", str(first_timestamp))
        summary_columns[1].metric("Last Timestamp", str(last_timestamp))
        summary_columns[2].metric(
            "GPS Available",
            "Yes" if gps_available else "No",
        )

        st.divider()
        st.subheader("Trip Route Map")

        gps_points = extract_gps_points(metadata_result.get("metadata"), selected_trip_path)
        if not gps_points:
            st.info("No GPS data available")
        else:
            gps_point_lookup = {str(point["filename"]).lower(): point for point in gps_points}

            map_columns = st.columns(2)
            show_markers = map_columns[0].checkbox("Show markers", value=True)
            show_route = map_columns[1].checkbox("Show route", value=True)

            map_result = build_trip_map(
                gps_points,
                show_markers=show_markers,
                show_route=show_route,
            )

            if map_result["sampled"]:
                st.caption(f"Rendering a sampled view for performance. GPS points: {map_result['summary']['total_points']}")

            trip_map = map_result["map"]
            if trip_map is None:
                st.info("No GPS data available")
            else:
                map_state = st_folium(
                    trip_map,
                    height=500,
                    key=f"trip_map_{selected_trip}",
                    returned_objects=["last_object_clicked", "last_object_clicked_tooltip", "last_object_clicked_popup"],
                )

                clicked_filename = map_state.get("last_object_clicked_tooltip") if isinstance(map_state, dict) else None
                if not clicked_filename and isinstance(map_state, dict):
                    popup_text = map_state.get("last_object_clicked_popup")
                    if isinstance(popup_text, str) and popup_text:
                        marker_line = popup_text.split("<br>", 1)[0]
                        clicked_filename = marker_line.replace("<strong>Filename:</strong>", "").strip() or None

                if clicked_filename:
                    clicked_record = gps_point_lookup.get(clicked_filename.lower())
                    if clicked_record is not None:
                        resolved_path = clicked_record.get("path") or image_path_lookup.get(clicked_record["filename"].lower())
                        if resolved_path is not None:
                            st.session_state.selected_map_image_path = str(resolved_path)

                st.markdown("**Trip Summary**")
                summary_columns = st.columns(4)
                summary_columns[0].metric("Total GPS points", str(map_result["summary"]["total_points"]))
                summary_columns[1].metric("Estimated distance", f"{map_result['summary']['estimated_distance_km']} km")

                start_coordinates = map_result["summary"]["start_coordinates"]
                end_coordinates = map_result["summary"]["end_coordinates"]
                summary_columns[2].metric(
                    "Start coordinates",
                    f"{start_coordinates[0]:.6f}, {start_coordinates[1]:.6f}" if start_coordinates else "Not available",
                )
                summary_columns[3].metric(
                    "End coordinates",
                    f"{end_coordinates[0]:.6f}, {end_coordinates[1]:.6f}" if end_coordinates else "Not available",
                )

                selected_map_image_value = st.session_state.get("selected_map_image_path")
                if not selected_map_image_value and gps_points:
                    first_point = gps_points[0]
                    fallback_path = first_point.get("path") or image_path_lookup.get(first_point["filename"].lower())
                    if fallback_path is not None:
                        st.session_state.selected_map_image_path = str(fallback_path)
                        selected_map_image_value = st.session_state.selected_map_image_path

                if selected_map_image_value:
                    selected_map_image_path = Path(selected_map_image_value)
                    selected_map_point = gps_point_lookup.get(selected_map_image_path.name.lower())

                    if selected_map_point is not None:
                        st.markdown("**Selected Image**")
                        image_detail_left, image_detail_right = st.columns([2, 1])

                        with image_detail_left:
                            try:
                                annotated_image = _annotate_image(selected_map_image_path, _cached_yolo_detections(str(selected_map_image_path), _image_signature(selected_map_image_path)).get("detections", []))
                                st.image(annotated_image, use_container_width=True)
                            except YOLODetectorError as exc:
                                st.error(str(exc))
                                try:
                                    with Image.open(selected_map_image_path) as image_handle:
                                        st.image(image_handle, use_container_width=True)
                                except Exception:
                                    st.error("Unable to load image")
                            except Exception:
                                st.error("Unable to load image")

                        with image_detail_right:
                            st.markdown(f"**Filename:** {selected_map_point['filename']}")
                            st.markdown(f"**Timestamp:** {_format_value(selected_map_point.get('timestamp'))}")
                            st.markdown(f"**Latitude:** {_format_value(selected_map_point.get('latitude'))}")
                            st.markdown(f"**Longitude:** {_format_value(selected_map_point.get('longitude'))}")
                        _render_ai_detection_results(selected_map_image_path)

            st.divider()
            st.subheader("Trip Analytics")

            analytics_bundle = _compute_analytics_bundle(metadata_result.get("metadata", {}), int(total_images))
            analytics_duration = analytics_bundle["duration"]
            analytics_intervals = analytics_bundle["intervals"]
            analytics_movement = analytics_bundle["movement"]
            analytics_gaps = analytics_bundle["gaps"]

            metrics_columns = st.columns(5)
            metrics_columns[0].metric("Duration", f"{analytics_duration['duration_minutes']:.0f} min")
            metrics_columns[1].metric("Distance", f"{analytics_movement['distance_km']:.1f} km")
            metrics_columns[2].metric("Avg Speed", f"{analytics_movement['avg_speed_kmh']:.1f} km/h")
            metrics_columns[3].metric("Images", str(total_images))
            metrics_columns[4].metric("Data Quality", f"{analytics_gaps['quality_score']}/100")

            analytics_timestamps, analytics_running_counts, analytics_gps_datetimes = _build_analytics_chart_data(image_records, gps_points)
            chart_columns = st.columns(2)
            chart_columns[0].plotly_chart(_build_time_chart(analytics_timestamps, analytics_running_counts), use_container_width=True)
            chart_columns[1].plotly_chart(_build_gps_density_chart(analytics_gps_datetimes), use_container_width=True)

            analytics_notes = st.columns(4)
            analytics_notes[0].metric("Avg interval", f"{analytics_intervals['average_seconds']:.1f} s")
            analytics_notes[1].metric("Min interval", f"{analytics_intervals['min_seconds']:.1f} s")
            analytics_notes[2].metric("Max interval", f"{analytics_intervals['max_seconds']:.1f} s")
            analytics_notes[3].metric("GPS points", str(analytics_movement['gps_points']))

            if analytics_gaps["missing_gps_points"] or analytics_gaps["missing_timestamps"] or analytics_gaps["irregular_intervals"]:
                st.caption(
                    "Data gaps detected: "
                    f"missing GPS={analytics_gaps['missing_gps_points']}, "
                    f"missing timestamps={analytics_gaps['missing_timestamps']}, "
                    f"irregular intervals={analytics_gaps['irregular_intervals']}"
                )
            if analytics_movement["gps_points"] == 0:
                st.info("No GPS data available")

            st.divider()
            st.subheader("Share & Export")
            export_columns = st.columns(3)
            if export_columns[0].button("Export Full Dataset"):
                _run_share_export("full", selected_trip_path, selected_trip, metadata_result.get("metadata", {}), image_records, analytics_bundle)
            if export_columns[1].button("Export Selected Trip"):
                _run_share_export("selected", selected_trip_path, selected_trip, metadata_result.get("metadata", {}), image_records, analytics_bundle)
            if export_columns[2].button("Export Lightweight Package"):
                _run_share_export("lightweight", selected_trip_path, selected_trip, metadata_result.get("metadata", {}), image_records, analytics_bundle)

        st.divider()
        st.subheader("Gallery")
        st.markdown(f"**Images:** {len(image_records)}")

        search_query = st.text_input("Search", placeholder="filename")
        sort_by = st.radio("Sort by", ["Name", "Time"], horizontal=True)

        filtered_records = image_records
        if search_query.strip():
            query = search_query.strip().lower()
            filtered_records = [record for record in filtered_records if query in record["name"].lower()]

        if sort_by == "Name":
            filtered_records = sorted(filtered_records, key=lambda record: record["name"].lower())
        else:
            filtered_records = sorted(
                filtered_records,
                key=lambda record: (record["timestamp_sort"], record["name"].lower()),
            )

        page_size = 12
        total_pages = max(1, (len(filtered_records) + page_size - 1) // page_size)
        if "gallery_page" not in st.session_state:
            st.session_state.gallery_page = 1

        st.session_state.gallery_page = min(st.session_state.gallery_page, total_pages)

        pagination_columns = st.columns([1, 1, 6])
        if pagination_columns[0].button("Previous", disabled=st.session_state.gallery_page <= 1, key="gallery_previous"):
            st.session_state.gallery_page -= 1
            st.rerun()
        if pagination_columns[1].button("Next", disabled=st.session_state.gallery_page >= total_pages, key="gallery_next"):
            st.session_state.gallery_page += 1
            st.rerun()

        start_index = (st.session_state.gallery_page - 1) * page_size
        end_index = start_index + page_size
        visible_records = filtered_records[start_index:end_index]

        if not visible_records:
            st.info("No images found")
        else:
            selected_image_path = st.session_state.get("selected_image_path")
            if not selected_image_path or not any(str(record["path"]) == selected_image_path for record in filtered_records):
                st.session_state.selected_image_path = str(visible_records[0]["path"])
                selected_image_path = st.session_state.selected_image_path

            for row_start in range(0, len(visible_records), 3):
                row_records = visible_records[row_start:row_start + 3]
                row_columns = st.columns(3)
                for column, record in zip(row_columns, row_records):
                    with column:
                        try:
                            preview_bytes = _load_preview_bytes(str(record["path"]))
                            st.image(preview_bytes, use_container_width=True)
                        except Exception:
                            st.error("Unable to load image")

                        if st.button(record["name"], key=f"open_{record['path']}"):
                            st.session_state.selected_image_path = str(record["path"])
                            st.rerun()

                        st.caption(_format_value(record["timestamp"]))

            selected_path_value = st.session_state.get("selected_image_path")
            if selected_path_value:
                detail_path = Path(selected_path_value)
                detail_record = next((record for record in image_records if record["path"] == detail_path), None)

                if detail_record is not None:
                    st.divider()
                    st.subheader("Image Detail View")
                    current_index = next(
                        (index for index, record in enumerate(filtered_records) if record["path"] == detail_record["path"]),
                        0,
                    )
                    nav_left, nav_center, nav_right = st.columns([1, 2, 1])
                    if nav_left.button("Previous", disabled=current_index <= 0, key="detail_previous"):
                        st.session_state.selected_image_path = str(filtered_records[current_index - 1]["path"])
                        st.rerun()
                    if nav_right.button("Next", disabled=current_index >= len(filtered_records) - 1, key="detail_next"):
                        st.session_state.selected_image_path = str(filtered_records[current_index + 1]["path"])
                        st.rerun()

                    detail_left, detail_right = st.columns([2, 1])

                    with detail_left:
                        try:
                            annotated_image = _annotate_image(detail_record["path"], _cached_yolo_detections(str(detail_record["path"]), _image_signature(detail_record["path"])).get("detections", []))
                            st.image(annotated_image, use_container_width=True)
                        except YOLODetectorError as exc:
                            st.error(str(exc))
                            try:
                                with Image.open(detail_record["path"]) as image_handle:
                                    st.image(image_handle, use_container_width=True)
                            except Exception:
                                st.error("Unable to load image")
                        except Exception:
                            st.error("Unable to load image")

                    with detail_right:
                        detail_timestamp = detail_record.get("timestamp")
                        latitude, longitude = _extract_lat_lon(metadata_result.get("metadata"), detail_record["name"])
                        st.markdown(f"**Filename:** {detail_record['name']}")
                        st.markdown(f"**Timestamp:** {_format_value(detail_timestamp)}")
                        st.markdown(f"**Latitude:** {_format_value(latitude)}")
                        st.markdown(f"**Longitude:** {_format_value(longitude)}")
                        _render_ai_detection_results(detail_record["path"])