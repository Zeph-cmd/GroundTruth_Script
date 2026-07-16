# RoadVision Dataset Explorer - Setup Guide for Buyers

## Quick Start (5 minutes)

### 1. Install Python
Download from: https://www.python.org/downloads/

**Minimum version**: Python 3.10 or newer (3.10, 3.11, 3.12 all work)

During installation:
- ✅ Check "Add Python to PATH"
- ✅ Check "Install pip"

### 2. Download RoadVision
Extract the `roadvision_script` folder to your desired location (e.g., `C:\RoadVision\`)

### 3. Install Dependencies
Open Command Prompt and run:
```bash
cd C:\RoadVision\roadvision_script
pip install -r requirements.txt
```

This installs:
- Streamlit (web interface)
- OpenCV (image processing)
- Ultralytics (YOLO AI detection)
- Folium (maps)
- Plotly (charts)
- Pandas, NumPy, Pillow (data handling)

### 4. Run the App
```bash
streamlit run app.py
```

Your browser will open automatically to `http://localhost:8501`

---

## System Requirements

### Minimum
- **OS**: Windows 10/11, macOS 10.15+, or Ubuntu 20.04+
- **RAM**: 4GB
- **Storage**: 2GB free space
- **Python**: 3.10 or newer
- **Internet**: Required (first run downloads YOLO model)

### Recommended
- **RAM**: 8GB+
- **GPU**: NVIDIA GPU with CUDA support (for faster AI processing)
- **Storage**: SSD recommended

---

## First Time Usage

### Step 1: Import a Trip
1. Click "Choose a .rvtrip package" in the sidebar
2. Select your `.rvtrip` file
3. Click "Read .rvtrip"
4. Wait for verification (green banner = all good!)

### Step 2: Explore Data
- **Trip Information**: View metadata, GPS points, distance
- **Photo Summary**: First/last timestamps, GPS availability
- **Trip Route Map**: See your route on Google Maps
- **Coverage Score**: How much of the route was captured
- **Trip Analytics**: Duration, speed, image intervals
- **Authenticity Score**: Data integrity verification

### Step 3: Export & Share
- **Export Selected Trip**: Single trip with images + metadata
- **Export Full Dataset**: All trips in one package
- **Export Lightweight Package**: Thumbnails for email sharing

---

## Common Issues

### "No module named 'streamlit'"
**Fix**: Run `pip install -r requirements.txt` again

### "Port 8501 in use"
**Fix**: Close other Streamlit apps or run `streamlit run app.py --server.port 8502`

### "YOLO model download failed"
**Fix**: Check internet connection, or manually download from https://github.com/ultralytics/assets/releases

### "GPU not detected"
**Fix**: App still works on CPU. For GPU: install NVIDIA CUDA drivers

### "Import fails with hash mismatch"
**Cause**: File was modified after export
**Fix**: Re-export from the mobile app

---

## Android Companion App (Optional)

To **collect** road data (create `.rvtrip` files), use the **RoadVisionMVP** Android app:

1. Open `RoadVisionMVP` folder in Android Studio
2. Connect Android device (Android 8.0+)
3. Click "Run" to install
4. Grant Camera + Location permissions
5. Tap "Start Trip" while driving/walking
6. App captures photos every 15 seconds with GPS
7. Tap "End Trip" → exports `.rvtrip` file
8. Transfer `.rvtrip` to your computer
9. Import into Desktop Explorer

**Android Requirements**:
- Android 8.0+ (API 26+)
- Camera + GPS
- Storage permission

---

## Support

**Documentation**: See `DOCUMENTATION.md` for technical details

**Issues**: Check the troubleshooting section above

**Contact**: [Your contact info]

---

## License

This software is provided as-is for evaluation and commercial use.

---

## Version History

- **v1.0** (2026-06-27): Initial release with verification, analytics, maps, AI detection
