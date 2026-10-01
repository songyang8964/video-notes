"""Diversity-aware greedy selection.

score = intrinsic importance + coverage bonus - similarity to already picked frames,
re-scored after every pick so a burst of interesting frames cannot crowd out the rest.
"""
from pathlib import Path

import numpy as np
from PIL import Image

W_SCENE, W_TEXT, W_QUALITY = 0.40, 0.35, 0.25
COVERAGE_WEIGHT, SIMILARITY_WEIGHT = 0.5, 0.6


def thumbnail_vector(path: Path, size=(48, 27)) -> np.ndarray:
    """Normalised, mean-centred gray thumbnail — cheap visual similarity vector."""
    with Image.open(path) as img:
        v = np.asarray(img.convert('L').resize(size), dtype=np.float32).ravel()
    v -= v.mean()
    norm = float(np.linalg.norm(v))
    return v / norm if norm else v


def intrinsic(c):
    return max(0.0, min(1.0, W_SCENE * c.get('scene_significance', 0.0)
                        + W_TEXT * c.get('text_novelty', 0.0) + W_QUALITY * c.get('quality', 0.0)))


def select(cands, max_frames, span_ms):
    """Pick up to max_frames from cands (dicts with 'time_ms' and optional 'vector'). Time-ordered."""
    pool, picked = list(cands), []
    span = max(span_ms, 1)
    while pool and len(picked) < max_frames:
        best, best_score, best_sim = 0, -1e9, 0.0
        for i, c in enumerate(pool):
            sim = max((float(np.dot(c['vector'], p['vector'])) for p in picked
                       if c.get('vector') is not None and p.get('vector') is not None), default=0.0)
            coverage = 1.0 if not picked else min(
                1.0, min(abs(p['time_ms'] - c['time_ms']) for p in picked) / (span / max(1, max_frames)))
            s = intrinsic(c) + COVERAGE_WEIGHT * coverage - SIMILARITY_WEIGHT * sim
            if s > best_score or (s == best_score and c['time_ms'] < pool[best]['time_ms']):
                best, best_score, best_sim = i, s, sim
        chosen = pool.pop(best)
        chosen['nearest_similarity'] = round(best_sim, 4)
        picked.append(chosen)
    return sorted(picked, key=lambda c: c['time_ms'])
