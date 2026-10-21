import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

import streamlit as st
from PIL import Image
import plotly.graph_objects as go
from streamlit_folium import st_folium

HISTORY_FILE = Path("uploads.json")


def _load_upload_history() -> list[dict]:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except:
            return []
    return []


def _save_upload_history(history: list[dict]) -> None:
    HISTORY_FILE.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")


def _log_upload(source_name: str, upload_type: str, trip_names: list[str]) -> None:
    history = _load_upload_history()
    now = datetime.now()
    history.append({
        "timestamp": now.isoformat(),
        "source": source_name,
        "type": upload_type,
        "trips": trip_names
    })
    _save_upload_history(history)
    # Also write to audit log
    log_audit_event("upload", f"Uploaded {source_name} ({upload_type})")


def _delete_history_entry(index: int) -> None:
    """Delete a specific history entry and its associated ZIP/trip data"""
    history = _load_upload_history()
    if 0 <= index < len(history):
        entry = history[index]
        source_name = entry.get("source", "")

        # Find and delete the ZIP file from _incoming/
        from utils.trip_importer import IMPORTED_ZIPS_DIR
        if IMPORTED_ZIPS_DIR.exists():
            for zip_file in IMPORTED_ZIPS_DIR.glob("*.zip"):
                if zip_file.name == source_name or zip_file.stem == source_name.replace(".zip", ""):
                    zip_file.unlink()
                    break

        # Delete extracted trips associated with this ZIP
        from utils.trip_loader import EXTRACTED_TRIPS_DIR
        if EXTRACTED_TRIPS_DIR.exists():
            zip_stem = source_name.replace(".zip", "")
            for extracted_dir in EXTRACTED_TRIPS_DIR.iterdir():
                if extracted_dir.is_dir() and extracted_dir.name.startswith(zip_stem):
                    shutil.rmtree(extracted_dir)

        # Remove the entry from history
        history.pop(index)
        _save_upload_history(history)

from utils.analytics import (
    calculate_image_intervals,
    calculate_trip_duration,
    detect_data_gaps,
    estimate_distance_from_gps,
)
from utils.share_export import ShareExportError, export_full_dataset, share_file
import licensing
import utils.ai_assistant as ai_assistant
from utils.metadata_loader import load_trip_metadata
from utils.godseye import build_dashboard_url
from utils.map_loader import build_trip_map, extract_gps_points
from utils.image_enhancer import get_enhanced_image_path
from utils.image_loader import get_trip_images
from utils.trip_loader import get_trip_folders
from utils.rvtrip import import_rvtrip, RvTripImportResult
from utils.trip_importer import TripImportError, import_trip_zip
from utils.yolo_detector import YOLODetectorError, detect_objects
from utils.audit_logger import log_audit_event, get_audit_logs, clear_audit_logs, verify_audit_password
from utils.authenticity_checker import compute_trip_authenticity


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
                    or entry.get("file")
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
                    lat = value.get("latitude") or value.get("lat")
                    lon = value.get("longitude") or value.get("lon")
                    # Treat 0 as None (invalid GPS)
                    return (lat if lat and lat != 0 else None), (lon if lon and lon != 0 else None)

        if isinstance(candidate, list):
            for entry in candidate:
                if not isinstance(entry, dict):
                    continue

                entry_name = str(
                    entry.get("filename")
                    or entry.get("file_name")
                    or entry.get("file")
                    or entry.get("name")
                    or entry.get("image")
                    or ""
                ).lower()
                if entry_name == normalized_name:
                    lat = entry.get("latitude") or entry.get("lat")
                    lon = entry.get("longitude") or entry.get("lon")
                    # Treat 0 as None (invalid GPS)
                    return (lat if lat and lat != 0 else None), (lon if lon and lon != 0 else None)

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
                "enhanced_path": None,
            }
        )
    return records


def _is_image_file(path: Path) -> bool:
    return path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


@st.cache_data(show_spinner=False)
def _cached_enhanced_image_path(image_path: str, signature: tuple[int, int], enable_realesrgan: bool) -> dict[str, str]:
    if not enable_realesrgan:
        return {"path": image_path, "status": "disabled"}

    try:
        enhanced_path = get_enhanced_image_path(image_path)
        return {"path": enhanced_path, "status": "enhanced"}
    except Exception as exc:
        logger.warning("OpenCV enhancement failed for %s: %s", image_path, exc)
        return {"path": image_path, "status": "fallback", "message": str(exc)}


def _resolve_image_path(record: dict, enable_realesrgan: bool) -> Path:
    image_path = Path(record["path"])
    cached_result = _cached_enhanced_image_path(str(image_path), _image_signature(image_path), enable_realesrgan)
    return Path(cached_result["path"])


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
        # Bucket by minute for better resolution
        bucket_counts: dict[str, int] = {}
        for timestamp in gps_datetimes:
            # Round to nearest minute
            rounded = timestamp.replace(second=0, microsecond=0)
            bucket = rounded.strftime("%H:%M")
            bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

        ordered_buckets = sorted(bucket_counts.items())
        figure.add_trace(
            go.Bar(x=[bucket for bucket, _ in ordered_buckets], y=[count for _, count in ordered_buckets], name="GPS Density")
        )
    figure.update_layout(
        title="GPS Density Over Time",
        xaxis_title="Time (minute)",
        yaxis_title="GPS Points",
        height=320,
        margin=dict(l=10, r=10, t=40, b=10),
    )
    return figure


@st.cache_data(show_spinner=False)
def _compute_analytics_bundle(metadata: dict, total_images: int, gps_points: list[dict] | None = None) -> dict:
    duration = calculate_trip_duration(metadata)
    intervals = calculate_image_intervals(metadata)
    movement = estimate_distance_from_gps(metadata, gps_points)
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


AI_WELCOME_TEXT = (
    "\U0001F916 **Welcome to GroundTruth!**\n\n"
    "I'm your GroundTruth Assistant. Ask me anything about the app — features, evidence "
    "verification, the authenticity score, coverage, maps, exports — or about your trip data.\n\n"
    "*Click the robot button (bottom-right) again to close this chat.*"
)


def _render_floating_robot_css():
    """Style the robot toggle button as a floating circular chat bubble."""
    st.markdown(
        "<style>"
        ".st-key-gt_robot_fab{height:0;overflow:visible;margin:0;}"
        ".st-key-gt_robot_fab button{position:fixed;bottom:24px;right:24px;z-index:999999;"
        "width:64px;height:64px;border-radius:50%;font-size:32px;line-height:1;padding:0;"
        "background:linear-gradient(135deg,#4f8cff,#7c3aed);color:#ffffff;border:none;"
        "box-shadow:0 6px 24px rgba(79,140,255,.45);transition:transform .18s ease;}"
        ".st-key-gt_robot_fab button:hover{transform:scale(1.1);filter:brightness(1.08);}"
        ".st-key-gt_robot_fab button:focus{color:#ffffff;box-shadow:0 6px 24px rgba(79,140,255,.45);"
        "border-color:#4f8cff;}"
        ".st-key-gt_robot_fab button p{font-size:32px;}"
        "</style>",
        unsafe_allow_html=True,
    )


def _render_ai_assistant():
    """Subscriber-only AI assistant (Botpress); opened via the floating robot button."""
    usage = licensing.get_ai_usage()

    if usage["access"] == "trial_locked":
        st.info("The AI Assistant is available to subscribers. Contact the developer to subscribe.")
        return
    if usage["access"] == "expired":
        st.info("Your access has expired, so the AI Assistant is locked. Contact the developer to renew.")
        return
    if not ai_assistant.is_configured():
        st.caption("The AI Assistant is being configured. Contact the developer if this message persists.")
        return

    # Floating robot toggle: click to open/close the chat.
    if st.button("\U0001F916", key="gt_robot_fab", help="GroundTruth Assistant — click to open / close the chat"):
        st.session_state.gt_chat_open = not st.session_state.get("gt_chat_open", False)
    _render_floating_robot_css()

    if not st.session_state.get("gt_chat_open"):
        return

    with st.chat_message("assistant"):
        st.markdown(AI_WELCOME_TEXT)

    if "ai_messages" not in st.session_state:
        st.session_state.ai_messages = []

    for message in st.session_state.ai_messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if usage["access"] == "limit_reached":
        st.warning(
            f"Question limit reached for this subscription ({usage['used']} of {usage['limit']} used). "
            "Renew or upgrade your plan for more."
        )
        return

    st.caption(f"Questions used: {usage['used']} of {usage['limit']} for this subscription")

    prompt = st.chat_input("Ask a question...")
    if not prompt:
        return

    st.session_state.ai_messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                replies = ai_assistant.send_question(prompt)
            except ai_assistant.AIAssistantError as exc:
                st.error(str(exc))
                return
            except Exception:
                st.error("Something went wrong talking to the AI service. Try again.")
                return

    # Only a successfully answered question consumes the subscription quota.
    licensing.consume_ai_use()
    reply_text = "\n\n".join(replies)
    st.session_state.ai_messages.append({"role": "assistant", "content": reply_text})
    st.rerun()


def _render_ai_usage_card():
    """Very visible AI usage card in the sidebar."""
    usage = licensing.get_ai_usage()

    if usage["access"] in ("allowed", "limit_reached"):
        used, remaining, limit = usage["used"], usage["remaining"], usage["limit"]
        pct = int(100 * used / limit) if limit else 100
        bar_color = "#22c55e" if pct < 60 else "#f59e0b" if pct < 100 else "#ef4444"
        if usage["access"] == "limit_reached":
            headline = "Limit reached"
        else:
            headline = f"{remaining} question{'s' if remaining != 1 else ''} left"
        st.markdown(
            "<div style='background:linear-gradient(135deg,#141a3a,#1c2350);"
            "border:1px solid #3a4380;border-radius:12px;padding:14px 16px;margin-top:14px;'>"
            "<div style='font-size:11px;letter-spacing:2px;color:#8fa0ff;font-weight:700;'>AI ASSISTANT</div>"
            f"<div style='font-size:26px;font-weight:800;color:#ffffff;margin:6px 0 2px 0;'>{headline}</div>"
            f"<div style='font-size:13px;color:#aab4e8;'>{used} of {limit} subscription questions used</div>"
            "<div style='background:#2a3160;border-radius:6px;height:10px;margin-top:10px;overflow:hidden;'>"
            f"<div style='background:{bar_color};width:{pct}%;height:100%;border-radius:6px;'></div>"
            "</div></div>",
            unsafe_allow_html=True,
        )
    elif usage["access"] == "trial_locked":
        st.markdown(
            "<div style='background:linear-gradient(135deg,#141a3a,#1c2350);"
            "border:1px solid #3a4380;border-radius:12px;padding:14px 16px;margin-top:14px;'>"
            "<div style='font-size:11px;letter-spacing:2px;color:#8fa0ff;font-weight:700;'>AI ASSISTANT</div>"
            "<div style='font-size:15px;font-weight:700;color:#ffffff;margin-top:6px;'>Subscribers only</div>"
            "<div style='font-size:13px;color:#aab4e8;margin-top:4px;'>Subscribe to unlock the AI Assistant.</div>"
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<div style='background:linear-gradient(135deg,#141a3a,#1c2350);"
            "border:1px solid #3a4380;border-radius:12px;padding:14px 16px;margin-top:14px;'>"
            "<div style='font-size:11px;letter-spacing:2px;color:#8fa0ff;font-weight:700;'>AI ASSISTANT</div>"
            "<div style='font-size:15px;font-weight:700;color:#ffffff;margin-top:6px;'>Locked</div>"
            "<div style='font-size:13px;color:#aab4e8;margin-top:4px;'>Subscription expired — renew to unlock.</div>"
            "</div>",
            unsafe_allow_html=True,
        )


@st.cache_data(show_spinner=False)
def _load_preview_bytes(image_path: str) -> bytes:
    return Path(image_path).read_bytes()


def _render_trial_expiry_indicator():
    """Blinking trial/license clock in the sidebar (state from licensing.py)."""
    # Remote kill switch (throttled inside licensing.py): a revoked device
    # locks immediately, even mid-session.
    revoked_reason = licensing.check_kill_status()
    if revoked_reason:
        st.sidebar.error(
            "Access to GroundTruth has been deactivated on this device. "
            "Contact the developer with your Device ID."
        )
        st.stop()

    status = licensing.get_status()

    if status["mode"] == "licensed":
        label = "LICENSED"
        sub = f"Licensed until {status['licensed_until']} · Device {status['device_id']}"
    elif status["mode"] == "expired":
        label = "EXPIRED"
        sub = f"Trial ended · Device {status['device_id']}"
    else:
        label = f"{status['days_left']} day{'s' if status['days_left'] != 1 else ''} left"
        sub = f"Trial access · Device {status['device_id']}"

    st.sidebar.markdown(
        f"""
        <style>
        @keyframes rvTrialBlink {{
            0%, 100% {{ opacity: 1; transform: scale(1); box-shadow: 0 0 0 0 rgba(255, 31, 31, 0.75); }}
            50% {{ opacity: 0.35; transform: scale(0.72); box-shadow: 0 0 14px 8px rgba(255, 31, 31, 0.25); }}
        }}
        </style>
        <div style="
            position: sticky;
            bottom: 8px;
            left: 4px;
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 10px 12px;
            margin-top: 24px;
            border: 1px solid rgba(255, 31, 31, 0.55);
            border-radius: 10px;
            background: rgba(120, 0, 0, 0.22);
            color: #ffdede;
            font-family: 'Segoe UI', sans-serif;
            font-size: 12px;
            line-height: 1.25;
        ">
            <span style="
                width: 13px;
                height: 13px;
                border-radius: 50%;
                background: #ff1f1f;
                display: inline-block;
                animation: rvTrialBlink 0.9s ease-in-out infinite;
                flex: 0 0 auto;
            "></span>
            <span>
                <strong style="color: #ffffff;">{label}</strong><br>
                <span style="opacity: 0.88;">{sub}</span>
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )


st.set_page_config(page_title="GroundTruth Dataset Explorer", layout="wide")
st.title("GroundTruth Dataset Explorer")

with st.sidebar:
    enable_ai_detection = st.radio(
        "AI Detection",
        ["Enable AI Detection", "Disable AI Detection"],
        index=0,
        help="Toggle YOLO object detection on/off. When disabled, images show without bounding boxes.",
    )
    ai_detection_enabled = enable_ai_detection == "Enable AI Detection"

    enable_realesrgan = st.checkbox(
        "Enable image enhancement (Real-ESRGAN)",
        value=False,
        help="Enhances each photo with AI upscaling. Slower but sharper. Results are cached.",
    )

    st.subheader("Import Trip")
    st.caption("Upload .rvtrip file only.")

    uploaded_rvtrip = st.file_uploader(
        "Choose a .rvtrip package",
        type=["rvtrip"],
        accept_multiple_files=False,
        label_visibility="collapsed",
        help="Pick the .rvtrip package from Windows Explorer.",
    )
    read_rvtrip_clicked = st.button("Read .rvtrip", disabled=uploaded_rvtrip is None)
    if read_rvtrip_clicked:
        try:
            result = import_rvtrip(uploaded_rvtrip, clear_old=True)
            _log_upload(uploaded_rvtrip.name, "rvtrip", [])
            st.session_state.verification_result = result.verification
            st.session_state.imported_trip_dir = str(result.trip_dir)
            st.success(f"Imported {result.trip_dir.name}")
            st.rerun()
        except Exception as exc:
            st.error(str(exc))

    trips = get_trip_folders()

    st.subheader("External View")
    st.link_button(
        "Open God's Eye Dashboard",
        build_dashboard_url(),
        use_container_width=True,
    )
    st.link_button(
        "Live Cybermap",
        "https://cybermap.kaspersky.com",
        use_container_width=True,
    )

    # Show verification banner if .rvtrip was imported
    if st.session_state.get("verification_result"):
        verification = st.session_state.verification_result
        status = verification["status"]
        
        if status == "VALID":
            st.markdown(
                f"""
                <div style="background-color:#28a745;padding:20px;border-radius:10px;margin-bottom:20px;">
                    <h3 style="color:white;margin:0;">✅ Trip Integrity: PASS</h3>
                    <p style="color:white;margin:10px 0 0 0;">
                        Files Verified: {verification['files_verified']}<br>
                        Verification Time: {verification['elapsed']}s<br>
                        Hash Algorithm: {verification['hash_algorithm'].upper()}
                    </p>
                </div>
                """,
                unsafe_allow_html=True
            )
        elif status == "WARNING":
            st.markdown(
                f"""
                <div style="background-color:#ffc107;padding:20px;border-radius:10px;margin-bottom:20px;">
                    <h3 style="color:black;margin:0;">⚠️ Trip Integrity: WARNING (read-only)</h3>
                    <p style="color:black;margin:10px 0 0 0;">
                        Files Verified: {verification['files_verified']} / {verification['total_files']}<br>
                        Missing: {len(verification['missing'])} files<br>
                        Verification Time: {verification['elapsed']}s<br>
                        Hash Algorithm: {verification['hash_algorithm'].upper()}
                    </p>
                </div>
                """,
                unsafe_allow_html=True
            )
            if verification['missing']:
                st.markdown("**Missing files:**")
                for missing_file in verification['missing'][:10]:
                    st.markdown(f"- {missing_file}")
                if len(verification['missing']) > 10:
                    st.markdown(f"- ... and {len(verification['missing']) - 10} more")
            
            # Add "Continue Anyway" button for WARNING
            if not st.session_state.get("verification_override", False):
                if st.button("Continue Anyway", key="override_warning", help="Override warning and proceed with full analytics"):
                    st.session_state.verification_override = True
                    st.rerun()
            else:
                st.info("✅ Warning overridden — showing full analytics")
                if st.button("Revert Override", key="revert_override"):
                    st.session_state.verification_override = False
                    st.rerun()
        elif status == "INVALID":
            st.markdown(
                f"""
                <div style="background-color:#dc3545;padding:20px;border-radius:10px;margin-bottom:20px;">
                    <h3 style="color:white;margin:0;">❌ Trip Integrity: FAIL</h3>
                    <p style="color:white;margin:10px 0 0 0;">
                        Files Verified: {verification['files_verified']} / {verification['total_files']}<br>
                        Mismatched hashes: {len(verification['mismatches'])} files<br>
                        Verification Time: {verification['elapsed']}s<br>
                        Hash Algorithm: {verification['hash_algorithm'].upper()}<br>
                        <strong>This trip has been modified or corrupted. Analytics and reports are disabled.</strong>
                    </p>
                </div>
                """,
                unsafe_allow_html=True
            )
            if verification['mismatches']:
                st.markdown("**Modified files:**")
                for mismatch in verification['mismatches'][:10]:
                    st.markdown(f"- {mismatch['file']}")
                if len(verification['mismatches']) > 10:
                    st.markdown(f"- ... and {len(verification['mismatches']) - 10} more")

    if trips:
        if st.button("Clear All Trips", help="Remove all trips to upload new content"):
            TRIPS_DIR.mkdir(parents=True, exist_ok=True)
            for item in TRIPS_DIR.iterdir():
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
            st.session_state.active_trip_path = None
            st.session_state.pop("gallery_page", None)
            st.session_state.pop("selected_image_path", None)
            st.session_state.pop("selected_map_image_path", None)
            _save_upload_history([])
            st.rerun()

    # History Section
    upload_history = _load_upload_history()
    if upload_history:
        with st.expander("Upload History", expanded=False):
            st.caption(f"{len(upload_history)} past upload(s)")
            for i, entry in enumerate(reversed(upload_history)):
                ts = entry.get("timestamp", "")
                try:
                    formatted_time = datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M")
                except:
                    formatted_time = ts
                source = entry.get("source", "Unknown")
                col_a, col_b = st.columns([4, 1])
                col_a.text(f"📦 {formatted_time} - {source}")
                if col_b.button("Delete", key=f"del_hist_{len(upload_history) - 1 - i}"):
                    _delete_history_entry(len(upload_history) - 1 - i)
                    st.rerun()
            if st.button("Clear History", key="clear_history"):
                _save_upload_history([])
                log_audit_event("history_clear", "Cleared upload history")
                st.rerun()

    # Audit Logs Section (password-protected)
    from utils.audit_logger import get_password, set_password, is_locked, lockout_remaining_seconds, MAX_FAILED_ATTEMPTS, _load_config, record_failed_attempt
    if st.session_state.get("audit_unlocked", False):
        with st.expander(" Audit Logs", expanded=True):
            audit_logs = get_audit_logs()
            if audit_logs:
                st.caption(f"{len(audit_logs)} audit event(s)")
                for log in reversed(audit_logs):
                    ts = log.get("timestamp", "")
                    event_type = log.get("event_type", "unknown")
                    details = log.get("details", "")
                    st.text(f"🕒 {ts} | {event_type.upper()}: {details}")
                if st.button("Clear Audit Logs", key="clear_audit"):
                    clear_audit_logs()
                    log_audit_event("audit_cleared", "Audit logs cleared")
                    st.session_state.audit_unlocked = False
                    st.rerun()
            else:
                st.info("No audit events recorded yet.")
            col_lock, col_pass = st.columns(2)
            if col_lock.button("🔒 Lock", key="lock_audit"):
                st.session_state.audit_unlocked = False
                st.rerun()
            if col_pass.button(" Change Password", key="change_pass_btn"):
                st.session_state.show_change_password = True
                st.rerun()

            if st.session_state.get("show_change_password", False):
                st.divider()
                st.markdown("### Change Audit Password")
                current_pass = st.text_input("Current password", type="password", key="current_pass_for_change")
                new_pass = st.text_input("New password", type="password", key="new_pass_for_change")
                confirm_pass = st.text_input("Confirm new password", type="password", key="confirm_pass_for_change")
                col_cancel, col_save = st.columns(2)
                if col_cancel.button("Cancel", key="cancel_pass_change"):
                    st.session_state.show_change_password = False
                    st.rerun()
                if col_save.button("Save New Password", key="save_pass_change"):
                    if current_pass != get_password():
                        st.error("Current password is incorrect")
                        record_failed_attempt()
                    elif new_pass != confirm_pass:
                        st.error("New passwords do not match")
                    elif len(new_pass) < 4:
                        st.error("Password must be at least 4 characters")
                    else:
                        set_password(new_pass)
                        log_audit_event("password_changed", "Audit log password was changed")
                        st.session_state.show_change_password = False
                        st.success("Password changed successfully")
                        st.rerun()
    else:
        with st.expander(" Audit Logs", expanded=False):
            if is_locked():
                remaining = lockout_remaining_seconds()
                mins = remaining // 60
                secs = remaining % 60
                st.error(f"Audit logs locked. Try again in {mins}m {secs}s")
            else:
                audit_password = st.text_input("Enter password to access audit logs", type="password", key="audit_pass_input")
                if st.button("Unlock", key="unlock_audit"):
                    if verify_audit_password(audit_password):
                        st.session_state.audit_unlocked = True
                        log_audit_event("audit_unlocked", "Audit logs accessed")
                        st.rerun()
                    else:
                        attempts_left = MAX_FAILED_ATTEMPTS - (_load_config().get("failed_attempts", 0))
                        st.error(f"Incorrect password. {attempts_left} attempts remaining before lockout.")

    active_trip_path = st.session_state.get("active_trip_path")
    if active_trip_path is not None:
        active_trip_path = Path(active_trip_path)
    if not active_trip_path or active_trip_path not in trips:
        active_trip_path = trips[0] if trips else None
    st.session_state.active_trip_path = str(active_trip_path) if active_trip_path is not None else None

    if len(trips) > 1:
        with st.expander("Switch trip", expanded=False):
            trip_labels = [trip.name for trip in trips]
            selected_index = trip_labels.index(active_trip_path.name) if active_trip_path is not None and active_trip_path.name in trip_labels else 0
            chosen_trip_name = st.radio("Trips", trip_labels, index=selected_index, label_visibility="collapsed")
            if chosen_trip_name != active_trip_path.name:
                active_trip_path = trips[trip_labels.index(chosen_trip_name)]
                st.session_state.active_trip_path = str(active_trip_path)
                # Reset trip-specific state
                st.session_state.pop("selected_image_path", None)
                st.session_state.pop("gallery_page", None)
                st.session_state.pop("selected_map_image_path", None)
                st.session_state.pop("map_zoom", None)
                st.rerun()

    if st.button("Refresh"):
        st.rerun()

    _render_trial_expiry_indicator()
    _render_ai_usage_card()


st.write("Welcome to GroundTruth Dataset Explorer.")

if not trips:
    st.info("No trips found.\nPlace exported trip folders inside ./trips/")
else:
    selected_trip_path = Path(st.session_state.active_trip_path) if st.session_state.active_trip_path else trips[0]
    metadata_result = load_trip_metadata(selected_trip_path)

    if metadata_result["status"] == "missing":
        st.info("No metadata found for this trip.")
    elif metadata_result["status"] == "invalid_json":
        st.error("Trip metadata is corrupted.")
    else:
        image_extensions = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
        fallback_total_images = sum(
            1
            for item in selected_trip_path.rglob("*")
            if item.is_file() and item.suffix.lower() in image_extensions
        )

        trip_id = metadata_result["trip_id"] or selected_trip_path.name
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
        if any(not _is_image_file(record["path"]) for record in image_records):
            st.warning("Some trip files are metadata JSON files. The gallery previews image files only.")
        if st.session_state.get("active_trip_path") is None:
            st.info("No trip loaded yet. Use the sidebar ZIP upload, then press Read ZIP.")

        if image_records:
            sample_preview = image_records[0]["path"]
            if sample_preview.suffix.lower() == ".json":
                st.warning("JSON files are metadata, not images. The gallery only previews image files.")

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
        st.subheader("Trip Imagery Map")

        gps_points = extract_gps_points(metadata_result.get("metadata"), selected_trip_path)
        if not gps_points:
            st.info("No GPS data available")
        else:
            gps_point_lookup = {str(point["filename"]).lower(): point for point in gps_points}

            first_point = gps_points[0]
            map_lat = float(first_point["latitude"])
            map_lon = float(first_point["longitude"])
            imagery_url = f"https://www.google.com/maps/@{map_lat},{map_lon},18z/data=!3m1!1e3"
            st.markdown(
                f'<a href="{imagery_url}" target="_blank" style="display:block;padding:10px 16px;background:#4285F4;color:white;text-decoration:none;border-radius:6px;text-align:center;font-weight:bold;">🛣️ View Imagery</a>',
                unsafe_allow_html=True,
            )

            map_controls = st.columns(2)
            show_markers = map_controls[0].checkbox("Show markers", value=True)
            show_route = map_controls[1].checkbox("Show route", value=True)

            map_result = build_trip_map(
                gps_points,
                show_markers=show_markers,
                show_route=show_route,
                imagery=True,
                marker_sample_limit=len(gps_points),
            )

            if map_result["sampled"]:
                st.caption(f"Rendering a sampled view for performance. GPS points: {map_result['summary']['total_points']}")

            # Display area/landmark name
            area_name = map_result["summary"].get("area_name", "Unknown location")
            if area_name and area_name != "Unknown location":
                st.success(f"**Trip Location:** {area_name}")

            trip_map = map_result["map"]
            if trip_map is None:
                st.info("No GPS data available")
            else:
                map_state = st_folium(
                    trip_map,
                    width=1500,
                    height=720,
                    key=f"trip_map_{selected_trip_path.name}",
                )

                stale_count = map_result["summary"].get("stale_fixes", 0)
                if stale_count:
                    st.warning(
                        f"{stale_count} stale GPS fix(es) excluded from the route (orange markers) — "
                        "likely recorded before the device acquired a fresh GPS lock. The data is kept."
                    )

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

                # Coverage Score section
                coverage = map_result["summary"].get("coverage")
                if coverage:
                    st.markdown("### Coverage Score")
                    coverage_columns = st.columns(4)
                    coverage_columns[0].metric("Route length", f"{coverage['route_length_km']:.1f} km")
                    coverage_columns[1].metric("Captured", f"{coverage['captured_km']:.1f} km")
                    coverage_columns[2].metric("Coverage", f"{coverage['coverage_pct']:.1f}%")
                    coverage_columns[3].metric(
                        "Longest unrecorded gap",
                        f"{coverage['longest_gap_m']:.0f} m" if coverage['longest_gap_m'] > 0 else "None"
                    )

            st.divider()
            st.subheader("Trip Analytics")

            # Gate analytics behind verification result (unless override is active)
            _verification_status = st.session_state.get("verification_result", {}).get("status") if st.session_state.get("verification_result") else None
            _verification_override = st.session_state.get("verification_override", False)
            if _verification_status == "INVALID" and not _verification_override:
                st.error("Trip Analytics disabled: integrity verification failed. This trip has been modified or corrupted.")
            else:
                if _verification_status == "WARNING" and not _verification_override:
                    st.warning("Trip Analytics shown in read-only mode (some files missing).")
                analytics_bundle = _compute_analytics_bundle(metadata_result.get("metadata", {}), int(total_images), gps_points)
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

                # Add two new meaningful charts
                # Chart 1: Interval variation over time (helps identify pauses/movement changes)
                if len(analytics_intervals.get("intervals", [])) >= 2:
                    interval_figure = go.Figure()
                    interval_figure.add_trace(go.Scatter(
                        y=analytics_intervals["intervals"],
                        mode="lines+markers",
                        name="Interval (seconds)",
                        line=dict(color="#00E5FF", width=2),
                        marker=dict(size=6)
                    ))
                    interval_figure.update_layout(
                        title="Photo Interval Variation",
                        xaxis_title="Photo #",
                        yaxis_title="Seconds Between Photos",
                        template="plotly_dark",
                        height=350
                    )
                    interval_figure.update_yaxes(range=[0, max(analytics_intervals["intervals"]) * 1.2])
                    chart_columns[0].plotly_chart(interval_figure, use_container_width=True)

                # Chart 2: AI Detection Summary (only when AI is enabled)
                if ai_detection_enabled:
                    detection_counts = []
                    detection_labels = []
                    for record in image_records:
                        try:
                            detection_state = _cached_yolo_detections(str(record["path"]), _image_signature(record["path"]))
                            results = detection_state.get("detections", [])
                            if results and len(results) > 0:
                                detection_counts.append(len(results))
                                detection_labels.append(record["name"])
                        except Exception:
                            pass

                    if detection_counts:
                        detection_figure = go.Figure()
                        detection_figure.add_trace(go.Bar(
                            x=detection_labels[:50],  # Show first 50 for readability
                            y=detection_counts[:50],
                            marker_color="#00E5FF",
                            name="Objects Detected"
                        ))
                        detection_figure.update_layout(
                            title="AI Detection Results (First 50 Images)",
                            xaxis_title="Image Filename",
                            yaxis_title="Number of Objects",
                            template="plotly_dark",
                            height=350,
                            xaxis_tickangle=-45
                        )
                        chart_columns[1].plotly_chart(detection_figure, use_container_width=True)

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

                # Trip Authenticity Score
                st.divider()
                authenticity_result = compute_trip_authenticity(
                    metadata_result.get("metadata", {}),
                    [Path(r["path"]) for r in image_records],
                    gps_points,
                )
                auth_score = authenticity_result["score"]
                auth_warnings = authenticity_result["warnings"]

                # Color-code the score
                if auth_score >= 90:
                    score_color = "green"
                    score_icon = "✅"
                elif auth_score >= 70:
                    score_color = "yellow"
                    score_icon = "⚠️"
                else:
                    score_color = "red"
                    score_icon = ""

                st.markdown(f"### {score_icon} Trip Authenticity: {auth_score}%")
                
                if auth_score >= 90:
                    st.success("Trip data appears authentic with minimal issues.")
                elif auth_score >= 70:
                    st.warning("Trip has some suspicious patterns. Review warnings below.")
                else:
                    st.error("Trip has significant authenticity concerns.")

                if auth_warnings:
                    st.markdown("**Warnings:**")
                    for warning in auth_warnings:
                        st.markdown(f"- {warning}")
                else:
                    st.markdown("*No warnings*")

                with st.expander("Detailed Analysis", expanded=False):
                    details = authenticity_result["details"]
                    
                    st.markdown("**Metadata Integrity:**")
                    meta_details = details.get("metadata", {})
                    if meta_details.get("image_count_match", False):
                        st.markdown("- ✅ Image count matches metadata")
                    else:
                        st.markdown(f"- ❌ Image count mismatch")
                    if meta_details.get("metadata_complete", False):
                        st.markdown("- ✅ All required metadata fields present")
                    else:
                        missing = meta_details.get("missing_fields", [])
                        st.markdown(f"- ❌ Missing fields: {', '.join(missing)}")
                    
                    st.markdown("**GPS Consistency:**")
                    gps_details = details.get("gps", {})
                    unexplained_gaps = gps_details.get("unexplained_gaps", 0)
                    st.markdown(f"- {unexplained_gaps} unexplained gap(s)")
                    speed_anomalies = gps_details.get("speed_anomalies", 0)
                    st.markdown(f"- {speed_anomalies} impossible speed anomaly(ies)")
                    if gps_details.get("coordinate_diversity", True):
                        st.markdown("- ✅ Good coordinate diversity")
                    else:
                        st.markdown("- ⚠️ Low coordinate diversity")
                    
                    st.markdown("**Temporal Consistency:**")
                    temporal_details = details.get("temporal", {})
                    future_ts = temporal_details.get("future_timestamps", 0)
                    st.markdown(f"- {future_ts} future timestamp(s)")
                    regularity = temporal_details.get("interval_regularity", "unknown")
                    st.markdown(f"- Interval regularity: {regularity}")
                    
                    st.markdown("**File Integrity:**")
                    file_details = details.get("files", {})
                    duplicates = file_details.get("duplicate_files", 0)
                    st.markdown(f"- {duplicates} duplicate file(s)")
                    if file_details.get("file_naming_consistent", False):
                        st.markdown("- ✅ Consistent file format")
                    else:
                        st.markdown("- ⚠️ Mixed file formats")

                st.divider()
                st.subheader("Share & Export")
                # Gate share/export behind verification (unless override is active)
                if _verification_status in ("WARNING", "INVALID") and not _verification_override:
                    st.warning("Share & Export disabled: trip integrity check failed.")
                else:
                    _selected_trip_name = metadata_result.get("trip_id") or selected_trip_path.name
                    if st.button("Export"):
                        _run_share_export("full", selected_trip_path, _selected_trip_name, metadata_result.get("metadata", {}), image_records, analytics_bundle)

                _render_ai_assistant()

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

        pagination_columns = st.columns([1, 1, 2, 4])
        show_all = pagination_columns[2].toggle("Show all", key="gallery_show_all")
        if pagination_columns[0].button("Previous", disabled=st.session_state.gallery_page <= 1 or show_all, key="gallery_previous"):
            st.session_state.gallery_page -= 1
            st.rerun()
        if pagination_columns[1].button("Next", disabled=st.session_state.gallery_page >= total_pages or show_all, key="gallery_next"):
            st.session_state.gallery_page += 1
            st.rerun()
        pagination_columns[3].caption(
            f"Page {st.session_state.gallery_page} of {total_pages} - {len(filtered_records)} images"
            if not show_all
            else f"Showing all {len(filtered_records)} images"
        )

        if show_all:
            start_index, end_index = 0, len(filtered_records)
        else:
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
                            preview_path = _resolve_image_path(record, enable_realesrgan)
                            preview_bytes = _load_preview_bytes(str(preview_path))
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
                            if enable_realesrgan:
                                with st.spinner("Enhancing image with Real-ESRGAN..."):
                                    detail_processing_path = _resolve_image_path(detail_record, enable_realesrgan)
                            else:
                                detail_processing_path = _resolve_image_path(detail_record, enable_realesrgan)
                            
                            if ai_detection_enabled:
                                annotated_image = _annotate_image(detail_processing_path, _cached_yolo_detections(str(detail_processing_path), _image_signature(detail_processing_path)).get("detections", []))
                                st.image(annotated_image, use_container_width=True)
                            else:
                                with Image.open(detail_processing_path) as image_handle:
                                    st.image(image_handle, use_container_width=True)
                        except YOLODetectorError as exc:
                            st.error(str(exc))
                            try:
                                with Image.open(_resolve_image_path(detail_record, enable_realesrgan)) as image_handle:
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

                        if latitude is not None and longitude is not None:
                            try:
                                lat_val = float(latitude)
                                lon_val = float(longitude)
                                detail_imagery_url = f"https://www.google.com/maps/@{lat_val},{lon_val},18z/data=!3m1!1e3"
                                st.markdown(
                                    f'<a href="{detail_imagery_url}" target="_blank" style="display:block;padding:6px 10px;background:#4285F4;color:white;text-decoration:none;border-radius:4px;text-align:center;font-size:13px;font-weight:bold;">🛣️ View Imagery</a>',
                                    unsafe_allow_html=True,
                                )
                            except (ValueError, TypeError):
                                pass

                        if ai_detection_enabled:
                            _render_ai_detection_results(detail_record["path"])