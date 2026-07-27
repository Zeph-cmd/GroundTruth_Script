# GroundTruth Dataset Explorer - Complete Documentation

## Executive Summary

GroundTruth is a dual-application system for road condition data collection and analysis. It consists of:
1. **Mobile/Companion App**: Collects road data (photos, GPS, video) and exports as `.rvtrip` packages
2. **Desktop Explorer**: Imports, verifies, analyzes, and exports trip data for decision-makers

This document covers the **Desktop Explorer** (this codebase).

---

## System Architecture

### High-Level Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              GroundTruth Explorer                             │
└─────────────────────────────────────────────────────────────────────────────

┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Import     │────▶│   Verify     │────▶│   Analyze    │────▶│   Export     │
│  (.rvtrip)   │     │  (SHA-256)   │     │  (Analytics) │     │  (ZIP/JSON)  │
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
        │                    │                    │                    │
        ▼                    ▼                    ▼                    ▼
   rvtrip.py          rvtrip.py          analytics.py        share_export.py
   trip_importer.py   app.py             map_loader.py       exporter.py
                                        authenticity_checker.py
```

### Module Dependency Graph

```
app.py (Main UI)
── utils/rvtrip.py (Import + Verification)
├── utils/trip_importer.py (Legacy ZIP import)
├── utils/trip_loader.py (Trip discovery)
├── utils/metadata_loader.py (Metadata parsing)
── utils/map_loader.py (GPS + Maps)
├── utils/analytics.py (Statistics)
├── utils/authenticity_checker.py (Tamper detection)
├── utils/yolo_detector.py (AI object detection)
── utils/image_enhancer.py (Image quality)
├── utils/image_loader.py (Image discovery)
├── utils/share_export.py (Export functions)
├── utils/exporter.py (CSV/JSON/ZIP export)
└── utils/audit_logger.py (Security logging)
```

---

## File Inventory

### Core Application

| File | Purpose | Lines |
|------|---------|-------|
| `app.py` | Main Streamlit UI (1,183 lines) | All UI, routing, state management |
| `check_gpu.py` | GPU capability checker | Diagnostic utility |

### Utils Package (`utils/`)

| File | Purpose | Key Functions |
|------|---------|---------------|
| `__init__.py` | Package marker | - |
| `rvtrip.py` | `.rvtrip` import + verification | `import_rvtrip()`, `verify_rvtrip()`, `create_rvtrip_manifest()` |
| `trip_importer.py` | Legacy ZIP import | `import_trip_zip()`, `import_trip_folder_files()` |
| `trip_loader.py` | Trip folder discovery | `get_trip_folders()` |
| `metadata_loader.py` | Parse metadata.json | `load_trip_metadata()` |
| `map_loader.py` | GPS extraction + maps | `extract_gps_points()`, `build_trip_map()` |
| `analytics.py` | Trip statistics | `calculate_trip_duration()`, `estimate_distance_from_gps()` |
| `authenticity_checker.py` | Tamper detection | `compute_trip_authenticity()` |
| `yolo_detector.py` | AI object detection | `detect_objects()` |
| `image_enhancer.py` | Image quality boost | `get_enhanced_image_path()` |
| `image_loader.py` | Image discovery | `get_trip_images()` |
| `share_export.py` | Export functions | `export_selected_trip()`, `export_full_dataset()` |
| `exporter.py` | CSV/JSON/ZIP export | `export_trip_csv()`, `export_trip_json()` |
| `audit_logger.py` | Security audit trail | `log_audit_event()`, `verify_audit_password()` |
| `ai_detector.py` | Mock detector (dev) | `detect_road_objects()` |

### Configuration Files

| File | Purpose |
|------|---------|
| `.streamlit/config.toml` | Streamlit settings (upload size: 2GB) |
| `audit_config.json` | Audit log password + lockout config |
| `audit_log.json` | Audit event log |
| `uploads.json` | Upload history |

---

## Core Features & Logic

### 1. Import System (`utils/rvtrip.py`)

**Purpose**: Accept `.rvtrip` packages with integrity verification

**Format**:
```
trip_name.rvtrip (ZIP archive)
├── manifest.json          # SHA-256 hashes
├── videos/                # Video files
├── frames/                # Extracted frames
└── metadata.json          # Trip metadata
```

**Manifest Formats Supported**:

**Format 1 (Flat)**:
```json
{
  "version": 1,
  "hashes": {
    "frames/image001.jpg": "abc123...",
    "metadata.json": "def456..."
  },
  "algorithm": "sha256"
}
```

**Format 2 (Structured - Companion App)**:
```json
{
  "trip_id": "trip_001",
  "created_at": "2026-06-27T10:00:00Z",
  "app_version": "1.0",
  "video_hashes": [
    {"filename": "video1.mp4", "hash": "abc123..."}
  ],
  "frame_hashes": [
    {"filename": "frame001.jpg", "hash": "def456..."}
  ],
  "metadata_hash": "ghi789...",
  "hash_algorithm": "SHA-256"
}
```

**Verification Process**:
1. Unpack ZIP to temp directory
2. Read manifest.json
3. Recompute SHA-256 for each file
4. Compare against manifest hashes
5. Return status: VALID / WARNING / INVALID

**Status Logic**:
- **VALID**: All hashes match
- **WARNING**: Some files missing, no mismatches
- **INVALID**: Hash mismatch detected

**Key Functions**:
```python
def import_rvtrip(uploaded_file, trip_name=None, clear_old=False) -> RvTripImportResult
def verify_rvtrip(rvtrip_path: Path) -> dict[str, Any]
def create_rvtrip_manifest(trip_folder: Path) -> dict[str, str]
```

---

### 2. Verification UI (`app.py`)

**Banner Display**:

**VALID (Green)**:
```
✅ Trip Integrity: PASS
Files Verified: 47
Verification Time: 1.2s
Hash Algorithm: SHA-256
```

**WARNING (Yellow)**:
```
️ Trip Integrity: WARNING (read-only)
Files Verified: 45 / 47
Missing: 2 files
  • frames/image0023.jpg
  • metadata/gps.csv
Verification Time: 0.9s
Hash Algorithm: SHA-256

[Continue Anyway] button
```

**INVALID (Red)**:
```
❌ Trip Integrity: FAIL
Files Verified: 47 / 47
Mismatched hashes: 3 files
  • frames/image0010.jpg
  • frames/image0015.jpg
  • metadata/metadata.json
Verification Time: 1.1s
Hash Algorithm: SHA-256

This trip has been modified or corrupted.
Analytics and reports are disabled.
```

**Override Logic**:
- WARNING state shows "Continue Anyway" button
- Clicking sets `st.session_state.verification_override = True`
- Analytics and export become fully available
- "Revert Override" button restores read-only mode

---

### 3. Trip Discovery (`utils/trip_loader.py`)

**Search Strategy**:
1. Scan `trips/` directory recursively for `metadata.json`
2. Extract ZIP archives in `trips/*.zip` to `trips/.extracted/`
3. Return list of trip directories containing metadata

**Directory Structure**:
```
trips/
├── .extracted/              # Auto-extracted ZIPs
│   └── trip_name_hash/
│       ├── metadata.json
│       └── images/
├── .extracted_rv/           # .rvtrip imports
│   └── trip_name/
│       ├── metadata.json
│       ├── frames/
│       └── videos/
└── _incoming/               # Uploaded files
    └── trip_name.rvtrip
```

---

### 4. Metadata Loading (`utils/metadata_loader.py`)

**Expected metadata.json Structure**:
```json
{
  "trip_id": "trip_001",
  "start_time": "2026-06-27T10:00:00Z",
  "end_time": "2026-06-27T10:30:00Z",
  "total_images": 100,
  "gps_available": true,
  "photos": [
    {
      "filename": "image001.jpg",
      "timestamp": "2026-06-27T10:00:00Z",
      "latitude": 6.690966,
      "longitude": -1.614282
    }
  ]
}
```

**Flexible Field Names**:
- `trip_id` OR `tripId` OR `id` OR `trip`
- `start_time` OR `startTime` OR `start`
- `photos` OR `images` OR `frames` OR `records`
- `latitude` OR `lat` OR `gps_latitude` OR `y`
- `longitude` OR `lon` OR `lng` OR `gps_longitude` OR `x`

---

### 5. GPS & Maps (`utils/map_loader.py`)

**GPS Point Extraction**:
1. Read from metadata.json photos array
2. Fallback: Extract from image EXIF data
3. Validate coordinates (-90 to 90, -180 to 180)
4. Reject (0, 0) as invalid (Gulf of Guinea)

**Distance Calculation**:
```python
Haversine formula:
  radius_km = 6371.0
  a = sin²(Δlat/2) + cos(lat1)·cos(lat2)·sin²(Δlon/2)
  distance = 2·R·atan2(√a, √(1-a))
```

**Coverage Score**:
```python
route_length_km = sum of all segment distances
gap_threshold = max(median_distance * 3, 100m)
gaps = segments > gap_threshold
captured_km = route_length - unrecorded_gaps
coverage_pct = (captured / route) * 100
```

**Map Visualization**:
- **Imagery View**: Satellite photos (`!3m1!1e3`)
- **Raw Map View**: Standard road map (`!3m1!1e2`)
- **Markers**: Start (green) + End (red)
- **Route**: Polyline connecting GPS points

**Area Name Resolution**:
- Uses Nominatim OpenStreetMap API
- Returns: road, suburb, city, district, state, country
- Example: "Osu, Accra, Greater Accra, Ghana"

---

### 6. Analytics (`utils/analytics.py`)

**Metrics Calculated**:

| Metric | Formula |
|--------|---------|
| Duration | `end_time - start_time` |
| Distance | Sum of Haversine distances between GPS points |
| Avg Speed | `distance / duration_hours` |
| Data Quality | `100 - (missing_gps * 40 + missing_ts * 35 + irregular * 3)` |
| Image Intervals | `median, min, max` of time between photos |

**GPS Gap Detection**:
```python
median_interval = median time between photos
gap_threshold = median_interval * 3
gaps = intervals > gap_threshold
```

---

### 7. Authenticity Checker (`utils/authenticity_checker.py`)

**Purpose**: Detect tampering, manipulation, or synthetic data

**Scoring System** (0-100):

| Category | Points | Checks |
|----------|--------|--------|
| Metadata Integrity | +35 | Image count match, required fields |
| GPS Consistency | +50 | No gaps, no speed anomalies, coordinate diversity |
| Temporal Consistency | +30 | No future timestamps, natural intervals |
| File Integrity | +30 | No duplicates, consistent naming |

**Red Flags**:
- Unexplained GPS gaps (>100m without time gap)
- Speed anomalies (>200 km/h)
- Suspiciously regular timestamps (CV < 0.05)
- Duplicate files
- Metadata mismatch

**Output**:
```json
{
  "score": 94,
  "warnings": ["2 unexplained gaps", "metadata mismatch"],
  "details": {
    "metadata": {"image_count_match": true, ...},
    "gps": {"unexplained_gaps": 2, ...},
    "temporal": {"interval_regularity": "natural", ...},
    "files": {"duplicate_files": 0, ...}
  }
}
```

---

### 8. AI Object Detection (`utils/yolo_detector.py`)

**Model**: YOLOv8m (COCO pretrained)

**Detection Classes**:
- Potholes
- Cracks
- Road signs
- Vehicles
- Pedestrians

**Process**:
1. Load YOLOv8m model (cached)
2. Run inference on image
3. Return detections with:
   - `label`: Object class
   - `confidence`: 0-1 score
   - `bbox`: [x1, y1, x2, y2]

**UI Toggle**:
- Enable/Disable AI Detection (default: enabled)
- When disabled, shows raw images without bounding boxes

---

### 9. Image Enhancement (`utils/image_enhancer.py`)

**Pipeline**:
1. **Denoise**: Fast Non-Local Means Denoising
2. **Sharpen**: Unsharp mask (kernel: `[[0,-1,0],[-1,5,-1],[0,-1,0]]`)
3. **Contrast**: CLAHE (Clip Limit: 2.0, Grid: 8x8)

**GPU Acceleration**:
- **CUDA**: If available, uploads image to GPU for denoising
- **OpenCL**: Fallback for AMD/Intel GPUs
- **CPU**: Final fallback (always works)

**Caching**:
- Enhanced images stored in `cache/opencv_enhance/`
- Cache key: SHA-1 of resolved file path
- Max cache size: 256 images (LRU)

---

### 10. Export System (`utils/share_export.py`)

**Export Types**:

| Type | Contents | Use Case |
|------|----------|----------|
| Full Dataset | All trips + metadata + images | Archive |
| Selected Trip | Single trip + images + analytics | Sharing |
| Lightweight | Single trip + thumbnails (1600x1600) | Email |

**Export Structure**:
```
GroundTruth_Trip_001.zip
├── metadata.json
── analytics.json
└── images/
    ├── image001.jpg
    ├── image002.jpg
    └── ...
```

**Recursive Trip Discovery**:
- Searches `trips/` recursively for `metadata.json`
- Handles nested structures (`.extracted_rv/`)
- Skips hidden directories

---

### 11. Audit Logger (`utils/audit_logger.py`)

**Security Features**:
- Password protection (default: `admin123`)
- Rate limiting: 4 failed attempts → 30-minute lockout
- Immutable event log

**Events Tracked**:
- Uploads (filename, type, timestamp)
- Audit log access
- Password changes
- History clear
- Trip clear

**Storage**:
- `audit_config.json`: Password + lockout state
- `audit_log.json`: Event log (append-only)

**Lockout Logic**:
```python
MAX_FAILED_ATTEMPTS = 4
LOCK_DURATION_SECONDS = 1800  # 30 minutes

if failed_attempts >= 4:
    lockout_until = current_time + 1800
```

---

## Data Flow Diagrams

### Import Flow

```
User uploads .rvtrip
        │
        ▼
┌───────────────────┐
│  import_rvtrip()  │
│  - Save to        │
│    _incoming/     │
│  - Extract to     │
│    .extracted_rv/ │
└───────────────────┘
        │
        ▼
┌───────────────────┐
│  verify_rvtrip()  │
│  - Read manifest  │
│  - Hash files     │
│  - Compare        │
└───────────────────┘
        │
        ▼
┌───────────────────┐
│  Display Banner   │
│  - VALID: Green   │
│  - WARNING: Yellow│
│  - INVALID: Red   │
└───────────────────┘
        │
        ▼
┌───────────────────┐
│  Store in         │
│  session_state    │
