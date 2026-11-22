"""Photo forensics (tamper-evidence) checks.

Layer 1 evidence scanner: looks for fingerprints that a photo was edited,
re-saved, or had its metadata rewritten. Findings are forensic *evidence*,
not mathematical proof -- a skilled actor can defeat individual checks.
Photos signed at capture time (a future Vision-side feature) provide proof.

Checks performed:
- Editing-software fingerprints in EXIF Software tag and raw XMP data
- EXIF thumbnail vs actual image mismatch (classic GPS-spoofing tell)
- Capture-time vs modification-time contradictions
- Missing camera make/model or manufacturer maker notes
- Lossy re-compression estimate (JPEG quantization tables)
- GPS sanity (valid ranges, zero coordinates, GPS-vs-capture time mismatch)
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

# Tools that, when found in metadata, indicate the photo passed through an
# editor after capture. Compared case-insensitively as substrings.
EDITOR_FINGERPRINTS = [
    "photoshop",
    "adobe xmp core",
    "lightroom",
    "gimp",
    "snapseed",
    "picsart",
    "canva",
    "exiftool",
    "fotor",
    "paint.net",
    "photoscape",
    "pixlr",
    "photopea",
    "affinity",
    "corel paintshop",
    "editedwithgooglephotos",
    "com.google.android.apps.photos",
    "windows photo editor",
    "paint 3d",
    "com.apple.photos",
]

# Amount by which the embedded thumbnail may differ from the real image
# (mean absolute pixel difference on 0-255) before it is called a mismatch.
THUMBNAIL_DIFF_THRESHOLD = 30.0

# Seconds of difference between capture time and modification time that
# indicate the file was written again after capture.
MODIFIED_AFTER_CAPTURE_THRESHOLD = 120

# Minutes of difference between GPS time and capture time considered notable.
GPS_TIME_MISMATCH_MINUTES = 30

_EXIF_TIME_FORMATS = ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S")


@dataclass
class ForensicFinding:
    """One check result. level: notice | suspicious | edited."""

    level: str
    title: str
    detail: str


@dataclass
class ForensicReport:
    """Full result for one photo."""

    file: str
    verdict: str  # clean | suspicious | edited | no_metadata
    findings: list[ForensicFinding] = field(default_factory=list)
    capture_time: str | None = None
    camera: str | None = None
    gps: str | None = None
    ela_score: float | None = None  # mean recompression error (informational)


def _parse_exif_time(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in _EXIF_TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _format_dt(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _estimate_jpeg_quality(img: Image.Image) -> float | None:
    """Rough IJG-style quality estimate from the JPEG quantization tables."""
    quantization = getattr(img, "quantization", None)
    if not quantization:
        return None
    try:
        from PIL import JpegImagePlugin

        tables = sorted(quantization.values(), key=sum, reverse=True)
        scale = JpegImagePlugin.get_sampling(img) if hasattr(JpegImagePlugin, "get_sampling") else None
        del scale  # quality estimate below does not need sampling
        quality = _quality_from_tables(tables)
        return quality
    except Exception:
        return None


def _quality_from_tables(tables: list[tuple[int, ...]]) -> float | None:
    # Standard tables from the IJG library (PIL uses the same).
    std_luminance = [
        16, 11, 10, 16, 24, 40, 51, 61,
        12, 12, 14, 19, 26, 58, 60, 55,
        14, 13, 16, 24, 40, 57, 69, 56,
        14, 17, 22, 29, 51, 87, 80, 62,
        18, 22, 37, 56, 68, 109, 103, 77,
        24, 35, 55, 64, 81, 104, 113, 92,
        49, 64, 78, 87, 103, 121, 120, 101,
        72, 92, 95, 98, 112, 100, 103, 99,
    ]
    if not tables:
        return None
    table = list(tables[0])
    if len(table) != 64:
        return None
    total = 0.0
    for coef, std in zip(table, std_luminance):
        if coef <= std:
            quality = 50.0 + 5000.0 / max(std, 1)
        else:
            quality = 200.0 - std * 5000.0 / max(coef, 1)
        total += quality
    return total / 64.0


def _extract_thumbnail_bytes(img: Image.Image, raw_bytes: bytes) -> bytes | None:
    """Pull the embedded EXIF thumbnail JPEG out of the raw file bytes."""
    exif_obj = img.getexif()
    offset = exif_obj.get(0x0201)
    length = exif_obj.get(0x0202)
    if not offset or not length:
        return None
    exif_payload = img.info.get("exif")
    if not exif_payload:
        return None
    # Thumbnail offsets are relative to the start of the TIFF stream, which
    # is the exif payload Pillow stores (after the "Exif\0\0" header).
    if isinstance(exif_payload, str):
        return None
    start = offset + 18 if offset < 18 else offset  # guard against odd offsets
    blob = exif_payload[offset : offset + length]
    if len(blob) < 100 or blob[0:2] != b"\xff\xd8":
        return None
    return bytes(blob)


def _thumbnail_matches(img: Image.Image, thumb_bytes: bytes) -> tuple[bool, float]:
    try:
        thumb = Image.open(io.BytesIO(thumb_bytes)).convert("L").resize((32, 32))
    except Exception:
        return True, 0.0  # undecodable thumbnail: do not blame the photo
    main = img.convert("L").resize((32, 32))
    diff = ImageChops.difference(thumb, main)
    stat = ImageStat.Stat(diff)
    mean_diff = sum(stat.mean) / len(stat.mean)
    return mean_diff <= THUMBNAIL_DIFF_THRESHOLD, mean_diff


def _scan_raw_fingerprints(raw_bytes: bytes) -> list[str]:
    lowered = raw_bytes[: 2 * 1024 * 1024].lower()
    hits = []
    for fingerprint in EDITOR_FINGERPRINTS:
        if fingerprint.encode("ascii", errors="ignore").lower() in lowered:
            hits.append(fingerprint)
    return hits


def _gps_summary(gps_ifd: dict) -> tuple[str | None, datetime | None]:
    def to_degrees(value):
        try:
            parts = []
            for item in value:
                if isinstance(item, tuple):
                    parts.append(float(item[0]) / float(item[1]) if float(item[1]) else 0.0)
                else:
                    parts.append(float(item))
            return parts
        except Exception:
            return None

    lat = to_degrees(gps_ifd.get(2))
    lat_ref = str(gps_ifd.get(1, "")).strip().upper()
    lon = to_degrees(gps_ifd.get(4))
    lon_ref = str(gps_ifd.get(3, "")).strip().upper()
    if lat is None or lon is None:
        return None, None
    lat_val = (lat[0] + lat[1] / 60.0 + lat[2] / 3600.0) if len(lat) >= 3 else lat[0]
    lon_val = (lon[0] + lon[1] / 60.0 + lon[2] / 3600.0) if len(lon) >= 3 else lon[0]
    if lat_ref == "S":
        lat_val = -lat_val
    if lon_ref == "W":
        lon_val = -lon_val
    gps_dt = None
    date_text = str(gps_ifd.get(29, "")).strip()
    time_value = to_degrees(gps_ifd.get(7))
    if date_text and time_value and len(time_value) >= 3:
        try:
            gps_dt = datetime.strptime(date_text, "%Y:%m:%d") + timedelta(
                hours=time_value[0], minutes=time_value[1], seconds=time_value[2]
            )
        except Exception:
            gps_dt = None
    return f"{lat_val:.6f}, {lon_val:.6f}", gps_dt


def analyze_photo(source, filename: str | None = None) -> ForensicReport:
    """Analyze one photo. `source` is a filesystem path or raw bytes."""
    if isinstance(source, (str, Path)):
        path = Path(source)
        name = path.name
        try:
            raw_bytes = path.read_bytes()
        except OSError as exc:
            return ForensicReport(file=name, verdict="clean", findings=[
                ForensicFinding("notice", "File unreadable", str(exc))
            ])
        file_mtime = path.stat().st_mtime if path.exists() else None
    else:
        raw_bytes = bytes(source)
        name = filename or "photo"
        file_mtime = None

    findings: list[ForensicFinding] = []
    try:
        img = Image.open(io.BytesIO(raw_bytes))
        img.load()
    except Exception as exc:
        return ForensicReport(file=name, verdict="suspicious", findings=[
            ForensicFinding("suspicious", "Not a decodable image", str(exc))
        ])

    is_jpeg = img.format == "JPEG"
    exif_obj = img.getexif()
    try:
        exif_ifd = exif_obj.get_ifd(0x8769)
    except Exception:
        exif_ifd = {}
    try:
        gps_ifd = exif_obj.get_ifd(0x8825)
    except Exception:
        gps_ifd = {}

    make = str(exif_obj.get(271, "")).strip()
    model = str(exif_obj.get(272, "")).strip()
    software = str(exif_obj.get(305, "")).strip()
    capture_dt = _parse_exif_time(exif_ifd.get(36867))
    modify_dt = _parse_exif_time(exif_obj.get(306))
    capture_text = _format_dt(capture_dt) if capture_dt else None
    camera_text = f"{make} {model}".strip() or None
    gps_text, gps_dt = _gps_summary(gps_ifd)

    if not exif_obj and not img.info.get("exif"):
        return ForensicReport(
            file=name,
            verdict="no_metadata",
            findings=[
                ForensicFinding(
                    "notice",
                    "No EXIF metadata",
                    "The photo carries no camera metadata at all, so tampering "
                    "cannot be judged from metadata. Judge visually or via capture signing.",
                )
            ],
            capture_time=capture_text,
            camera=camera_text,
            gps=gps_text,
        )

    # 1. Editing-software fingerprints.
    if software:
        lowered = software.lower()
        matched = next((f for f in EDITOR_FINGERPRINTS if f in lowered), None)
        if matched:
            findings.append(ForensicFinding(
                "edited",
                "Editing software recorded in EXIF",
                f"Software tag says '{software}' (matched '{matched}') -- the file "
                "passed through an editor after capture.",
            ))
        else:
            findings.append(ForensicFinding(
                "notice",
                "Software tag present",
                f"Software tag: '{software}' (not a known editor).",
            ))
    raw_hits = _scan_raw_fingerprints(raw_bytes)
    if raw_hits:
        findings.append(ForensicFinding(
            "edited",
            "Editing-software fingerprint in metadata",
            f"Found '{raw_hits[0]}'"
            + (f" (+{len(raw_hits) - 1} more)" if len(raw_hits) > 1 else "")
            + " embedded in the file -- an editor wrote into this photo's metadata.",
        ))

    # 2. Embedded thumbnail vs actual image.
    if is_jpeg:
        thumb_bytes = _extract_thumbnail_bytes(img, raw_bytes)
        if thumb_bytes:
            matches, mean_diff = _thumbnail_matches(img, thumb_bytes)
            if not matches:
                findings.append(ForensicFinding(
                    "edited",
                    "EXIF thumbnail does not match the image",
                    f"Embedded preview differs from the photo (mean pixel difference "
                    f"{mean_diff:.1f}/255). A tool rewrote the image but not the preview "
                    "-- a classic sign of metadata (e.g. GPS) spoofing.",
                ))
        else:
            findings.append(ForensicFinding(
                "notice",
                "No embedded thumbnail",
                "Phone cameras normally embed a preview thumbnail; its absence "
                "suggests the file passed through software.",
            ))

    # 3. Time contradictions.
    if capture_dt and modify_dt:
        delta = (modify_dt - capture_dt).total_seconds()
        if delta > MODIFIED_AFTER_CAPTURE_THRESHOLD:
            findings.append(ForensicFinding(
                "edited",
                "Photo modified after capture",
                f"Captured {_format_dt(capture_dt)} but file written "
                f"{_format_dt(modify_dt)} ({delta / 3600:.1f} h later).",
            ))
    elif is_jpeg and not capture_dt:
        findings.append(ForensicFinding(
            "notice",
            "Capture time missing",
            "No DateTimeOriginal in EXIF; original capture time cannot be confirmed.",
        ))
    if capture_dt and file_mtime is not None:
        mtime_dt = datetime.fromtimestamp(file_mtime)
        if (capture_dt - mtime_dt).total_seconds() > 600:
            findings.append(ForensicFinding(
                "suspicious",
                "Capture time is after the file existed",
                f"EXIF says captured {_format_dt(capture_dt)} but the file on disk was "
                f"last written {_format_dt(mtime_dt)} -- timestamps disagree.",
            ))

    # 4. Camera identity.
    if is_jpeg:
        if not camera_text:
            findings.append(ForensicFinding(
                "notice",
                "Camera make/model missing",
                "Real camera photos carry Make/Model; their absence suggests stripping.",
            ))
        has_maker_note = 37500 in exif_obj or 37500 in exif_ifd
        if camera_text and not has_maker_note:
            findings.append(ForensicFinding(
                "notice",
                "Manufacturer maker notes missing",
                f"'{camera_text}' cameras normally embed proprietary maker notes; "
                "they are absent, so the file likely passed through a tool.",
            ))

    # 5. Re-compression evidence.
    if is_jpeg:
        quality = _estimate_jpeg_quality(img)
        if quality is not None and quality < 75:
            findings.append(ForensicFinding(
                "notice",
                "Heavily re-compressed",
                f"JPEG quantization tables suggest quality ~{quality:.0f}. Camera "
                "originals are usually 80-100; low values indicate re-saving by software.",
            ))

    # 6. GPS sanity.
    if gps_ifd:
        if gps_text:
            lat_text, lon_text = gps_text.split(",")
            lat_val = float(lat_text.strip())
            lon_val = float(lon_text.strip())
            if abs(lat_val) > 90 or abs(lon_val) > 180:
                findings.append(ForensicFinding(
                    "suspicious",
                    "Invalid GPS coordinates",
                    f"Coordinates {gps_text} fall outside valid ranges.",
                ))
            elif lat_val == 0.0 and lon_val == 0.0:
                findings.append(ForensicFinding(
                    "suspicious",
                    "Null-island coordinates",
                    "GPS reads exactly 0.000000, 0.000000 -- almost always a "
                    "placeholder written by software, not a real fix.",
                ))
        if capture_dt and gps_dt:
            delta_minutes = abs((capture_dt - gps_dt).total_seconds()) / 60.0
            if delta_minutes > GPS_TIME_MISMATCH_MINUTES:
                findings.append(ForensicFinding(
                    "notice",
                    "GPS time disagrees with capture time",
                    f"GPS stamp and capture time differ by {delta_minutes:.0f} minutes "
                    "(can be a timezone offset, or spoofed metadata).",
                ))

    # Informational ELA score: recompress and measure the error level.
    ela_score = None
    if is_jpeg and img.mode in ("RGB", "L"):
        try:
            buffer = io.BytesIO()
            img.save(buffer, "JPEG", quality=90)
            resaved = Image.open(buffer).convert("L").resize((64, 64))
            base = img.convert("L").resize((64, 64))
            stat = ImageStat.Stat(ImageChops.difference(base, resaved))
            ela_score = sum(stat.mean) / len(stat.mean)
        except Exception:
            ela_score = None

    if any(f.level == "edited" for f in findings):
        verdict = "edited"
    elif any(f.level == "suspicious" for f in findings):
        verdict = "suspicious"
    else:
        verdict = "clean"

    return ForensicReport(
        file=name,
        verdict=verdict,
        findings=findings,
        capture_time=capture_text,
        camera=camera_text,
        gps=gps_text,
        ela_score=ela_score,
    )


def scan_trip(trip_path: Path, image_paths: list[Path] | None = None) -> list[ForensicReport]:
    """Scan every image in a trip folder (or the given list of paths)."""
    if image_paths is None:
        extensions = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
        image_paths = sorted(
            p for p in Path(trip_path).rglob("*")
            if p.is_file() and p.suffix.lower() in extensions and p.suffix.lower() != ".json"
        )
    return [analyze_photo(p) for p in image_paths]


def render_ela_image(source) -> Image.Image | None:
    """Error-level-analysis visualization: bright areas = recompressed/edited zones."""
    try:
        if isinstance(source, (str, Path)):
            img = Image.open(Path(source))
        else:
            img = Image.open(io.BytesIO(bytes(source)))
        img = img.convert("RGB")
        buffer = io.BytesIO()
        img.save(buffer, "JPEG", quality=90)
        resaved = Image.open(buffer).convert("RGB")
        diff = ImageChops.difference(img, resaved)
        # Amplify so subtle tampering zones become visible.
        return diff.point(lambda v: min(255, v * 15))
    except Exception:
        return None


VERDICT_LABELS = {
    "clean": "Clean",
    "suspicious": "Suspicious",
    "edited": "Edited",
    "no_metadata": "No metadata to judge",
}
