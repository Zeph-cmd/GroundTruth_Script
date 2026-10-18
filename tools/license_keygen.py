# ---------------------------------------------------------------------------
# tools/license_keygen.py - ONE-TIME setup: generate the GroundTruth signing
# keypair. Run on the SELLER machine only.
# ---------------------------------------------------------------------------
#   python tools/license_keygen.py
#
# Outputs:
#   1. license_private_key.pem  (gitignored - BACK THIS UP SAFELY, it is the
#      signing authority for every activation code you will ever issue)
#   2. Prints the public key and patches licensing.py with it automatically.
#
# The private key NEVER goes into the exe. Only the public key ships.
# ---------------------------------------------------------------------------
from __future__ import annotations

import base64
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
KEY_PATH = PROJECT_DIR / "license_private_key.pem"
LICENSING_PATH = PROJECT_DIR / "licensing.py"


def main() -> int:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        print("ERROR: 'cryptography' is not installed. Run: pip install cryptography")
        return 1

    if KEY_PATH.exists():
        answer = input(f"{KEY_PATH.name} already exists. Overwrite? Codes signed with the OLD key will stop working! [y/N] ")
        if answer.strip().lower() != "y":
            print("Aborted. Existing key kept.")
            return 0

    private_key = Ed25519PrivateKey.generate()
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    KEY_PATH.write_bytes(pem)

    public_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    public_b32 = base64.b32encode(public_raw).decode("ascii").rstrip("=")

    source = LICENSING_PATH.read_text(encoding="utf-8")
    needle = 'LICENSE_PUBLIC_KEY_B32 = "'
    start = source.index(needle) + len(needle)
    end = source.index('"', start)
    existing = source[start:end]
    if existing and "--force" not in sys.argv:
        print(f"licensing.py already contains a public key ({existing[:8]}...). Use --force to replace it.")
    else:
        source = source[:start] + public_b32 + source[end:]
        LICENSING_PATH.write_text(source, encoding="utf-8")
        print("licensing.py updated with the new public key.")

    print()
    print("=" * 60)
    print("Private key written to: ", KEY_PATH)
    print("  -> BACK IT UP somewhere safe (USB/password manager).")
    print("  -> It is gitignored; never commit or ship it.")
    print("Public key (embedded in licensing.py):")
    print(" ", public_b32)
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
