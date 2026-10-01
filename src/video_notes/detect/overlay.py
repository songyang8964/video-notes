"""Fixed overlay band (burned-in captions/banners) estimation.

Edge rows that change far more often than the body are an overlay; measuring change
inside them would count caption churn as screen changes. Only contiguous edge bands
are excluded; a busy centre is content (animation), not overlay.
"""
import numpy as np

ROW_HIT_RATIO = 0.02   # a row "changed" when this share of its pixels changed
HOT_MULTIPLE = 4.0     # x median row frequency counts as abnormally busy
MIN_HOT = 0.005        # absolute floor for nearly static videos
MAX_BAND = 0.20        # an edge band larger than this is content, not overlay
FULL = (0.0, 1.0)


def body_band(row_freq):
    freq = np.asarray(row_freq, dtype=float)
    n = len(freq)
    if n < 8:
        return FULL
    hot = freq > max(HOT_MULTIPLE * float(np.median(freq)), MIN_HOT)
    top = 0
    while top < n and hot[top]:
        top += 1
    bottom = n
    while bottom > top and hot[bottom - 1]:
        bottom -= 1
    if top > n * MAX_BAND:
        top = 0
    if n - bottom > n * MAX_BAND:
        bottom = n
    if top >= bottom:
        return FULL
    return (top / n, bottom / n)


def crop(img, band):
    lo, hi = band
    if (lo, hi) == FULL:
        return img
    h = img.shape[0]
    a, b = int(round(h * lo)), int(round(h * hi))
    return img[a:b] if b - a >= 1 else img


def content_area(img, delta: float = 25.0) -> float:
    """Share of pixels clearly different from the background median — "is there content"."""
    a = np.asarray(img, dtype=np.float32)
    return float((np.abs(a - float(np.median(a))) > delta).mean())
