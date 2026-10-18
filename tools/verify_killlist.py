# ---------------------------------------------------------------------------
# verify_killlist.py - Regression tests for the GroundTruth kill switch.
# ---------------------------------------------------------------------------
# Run:  .venv310\Scripts\python.exe tools\verify_killlist.py
#
# Uses file:// URLs so no network is needed. All caches and lists live in a
# temp directory; the real kill list and license stores are NOT modified.
# Requires license_private_key.pem (seller machine).
# ---------------------------------------------------------------------------
from __future__ import annotations

import base64
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tools"))

import licensing  # noqa: E402
from killlist_tool import _sign_and_save, DEFAULT_KEY  # noqa: E402

PASS, FAIL = 0, 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    mark = "PASS" if ok else "FAIL"
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"[{mark}] {name}" + (f"  ({detail})" if detail and not ok else ""))


def path_url(p: Path) -> str:
    return p.as_uri()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="gt_killlist_"))
    try:
        # --- Isolate the check from the real machine state ---------------
        real_url = licensing.KILLLIST_URL
        real_cache_path = licensing._killlist_cache_path
        real_interval = licensing.KILLLIST_CHECK_INTERVAL
        licensing.KILLLIST_CHECK_INTERVAL = 0  # always re-check
        licensing._killlist_cache_path = lambda: tmp / "killlist.cache.json"
        licensing.get_status()  # ensure_initialized

        # 1. The shipped docs/killlist.json (empty, signed) is valid and allows.
        shipped = json.loads((REPO_ROOT / "docs" / "killlist.json").read_text(encoding="utf-8"))
        check("shipped docs/killlist.json signature valid", licensing._verify_killlist_doc(shipped) is not None)
        licensing.KILLLIST_URL = path_url(REPO_ROOT / "docs" / "killlist.json")
        check("empty list -> allowed", licensing.check_kill_status(force=True) is None)

        # 2. Revoke this device -> blocked.
        device_id = licensing.get_device_id()
        kill_dev = tmp / "kill_dev.json"
        _sign_and_save({"updated": "", "revoked": [{"device": device_id, "reason": "Self Test"}]}, DEFAULT_KEY, kill_dev)
        licensing.KILLLIST_URL = path_url(kill_dev)
        check("revoked device -> blocked", licensing.check_kill_status(force=True) == "Self Test")

        # 3. Offline (unreachable URL) -> cache keeps the kill sticky.
        licensing.KILLLIST_URL = "https://127.0.0.1:1/nothing.json"
        check("offline with cache -> still blocked", licensing.check_kill_status(force=True) == "Self Test")

        # 4. Cache is HMAC-sealed: 'mac' sits at top level; hand-edited cache fails _unseal.
        cache_file = tmp / "killlist.cache.json"
        raw = json.loads(cache_file.read_text(encoding="utf-8"))
        check("cache is sealed (has mac)", "mac" in raw)
        check("unseal rejects hand-edited cache", licensing._unseal("killlist", json.dumps({**raw, "revoked": []})) is None)

        # 5. Restore -> allowed again.
        empty = tmp / "empty.json"
        _sign_and_save({"updated": "", "revoked": []}, DEFAULT_KEY, empty)
        licensing.KILLLIST_URL = path_url(empty)
        check("restored -> allowed", licensing.check_kill_status(force=True) is None)

        # 6. Tampered list (valid doc, edited after signing) -> rejected, falls back to cache of empty.
        tampered = tmp / "tampered.json"
        doc = json.loads(kill_dev.read_text(encoding="utf-8"))
        doc["revoked"] = [{"device": "GT-AAAA-BBBB-CCCC", "reason": "Self Test"}]  # edited, sig now stale
        tampered.write_text(json.dumps(doc), encoding="utf-8")
        licensing.KILLLIST_URL = path_url(tampered)
        check("tampered list -> rejected (stale sig)", licensing.check_kill_status(force=True) is None)
        check("tampered doc fails direct verify", licensing._verify_killlist_doc(doc) is None)

        # 7. Wrong-key signature -> rejected.
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        rogue = tmp / "rogue.json"
        rogue_doc = {"updated": "", "revoked": [{"device": device_id, "reason": "rogue"}]}
        payload = {k: v for k, v in rogue_doc.items()}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        rogue_doc["sig"] = base64.b64encode(Ed25519PrivateKey.generate().sign(canonical)).decode("ascii")
        rogue.write_text(json.dumps(rogue_doc), encoding="utf-8")
        licensing.KILLLIST_URL = path_url(rogue)
        check("rogue-key signature -> rejected", licensing.check_kill_status(force=True) is None)

        # 8. Serial match: revoke by license serial alone.
        state = licensing._load_state()
        today = licensing._effective_today(state)
        lic = licensing._active_license(state, today)
        if lic:
            kill_serial = tmp / "kill_serial.json"
            _sign_and_save(
                {"updated": "", "revoked": [{"serial": lic["serial"], "reason": "serial Self Test"}]},
                DEFAULT_KEY, kill_serial,
            )
            licensing.KILLLIST_URL = path_url(kill_serial)
            check("revoked serial -> blocked", licensing.check_kill_status(force=True) == "serial Self Test")
        else:
            print("[SKIP] serial test (no active license on this machine)")

        # 9. Device-ID kill survives a NEW activation code (device match precedes serial).
        licensing.KILLLIST_URL = path_url(kill_dev)
        check("device kill still blocks", licensing.check_kill_status(force=True) == "Self Test")

        print(f"\n{PASS} passed, {FAIL} failed")
        return 1 if FAIL else 0
    finally:
        licensing.KILLLIST_URL = real_url
        licensing._killlist_cache_path = real_cache_path
        licensing.KILLLIST_CHECK_INTERVAL = real_interval
        licensing._killlist_cache.update({"checked": False, "last_check": 0.0, "reason": None})
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
