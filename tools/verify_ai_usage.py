# Verification for the AI assistant gating + per-subscription quota.
#   .venv310\Scripts\python.exe tools\verify_ai_usage.py
import os
import sqlite3
import sys
import winreg
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import licensing
from tools import license_issuer

PROJECT = Path(__file__).resolve().parent.parent
results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))
    print(("PASS" if condition else "FAIL"), "-", name, ("| " + detail if detail else ""))


def _delete_registry_state():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\GroundTruth", 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, "State_v1")
    except FileNotFoundError:
        pass


appdata_lic = Path(os.environ["APPDATA"]) / "GroundTruth" / ".lic"
local_state = Path(os.environ["LOCALAPPDATA"]) / "GroundTruth" / "gtstate.bin"

# 0. Clean slate -> fresh trial (no license)
for p in (appdata_lic, local_state):
    if p.exists():
        p.unlink()
_delete_registry_state()

# 1. Trial user -> AI locked
licensing.ensure_initialized()
usage = licensing.get_ai_usage()
check("trial user locked out of AI", usage["access"] == "trial_locked", usage["access"])

# 2. Activate a 30d license -> allowed with 5-question quota
code30, _ = license_issuer.issue_code(
    "Self Test", licensing.get_device_id(), "30d",
    PROJECT / "license_private_key.pem", PROJECT / "licenses.db",
)
licensing.activate(code30)
usage = licensing.get_ai_usage()
check("monthly subscriber gets 5-question quota", usage["access"] == "allowed" and usage["limit"] == 5 and usage["remaining"] == 5, str(usage))

# 3. Consume all 5 -> limit reached
for _ in range(5):
    licensing.consume_ai_use()
usage = licensing.get_ai_usage()
check("limit reached after 5 questions", usage["access"] == "limit_reached" and usage["used"] == 5, str(usage))

# 4. Tamper test: delete one store -> quota NOT reset
appdata_lic.unlink()
usage = licensing.get_ai_usage()
check("store deletion does not refill quota", usage["access"] == "limit_reached", usage["access"])

# 5. Renewal: activate a 1y code (new serial) -> fresh 30-question quota
code1y, _ = license_issuer.issue_code(
    "Self Test", licensing.get_device_id(), "1y",
    PROJECT / "license_private_key.pem", PROJECT / "licenses.db",
)
licensing.activate(code1y)
usage = licensing.get_ai_usage()
check("yearly renewal resets quota to 30", usage["access"] == "allowed" and usage["limit"] == 30 and usage["used"] == 0, str(usage))

# 6. Cleanup: remove self-test license rows from the ledger
db_path = PROJECT / "licenses.db"
if db_path.exists():
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM activations WHERE customer = 'Self Test'")
    conn.commit()
    conn.close()

failed = [r for r in results if not r[1]]
print()
print(f"{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)
