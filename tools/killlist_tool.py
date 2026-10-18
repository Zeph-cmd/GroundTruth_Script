# ---------------------------------------------------------------------------
# killlist_tool.py - Seller-side manager for the GroundTruth kill switch.
# ---------------------------------------------------------------------------
# RUN ON THE SELLER MACHINE ONLY (needs license_private_key.pem, never
# distributed). It edits docs/killlist.json in the repo, signs it with the
# same ed25519 key used for activation codes, and you `git push` to publish:
#
#   git add docs/killlist.json && git commit -m "killswitch" && git push
#
# Examples:
#   python tools/killlist_tool.py --list
#   python tools/killlist_tool.py --revoke --device GT-KILS-5FED-EJAA --reason chargeback
#   python tools/killlist_tool.py --revoke --serial D308 --reason refund
#   python tools/killlist_tool.py --restore --device GT-KILS-5FED-EJAA
# ---------------------------------------------------------------------------
from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LIST = REPO_ROOT / "docs" / "killlist.json"
DEFAULT_KEY = REPO_ROOT / "license_private_key.pem"


def _canonical(doc: dict) -> bytes:
    payload = {k: v for k, v in doc.items() if k != "sig"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _load_private_key(key_path: Path):
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    return load_pem_private_key(key_path.read_bytes(), password=None)


def _load_list(path: Path) -> dict:
    if not path.exists():
        return {"updated": "", "revoked": []}
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc.setdefault("revoked", [])
    return doc


def _sign_and_save(doc: dict, key_path: Path, path: Path) -> None:
    doc["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc.pop("sig", None)
    signature = _load_private_key(key_path).sign(_canonical(doc))
    doc["sig"] = base64.b64encode(signature).decode("ascii")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage the GroundTruth kill list.")
    parser.add_argument("--list", action="store_true", help="Show current revoked entries.")
    parser.add_argument("--revoke", action="store_true", help="Add a device/serial to the kill list.")
    parser.add_argument("--restore", action="store_true", help="Remove a device/serial from the kill list.")
    parser.add_argument("--device", help="Device ID, e.g. GT-KILS-5FED-EJAA")
    parser.add_argument("--serial", help="License serial (2 hex chars), e.g. D308")
    parser.add_argument("--reason", default="deactivated", help="Internal note (NOT shown to the client).")
    parser.add_argument("--file", type=Path, default=DEFAULT_LIST, help=f"Kill list path (default {DEFAULT_LIST})")
    parser.add_argument("--key", type=Path, default=DEFAULT_KEY, help=f"Private key path (default {DEFAULT_KEY})")
    args = parser.parse_args()

    if not (args.list or args.revoke or args.restore):
        parser.error("choose one of --list / --revoke / --restore")
    if (args.revoke or args.restore) and not (args.device or args.serial):
        parser.error("--revoke/--restore need --device and/or --serial")
    if (args.revoke or args.restore) and not args.key.exists():
        print(f"ERROR: private key not found: {args.key}", file=sys.stderr)
        return 1

    doc = _load_list(args.file)
    changed = False

    if args.list:
        pass
    elif args.revoke:
        entry = {}
        if args.device:
            entry["device"] = args.device.strip().upper()
        if args.serial:
            entry["serial"] = args.serial.strip().upper()
        entry["reason"] = args.reason
        existing = {
            json.dumps({k: v for k, v in e.items() if k != "reason"}, sort_keys=True)
            for e in doc["revoked"] if isinstance(e, dict)
        }
        if json.dumps({k: v for k, v in entry.items() if k != "reason"}, sort_keys=True) in existing:
            print("Already on the kill list - nothing to do.")
        else:
            doc["revoked"].append(entry)
            changed = True
            print(f"REVOKED: {entry.get('device') or entry.get('serial')}")
    elif args.restore:
        before = len(doc["revoked"])
        doc["revoked"] = [
            e for e in doc["revoked"]
            if not (
                (not args.device or str(e.get("device", "")).upper() == args.device.strip().upper())
                and (not args.serial or str(e.get("serial", "")).upper() == args.serial.strip().upper())
            )
        ]
        changed = len(doc["revoked"]) != before
        print("RESTORED (removed from kill list)." if changed else "No matching entry found - nothing to do.")

    if changed:
        _sign_and_save(doc, args.key, args.file)
        print(f"Signed and saved: {args.file}")
        print("Now publish it:")
        print('  git add docs/killlist.json && git commit -m "killswitch update" && git push')

    print(f"\nKill list ({args.file}) - {len(doc['revoked'])} revoked:")
    for e in doc["revoked"]:
        print(f"  - device={e.get('device', '-')}  serial={e.get('serial', '-')}  reason={e.get('reason', '-')}")
    if not doc["revoked"]:
        print("  (empty)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
