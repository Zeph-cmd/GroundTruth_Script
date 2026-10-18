# ---------------------------------------------------------------------------
# licensing.py - Device-bound trial + signed activation codes for GroundTruth
# ---------------------------------------------------------------------------
# Flow:
#   1. Each machine gets a stable Device ID derived from a hardware
#      fingerprint (MachineGuid + volume serial + CPU + hostname).
#   2. First run starts a TRIAL_DAYS free trial, stored in THREE redundant
#      HMAC-sealed locations (APPDATA file, LOCALAPPDATA file, HKCU registry).
#      The earliest recorded start wins, so deleting one store does not
#      reset the trial, and a "last seen" floor defeats clock rollback.
#   3. When the trial expires the customer sends their Device ID, pays, and
#      receives an activation code signed with the seller's ed25519 private
#      key (tools/license_issuer.py). The app embeds ONLY the public key,
#      so codes cannot be forged even from a fully unpacked exe.
#   4. An active license (30-day or 1-year plan) always overrides the trial.
#
# NOTE on offline honesty: an offline scheme can always be defeated by a
# skilled attacker with full disk access (e.g. re-sealing local state using
# the embedded HMAC key). This design raises the bar far above casual
# tampering; the server-based upgrade path closes it completely later.
# ---------------------------------------------------------------------------
from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import json
import os
import socket
import time
from datetime import date, timedelta
from pathlib import Path

APP_NAME = "GroundTruth"
TRIAL_DAYS = 15

# AI Assistant: questions allowed per subscription period (subscribers only).
# Botpress free tier = 100 conversations TOTAL (they do not renew), so the
# quota is per license, not per day: 5 for a 30-day plan, 30 for 1 year.
AI_PLAN_LIMITS = {"30D": 5, "1Y": 30}

# Days from this epoch are packed into 2 bytes in the code payload.
# Supports expiry dates up to ~year 2205.
_DATE_EPOCH = date(2026, 1, 1)

# Plans supported by the issuer. value = days of access.
PLANS = {"30D": 30, "1Y": 365}

# Ed25519 PUBLIC key (raw 32 bytes, base32). Generated once by
# tools/license_keygen.py on the seller machine. The private key NEVER
# ships inside the exe - codes signed by it are the only way to unlock.
LICENSE_PUBLIC_KEY_B32 = "XEWD6QDGOU7YTJ4GGGV2CUXY6A6FT6I4EL2B34HZVX37XTHAJTLA"  # SET BY tools/license_keygen.py

# Local state sealing key. Tamper-evidence only (it ships in the exe);
# its job is to make casual hand-editing of the state stores fail.
_STATE_HMAC_KEY = b"GT-state-seal-v1-8f42c1d9a7b34e5f"

# Code payload layout (12 bytes):
#   device_hash 7 | plan 1 (0=30D, 1=1Y) | expiry_days_le16 | serial 2
_PAYLOAD_LEN = 12
_SIG_LEN = 64


class LicensingError(Exception):
    """Friendly, user-displayable licensing failure."""


# ===========================================================================
# Device fingerprint
# ===========================================================================
def _registry_value(root, path: str, name: str) -> str:
    try:
        import winreg

        with winreg.OpenKey(root, path) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return str(value)
    except Exception:
        return ""


def _machine_guid() -> str:
    import winreg

    return _registry_value(
        winreg.HKEY_LOCAL_MACHINE,
        r"SOFTWARE\Microsoft\Cryptography",
        "MachineGuid",
    )


def _cpu_name() -> str:
    import winreg

    return _registry_value(
        winreg.HKEY_LOCAL_MACHINE,
        r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        "ProcessorNameString",
    )


def _volume_serial() -> str:
    try:
        system_drive = os.environ.get("SystemDrive", "C:") + "\\"
        kernel32 = ctypes.windll.kernel32
        serial = ctypes.c_ulong(0)
        result = kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(system_drive),
            None,
            0,
            ctypes.byref(serial),
            None,
            None,
            None,
            0,
        )
        if result:
            return str(serial.value)
    except Exception:
        pass
    return ""


def _device_hash_bytes() -> bytes:
    parts = "|".join(
        [
            _machine_guid(),
            _volume_serial(),
            _cpu_name(),
            socket.gethostname(),
        ]
    )
    return hashlib.sha256(parts.encode("utf-8")).digest()


def get_device_id() -> str:
    """Stable, copyable device identifier: GT-XXXX-XXXX-XXXX (base32).

    7 fingerprint bytes encode to exactly 12 base32 chars; the issuer decodes
    the ID back to those 7 bytes, so ID and code payload always agree.
    """
    raw = base64.b32encode(_device_hash_bytes()[:7]).decode("ascii").rstrip("=")
    raw = raw[:12]
    return f"GT-{raw[0:4]}-{raw[4:8]}-{raw[8:12]}"


# ===========================================================================
# Redundant sealed state stores
# ===========================================================================
def _user_data_dir() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME


def _local_data_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / APP_NAME


def _set_hidden(path: Path) -> None:
    try:
        if os.name == "nt":
            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x2)  # FILE_ATTRIBUTE_HIDDEN
    except Exception:
        pass


def _clear_hidden(path: Path) -> None:
    try:
        if os.name == "nt":
            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x80)  # NORMAL
    except Exception:
        pass


def _seal(store_id: str, body: dict) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    mac = hmac.new(_STATE_HMAC_KEY, f"{store_id}|{canonical}".encode("utf-8"), hashlib.sha256).hexdigest()
    return json.dumps({**body, "mac": mac}, separators=(",", ":"))


def _unseal(store_id: str, raw: str) -> dict | None:
    try:
        data = json.loads(raw)
        mac = data.pop("mac", "")
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        expected = hmac.new(_STATE_HMAC_KEY, f"{store_id}|{canonical}".encode("utf-8"), hashlib.sha256).hexdigest()
        if hmac.compare_digest(mac, expected):
            return data
    except Exception:
        pass
    return None


def _store_paths() -> list[tuple[str, Path]]:
    return [
        ("appdata", _user_data_dir() / ".lic"),
        ("localdata", _local_data_dir() / "gtstate.bin"),
    ]


def _read_store(store_id: str, path: Path) -> dict | None:
    try:
        if path.exists():
            return _unseal(store_id, path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _write_store(store_id: str, path: Path, body: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _clear_hidden(path)
        path.write_text(_seal(store_id, body), encoding="utf-8")
        _set_hidden(path)
    except Exception:
        pass


_REG_STORE_ID = "registry"
_REG_KEY_PATH = rf"Software\{APP_NAME}"
_REG_VALUE_NAME = "State_v1"


def _read_registry_store() -> dict | None:
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY_PATH) as key:
            raw, _ = winreg.QueryValueEx(key, _REG_VALUE_NAME)
        return _unseal(_REG_STORE_ID, str(raw))
    except Exception:
        return None


def _write_registry_store(body: dict) -> None:
    if os.name != "nt":
        return
    try:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REG_KEY_PATH) as key:
            winreg.SetValueEx(key, _REG_VALUE_NAME, 0, winreg.REG_SZ, _seal(_REG_STORE_ID, body))
    except Exception:
        pass


def _load_state() -> dict:
    """Merge all valid stores: earliest trial start wins, latest last_seen wins."""
    states = []
    for store_id, path in _store_paths():
        state = _read_store(store_id, path)
        if state:
            states.append(state)
    registry_state = _read_registry_store()
    if registry_state:
        states.append(registry_state)

    if not states:
        # Migration: honor a legacy plain-JSON .lic start date if present.
        legacy_start = _read_legacy_start_date()
        start = legacy_start or date.today().isoformat()
        return {"start": start, "last_seen": date.today().isoformat(), "licenses": [], "ai_usage": None}

    starts = [s["start"] for s in states if s.get("start")]
    last_seens = [s["last_seen"] for s in states if s.get("last_seen")]
    licenses: dict[str, dict] = {}
    for state in states:
        for lic in state.get("licenses", []):
            key = f"{lic.get('serial')}|{lic.get('expiry')}"
            licenses[key] = lic

    return {
        "start": min(starts) if starts else date.today().isoformat(),
        "last_seen": max(last_seens) if last_seens else date.today().isoformat(),
        "licenses": list(licenses.values()),
        "ai_usage": _merge_ai_usage(states),
    }


def _merge_ai_usage(states: list[dict]) -> dict | None:
    """Merge AI usage across stores.

    Usage is bound to a license serial. Taking the highest count means
    reverting/deleting one store cannot reset the quota.
    """
    entries = [s.get("ai_usage") for s in states if isinstance(s.get("ai_usage"), dict) and s["ai_usage"].get("serial")]
    if not entries:
        return None
    return max(entries, key=lambda e: e.get("count", 0))


def _save_state(state: dict) -> None:
    for store_id, path in _store_paths():
        _write_store(store_id, path, state)
    _write_registry_store(state)


def _read_legacy_start_date() -> str | None:
    """Read the pre-hardening plain .lic (start_date only), if it exists."""
    legacy = _user_data_dir() / ".lic"
    try:
        if legacy.exists():
            data = json.loads(legacy.read_text(encoding="utf-8"))
            start_text = str(data.get("start_date", ""))[:10]
            if start_text:
                date.fromisoformat(start_text)
                return start_text
    except Exception:
        pass
    return None


def _effective_today(state: dict) -> date:
    """Today, floored at the newest timestamp ever observed (clock-rollback guard)."""
    try:
        last_seen = date.fromisoformat(state["last_seen"])
    except Exception:
        last_seen = date.today()
    return max(date.today(), last_seen)


def ensure_initialized() -> None:
    """Create sealed trial state on first run; migrate legacy state; refresh last-seen."""
    state = _load_state()
    today = date.today().isoformat()
    changed = False
    if not _any_store_exists():
        # First run, or migration from a legacy plain .lic: establish the
        # sealed stores now so the earliest-start defense exists immediately.
        if not _read_legacy_start_date():
            state["start"] = today
        changed = True
    if today > state.get("last_seen", today):
        state["last_seen"] = today
        changed = True
    if changed:
        _save_state(state)


def _any_store_exists() -> bool:
    """True if at least one VALID SEALED store exists (legacy plain .lic doesn't count)."""
    for store_id, path in _store_paths():
        if _read_store(store_id, path) is not None:
            return True
    return _read_registry_store() is not None


# ===========================================================================
# Activation codes (ed25519-signed)
# ===========================================================================
def _load_public_key():
    if not LICENSE_PUBLIC_KEY_B32:
        raise LicensingError("Licensing is not configured (missing public key).")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    padded = LICENSE_PUBLIC_KEY_B32 + "=" * ((8 - len(LICENSE_PUBLIC_KEY_B32) % 8) % 8)
    raw = base64.b32decode(padded)
    return Ed25519PublicKey.from_public_bytes(raw)


def _decode_code(code: str) -> tuple[bytes, bytes]:
    cleaned = code.strip().upper().replace(" ", "")
    if cleaned.startswith("GT-"):
        cleaned = cleaned[3:]
    cleaned = cleaned.replace("-", "")
    if len(cleaned) < 20:
        raise LicensingError("Invalid code.")
    padded = cleaned + "=" * ((8 - len(cleaned) % 8) % 8)
    try:
        raw = base64.b32decode(padded)
    except Exception:
        raise LicensingError("Invalid code.")
    if len(raw) < _PAYLOAD_LEN + _SIG_LEN:
        raise LicensingError("Invalid code.")
    return raw[:_PAYLOAD_LEN], raw[_PAYLOAD_LEN : _PAYLOAD_LEN + _SIG_LEN]


def verify_activation_code(code: str) -> dict:
    """Validate a signed activation code for THIS device.

    Returns {'serial', 'plan', 'expiry' (iso date string)} on success.
    Raises LicensingError with a user-friendly message otherwise.
    """
    payload, signature = _decode_code(code)

    from cryptography.exceptions import InvalidSignature

    try:
        _load_public_key().verify(signature, payload)
    except InvalidSignature:
        raise LicensingError("Invalid code.")
    except LicensingError:
        raise

    device_hash = payload[0:7]
    if device_hash != _device_hash_bytes()[:7]:
        raise LicensingError("This code was issued for a different device.")

    plan_byte = payload[7]
    expiry_days = int.from_bytes(payload[8:10], "little")
    serial = payload[10:12].hex().upper()

    plan = "30D" if plan_byte == 0 else "1Y" if plan_byte == 1 else None
    if plan is None:
        raise LicensingError("Invalid code.")

    expiry = _DATE_EPOCH + timedelta(days=expiry_days)
    if expiry < date.today():
        raise LicensingError("This code has expired. Contact support.")

    return {"serial": serial, "plan": plan, "expiry": expiry.isoformat()}


def activate(code: str) -> dict:
    """Verify a code and persist it as this machine's license."""
    record = verify_activation_code(code)
    state = _load_state()
    key = f"{record['serial']}|{record['expiry']}"
    if key not in {f"{l.get('serial')}|{l.get('expiry')}" for l in state["licenses"]}:
        state["licenses"].append(record)
    _save_state(state)
    return record


# ===========================================================================
# Status
# ===========================================================================
def get_status() -> dict:
    """Return the current licensing status.

    {'mode': 'trial'|'licensed'|'expired',
     'days_left': int|None,
     'licensed_until': iso|None,
     'device_id': str}
    """
    ensure_initialized()
    state = _load_state()
    today = _effective_today(state)

    active = [lic for lic in state.get("licenses", []) if date.fromisoformat(lic["expiry"]) >= today]
    device_id = get_device_id()

    if active:
        licensed_until = max(date.fromisoformat(lic["expiry"]) for lic in active)
        return {
            "mode": "licensed",
            "days_left": (licensed_until - today).days,
            "licensed_until": licensed_until.isoformat(),
            "device_id": device_id,
        }

    try:
        start = date.fromisoformat(state["start"])
    except Exception:
        start = today
    days_left = TRIAL_DAYS - (today - start).days
    if days_left > 0:
        return {"mode": "trial", "days_left": days_left, "licensed_until": None, "device_id": device_id}
    # Expired: distinguish a lapsed subscription from a finished trial.
    had_license = bool(state.get("licenses"))
    return {
        "mode": "expired",
        "days_left": 0,
        "days_over": -days_left,
        "reason": "subscription" if had_license else "trial",
        "licensed_until": None,
        "device_id": device_id,
    }


# ===========================================================================
# AI Assistant usage (subscribers only; quota per license, see AI_PLAN_LIMITS)
# ===========================================================================
def _active_license(state: dict, today: date) -> dict | None:
    """The governing license: the active one that expires last."""
    active = [
        lic for lic in state.get("licenses", [])
        if date.fromisoformat(lic["expiry"]) >= today
    ]
    if not active:
        return None
    return max(active, key=lambda lic: date.fromisoformat(lic["expiry"]))


def get_ai_usage() -> dict:
    """Return AI assistant access for this device.

    {'access': 'allowed'|'trial_locked'|'expired'|'limit_reached',
     'used': int, 'remaining': int, 'limit': int}
    """
    status = get_status()
    if status["mode"] == "trial":
        return {"access": "trial_locked", "used": 0, "remaining": 0, "limit": 0}
    if status["mode"] == "expired":
        return {"access": "expired", "used": 0, "remaining": 0, "limit": 0}

    state = _load_state()
    today = _effective_today(state)
    license_record = _active_license(state, today)
    limit = AI_PLAN_LIMITS.get(license_record.get("plan"), AI_PLAN_LIMITS["30D"]) if license_record else 0

    usage = state.get("ai_usage") or {}
    # Usage is bound to the governing license serial; activating a new code
    # (new serial) starts a fresh quota automatically.
    used = usage.get("count", 0) if usage.get("serial") == (license_record or {}).get("serial") else 0
    remaining = max(limit - used, 0)
    if remaining == 0:
        return {"access": "limit_reached", "used": used, "remaining": 0, "limit": limit}
    return {"access": "allowed", "used": used, "remaining": remaining, "limit": limit}


def consume_ai_use() -> dict:
    """Record one AI question against the active license. Call only AFTER a successful send."""
    state = _load_state()
    today = _effective_today(state)
    license_record = _active_license(state, today)
    if not license_record:
        return {"used": 0, "remaining": 0}

    usage = state.get("ai_usage") or {}
    used = usage.get("count", 0) if usage.get("serial") == license_record.get("serial") else 0
    state["ai_usage"] = {"serial": license_record.get("serial"), "count": used + 1}
    if today.isoformat() > state.get("last_seen", ""):
        state["last_seen"] = today.isoformat()
    _save_state(state)
    limit = AI_PLAN_LIMITS.get(license_record.get("plan"), AI_PLAN_LIMITS["30D"])
    return {"used": used + 1, "remaining": max(limit - used - 1, 0)}


# ===========================================================================
# Remote kill switch (per-device revocation via a signed kill list)
# ===========================================================================
# A small ed25519-signed JSON file hosted on GitHub Pages lists revoked
# devices/serials. The seller edits one JSON, pushes, and that ONE device
# locks with the "Deactivated" dialog - everyone else keeps working.
# Offline behaviour is fail-open but sticky: the last-known-good list is
# cached (HMAC-sealed), so a device that has seen a kill stays dead even
# with no internet. Never raises.
KILLLIST_URL = "https://zeph-cmd.github.io/GroundTruth_Script/killlist.json"
KILLLIST_CHECK_INTERVAL = 600  # seconds between live re-checks

_killlist_cache = {"checked": False, "last_check": 0.0, "reason": None}


def _killlist_cache_path() -> Path:
    return _user_data_dir() / "killlist.cache.json"


def _killlist_canonical(doc: dict) -> bytes:
    """Canonical bytes that the signature covers (everything except 'sig')."""
    payload = {k: v for k, v in doc.items() if k != "sig"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _verify_killlist_doc(doc: object) -> dict | None:
    """Return the doc if it is a dict with a valid embedded signature, else None."""
    if not isinstance(doc, dict) or not doc.get("sig"):
        return None
    try:
        sig = base64.b64decode(doc["sig"])
        _load_public_key().verify(sig, _killlist_canonical(doc))
        return doc
    except Exception:
        return None


def _read_killlist_cache() -> dict | None:
    try:
        return _unseal("killlist", _killlist_cache_path().read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_killlist_cache(doc: dict) -> None:
    try:
        _user_data_dir().mkdir(parents=True, exist_ok=True)
        _killlist_cache_path().write_text(_seal("killlist", doc), encoding="utf-8")
    except Exception:
        pass


def check_kill_status(force: bool = False) -> str | None:
    """Return the revocation reason for THIS device, or None if allowed.

    Live fetch is throttled to one request per KILLLIST_CHECK_INTERVAL
    seconds; between checks (and whenever offline) the last-known-good
    cached list decides. Matching is on Device ID first, then license
    serial (so a device revoked by ID stays dead even with a new code).
    """
    cache = _killlist_cache
    now = time.time()
    if not force and cache["checked"] and (now - cache["last_check"]) < KILLLIST_CHECK_INTERVAL:
        return cache["reason"]

    cache["checked"] = True
    cache["last_check"] = now

    doc = None
    if KILLLIST_URL:
        try:
            import urllib.request

            req = urllib.request.Request(
                KILLLIST_URL, headers={"User-Agent": f"{APP_NAME}-Licensing/1.0"}
            )
            with urllib.request.urlopen(req, timeout=4) as resp:
                doc = _verify_killlist_doc(json.loads(resp.read().decode("utf-8")))
        except Exception:
            doc = None

    if doc is not None:
        _write_killlist_cache(doc)
    else:
        doc = _verify_killlist_doc(_read_killlist_cache())

    reason = None
    if doc is not None:
        try:
            device_id = get_device_id()
        except Exception:
            device_id = None
        state = _load_state()
        today = _effective_today(state)
        serial = (_active_license(state, today) or {}).get("serial", "")
        for entry in doc.get("revoked") or []:
            if not isinstance(entry, dict):
                continue
            if device_id and entry.get("device") == device_id:
                reason = entry.get("reason") or "deactivated"
                break
            if serial and entry.get("serial") and str(entry.get("serial")).upper() == str(serial).upper():
                reason = entry.get("reason") or "deactivated"
                break

    cache["reason"] = reason
    return reason
