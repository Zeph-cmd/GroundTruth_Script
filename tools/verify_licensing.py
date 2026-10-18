# End-to-end verification for the GroundTruth licensing scheme.
# Run: .venv310\Scripts\python.exe tools\verify_licensing.py
import base64
import json
import os
import sqlite3
import sys
import winreg
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import licensing
from tools import license_issuer

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
project = Path(__file__).resolve().parent.parent

# --- 0. Clean slate: remove any sealed stores ---
for p in (appdata_lic, local_state):
    if p.exists():
        p.unlink()
_delete_registry_state()

# --- 1. Device ID stable across calls ---
dev1, dev2 = licensing.get_device_id(), licensing.get_device_id()
check("device id stable", dev1 == dev2 and dev1.startswith("GT-") and len(dev1) == len("GT-XXXX-XXXX-XXXX"), dev1)

# --- 2. Legacy migration: plain .lic with old start date adopted ---
appdata_lic.parent.mkdir(parents=True, exist_ok=True)
old_start = date.today() - timedelta(days=40)
appdata_lic.write_text(json.dumps({"start_date": old_start.isoformat()}), encoding="utf-8")
st = licensing.get_status()
check("legacy .lic migrated", st["mode"] == "expired", f"mode={st['mode']}")
check("legacy migration wrote sealed stores", appdata_lic.read_text(encoding="utf-8").startswith("{") and "mac" in appdata_lic.read_text(encoding="utf-8"))

# --- 3. Tamper test: delete the APPDATA store -> NOT reset (registry/localdata kept earliest) ---
appdata_lic.unlink()
st = licensing.get_status()
check("store deletion does not reset trial", st["mode"] == "expired", f"mode={st['mode']} (earliest start kept)")

# --- 4. Issue code for THIS device, tamper variant, foreign device ---
code, rec = license_issuer.issue_code("Self Test", st["device_id"], "30d", project / "license_private_key.pem", project / "licenses.db")
check("code issued + recorded in ledger", len(code) > 40, f"serial={rec['serial']} expiry={rec['expiry']}")

tampered = code[:-2] + ("AA" if code[-2:] != "AA" else "BB")
try:
    licensing.verify_activation_code(tampered)
    tampered_ok = False
except licensing.LicensingError as e:
    tampered_ok = "Invalid" in str(e)
check("tampered code rejected", tampered_ok)

foreign_dev = "GT-AAAA-BBBB-CCCC"
fcode, _ = license_issuer.issue_code("Foreign", foreign_dev, "30d", project / "license_private_key.pem", project / "licenses.db")
try:
    licensing.verify_activation_code(fcode)
    foreign_ok = False
except licensing.LicensingError as e:
    foreign_ok = "different device" in str(e)
check("foreign-device code rejected", foreign_ok)

# --- 5. Activate the real code -> licensed ---
record = licensing.activate(code)
st = licensing.get_status()
expected_until = (date.today() + timedelta(days=30)).isoformat()
check("activation grants licensed status", st["mode"] == "licensed" and st["licensed_until"] == expected_until, f"until={st['licensed_until']}")

# --- 6. Re-activating the same code again is idempotent ---
record2 = licensing.activate(code)
check("re-activation idempotent", record2["serial"] == record["serial"])

# --- 7. Nuke ALL state -> fresh 15-day trial ---
for p in (appdata_lic, local_state):
    if p.exists():
        p.unlink()
_delete_registry_state()
st = licensing.get_status()
check("full wipe restarts trial", st["mode"] == "trial" and st["days_left"] == 15, f"mode={st['mode']} days_left={st['days_left']}")

# --- 8. Re-activate on the fresh state so this machine ends LICENSED ---
record3 = licensing.activate(code)
st = licensing.get_status()
check("final state licensed", st["mode"] == "licensed", f"until={st['licensed_until']}")

# --- 9. Ledger contents ---
conn = sqlite3.connect(project / "licenses.db")
rows = conn.execute("SELECT customer, device_id, plan, status FROM activations").fetchall()
conn.close()
check("ledger rows present", len(rows) >= 2, f"{len(rows)} rows")

failed = [r for r in results if not r[1]]
print()
print(f"{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)
