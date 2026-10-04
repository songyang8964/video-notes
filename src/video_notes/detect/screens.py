"""Whole-video screen detection with caching.

detect_screens() decodes the video once for the overlay band, once for the three signals,
optionally once more for PySceneDetect's AdaptiveDetector, and caches everything in
cache_dir. Re-runs (and every chapter window) reuse the cache; only thresholds are cheap
to change. Returned candidates are source-time seconds with their provenance.
"""
import json
import math
from pathlib import Path

import numpy as np

from . import decode, events as events_mod, overlay

SIGNALS_SCHEMA = 'video-notes-signals/1'
ADAPTIVE_SCHEMA = 'video-notes-adaptive/2'
CUT_DELTA = 25.0
ANCHOR_RESET, CUT_RESET, RATE_SETTLE = 0.02, 0.02, 0.0015
DEFAULTS = dict(anchor_threshold=0.02, rate_threshold=0.0015, cut_area_threshold=0.002)


def _scan_rows(path):
    prev, hits, n = None, None, 0
    for _t, f in decode.decode_gray_frames(path):
        if prev is not None:
            changed = (np.abs(f - prev) > CUT_DELTA).mean(axis=1) > overlay.ROW_HIT_RATIO
            hits = changed.astype(np.float64) if hits is None else hits + changed
            n += 1
        prev = f
    return (hits / n) if n else np.zeros(0)


def _measure(path, band):
    times, anchor_s, rate_s, area_s = [], [0.0], [0.0], [0.0]
    it = decode.decode_gray_frames(path)
    try:
        t0, first = next(it)
    except StopIteration:
        return None
    times.append(t0 or 0.0)
    anchor = prev = overlay.crop(first, band)
    unsettled = False
    for i, (t, raw) in enumerate(it, 1):
        times.append(t if t is not None else times[-1])
        f = overlay.crop(raw, band)
        delta = np.abs(f - prev)
        anchor_diff = float(np.abs(f - anchor).mean()) / 255.0
        inst = float(delta.mean()) / 255.0
        area = float((delta > CUT_DELTA).mean())
        anchor_s.append(anchor_diff)
        rate_s.append(inst)
        area_s.append(area)
        prev = f
        if not unsettled:
            unsettled = anchor_diff > ANCHOR_RESET or area > CUT_RESET
        elif inst <= RATE_SETTLE:
            anchor, unsettled = f, False
    return dict(time_series=np.array(times), anchor_series=np.array(anchor_s),
                rate_series=np.array(rate_s), area_series=np.array(area_s))


def measured_signals(video: Path, cache_dir: Path, log=print):
    cache = cache_dir / 'detect_signals.npz'
    if cache.is_file():
        data = np.load(cache)
        if str(data['schema']) == SIGNALS_SCHEMA:
            return {k: data[k] for k in ('time_series', 'anchor_series', 'rate_series', 'area_series')} | {
                'band': tuple(float(x) for x in data['band'])}
    log('screen detection: overlay band pass (whole video)')
    band = overlay.body_band(_scan_rows(video))
    log(f'screen detection: signal pass, body band {band[0]:.0%}-{band[1]:.0%}')
    measured = _measure(video, band)
    if measured is None:
        raise RuntimeError('video has no decodable frames')
    cache_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, schema=SIGNALS_SCHEMA, band=np.array(band), **measured)
    return measured | {'band': band}


def measured_fps(time_series):
    """fps from real PTS — container rates are unreliable for VFR screen recordings."""
    steps = np.diff(np.asarray(time_series, dtype=float))
    steps = steps[steps > 0]
    return 1.0 / float(np.median(steps)) if len(steps) else 25.0


def picture_end(video: Path, cache_dir: Path, log=print):
    """End of the decodable picture in seconds (last decoded frame + one frame). Shorter than the
    container duration when the recording has a damaged tail; no frame is taken after it."""
    times = measured_signals(video, cache_dir, log)['time_series']
    return float(times[-1]) + 1.0 / measured_fps(times)


def _stable_time(video, t0, duration, offset=1.5, retry=1.0, ssim_threshold=0.97):
    from skimage.metrics import structural_similarity as ssim
    t = min(t0 + offset, duration - 0.05)
    a = decode.gray_at(video, t)
    b = decode.gray_at(video, min(t + 0.5, duration - 0.02))
    if a is not None and b is not None and a.shape == b.shape and ssim(a, b) < ssim_threshold:
        return min(t0 + offset + retry, duration - 0.05)
    return t


def adaptive_times(video: Path, cache_dir: Path, time_series, log=print):
    """Optional PySceneDetect AdaptiveDetector cut times, mapped from frame numbers to real PTS.

    Only the detection time feeds event finding (settle points come from the signals), so cuts are
    not individually re-decoded."""
    cache = cache_dir / 'detect_adaptive.json'
    if cache.is_file():
        data = json.loads(cache.read_text(encoding='utf-8'))
        if data.get('schema') == ADAPTIVE_SCHEMA:
            return data['times']
    try:
        from scenedetect import SceneManager, open_video
        from scenedetect.detectors import AdaptiveDetector
    except ImportError:
        log('screen detection: PySceneDetect not installed; skipping the auxiliary detector')
        return []
    log('screen detection: AdaptiveDetector pass (whole video, one time)')
    manager = SceneManager()
    manager.add_detector(AdaptiveDetector(adaptive_threshold=2.0))
    manager.detect_scenes(open_video(str(video)), show_progress=False)
    last = len(time_series) - 1
    indices = [s[0].get_frames() for s in manager.get_scene_list()[1:]]
    times = [float(time_series[min(i, last)]) for i in indices]
    if indices and max(indices) > last:
        log(f'screen detection: auxiliary detector saw {max(indices) + 1} frames vs {last + 1} measured; clamped')
    cache.write_text(json.dumps(dict(schema=ADAPTIVE_SCHEMA, indices=indices, times=times)), encoding='utf-8')
    return times


def _changed(a, b, threshold, band):
    from skimage.metrics import structural_similarity as ssim
    if a is None or b is None or a.shape != b.shape:
        return True  # keep the candidate when it cannot be judged
    return ssim(overlay.crop(a, band), overlay.crop(b, band)) < threshold


def detect_screens(video: Path, cache_dir: Path, duration_s: float, *, use_adaptive=True,
                   pair_dup_threshold=0.93, log=print, **thresholds):
    """All screen candidates for the whole video, cached as screens.json.

    Each candidate: {'time', 'detected_at', 'source' in initial|screen-start|screen-end}."""
    params = DEFAULTS | thresholds
    key = json.dumps(dict(params, adaptive=use_adaptive, pair=pair_dup_threshold), sort_keys=True)
    cache = cache_dir / 'screens.json'
    if cache.is_file():
        data = json.loads(cache.read_text(encoding='utf-8'))
        if data.get('key') == key:
            return data['candidates']
    measured = measured_signals(video, cache_dir, log)
    fps = measured_fps(measured['time_series'])
    extra = adaptive_times(video, cache_dir, measured['time_series'], log) if use_adaptive else []
    found = events_mod.find(measured, extra_times=extra, **params)
    log(f'screen detection: {len(found)} screen changes, measured fps {fps:.2f}')
    band = measured['band']
    first = _stable_time(video, 0.0, duration_s, offset=0.5)
    # A screen's end state is kept only if it differs from the screen's start (e.g. a build-up
    # slide or a typed command); every pair is compared from one sequential decode.
    starts = [first] + [e['after_time'] for e in found]
    ends = [e['before_time'] for e in found] + ([duration_s - 0.2] if math.isfinite(duration_s) else [])
    pairs = [(s, e) for s, e in zip(starts, ends) if e - s > max(1.0 / fps, 0.0)]
    log(f'screen detection: comparing {len(pairs)} screen start/end pairs (one sequential pass)')
    grays = decode.gray_many(video, [t for pair in pairs for t in pair])
    keep_end = {e for s, e in pairs if _changed(grays.get(s), grays.get(e), pair_dup_threshold, band)}
    candidates = [dict(time=first, detected_at=0.0, source='initial')]
    for e in found:
        if e['before_time'] in keep_end:
            candidates.append(dict(time=e['before_time'], detected_at=e['time'], source='screen-end'))
        candidates.append(dict(time=e['after_time'], detected_at=e['time'], source='screen-start'))
    if math.isfinite(duration_s) and (duration_s - 0.2) in keep_end and duration_s - starts[-1] > 2:
        candidates.append(dict(time=duration_s - 0.2, detected_at=duration_s, source='screen-end'))
    cache.write_text(json.dumps(dict(key=key, fps=fps, band=list(band), candidates=candidates)), encoding='utf-8')
    return candidates
