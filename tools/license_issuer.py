# ---------------------------------------------------------------------------
# tools/license_issuer.py - Issue signed GroundTruth activation codes (SELLER
# machine only). Requires license_private_key.pem (tools/license_keygen.py).
# ---------------------------------------------------------------------------
# Issue a 30-day license:
#   python tools/license_issuer.py --customer "Acme Ltd" --device GT-ABCD-EFGH-JKLM --plan 30d
# Issue a 1-year license:
#   python tools/license_issuer.py --customer "Acme Ltd" --device GT-ABCD-EFGH-JKLM --plan 1y
# List all activations:
#   python tools/license_issuer.py --list
# Export the ledger to CSV:
#   python tools/license_issuer.py --export-csv activations.csv
# ---------------------------------------------------------------------------
from __future__ import annotations

import argparse
import base64
import os
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from licensing import _DATE_EPOCH, PLANS  # noqa: E402

DEFAULT_KEY = PROJECT_DIR / "license_private_key.pem"
DEFAULT_DB = PROJECT_DIR / "licenses.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS activations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issued_at TEXT NOT NULL,
    customer TEXT NOT NULL,
    device_id TEXT NOT NULL,
    plan TEXT NOT NULL,
    expiry TEXT NOT NULL,
    code_serial TEXT NOT NULL UNIQUE,
    code TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    notes TEXT DEFAULT ''
);
"""


def _normalize_device(device: str) -> str:
    cleaned = device.strip().upper().replace("-", "").replace(" ", "")
    if cleaned.startswith("GT"):
        cleaned = cleaned[2:]
    if len(cleaned) != 12:
        raise SystemExit("ERROR: device ID must look like GT-XXXX-XXXX-XXXX (12 characters after GT-).")
    device_id = f"GT-{cleaned[0:4]}-{cleaned[4:8]}-{cleaned[8:12]}"
    # Validate base32 (A-Z, 2-7) and decodable to the 8-byte device hash.
    try:
        base64.b32decode(cleaned + "=" * ((8 - len(cleaned) % 8) % 8))
    except Exception:
        raise SystemExit("ERROR: device ID contains invalid characters (expected base32: A-Z, 2-7).")
    return device_id


def issue_code(customer: str, device_id: str, plan_name: str, key_path: Path, db_path: Path, notes: str = "") -> tuple[str, dict]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if not key_path.exists():
        raise SystemExit(f"ERROR: signing key not found at {key_path}. Run tools/license_keygen.py first.")

    plan_name = plan_name.upper()
    if plan_name not in PLANS:
        raise SystemExit(f"ERROR: unknown plan '{plan_name}'. Choose from: {', '.join(sorted(PLANS))}")
    plan_days = PLANS[plan_name]
    plan_byte = 0 if plan_name == "30D" else 1

    cleaned = device_id.replace("-", "")[2:]  # 12 base32 chars
    device_hash = base64.b32decode(cleaned + "=" * ((8 - len(cleaned) % 8) % 8))
    if len(device_hash) != 7:
        raise SystemExit("ERROR: device ID does not decode to 7 fingerprint bytes.")

    expiry = date.today() + timedelta(days=plan_days)
    expiry_days = (expiry - _DATE_EPOCH).days
    if not 0 <= expiry_days <= 0xFFFF:
        raise SystemExit("ERROR: expiry date out of representable range.")

    serial = os.urandom(2)
    payload = device_hash + bytes([plan_byte]) + expiry_days.to_bytes(2, "little") + serial

    private_key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        raise SystemExit("ERROR: the key file is not an ed25519 private key.")
    signature = private_key.sign(payload)

    raw = payload + signature
    b32 = base64.b32encode(raw).decode("ascii").rstrip("=")
    chunks = [b32[i : i + 6] for i in range(0, len(b32), 6)]
    code = "GT-" + "-".join(chunks)

    record = {
        "serial": serial.hex().upper(),
        "plan": plan_name,
        "expiry": expiry.isoformat(),
        "device_id": device_id,
        "customer": customer,
        "code": code,
    }

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(SCHEMA)
        conn.execute(
            "INSERT INTO activations (issued_at, customer, device_id, plan, expiry, code_serial, code, status, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)",
            (date.today().isoformat(), customer, device_id, plan_name, expiry.isoformat(), record["serial"], code, notes),
        )
        conn.commit()
    finally:
        conn.close()
    return code, record


def list_activations(db_path: Path) -> None:
    if not db_path.exists():
        print("No ledger yet (licenses.db does not exist).")
        return
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, issued_at, customer, device_id, plan, expiry, code_serial, status FROM activations ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        print("Ledger is empty.")
        return
    print(f"{'ID':>4}  {'Issued':<12} {'Customer':<24} {'Device ID':<17} {'Plan':<5} {'Expiry':<12} {'Serial':<6} Status")
    for row in rows:
        print(f"{row[0]:>4}  {row[1]:<12} {row[2]:<24} {row[3]:<17} {row[4]:<5} {row[5]:<12} {row[6]:<6} {row[7]}")


def export_csv(db_path: Path, csv_path: Path) -> None:
    import csv

    if not db_path.exists():
        raise SystemExit("ERROR: no ledger yet (licenses.db does not exist).")
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, issued_at, customer, device_id, plan, expiry, code_serial, code, status, notes FROM activations ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "issued_at", "customer", "device_id", "plan", "expiry", "code_serial", "code", "status", "notes"])
        writer.writerows(rows)
    print(f"Exported {len(rows)} row(s) to {csv_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Issue signed GroundTruth activation codes.")
    parser.add_argument("--customer", help="Customer or company name (ledger record).")
    parser.add_argument("--device", help="Customer's device ID, e.g. GT-ABCD-EFGH-JKLM")
    parser.add_argument("--plan", default="30d", help="30d or 1y (default: 30d)")
    parser.add_argument("--key", default=str(DEFAULT_KEY), help="Path to the ed25519 private key PEM.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to the SQLite ledger.")
    parser.add_argument("--notes", default="", help="Optional free-text note stored in the ledger.")
    parser.add_argument("--list", action="store_true", help="List all issued activations.")
    parser.add_argument("--export-csv", help="Export the ledger to a CSV file and exit.")
    args = parser.parse_args()

    db_path = Path(args.db)
    if args.export_csv:
        export_csv(db_path, Path(args.export_csv))
        return 0
    if args.list:
        list_activations(db_path)
        return 0

    if not args.customer or not args.device:
        parser.error("--customer and --device are required to issue a code (or use --list / --export-csv).")

    device_id = _normalize_device(args.device)
    code, record = issue_code(args.customer, device_id, args.plan, Path(args.key), db_path, args.notes)

    print()
    print("=" * 60)
    print(f"Customer : {record['customer']}")
    print(f"Device   : {record['device_id']}")
    print(f"Plan     : {record['plan']} (expires {record['expiry']})")
    print(f"Serial   : {record['serial']}")
    print(f"Ledger   : {db_path}")
    print("-" * 60)
    print("Activation code (send this to the customer):")
    print()
    print(code)
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
