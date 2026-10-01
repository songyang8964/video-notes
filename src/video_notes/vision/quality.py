"""Cheap pixel-statistics quality gate (blur, too dark, too bright)."""
import math
from pathlib import Path

import numpy as np
from PIL import Image

BLUR_FLOOR = 8.0      # Laplacian variance below this reads as blur, fade or dissolve
DARK_FLOOR = 12.0     # mean luma 0..255
BRIGHT_CEIL = 243.0


def laplacian_variance(gray: np.ndarray) -> float:
    g = gray.astype(np.float64)
    lap = -4 * g[1:-1, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:] + g[:-2, 1:-1] + g[2:, 1:-1]
    return float(lap.var()) if lap.size else 0.0


def score(path: Path, width: int = 640):
    """{'quality', 'blur', 'brightness', 'reject', 'reason'} — quality saturates with sharpness."""
    with Image.open(path) as img:
        img = img.convert('L')
        if img.width > width:
            img = img.resize((width, round(img.height * width / img.width)))
        gray = np.asarray(img)
    brightness = float(gray.mean())
    blur = laplacian_variance(gray)
    if brightness < DARK_FLOOR:
        return dict(quality=0.0, blur=blur, brightness=brightness, reject=True, reason='too_dark')
    if brightness > BRIGHT_CEIL:
        return dict(quality=0.0, blur=blur, brightness=brightness, reject=True, reason='too_bright')
    if blur < BLUR_FLOOR:
        return dict(quality=0.0, blur=blur, brightness=brightness, reject=True, reason='blurry')
    return dict(quality=min(1.0, math.log10(1 + blur) / 3), blur=blur, brightness=brightness,
                reject=False, reason='')
