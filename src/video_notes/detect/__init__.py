"""Screen-change detection.

Measures three signals per frame on a low-resolution body crop (excluding burned-in overlay
bands): drift from an anchor frame (slow build-up such as writing), the area of pixels that
changed sharply (cuts), and instantaneous change rate (motion; also used to find settled
frames). Peaks become screen-change events; each event yields a settled frame before (the
finished state of the outgoing screen) and after (the new screen). An optional PySceneDetect
pass adds extra cut times. Frame rate comes from measured PTS, so variable-frame-rate screen
recordings work, and all expensive decoding is done once per video and cached.
"""
