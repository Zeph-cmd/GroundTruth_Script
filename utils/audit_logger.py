from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any


AUDIT_LOG_FILE = Path("audit_log.json")
AUDIT_CONFIG_FILE = Path("audit_config.json")

DEFAULT_PASSWORD = "admin123"
MAX_FAILED_ATTEMPTS = 4
LOCK_DURATION_SECONDS = 30 * 60  # 30 minutes


def _load_config() -> dict[str, Any]:
    if AUDIT_CONFIG_FILE.exists():
        try:
            with AUDIT_CONFIG_FILE.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {
        "password": DEFAULT_PASSWORD,
        "failed_attempts": 0,
        "lockout_until": 0,
    }


def _save_config(config: dict[str, Any]) -> None:
    with AUDIT_CONFIG_FILE.open("w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def get_password() -> str:
    return _load_config()["password"]


def set_password(new_password: str) -> None:
    config = _load_config()
    config["password"] = new_password
    config["failed_attempts"] = 0
    config["lockout_until"] = 0
    _save_config(config)


def is_locked() -> bool:
    config = _load_config()
    if config["lockout_until"] <= 0:
        return False
    if time.time() < config["lockout_until"]:
        return True
    # Lock expired — reset
    config["failed_attempts"] = 0
    config["lockout_until"] = 0
    _save_config(config)
    return False


def lockout_remaining_seconds() -> int:
    config = _load_config()
    remaining = int(config["lockout_until"] - time.time())
    return max(0, remaining)


def record_failed_attempt() -> None:
    config = _load_config()
    config["failed_attempts"] = config.get("failed_attempts", 0) + 1
    if config["failed_attempts"] >= MAX_FAILED_ATTEMPTS:
        config["lockout_until"] = time.time() + LOCK_DURATION_SECONDS
    _save_config(config)


def reset_failed_attempts() -> None:
    config = _load_config()
    config["failed_attempts"] = 0
    config["lockout_until"] = 0
    _save_config(config)


def verify_audit_password(password: str) -> bool:
    if is_locked():
        return False
    if password == get_password():
        reset_failed_attempts()
        return True
    record_failed_attempt()
    return False


def _load_audit_log() -> list[dict[str, Any]]:
    if AUDIT_LOG_FILE.exists():
        try:
            with AUDIT_LOG_FILE.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    return []


def _save_audit_log(logs: list[dict[str, Any]]) -> None:
    with AUDIT_LOG_FILE.open("w", encoding="utf-8") as f:
        json.dump(logs, f, indent=2, ensure_ascii=False)


def log_audit_event(event_type: str, details: str) -> None:
    logs = _load_audit_log()
    logs.append({
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "event_type": event_type,
        "details": details,
    })
    _save_audit_log(logs)


def get_audit_logs() -> list[dict[str, Any]]:
    return _load_audit_log()


def clear_audit_logs() -> None:
    _save_audit_log([])
