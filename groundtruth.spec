# -*- mode: python ; coding: utf-8 -*-
# ============================================================================
# groundtruth.spec - PyInstaller spec for GroundTruth (onefile, windowed)
# ============================================================================
# Build:   pyinstaller groundtruth.spec --noconfirm
# Or use:  build.bat
#
# Entry point: launcher.py (trial gate + access-code unlock + streamlit launch)
#
# MEMORY-SAFE STRATEGY:
#   - Do NOT manually collect_all('torch'/'ultralytics'/'cv2'). PyInstaller's
#     contrib hooks (hook-torch.py, hook-cv2.py, etc.) already handle binaries
#     and data for these. Calling collect_all again duplicates the module graph
#     and OOMs on machines with <4GB free RAM.
#   - Only collect DATA files (not submodules) for frontend viz libs.
#   - EXCLUDE heavy transitive deps the app never uses (numba,
#     llvmlite, matplotlib, IPython, jupyter, etc.) to shrink memory + size.
#   - NOTE: pyarrow is NOT excluded — st_folium (Streamlit Custom Component)
#     requires it for Arrow serialization between Python and the JS frontend.
# ============================================================================

import os
import sys
from pathlib import Path

sys.setrecursionlimit(5000)

block_cipher = None

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

import site

site_dirs = site.getsitepackages()
SITE_PKG = Path(site_dirs[0]) if site_dirs else Path(sys.prefix) / "Lib" / "site-packages"

PROJECT_DIR = Path(SPECPATH) if "SPECPATH" in globals() else Path.cwd()


def _pkg_dir(rel: str):
    """Return [(src_dir, dest_dir)] if a package data dir exists."""
    p = SITE_PKG / rel
    return [(str(p), rel.replace("/", os.sep))] if p.exists() else []


datas = []
binaries = []

# --- Frontend / viz DATA files only (no submodule tracing) ---
datas += _pkg_dir("streamlit/static")
datas += collect_data_files("streamlit")
datas += _pkg_dir("folium/templates")
datas += _pkg_dir("branca/templates")
datas += collect_data_files("folium")
datas += collect_data_files("branca")
datas += collect_data_files("plotly")
datas += collect_data_files("altair")
datas += collect_data_files("pydeck")
# streamlit_folium needs its frontend/build dir bundled, otherwise it crashes
# with StreamlitAPIException: No such component directory at runtime.
datas += collect_data_files("streamlit_folium")

# --- Package METADATA (.dist-info) ---
# Streamlit (and many of its deps) call importlib.metadata.version() at
# runtime. PyInstaller's collect_data_files does NOT copy .dist-info dirs,
# which causes "No package metadata was found for streamlit" crashes.
# copy_metadata() fixes this. We collect metadata for all bundled packages
# that are known to look up their own version.
_metadata_pkgs = [
    # NOTE: scipy, matplotlib omitted here because they are excluded below.
    # Only collect metadata for INCLUDED packages.
    "streamlit", "streamlit-folium", "folium", "branca", "plotly",
    "altair", "pydeck", "PIL", "pillow", "numpy", "cv2", "opencv-python",
    "pandas",
    "torch", "torchvision", "ultralytics",
    "typing_extensions", "protobuf", "jinja2",
    # pyarrow metadata (st_folium runtime dependency)
    "pyarrow",
]
for _mp in _metadata_pkgs:
    try:
        datas += copy_metadata(_mp)
    except Exception:
        pass

# --- App source + model weights + config ---
datas += [(str(PROJECT_DIR / "app.py"), ".")]

utils_dir = PROJECT_DIR / "utils"
if utils_dir.exists():
    datas.append((str(utils_dir), "utils"))

for pt in ("yolov8m.pt", "yolov8n.pt"):
    p = PROJECT_DIR / pt
    if p.exists():
        datas.append((str(p), "."))

weights_dir = PROJECT_DIR / "weights"
if weights_dir.exists():
    datas.append((str(weights_dir), "weights"))

audit_cfg = PROJECT_DIR / "audit_config.json"
if audit_cfg.exists():
    datas.append((str(audit_cfg), "."))

# --- Hidden imports (declare them; let contrib hooks + Analysis collect) ---
hiddenimports = [
    "licensing",
    "cryptography",
    "cryptography.hazmat.primitives.asymmetric.ed25519",
    "streamlit",
    "streamlit.web",
    "streamlit.web.bootstrap",
    "streamlit.runtime",
    "streamlit.runtime.scriptrunner",
    # magic_funcs is imported dynamically via AST injection in magic.py
    # (line 169). PyInstaller can't detect it. Without this, every app
    # script crashes with ModuleNotFoundError on first run.
    "streamlit.runtime.scriptrunner.magic_funcs",
    "streamlit.runtime.scriptrunner.magic",
    "streamlit.runtime.scriptrunner.script_cache",
    "streamlit.runtime.scriptrunner_utils",
    "streamlit.runtime.scriptrunner_utils.exceptions",
    "streamlit.runtime.scriptrunner_utils.script_requests",
    "streamlit.runtime.scriptrunner_utils.script_run_context",
    "streamlit_folium",
    # streamlit_folium/folium are runtime dependencies for the route map.
    "folium",
    "folium.elements",
    "folium.plugins",
    "branca",
    "xyzservices",
    "jinja2",
    # pyarrow is REQUIRED by Streamlit Custom Components (st_folium) for
    # Arrow-format data serialization. Without it, st_folium throws:
    #   StreamlitAPIException: To use Custom Components, you need to install PyArrow
    "pyarrow",
    "plotly",
    "plotly.graph_objects",
    "altair",
    "pydeck",
    # streamlit_folium -> Streamlit custom component handling imports
    # streamlit.dataframe_util, which requires pandas at runtime.
    "pandas",
    "pandas._libs",
    "pandas.api.types",
    "pandas.api.extensions",
    "pandas.io.formats.style",
    "pandas.util",
    "cv2",
    "PIL",
    "PIL._tkinter_finder",
    "ultralytics",
    "ultralytics.nn",
    "ultralytics.nn.tasks",
    "ultralytics.engine",
    "ultralytics.engine.model",
    "ultralytics.engine.predictor",
    "torch",
    "torch.nn",
    "torch.nn.functional",
    "torchvision",
    "numpy",
    "tkinter",
    "tkinter.ttk",
    "utils",
]

if utils_dir.exists():
    for f in utils_dir.glob("*.py"):
        hiddenimports.append("utils." + f.stem)

# --- Excludes: heavy transitive deps NOT used by this app ---
# These were being pulled in and ballooning both memory and final exe size.
#
# VERIFIED (no direct imports in our code): scipy, matplotlib, jupyter,
# notebook, IPython. They are pure transitive deps that bloat the module graph
# and OOM the Analysis phase on low-RAM machines.
#
# pandas is included because st_folium/Streamlit custom component handling
# imports streamlit.dataframe_util, which requires pandas at runtime.
excludes = [
    # Big-data / JIT not needed for YOLO inference.
    # NOTE: pyarrow is NOT excluded — st_folium requires it at runtime.
    "numba",
    "llvmlite",
    # scipy has a massive module graph and is only a transitive dep here;
    # YOLOv8 inference uses torch + numpy, not scipy. Removing it saves the
    # most memory during Analysis (the previous OOM happened in scipy hooks).
    "scipy",
    # Plotting / notebook backends not used (we use plotly via streamlit)
    "matplotlib",
    "notebook",
    "jupyter",
    "jupyterlab",
    "IPython",
    "ipykernel",
    "ipywidgets",
    # Dev tooling
    "pytest",
    "sphinx",
    # Heavy torch subsystems not needed for CPU inference
    "torch.testing._internal",
    "torch.testing",
    "torch.utils.tensorboard",
    "torch.utils.benchmark",
    "torch.utils.cpp_extension",
    "torch.distributed",
    "torch.distributed.elastic",
    "torch.distributed.fsdp",
    "torch.distributed.rpc",
    "torch.distributed.nn",
    "torch.onnx",
    "torch.jit",
    "torch.quantization",
    "torchprof",
    "torchcontrib",
]

a = Analysis(
    [str(PROJECT_DIR / "launcher.py")],
    pathex=[str(PROJECT_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="GroundTruth",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon=PROJECT_DIR / "groundtruth.ico",
)