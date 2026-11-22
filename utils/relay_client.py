"""
Cloud trip relay client for the GroundTruth Explorer (PC receiving side).

Talks to the `trip-relay` Supabase Edge Function using a server-issued device
credential. The credential secret is generated on this machine and is stored
locally; the server only ever keeps its SHA-256 hash. All relay features are
disabled server-side by default (trip_relay_config.enabled = false), so every
function here fails softly when the feature is off or unreachable.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import shutil
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

SUPABASE_URL = "https://tmzimdybfwbdwkjlvekx.supabase.co"
# Public anon key (safe to ship; relay RPCs are service-role only).
SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InRtemltZHliZndiZHdramx2ZWt4Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzUwNjIwNDIsImV4cCI6MjA5MDYzODA0Mn0."
    "PIDqi0nfXwZcSXp166hg3IO1Zle1JV__nAsbta_CTvU"
)
RELAY_URL = f"{SUPABASE_URL}/functions/v1/trip-relay"

STATUS_CACHE_SECONDS = 60


class RelayError(Exception):
    """User-displayable relay failure. .code is a stable machine-readable tag."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


def _credential_path() -> Path:
    appdata = os.environ.get("APPDATA", str(Path.home()))
    return Path(appdata) / "GroundTruth" / "relay_device.json"


# ---------------------------------------------------------------------------
# Status / enrollment
# ---------------------------------------------------------------------------
def relay_enabled(timeout: float = 5.0) -> bool:
    """True only if the server reports the relay enabled. Any failure -> False."""
    try:
        data = _request("GET", None, timeout=timeout)
        return bool(data.get("enabled"))
    except Exception:
        return False


def get_credential() -> dict[str, Any] | None:
    try:
        data = json.loads(_credential_path().read_text(encoding="utf-8"))
        secret = data.get("secret_hex", "")
        if len(secret) == 64 and all(c in "0123456789abcdef" for c in secret):
            return data
    except Exception:
        pass
    return None


def enroll(license_code: str, fingerprint: str, timeout: float = 8.0) -> dict[str, Any]:
    """Enroll this PC against a paid license code and store the credential."""
    code = (license_code or "").strip()
    fingerprint = (fingerprint or "").strip()
    if not code or not fingerprint:
        raise RelayError("invalid_request", "License code and device ID are required.")
    secret_hex = secrets.token_hex(32)
    try:
        data = _request(
            "POST",
            {
                "action": "enroll",
                "license_code": code,
                "device_type": "pc",
                "device_fingerprint": fingerprint,
            },
            credential_hex=secret_hex,
            timeout=timeout,
        )
    except RelayError as exc:
        if exc.code == "unauthorized":
            raise RelayError(
                "unauthorized",
                "Cloud sharing could not be connected. Check that this code is "
                "activated on this PC and that a free trial was started with the "
                "same email before paying.",
            )
        raise
    record = {
        "secret_hex": secret_hex,
        "device_id": data.get("device_id"),
        "enrolled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path = _credential_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def ensure_enrolled(license_code: str, fingerprint: str) -> dict[str, Any]:
    """Return the stored credential, enrolling first if needed."""
    record = get_credential()
    if record:
        return record
    return enroll(license_code, fingerprint)


# ---------------------------------------------------------------------------
# Inbox / download / acknowledge
# ---------------------------------------------------------------------------
def list_inbox(timeout: float = 8.0) -> list[dict[str, Any]]:
    record = get_credential()
    if not record:
        return []
    data = _request(
        "POST", {"action": "list"}, credential_hex=record["secret_hex"], timeout=timeout
    )
    trips = data.get("trips") or []
    return [t for t in trips if isinstance(t, dict)]


def claim(trip_id: str, timeout: float = 8.0) -> dict[str, Any]:
    record = get_credential()
    if not record:
        raise RelayError("unauthorized", "Cloud sharing is not connected on this PC.")
    data = _request(
        "POST",
        {"action": "claim", "trip_id": trip_id},
        credential_hex=record["secret_hex"],
        timeout=timeout,
    )
    if not data.get("claim_token") or not data.get("download_url"):
        raise RelayError("invalid_response", "Cloud server returned an incomplete response.")
    return data


def acknowledge(trip_id: str, claim_token: str, timeout: float = 8.0) -> None:
    record = get_credential()
    if not record:
        raise RelayError("unauthorized", "Cloud sharing is not connected on this PC.")
    _request(
        "POST",
        {"action": "ack", "trip_id": trip_id, "claim_token": claim_token},
        credential_hex=record["secret_hex"],
        timeout=timeout,
    )


def download_trip(trip: dict[str, Any], dest_dir: Path, timeout: float = 900.0) -> Path:
    """Claim, download, verify (size + sha256) and acknowledge a cloud trip.

    Returns the local .rvtrip path. The local copy is always kept if it is
    complete, even when the acknowledgment fails.
    """
    trip_id = str(trip.get("trip_id") or "")
    expected_size = int(trip.get("size_bytes") or 0)
    if not trip_id or expected_size <= 0:
        raise RelayError("invalid_request", "This cloud trip entry is incomplete.")

    claim_data = claim(trip_id)
    download_url = claim_data["download_url"]
    claim_token = claim_data["claim_token"]

    dest_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="relay_download_", suffix=".rvtrip", dir=str(dest_dir))
    hasher = hashlib.sha256()
    received = 0
    try:
        with os.fdopen(fd, "wb") as out:
            request = urllib.request.Request(download_url, method="GET")
            with urllib.request.urlopen(request, timeout=timeout) as response:
                while True:
                    chunk = response.read(1024 * 512)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > expected_size:
                        raise RelayError("size_mismatch", "Downloaded data is larger than expected.")
                    hasher.update(chunk)
                    out.write(chunk)
        if received != expected_size:
            raise RelayError(
                "size_mismatch",
                f"Download incomplete ({received} of {expected_size} bytes). Please retry.",
            )
        declared_sha = str(trip.get("sha256") or "")
        if declared_sha:
            declared_normalized = declared_sha.replace("\\x", "").lower()
            if hasher.hexdigest() != declared_normalized:
                raise RelayError(
                    "hash_mismatch",
                    "Downloaded file does not match the cloud copy. Please retry.",
                )
        final_path = dest_dir / f"{Path(str(trip.get('object_key') or trip_id)).stem}.rvtrip"
        os.replace(tmp_name, final_path)
    except RelayError:
        _discard(tmp_name)
        raise
    except urllib.error.HTTPError as exc:
        _discard(tmp_name)
        if exc.code == 400:
            raise RelayError("not_available", "This cloud trip is no longer available.")
        raise RelayError("download_failed", f"Cloud download failed (HTTP {exc.code}).")
    except urllib.error.URLError:
        _discard(tmp_name)
        raise RelayError("download_failed", "Could not reach the cloud server. Check your internet.")
    except Exception:
        _discard(tmp_name)
        raise

    try:
        acknowledge(trip_id, claim_token)
    except RelayError:
        # The complete, verified copy is safe on disk; the server will expire
        # and clean the trip when its window ends. Never delete the local file.
        pass

    _copy_to_downloads(final_path)
    return final_path


def downloads_dir() -> Path:
    """User-friendly secondary location for downloaded cloud trips."""
    return Path.home() / "Downloads" / "GroundTruth"


def read_trip_label(rvtrip_path: Path) -> str | None:
    """Human-readable trip name read from metadata.json inside the package.

    Uses the stored tripName when Vision provides one; otherwise composes
    "Trip {id} — {date} — {N} photos". Returns None when the package has no
    readable metadata, so callers can fall back to the filename.
    """
    try:
        with zipfile.ZipFile(rvtrip_path, "r") as archive:
            metadata_name = next(
                (name for name in archive.namelist() if Path(name).name == "metadata.json"),
                None,
            )
            if not metadata_name:
                return None
            data = json.loads(archive.read(metadata_name).decode("utf-8"))
        if not isinstance(data, dict):
            return None
        trip_name = data.get("tripName") or data.get("trip_name")
        if isinstance(trip_name, str) and trip_name.strip():
            return trip_name.strip()
        parts = []
        trip_id = data.get("tripId") or data.get("trip_id")
        if trip_id is not None:
            parts.append(f"Trip {trip_id}")
        end_time = data.get("endTime") or data.get("end_time")
        if isinstance(end_time, str) and end_time:
            parts.append(end_time[:10])
        photo_count = data.get("photoCount") or data.get("photo_count") or data.get("total_images")
        if photo_count:
            parts.append(f"{photo_count} photos")
        return " — ".join(parts) if parts else None
    except Exception:
        return None


def _copy_to_downloads(final_path: Path) -> None:
    """Best-effort copy of a verified download to Downloads\\GroundTruth.

    The primary copy in trips/_relay must never be lost because of this, so
    any failure here is logged and swallowed.
    """
    try:
        target_dir = downloads_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(final_path, target_dir / final_path.name)
    except OSError:
        logging.getLogger(__name__).debug(
            "Could not copy downloaded trip to Downloads folder", exc_info=True
        )


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------
def _discard(tmp_name: str) -> None:
    try:
        os.unlink(tmp_name)
    except OSError:
        pass


def _request(
    method: str,
    payload: dict[str, Any] | None,
    credential_hex: str | None = None,
    timeout: float = 8.0,
) -> dict[str, Any]:
    headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Content-Type": "application/json",
    }
    if credential_hex:
        headers["x-relay-credential"] = credential_hex
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(RELAY_URL, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
            code = str(detail.get("error") or "http_error")
        except Exception:
            code = "http_error"
        friendly = {
            "relay_disabled": "Cloud sharing is currently switched off.",
            "unauthorized": "Cloud sharing is not authorized on this PC.",
            "trip_limit_reached": "Cloud trip limit reached (5 at a time). Wait for a trip to expire or be downloaded.",
            "storage_full": "The cloud storage is temporarily full. Try again later.",
            "not_available": "This cloud trip is no longer available.",
            "download_in_progress": "This trip is already being downloaded on another session.",
            "not_acknowledgeable": "This cloud trip can no longer be confirmed.",
            "not_reservable": "The cloud upload could not be confirmed.",
            "rate_limited": "Too many requests. Please wait a moment and try again.",
        }.get(code, f"Cloud server error ({code}).")
        raise RelayError(code, friendly)
    except urllib.error.URLError:
        raise RelayError("unreachable", "Could not reach the cloud server. Check your internet.")
    except TimeoutError:
        # socket.timeout during read/connect; urlopen does not always wrap it.
        raise RelayError("unreachable", "The cloud server took too long to respond. Try again.")
    except OSError:
        raise RelayError("unreachable", "Network problem while contacting the cloud server.")
    except (ValueError, KeyError):
        raise RelayError("invalid_response", "Cloud server returned an unexpected response.")
