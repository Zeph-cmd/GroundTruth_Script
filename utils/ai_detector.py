from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DETECTION_LABELS = ("pothole", "crack", "flood", "road_sign")


@dataclass
class MockRoadObjectDetector:
    seed_prefix: str = "roadvision"

    def predict(self, image: Any) -> dict[str, float]:
        image_key = getattr(image, "name", None) or str(image)
        digest = hashlib.sha256(f"{self.seed_prefix}:{image_key}".encode("utf-8")).digest()

        confidence_map: dict[str, float] = {}
        for index, label in enumerate(DETECTION_LABELS):
            raw_value = digest[index] / 255.0
            confidence_map[label] = round(0.15 + (raw_value * 0.75), 2)

        return confidence_map


def load_model() -> MockRoadObjectDetector:
    return MockRoadObjectDetector()


def detect_road_objects(image_path: str | Path) -> dict[str, float]:
    model = load_model()
    return model.predict(Path(image_path))