"""Optional Tesseract OCR and text novelty.

The image is passed on stdin, which avoids failures with non-ASCII paths on Windows. Missing Tesseract degrades to no OCR, reported by doctor.
"""
import io
import os
import re
import shutil
import subprocess
from pathlib import Path

from PIL import Image

CAPTION_BAND_TOP = 0.12
CAPTION_BAND_BOTTOM = 0.78
TOKEN_CHANGE_SATURATION = 3
SUBTITLE_DISCOUNT = 0.1


def tesseract_path(configured=None):
    for candidate in (configured, os.environ.get('VIDEO_NOTES_TESSERACT'), shutil.which('tesseract'),
                      r'C:\Program Files\Tesseract-OCR\tesseract.exe'):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return None


def _ocr(image: Image.Image, exe: str, langs: str) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    env = dict(os.environ)
    tessdata = Path(exe).parent / 'tessdata'
    if tessdata.is_dir() and 'TESSDATA_PREFIX' not in env:
        env['TESSDATA_PREFIX'] = str(tessdata)
    result = subprocess.run([exe, 'stdin', 'stdout', '-l', langs, '--psm', '6'], input=buffer.getvalue(),
                            capture_output=True, timeout=120, env=env)
    return result.stdout.decode('utf-8', errors='replace') if result.returncode == 0 else ''


def ocr_frame(path: Path, exe: str, langs: str = 'eng'):
    """(content_text, subtitle_text): the middle band and the bottom caption band separately."""
    with Image.open(path) as img:
        img = img.convert('L')
        w, h = img.size
        content = img.crop((0, int(h * CAPTION_BAND_TOP), w, int(h * CAPTION_BAND_BOTTOM)))
        subtitle = img.crop((0, int(h * CAPTION_BAND_BOTTOM), w, h))
        return _ocr(content, exe, langs), _ocr(subtitle, exe, langs)


def normalize(text: str) -> str:
    return re.sub(r'\s+', ' ', text.lower()).strip()


def text_delta(a: str, b: str) -> float:
    """max(Jaccard distance, saturating changed-token count) — robust to dilution and OCR jitter."""
    A, B = set(normalize(a).split()), set(normalize(b).split())
    if not A and not B:
        return 0.0
    if not A or not B:
        return 1.0
    inter = len(A & B)
    ratio = 1 - inter / (len(A) + len(B) - inter)
    changed = (len(A) - inter) + (len(B) - inter)
    return max(ratio, min(1.0, changed / TOKEN_CHANGE_SATURATION))


def text_novelty(items):
    """items: time-ordered dicts with 'ocr' and 'ocr_subtitle'. Adds 'text_novelty' in place.

    New content text that persists into the next frame keeps full weight; churn is discounted,
    and a frame with no text has no new text whatever came before."""
    for i, c in enumerate(items):
        if i == 0:
            c['text_novelty'] = 0.0
            continue
        prev, nxt = items[i - 1], items[i + 1] if i + 1 < len(items) else None
        content_in = text_delta(prev['ocr'], c['ocr']) if normalize(c['ocr']) else 0.0
        subtitle = text_delta(prev['ocr_subtitle'], c['ocr_subtitle']) if normalize(c['ocr_subtitle']) else 0.0
        out = text_delta(c['ocr'], nxt['ocr']) if nxt else 0.0
        persistence = 1 - out * (1 - SUBTITLE_DISCOUNT)
        c['text_novelty'] = min(1.0, content_in * persistence + SUBTITLE_DISCOUNT * subtitle)
    return items
