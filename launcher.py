# ---------------------------------------------------------------------------
# launcher.py - Trial gate + signed activation-code unlock for GroundTruth
# ---------------------------------------------------------------------------
# 1. First run starts a 15-day free trial, bound to this device's fingerprint
#    and stored redundantly (see licensing.py).
# 2. Every run, the trial clock is checked. When expired, launch is blocked
#    and a small Tk window shows this machine's Device ID and asks for an
#    activation code.
# 3. Activation codes are ed25519-signed by the seller (tools/license_issuer.py)
#    and bound to the Device ID. The exe contains only the public key, so
#    codes cannot be forged from a unpacked build.
# 4. A valid code grants access for its plan duration (30-day or 1-year).
# 5. Built with PyInstaller --onefile, no console window.
# ---------------------------------------------------------------------------
import os
import sys
from datetime import date
from pathlib import Path

# Enable faulthandler early so we get tracebacks on segfaults/crashes
try:
    import faulthandler

    faulthandler.enable()
except Exception:
    pass

# ===========================================================================
# CONFIG
# ===========================================================================
APP_NAME = "GroundTruth"
TRIAL_DAYS = 15  # free trial per machine (see licensing.py)

# ===========================================================================
# Windowed-mode fix + crash logging (PyInstaller --windowed sets stdout/stderr
# to None, so all errors are silently swallowed)
# ===========================================================================
if getattr(sys, "frozen", False):
    try:
        _crash_log_path = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME / "crash.log"
        _crash_log_path.parent.mkdir(parents=True, exist_ok=True)
        _log_file = open(_crash_log_path, "w", encoding="utf-8")
        sys.stdout = _log_file
        sys.stderr = _log_file
    except Exception:
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
else:
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")

# ===========================================================================
# Paths - user data lives in %APPDATA%\GroundTruth so the exe is fully portable
# ===========================================================================
USER_DATA_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

# Make frozen app modules importable (launcher.py, licensing.py, app.py, utils/)
if getattr(sys, "frozen", False):
    base = Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS") else Path(sys.executable).parent
    for p in (str(base), str(base / "src")):
        if p not in sys.path:
            sys.path.insert(0, p)

# Run from the user data dir so trips/uploads persist
os.chdir(str(USER_DATA_DIR))

# Write a Streamlit config.toml in the CWD so it's picked up as the
# "project-level" config during the *initial* config parse (before conflict
# checks run). Setting global.developmentMode=false here is the ONLY reliable
# way to avoid the "server.port does not work when global.developmentMode is
# true" RuntimeError — env vars only work for "sensitive" options, and
# flag_options are applied too late by bootstrap.run().
_streamlit_conf_dir = USER_DATA_DIR / ".streamlit"
_streamlit_conf_dir.mkdir(parents=True, exist_ok=True)
(_streamlit_conf_dir / "config.toml").write_text(
    "\n".join(
        [
            "[global]",
            "developmentMode = false",
            "",
            "[server]",
            "headless = true",
            "port = 8502",
            'address = "127.0.0.1"',
            "runOnSave = false",
            "maxUploadSize = 2048",
            "",
            "[browser]",
            "gatherUsageStats = false",
            "",
        ]
    ),
    encoding="utf-8",
)

import licensing  # noqa: E402  (after sys.path setup so frozen builds resolve it)


# ===========================================================================
# Locked dialog (Tk)
# ===========================================================================
DEVELOPER_WEBSITE = "https://zephobed.me"
DEVELOPER_PHONE_WHATSAPP = "0257711831"


def show_deactivated_dialog(reason: str) -> None:
    """
    Kill-switch window: this device/serial was remotely revoked by the
    developer. No activation entry - only the developer can reverse it
    (by removing the entry from the kill list).
    """
    import tkinter as tk
    import webbrowser
    from tkinter import ttk

    device_id = licensing.get_device_id()

    root = tk.Tk()
    root.title("GroundTruth - Deactivated")
    root.geometry("560x330")
    root.resizable(False, False)

    w, h = 560, 330
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 2
    root.geometry(f"{w}x{h}+{x}+{y}")

    ttk.Label(root, text="Deactivated", font=("Segoe UI", 18, "bold")).pack(pady=(24, 4))
    ttk.Label(
        root,
        text=(
            "Access to GroundTruth has been deactivated on this device.\n"
            "If you believe this is a mistake, contact the developer below with your Device ID."
        ),
        justify="center",
        font=("Segoe UI", 10),
    ).pack(pady=(0, 12))

    # --- Device ID + Get Device ID (copy) button ---
    device_frame = ttk.Frame(root)
    device_frame.pack(pady=2)
    ttk.Label(device_frame, text="Your Device ID:", font=("Segoe UI", 10, "bold")).pack(side="left", padx=(0, 8))
    device_var = tk.StringVar(value=device_id)
    ttk.Entry(device_frame, textvariable=device_var, width=22, font=("Consolas", 12), state="readonly").pack(side="left")

    copy_status = tk.StringVar()
    copy_status.set("")

    def get_device_id():
        root.clipboard_clear()
        root.clipboard_append(device_id)
        copy_status.set("Device ID copied - send it to the developer.")

    ttk.Button(root, text="Get Device ID", command=get_device_id).pack(pady=(6, 2))
    ttk.Label(root, textvariable=copy_status, foreground="#1a7f37", font=("Segoe UI", 9)).pack()

    # --- Developer contact ---
    contact = ttk.LabelFrame(root, text="Contact Developer", padding=(12, 6))
    contact.pack(pady=(12, 2), padx=30, fill="x")
    ttk.Label(
        contact,
        text=f"Website:  zephobed.me\nPhone / WhatsApp:  {DEVELOPER_PHONE_WHATSAPP}",
        justify="center",
        font=("Segoe UI", 10),
    ).pack()

    def open_website():
        try:
            webbrowser.open(DEVELOPER_WEBSITE)
        except Exception:
            pass

    def copy_whatsapp():
        root.clipboard_clear()
        root.clipboard_append(DEVELOPER_PHONE_WHATSAPP)
        copy_status.set("Phone / WhatsApp number copied.")

    contact_btns = ttk.Frame(contact)
    contact_btns.pack(pady=(6, 0))
    ttk.Button(contact_btns, text="Visit Website", command=open_website).grid(row=0, column=0, padx=6)
    ttk.Button(contact_btns, text="Copy WhatsApp Number", command=copy_whatsapp).grid(row=0, column=1, padx=6)

    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()


def show_locked_dialog() -> bool:
    """
    Show the locked window: access expired, Device ID to send the developer,
    developer contact details, and an activation-code entry.
    Returns True once a valid code activates access.
    """
    import tkinter as tk
    import webbrowser
    from tkinter import ttk, messagebox

    status = licensing.get_status()
    device_id = status["device_id"]
    reason = status.get("reason", "trial")

    if reason == "subscription":
        headline = "Subscription Expired"
        message = (
            "Your access period has ended.\n"
            "To renew, contact the developer below with your Device ID."
        )
    else:
        headline = "Trial Expired"
        message = (
            f"Your {TRIAL_DAYS}-day free trial has ended.\n"
            "To continue using GroundTruth, contact the developer below with your Device ID."
        )

    unlocked = {"value": False}

    root = tk.Tk()
    root.title("GroundTruth - Access Expired")
    root.geometry("560x460")
    root.resizable(False, False)

    # Center on screen
    w, h = 560, 460
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 2
    root.geometry(f"{w}x{h}+{x}+{y}")

    ttk.Label(root, text=headline, font=("Segoe UI", 18, "bold")).pack(pady=(22, 4))
    ttk.Label(root, text=message, justify="center", font=("Segoe UI", 10)).pack(pady=(0, 10))

    # --- Device ID + Get Device ID (copy) button ---
    device_frame = ttk.Frame(root)
    device_frame.pack(pady=2)
    ttk.Label(device_frame, text="Your Device ID:", font=("Segoe UI", 10, "bold")).pack(side="left", padx=(0, 8))
    device_var = tk.StringVar(value=device_id)
    device_entry = ttk.Entry(device_frame, textvariable=device_var, width=22, font=("Consolas", 12), state="readonly")
    device_entry.pack(side="left")

    def get_device_id():
        root.clipboard_clear()
        root.clipboard_append(device_id)
        copy_status.set("Device ID copied - send it to the developer.")

    copy_status = tk.StringVar()
    ttk.Button(root, text="Get Device ID", command=get_device_id).pack(pady=(4, 2))
    ttk.Label(root, textvariable=copy_status, foreground="#1a7f37", font=("Segoe UI", 9)).pack()

    # --- Developer contact ---
    contact = ttk.LabelFrame(root, text="Contact Developer", padding=(12, 6))
    contact.pack(pady=(10, 2), padx=30, fill="x")
    ttk.Label(
        contact,
        text=f"Website:  zephobed.me\nPhone / WhatsApp:  {DEVELOPER_PHONE_WHATSAPP}",
        justify="center",
        font=("Segoe UI", 10),
    ).pack()

    def open_website():
        try:
            webbrowser.open(DEVELOPER_WEBSITE)
        except Exception:
            pass

    def copy_whatsapp():
        root.clipboard_clear()
        root.clipboard_append(DEVELOPER_PHONE_WHATSAPP)
        copy_status.set("Phone / WhatsApp number copied.")

    contact_btns = ttk.Frame(contact)
    contact_btns.pack(pady=(6, 0))
    ttk.Button(contact_btns, text="Visit Website", command=open_website).grid(row=0, column=0, padx=6)
    ttk.Button(contact_btns, text="Copy WhatsApp Number", command=copy_whatsapp).grid(row=0, column=1, padx=6)

    # --- Activation code entry ---
    ttk.Label(root, text="Enter activation code:", font=("Segoe UI", 10, "bold")).pack(pady=(10, 2))
    code_var = tk.StringVar()
    entry = ttk.Entry(root, textvariable=code_var, width=40, font=("Consolas", 11))
    entry.pack(pady=4)
    entry.focus_set()

    status_var = tk.StringVar()
    ttk.Label(root, textvariable=status_var, foreground="#c0392b", font=("Segoe UI", 9)).pack()

    def on_submit(_event=None):
        code = code_var.get().strip()
        if not code:
            status_var.set("Please enter an activation code.")
            return
        try:
            record = licensing.activate(code)
        except licensing.LicensingError as exc:
            status_var.set(str(exc))
            code_var.set("")
            return
        except Exception:
            status_var.set("Could not validate the code. Try again.")
            code_var.set("")
            return
        messagebox.showinfo(
            "GroundTruth",
            f"Activation successful.\nAccess granted until {record['expiry']}.\nThe app will now start.",
        )
        unlocked["value"] = True
        root.destroy()

    def on_cancel():
        root.destroy()

    btns = ttk.Frame(root)
    btns.pack(pady=10)
    ttk.Button(btns, text="Activate", command=on_submit).grid(row=0, column=0, padx=8)
    ttk.Button(btns, text="Exit", command=on_cancel).grid(row=0, column=1, padx=8)

    root.bind("<Return>", on_submit)
    root.bind("<Escape>", lambda e: on_cancel())
    root.protocol("WM_DELETE_WINDOW", on_cancel)

    root.mainloop()
    return unlocked["value"]


# ===========================================================================
# Launch the Streamlit app
# ===========================================================================
def run_streamlit_app():
    import threading
    import traceback

    # NOTE: Streamlit env-var config is unreliable across versions. Instead we
    # pass flag_options directly to bootstrap.run(), which is the documented
    # programmatic entry-point and is version-stable.

    try:
        from streamlit.web import bootstrap as st_bootstrap
    except Exception:
        from streamlit import bootstrap as st_bootstrap  # type: ignore

    app_path = "app.py"
    if not Path(app_path).exists() and Path(__file__).resolve().parent.joinpath("app.py").exists():
        app_path = str(Path(__file__).resolve().parent / "app.py")

    def _open_browser():
        import time
        import webbrowser

        time.sleep(2.5)
        try:
            webbrowser.open("http://127.0.0.1:8502")
        except Exception:
            pass

    threading.Thread(target=_open_browser, daemon=True).start()

    # Production mode is REQUIRED when setting server.port programmatically.
    # In dev mode Streamlit refuses non-default ports.
    # Setting env vars BEFORE importing streamlit ensures they're picked up
    # during the initial config load.
    os.environ["STREAMLIT_GLOBAL_DEVELOPMENT_MODE"] = "false"
    os.environ["STREAMLIT_SERVER_HEADLESS"] = "true"
    os.environ["STREAMLIT_SERVER_RUN_ON_SAVE"] = "false"
    os.environ["STREAMLIT_SERVER_FILE_WATCHER_TYPE"] = "none"
    os.environ["STREAMLIT_SERVER_PORT"] = "8502"
    os.environ["STREAMLIT_SERVER_ADDRESS"] = "127.0.0.1"

    flag_options = {
        "server.port": 8502,
        "server.address": "127.0.0.1",
    }

    try:
        st_bootstrap.run(app_path, False, [], flag_options=flag_options)
        return 0
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 0
    except Exception:
        try:
            traceback.print_exc()
        except Exception:
            pass
        return 1


# ===========================================================================
# Main gate
# ===========================================================================
def main():
    # Kill switch first: a revoked device stays dead even while a license
    # or trial would otherwise be active.
    try:
        revoked_reason = licensing.check_kill_status()
    except Exception:
        revoked_reason = None
    if revoked_reason:
        show_deactivated_dialog(revoked_reason)
        return 1

    status = licensing.get_status()

    if status["mode"] == "licensed":
        return run_streamlit_app()

    if status["mode"] == "expired":
        while True:
            entered = show_locked_dialog()
            if not entered:
                return 0  # user cancelled / closed
            status = licensing.get_status()
            if status["mode"] != "expired":
                return run_streamlit_app()

    # Within trial
    return run_streamlit_app()


if __name__ == "__main__":
    raise SystemExit(main())
