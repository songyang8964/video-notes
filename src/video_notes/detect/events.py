"""Event finding from the measured signal series.

An event is a peak cluster across the three signals (plus optional extra detector times);
its time is the earliest peak. Each event gets a settled frame before (end state of the
outgoing screen) and after (start of the new screen), searched no further than the
neighbouring event and SETTLE_LIMIT seconds.
"""
import numpy as np

RATE_PEAK_MULTIPLE = 8.0
MERGE_WINDOW = 0.5
SETTLE_LIMIT = 1.0


def _peaks(series, height):
    if len(series) < 3:
        return np.zeros(0, dtype=int)
    s = np.asarray(series, dtype=float)
    hit = s > height
    rise = s[1:-1] >= s[:-2]
    fall = s[1:-1] > s[2:]
    return np.flatnonzero(hit[1:-1] & rise & fall) + 1


def find(measured, *, anchor_threshold, rate_threshold, cut_area_threshold, extra_times=None,
         merge_window=MERGE_WINDOW, settle_limit=SETTLE_LIMIT):
    ts = np.asarray(measured['time_series'], dtype=float)
    if len(ts) == 0:
        return []
    rate = np.asarray(measured['rate_series'], dtype=float)
    tagged = [(int(i), 'cut') for i in _peaks(measured['area_series'], cut_area_threshold)]
    tagged += [(int(i), 'anchor') for i in _peaks(measured['anchor_series'], anchor_threshold)]
    tagged += [(int(i), 'rate') for i in _peaks(rate, rate_threshold * RATE_PEAK_MULTIPLE)]
    tagged += [(int(np.searchsorted(ts, float(t))), 'adaptive') for t in (extra_times or [])]
    tagged = sorted((i, s) for i, s in tagged if 0 <= i < len(ts))
    if not tagged:
        return []
    clusters = []
    for i, sig in tagged:
        if clusters and ts[i] - ts[clusters[-1]['index']] <= merge_window:
            clusters[-1]['signals'].add(sig)
            clusters[-1]['last'] = max(clusters[-1]['last'], i)
            continue
        clusters.append({'index': i, 'last': i, 'signals': {sig}})
    events = []
    for k, c in enumerate(clusters):
        prev_bound = clusters[k - 1]['last'] if k else 0
        next_bound = clusters[k + 1]['index'] if k + 1 < len(clusters) else len(ts) - 1
        before = _settled(rate, ts, c['index'], -1, prev_bound, rate_threshold, settle_limit)
        after = _settled(rate, ts, c['last'], +1, next_bound, rate_threshold, settle_limit)
        events.append({'index': c['index'], 'time': float(ts[c['index']]), 'signals': sorted(c['signals']),
                       'before_time': float(ts[before]), 'after_time': float(ts[after])})
    return events


def _settled(rate, ts, start, step, bound, rate_threshold, settle_limit):
    i = best = start
    while True:
        nxt = i + step
        if not (0 <= nxt < len(rate)) or (nxt - bound) * step > 0 or abs(ts[nxt] - ts[start]) > settle_limit:
            break
        i = nxt
        if rate[i] < rate[best]:
            best = i
        if rate[i] <= rate_threshold:
            return i
    return best
