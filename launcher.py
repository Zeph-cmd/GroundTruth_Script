# ---------------------------------------------------------------------------
# launcher.py - Simple trial + access-code wrapper for GroundTruth
# ---------------------------------------------------------------------------
# 1. On first run, save today's date to hidden file %APPDATA%\GroundTruth\.lic
# 2. Every run, compute days_since_start = today - saved_date
# 3. If days_since_start >= 30, block launch and show a small Tk window asking
#    for an access code.
# 4. VALID_CODES is a hardcoded list of 50 random codes (one-time use).
# 5. On valid code, reset saved date to today and remove the code from use.
# 6. Wrong code -> "Invalid code", stays locked.
# 7. Built with PyInstaller --onefile, no console window.
# ---------------------------------------------------------------------------
import os
import sys
import json
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
TRIAL_DAYS = 30

# 50 random one-time-use access codes. Give ONE to a client when their trial
# expires. Each code works exactly once per machine.
VALID_CODES = [
    "GT-VCUS-HGYK", "GT-GVE2-84HN", "GT-YVGW-KVAU", "GT-47MV-ZTTG",
    "GT-9ANN-SQAW", "GT-XHZS-F2BB", "GT-KQDF-26AA", "GT-L4VL-YNCB",
    "GT-4N2M-FH3D", "GT-8JF9-2S94", "GT-CEW2-BDHN", "GT-YX2B-5SDH",
    "GT-F9E6-A9AR", "GT-T3S7-LT9Z", "GT-W662-D98L", "GT-L2VX-69X6",
    "GT-78KP-6M9C", "GT-RS4P-ANMP", "GT-H2R8-ETBX", "GT-E6VX-3UFK",
    "GT-JVL9-AL9D", "GT-PP7D-QCVU", "GT-78JX-3UUD", "GT-FTPE-VXMV",
    "GT-ALCT-62BA", "GT-9KFU-FMK7", "GT-7PAP-HNL3", "GT-4M55-JQF2",
    "GT-5PCA-JGMC", "GT-8LV8-HJA2", "GT-YCQ8-5ZH3", "GT-TXCM-UABQ",
    "GT-BDR6-KGWE", "GT-GBMZ-M983", "GT-S8HK-3E76", "GT-V5AY-FTLB",
    "GT-P3F2-KYPC", "GT-95XE-FZ5U", "GT-QKV3-AFQY", "GT-MSUG-A7GK",
    "GT-R6EN-85QW", "GT-V5DF-URSK", "GT-V7FY-7YEW", "GT-CF5B-9SVG",
    "GT-K4TR-K5VN", "GT-NYQ3-R6GD", "GT-A4AL-9QNZ", "GT-C4F3-G5WJ",
    "GT-JDM5-FGZM", "GT-LWLN-4LXX",
]

# ===========================================================================
# Windowed-mode fix + crash logging (PyInstaller --windowed sets stdout/stderr
# to None, so all errors are silently swallowed)
# ===========================================================================
_crash_log_path = None
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
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")
else:
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

# ===========================================================================
# Paths - user data lives in %APPDATA%\GroundTruth so the exe is fully portable
# ===========================================================================
USER_DATA_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
LIC_FILE = USER_DATA_DIR / ".lic"
USED_CODES_FILE = USER_DATA_DIR / ".used_codes"

# Make frozen app modules importable
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
    '\n'.join([
        '[global]',
        'developmentMode = false',
        '',
        '[server]',
        'headless = true',
        'port = 8502',
        'address = "127.0.0.1"',
        'runOnSave = false',
        'fileWatcherType = "none"',
        'maxUploadSize = 2048',
        '',
        '[browser]',
        'gatherUsageStats = false',
        '',
    ]),
    encoding='utf-8',
)


# ===========================================================================
# Trial logic
# ===========================================================================
def _today():
    return date.today()


def _read_start_date():
    """Read saved trial-start date from hidden .lic file. Returns date or None."""
    if not LIC_FILE.exists():
        return None
    try:
        data = json.loads(LIC_FILE.read_text(encoding="utf-8"))
        return date.fromisoformat(data.get("start_date", "")[:10])
    except Exception:
        return None


def _write_start_date(d):
    """Save trial-start date and mark the file hidden."""
    try:
        # Clear hidden attribute first so we can overwrite (Windows)
        if LIC_FILE.exists():
            try:
                import ctypes
                ctypes.windll.kernel32.SetFileAttributesW(str(LIC_FILE), 0x80)  # NORMAL
            except Exception:
                pass
        LIC_FILE.write_text(json.dumps({"start_date": d.isoformat()}), encoding="utf-8")
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(str(LIC_FILE), 0x2)  # hidden
        except Exception:
            pass
    except Exception:
        pass


def _load_used_codes():
    """Return the set of codes already consumed on this machine."""
    if not USED_CODES_FILE.exists():
        return set()
    try:
        data = json.loads(USED_CODES_FILE.read_text(encoding="utf-8"))
        return set(data.get("used", []))
    except Exception:
        return set()


def _save_used_code(code):
    """Record a code as used (single-use enforcement)."""
    used = _load_used_codes()
    used.add(code)
    try:
        # Clear hidden attribute first so we can overwrite (Windows)
        if USED_CODES_FILE.exists():
            try:
                import ctypes
                ctypes.windll.kernel32.SetFileAttributesW(str(USED_CODES_FILE), 0x80)  # NORMAL
            except Exception:
                pass
        USED_CODES_FILE.write_text(json.dumps({"used": list(used)}), encoding="utf-8")
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(str(USED_CODES_FILE), 0x2)  # hidden
        except Exception:
            pass
    except Exception:
        pass


def _is_code_valid(code):
    """True if code is in VALID_CODES and hasn't been used yet on this machine."""
    code = code.strip().upper()
    if code not in VALID_CODES:
        return False
    if code in _load_used_codes():
        return False
    return True


# ===========================================================================
# Locked dialog (Tk)
# ===========================================================================
def show_locked_dialog(days_over):
    """
    Show a small window: 'Trial expired. Enter access code to continue.'
    Returns True if a valid code was entered (and trial reset), else False.
    """
    import tkinter as tk
    from tkinter import ttk, messagebox

    unlocked = {"value": False}

    root = tk.Tk()
    root.title("GroundTruth - Trial Expired")
    root.geometry("420x280")
    root.resizable(False, False)

    # Center on screen
    w, h = 420, 280
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 2
    root.geometry(f"{w}x{h}+{x}+{y}")

    ttk.Label(root, text="Trial Expired", font=("Segoe UI", 18, "bold")).pack(pady=(30, 8))
    ttk.Label(
        root,
        text=f"Your {TRIAL_DAYS}-day trial has ended.\nEnter an access code to continue.",
        justify="center",
        font=("Segoe UI", 10),
    ).pack(pady=(0, 20))

    code_var = tk.StringVar()
    entry = ttk.Entry(root, textvariable=code_var, width=24, font=("Consolas", 13))
    entry.pack(pady=4)
    entry.focus_set()

    status_var = tk.StringVar()
    ttk.Label(root, textvariable=status_var, foreground="#c0392b", font=("Segoe UI", 9)).pack()

    def on_submit(_event=None):
        code = code_var.get().strip()
        if not code:
            status_var.set("Please enter an access code.")
            return
        if _is_code_valid(code):
            _save_used_code(code.upper())
            _write_start_date(_today())  # reset trial
            unlocked["value"] = True
            messagebox.showinfo("GroundTruth", "Access granted. The app will now start.")
            root.destroy()
        else:
            status_var.set("Invalid code")
            code_var.set("")

    def on_cancel():
        root.destroy()

    btns = ttk.Frame(root)
    btns.pack(pady=12)
    ttk.Button(btns, text="Submit", command=on_submit).grid(row=0, column=0, padx=8)
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
    # Streamlit validates config conflicts at IMPORT time and during server
    # __init__. If global.developmentMode=True (the default) and server.port
    # is set, it raises RuntimeError. The flag_options below are passed to
    # bootstrap.run() but some are applied too late - after conflict checks.
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
    start_date = _read_start_date()

    # First run: save today
    if start_date is None:
        _write_start_date(_today())
        return run_streamlit_app()

    days_since = (_today() - start_date).days

    if days_since >= TRIAL_DAYS:
        # Locked - show access-code dialog
        while True:
            entered = show_locked_dialog(days_since - TRIAL_DAYS)
            if not entered:
                return 0  # user cancelled / closed
            # After a valid unlock the trial was reset to today; re-check
            start_date = _read_start_date()
            days_since = (_today() - start_date).days
            if days_since < TRIAL_DAYS:
                return run_streamlit_app()

    # Within trial
    return run_streamlit_app()


if __name__ == "__main__":
    raise SystemExit(main())