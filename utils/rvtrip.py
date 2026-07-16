"""
.rvtrip integrity verification module.

Verifies .rvtrip packages by:
1. Unpacking the ZIP
2. Reading manifest.json
3. Recomputing SHA-256 for all files
4. Comparing against manifest hashes

Returns verification status: VALID, WARNING, or INVALID
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

HASH_ALGORITHM = "sha256"


def _compute_sha256(filepath: Path) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def verify_rvtrip(rvtrip_path: Path) -> dict[str, Any]:
    """
    Verify integrity of a .rvtrip file.
    
    Supports two manifest formats:
    1. Flat: {"hashes": {"path": "hash", ...}}
    2. Structured (companion app): {
         "video_hashes": [{"filename": "...", "hash": "..."}, ...],
         "frame_hashes": [{"filename": "...", "hash": "..."}, ...],
         "metadata_hash": "hash"
       }
    
    Returns dict with:
      - status: "VALID" | "WARNING" | "INVALID"
      - files_verified: int
      - total_files: int
      - mismatches: list of {file, expected, actual}
      - missing: list of missing filenames
      - elapsed: float (seconds)
      - hash_algorithm: str
    """
    start = time.time()
    
    result: dict[str, Any] = {
        "status": "INVALID",
        "files_verified": 0,
        "total_files": 0,
        "mismatches": [],
        "missing": [],
        "elapsed": 0.0,
        "hash_algorithm": HASH_ALGORITHM,
    }
    
    try:
        with zipfile.ZipFile(rvtrip_path, "r") as zf:
            # Find manifest.json (case-insensitive)
            manifest_name = None
            for name in zf.namelist():
                if name.lower().endswith("manifest.json"):
                    manifest_name = name
                    break
            
            if not manifest_name:
                logger.error("manifest.json not found in %s", rvtrip_path.name)
                result["elapsed"] = time.time() - start
                return result
            
            # Read manifest
            with zf.open(manifest_name) as mf:
                manifest = json.loads(mf.read().decode("utf-8"))
            
            # Build file->hash map from either format
            file_hashes: dict[str, str] = {}
            
            # Format 1: Flat hashes dict
            if "hashes" in manifest and isinstance(manifest["hashes"], dict):
                file_hashes = manifest["hashes"]
            else:
                # Format 2: Structured (companion app)
                video_hashes = manifest.get("video_hashes", [])
                if isinstance(video_hashes, list):
                    for item in video_hashes:
                        if isinstance(item, dict) and "filename" in item and "hash" in item:
                            file_hashes[f"videos/{item['filename']}"] = item["hash"]
                
                frame_hashes = manifest.get("frame_hashes", [])
                if isinstance(frame_hashes, list):
                    for item in frame_hashes:
                        if isinstance(item, dict) and "filename" in item and "hash" in item:
                            file_hashes[f"frames/{item['filename']}"] = item["hash"]
                
                metadata_hash = manifest.get("metadata_hash")
                if metadata_hash:
                    file_hashes["metadata.json"] = metadata_hash
            
            result["total_files"] = len(file_hashes)
            
            if not file_hashes:
                logger.warning("No hashes found in manifest")
                result["status"] = "WARNING"
                result["elapsed"] = time.time() - start
                return result
            
            # Extract to temp dir and verify
            with tempfile.TemporaryDirectory(prefix="rvtrip_verify_") as tmpdir:
                zf.extractall(tmpdir)
                tmpdir_path = Path(tmpdir)
                
                for rel_path, expected_hash in file_hashes.items():
                    # Handle nested paths (videos/, frames/, metadata/)
                    file_path = tmpdir_path / rel_path
                    
                    if not file_path.exists():
                        result["missing"].append(rel_path)
                        continue
                    
                    actual_hash = _compute_sha256(file_path)
                    result["files_verified"] += 1
                    
                    if actual_hash != expected_hash:
                        result["mismatches"].append({
                            "file": rel_path,
                            "expected": expected_hash,
                            "actual": actual_hash,
                        })
                
                # Determine status
                if result["mismatches"]:
                    result["status"] = "INVALID"
                elif result["missing"]:
                    result["status"] = "WARNING"
                else:
                    result["status"] = "VALID"
    
    except zipfile.BadZipFile:
        logger.error("Invalid ZIP file: %s", rvtrip_path)
        result["status"] = "INVALID"
    except json.JSONDecodeError as e:
        logger.error("Invalid manifest.json: %s", e)
        result["status"] = "INVALID"
    except Exception as e:
        logger.error("Verification failed: %s", e, exc_info=True)
        result["status"] = "INVALID"
    
    result["elapsed"] = round(time.time() - start, 2)
    return result


def create_rvtrip_manifest(trip_folder: Path) -> dict[str, str]:
    """
    Create a manifest with SHA-256 hashes for all files in a trip folder.
    
    Returns dict of {relative_path: sha256_hash}
    """
    hashes: dict[str, str] = {}
    
    for file_path in trip_folder.rglob("*"):
        if file_path.is_file() and file_path.name.lower() != "manifest.json":
            rel_path = file_path.relative_to(trip_folder).as_posix()
            hashes[rel_path] = _compute_sha256(file_path)
    
    return hashes


def write_manifest(trip_folder: Path, hashes: dict[str, str]) -> Path:
    """Write manifest.json to a trip folder."""
    manifest = {
        "version": 1,
        "hashes": hashes,
        "algorithm": HASH_ALGORITHM,
    }
    
    manifest_path = trip_folder / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    
    return manifest_path


def package_rvtrip(trip_folder: Path, output_path: Path) -> Path:
    """
    Package a trip folder as a .rvtrip file with integrity manifest.
    
    Args:
        trip_folder: Path to the extracted trip directory
        output_path: Path for the output .rvtrip file
    
    Returns:
        Path to the created .rvtrip file
    """
    # Create manifest
    hashes = create_rvtrip_manifest(trip_folder)
    manifest_path = write_manifest(trip_folder, hashes)
    
    try:
        # Create ZIP
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in trip_folder.rglob("*"):
                if file_path.is_file():
                    arcname = file_path.relative_to(trip_folder).as_posix()
                    zf.write(file_path, arcname)
        
        return output_path
    finally:
        # Clean up manifest from original folder
        if manifest_path.exists():
            manifest_path.unlink()


# ---------------------------------------------------------------------------
# Import: unpack + verify in one step
# ---------------------------------------------------------------------------

import re
from typing import Any

TRIPS_DIR = Path("trips")
IMPORTED_RVTRIPS_DIR = TRIPS_DIR / "_incoming"
EXTRACTED_RVTRIPS_DIR = TRIPS_DIR / ".extracted_rv"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return slug or "Imported_Trip"


def _unique_path(base_dir: Path, name: str, suffix: str = "") -> Path:
    candidate = base_dir / f"{name}{suffix}"
    counter = 1
    while candidate.exists():
        candidate = base_dir / f"{name}_{counter}{suffix}"
        counter += 1
    return candidate


def _uploaded_bytes(uploaded_file: Any) -> bytes:
    if hasattr(uploaded_file, "getbuffer"):
        return bytes(uploaded_file.getbuffer())
    if hasattr(uploaded_file, "read"):
        return uploaded_file.read()
    raise ValueError("unsupported uploaded file")


class RvTripImportResult:
    """Result of importing a .rvtrip file."""

    def __init__(
        self,
        trip_dir: Path,
        verification: dict[str, Any],
        trip_name: str,
    ):
        self.trip_dir = trip_dir
        self.verification = verification
        self.trip_name = trip_name

    @property
    def is_valid(self) -> bool:
        return self.verification["status"] == "VALID"

    @property
    def is_warning(self) -> bool:
        return self.verification["status"] == "WARNING"

    @property
    def is_invalid(self) -> bool:
        return self.verification["status"] == "INVALID"


def import_rvtrip(uploaded_file: Any, trip_name: str | None = None, clear_old: bool = False) -> RvTripImportResult:
    """
    Import a .rvtrip file: unpack, verify, return result.
    
    Args:
        uploaded_file: Streamlit uploaded file object (.rvtrip)
        trip_name: Optional override for the trip folder name
        clear_old: If True, clear previous imports first
    
    Returns:
        RvTripImportResult with extracted dir + verification dict
    
    Raises:
        ValueError: if uploaded_file is None or not .rvtrip
        zipfile.BadZipFile: if file is not a valid ZIP
    """
    if uploaded_file is None:
        raise ValueError("choose a .rvtrip file first")

    raw_name = str(getattr(uploaded_file, "name", ""))
    if not raw_name.lower().endswith(".rvtrip"):
        raise ValueError(f"file must be a .rvtrip package, got: {raw_name}")

    if clear_old:
        if IMPORTED_RVTRIPS_DIR.exists():
            shutil.rmtree(IMPORTED_RVTRIPS_DIR)
        if EXTRACTED_RVTRIPS_DIR.exists():
            shutil.rmtree(EXTRACTED_RVTRIPS_DIR)

    IMPORTED_RVTRIPS_DIR.mkdir(parents=True, exist_ok=True)

    # Determine folder name
    base = (trip_name.strip() if trip_name and trip_name.strip() else Path(raw_name).stem)
    safe = _slugify(base)
    folder_name = _unique_path(EXTRACTED_RVTRIPS_DIR, safe).name

    # Save uploaded file
    rvtrip_path = IMPORTED_RVTRIPS_DIR / f"{folder_name}.rvtrip"
    rvtrip_path.write_bytes(_uploaded_bytes(uploaded_file))

    # Extract
    trip_dir = EXTRACTED_RVTRIPS_DIR / folder_name
    with zipfile.ZipFile(rvtrip_path, "r") as zf:
        for info in zf.infolist():
            member = Path(info.filename)
            if member.is_absolute() or ".." in member.parts:
                continue
            if info.is_dir():
                (trip_dir / member).mkdir(parents=True, exist_ok=True)
            else:
                dest = trip_dir / member
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(dest, "wb") as dst:
                    dst.write(src.read())

    # Verify
    verification = verify_rvtrip(rvtrip_path)

    return RvTripImportResult(
        trip_dir=trip_dir,
        verification=verification,
        trip_name=trip_name or folder_name,
    )
