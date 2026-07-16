"""Fast image enhancement with GPU acceleration when available."""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


ENHANCED_CACHE_DIR = Path("cache") / "opencv_enhance"

# Detect GPU acceleration availability
_HAS_CUDA = hasattr(cv2, 'cuda') and cv2.cuda.getCudaEnabledDeviceCount() > 0
_HAS_OPENCL = hasattr(cv2, 'ocl') and cv2.ocl.haveOpenCL()


def _source_cache_key(image_path: Path) -> str:
    resolved_path = str(image_path.resolve()).encode("utf-8", errors="ignore")
    return hashlib.sha1(resolved_path).hexdigest()


def _enhanced_image_path(image_path: Path) -> Path:
    return ENHANCED_CACHE_DIR / f"{_source_cache_key(image_path)}.png"


def _opencv_enhance_gpu(image_path: Path) -> Path:
    """GPU-accelerated enhancement using OpenCL/CUDA."""
    output_path = _enhanced_image_path(image_path)
    if output_path.exists():
        return output_path

    ENHANCED_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Read and upload to GPU
    img = cv2.imread(str(image_path))
    if img is None:
        return image_path

    if _HAS_CUDA:
        gpu_img = cv2.cuda_GpuMat()
        gpu_img.upload(img)
        
        # GPU denoise
        gpu_img = cv2.cuda.fastNlMeansDenoisingColored(gpu_img, None, 5, 5, 7, 21)
        
        # Download for sharpening (CUDA doesn't have filter2D easily)
        img = gpu_img.download()
    elif _HAS_OPENCL:
        cv2.ocl.setUseOpenCL(True)
        # OpenCL-accelerated denoise
        img = cv2.fastNlMeansDenoisingColored(img, None, 5, 5, 7, 21)
    else:
        img = cv2.fastNlMeansDenoisingColored(img, None, 5, 5, 7, 21)

    # Sharpen (always CPU, fast enough)
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    img = cv2.filter2D(img, -1, kernel)

    # Contrast boost (CLAHE)
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    lab = cv2.merge([l, a, b])
    img = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    cv2.imwrite(str(output_path), img)
    return output_path


def _opencv_enhance_cpu(image_path: Path) -> Path:
    """CPU-only enhancement (fallback)."""
    output_path = _enhanced_image_path(image_path)
    if output_path.exists():
        return output_path

    ENHANCED_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    img = cv2.imread(str(image_path))
    if img is None:
        return image_path

    # Denoise
    img = cv2.fastNlMeansDenoisingColored(img, None, 5, 5, 7, 21)

    # Sharpen
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    img = cv2.filter2D(img, -1, kernel)

    # Contrast boost
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    lab = cv2.merge([l, a, b])
    img = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    cv2.imwrite(str(output_path), img)
    return output_path


def _opencv_enhance(image_path: Path) -> Path:
    """Enhance with GPU if available, else CPU."""
    if _HAS_CUDA or _HAS_OPENCL:
        return _opencv_enhance_gpu(image_path)
    else:
        return _opencv_enhance_cpu(image_path)


@lru_cache(maxsize=256)
def get_enhanced_image_path(image_path_str: str) -> str:
    return str(_opencv_enhance(Path(image_path_str)))
