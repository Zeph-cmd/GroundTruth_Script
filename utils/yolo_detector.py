from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any


MODEL_NAME = "yolov8m.pt"


def _resolve_model_path() -> str:
    """
    Resolve the YOLO model file, accounting for:
      - Running from source (cwd)
      - Running from a PyInstaller --onefile frozen exe (sys._MEIPASS)
      - Persistent user data dir (APPDATA after launcher chdir)
    """
    # 1) Explicit override
    env_path = os.environ.get("GROUNDTRUTH_MODEL_PATH")
    if env_path and Path(env_path).exists():
        return env_path

    # 2) Frozen exe: model bundled into _MEIPASS root
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates = [
                Path(meipass) / MODEL_NAME,
                Path(meipass) / "weights" / MODEL_NAME,
            ]
            for c in candidates:
                if c.exists():
                    return str(c)

    # 3) Next to this file (source tree)
    here = Path(__file__).resolve().parent
    local = here / MODEL_NAME
    if local.exists():
        return str(local)

    # 4) CWD (original behavior)
    cwd_model = Path.cwd() / MODEL_NAME
    if cwd_model.exists():
        return str(cwd_model)

    # 5) Fallback to name (lets ultralytics download it)
    return MODEL_NAME


class YOLODetectorError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def load_model():
    try:
        from ultralytics import YOLO
    except Exception as exc:  # pragma: no cover - import failure path is environment dependent
        raise YOLODetectorError(
            "ultralytics is not available. Install dependencies from requirements.txt to enable YOLO detection."
        ) from exc

    try:
        return YOLO(_resolve_model_path())
    except Exception as exc:  # pragma: no cover - model download/runtime path is environment dependent
        raise YOLODetectorError(
            "Unable to load YOLOv8 pretrained weights. Check network access or local model availability."
        ) from exc


def _coerce_bbox(xyxy: Any) -> list[float]:
    values = xyxy.tolist() if hasattr(xyxy, "tolist") else xyxy
    return [float(values[0]), float(values[1]), float(values[2]), float(values[3])]


def detect_objects(image_path: str | Path) -> list[dict[str, Any]]:
    image_file = Path(image_path)
    if not image_file.exists():
        raise YOLODetectorError(f"Image not found: {image_file}")

    model = load_model()

    try:
        results = model.predict(source=str(image_file), verbose=False, conf=0.25)
    except Exception as exc:  # pragma: no cover - inference path is environment dependent
        raise YOLODetectorError("YOLO inference failed. Try again or use a smaller image.") from exc

    detections: list[dict[str, Any]] = []
    if not results:
        return detections

    names = getattr(model, "names", {})
    first_result = results[0]
    boxes = getattr(first_result, "boxes", None)
    if boxes is None:
        return detections

    for box in boxes:
        class_index = int(box.cls.item()) if hasattr(box.cls, "item") else int(box.cls)
        label = names.get(class_index, str(class_index)) if isinstance(names, dict) else str(class_index)
        confidence = float(box.conf.item()) if hasattr(box.conf, "item") else float(box.conf)
        detections.append(
            {
                "label": label,
                "confidence": round(confidence, 4),
                "bbox": _coerce_bbox(box.xyxy[0]),
            }
        )

    return detections