"""PyAV decoding primitives with bounded decoder threads, retries and batched sequential reads."""
import gc
import time
from pathlib import Path

import av
import numpy as np

DECODE_THREADS = 2


def _decoded(container, stream):
    """Frames up to the first undecodable packet. A damaged recording (e.g. a meeting recorder
    that crashed) often has a corrupt tail; the picture before it is still valid."""
    frames = container.decode(stream)
    while True:
        try:
            yield next(frames)
        except (StopIteration, av.FFmpegError):
            return


def decode_gray_frames(path: Path, w: int = 64, h: int = 36):
    """Stream (pts_seconds, float32 gray w×h) for every frame; memory independent of length."""
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = 'AUTO'
        for frame in _decoded(container, stream):
            gray = frame.reformat(width=w, height=h, format='gray', interpolation='BICUBIC')
            yield frame.time, gray.to_ndarray().astype(np.float32)


def _frame_at(container, time_s: float):
    stream = container.streams.video[0]
    stream.thread_type = 'AUTO'
    stream.thread_count = DECODE_THREADS
    try:
        container.seek(max(int(time_s / stream.time_base), 0), stream=stream)
    except av.FFmpegError:
        pass  # unseekable container: decode sequentially from the start
    last = None
    for frame in _decoded(container, stream):
        if frame.time is None:
            continue
        if frame.time >= time_s - 1e-3:
            return frame
        last = frame
    return last


def gray_many(path: Path, times, w: int = 400, h: int = 225):
    """Gray arrays for many times in ONE sequential decode: {requested_time: array}.

    Thousands of random seeks (two per screen change) take ~1-2 s each on long-GOP
    H.264; a single forward pass costs the same as one signal-measurement pass."""
    wanted = sorted(set(float(t) for t in times))
    out, k = {}, 0
    if not wanted:
        return out
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = 'AUTO'
        last = None
        for frame in _decoded(container, stream):
            if frame.time is None:
                continue
            while k < len(wanted) and frame.time >= wanted[k] - 1e-3:
                out[wanted[k]] = frame.reformat(width=w, height=h, format='gray',
                                                interpolation='BICUBIC').to_ndarray()
                k += 1
            if k >= len(wanted):
                break
            last = frame
        if last is not None:  # requests past the final frame get the last frame
            arr = last.reformat(width=w, height=h, format='gray', interpolation='BICUBIC').to_ndarray()
            for t in wanted[k:]:
                out[t] = arr
    return out


def gray_at(path: Path, time_s: float, w: int = 200, h: int = 112, attempts: int = 3):
    """Gray array at time_s. Retries transient decode/memory failures; None if it never succeeds."""
    for attempt in range(1, attempts + 1):
        try:
            with av.open(str(path)) as container:
                frame = _frame_at(container, time_s)
                if frame is None:
                    return None
                return frame.reformat(width=w, height=h, format='gray', interpolation='BICUBIC').to_ndarray()
        except (av.FFmpegError, MemoryError, OSError):
            if attempt == attempts:
                return None
            gc.collect()
            time.sleep(2 * attempt)
