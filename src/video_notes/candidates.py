"""Per-chapter candidate pool: detected screens (+ heartbeat frames inside long static screens,
+ requested times) → original frames → quality/blank gate → OCR novelty → diversity shortlist
→ reading copies and contact sheets. No model calls; every rejection keeps a reason.

Each candidate carries the dialogue spoken while its screen was displayed (screen-aligned
transcript), so the vision model can judge whether a picture matches what was being said.
"""
import csv
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import media
from .detect import overlay
from .srt import stamp
from .vision import ocr as ocr_mod, quality, select

FIELDS = ['frame_id', 'chapter_id', 'actual_ms', 'requested_ms', 'pts_ticks', 'time', 'source', 'status',
          'reason', 'quality', 'text_novelty', 'screen', 'cues', 'ocr', 'path']
SIGNIFICANCE = {'requested': 1.0, 'screen-start': 0.6, 'initial': 0.5, 'screen-end': 0.4, 'heartbeat': 0.15,
                'chapter-seed': 0.2}
BLANK_AREA = 0.001
HEARTBEAT_MS = 20000       # one sample per 20 s inside screens with no detected change
READ_LONG_EDGE = 1280      # reading copies for the vision model; text stays legible
DUPLICATE_AREA = 0.01      # < 1% of pixels clearly changed (320×180 gray) = the same screen.
                           # Measured: same screen 0.00–0.97%, different slides ≥ 2% even when
                           # two text slides share a template (thumbnail cosine could not separate them)
KEEP_ORDER = {'requested': 0, 'screen-end': 1, 'screen-start': 2, 'initial': 3, 'heartbeat': 4, 'chapter-seed': 5}


def _font(size):
    for name in ('msyh.ttc', 'arial.ttf', 'DejaVuSans.ttf'):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def reading_copy(path: Path, long_edge=READ_LONG_EDGE):
    """Reduced JPEG beside the original (same frame). Originals stay for fine detail."""
    target = path.with_name(path.stem + '.read.jpg')
    if not target.is_file():
        with Image.open(path) as img:
            img = img.convert('RGB')
            scale = long_edge / max(img.size)
            if scale < 1:
                img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
            img.save(target, quality=90)
    return target


def contact_sheets(rows, directory: Path, per_sheet=12, columns=4, tile=(480, 270)):
    font, sheets = _font(22), []
    for number, offset in enumerate(range(0, len(rows), per_sheet), 1):
        batch = rows[offset:offset + per_sheet]
        lines = (len(batch) + columns - 1) // columns
        sheet = Image.new('RGB', (columns * tile[0], lines * (tile[1] + 30)), 'white')
        draw = ImageDraw.Draw(sheet)
        for i, row in enumerate(batch):
            x, y = (i % columns) * tile[0], (i // columns) * (tile[1] + 30)
            with Image.open(row['path']) as img:
                sheet.paste(img.convert('RGB').resize(tile), (x, y + 30))
            draw.text((x + 6, y + 4), f"{row['frame_id']}  {row['time']}", fill='black', font=font)
        target = directory / f'sheet-{number:02}.jpg'
        sheet.save(target, quality=85)
        sheets.append(target)
    return sheets


def _gray(path):
    with Image.open(path) as img:
        return np.asarray(img.convert('L').resize((320, 180)), dtype=np.float32)


def changed_area(a, b):
    return float((np.abs(a - b) > 25).mean())


def drop_duplicates(live):
    """Keep one frame per visibly identical screen; prefer requested, then finished states."""
    kept = []
    for r in sorted(live, key=lambda r: (KEEP_ORDER.get(r['source'], 9), r['actual_ms'])):
        g = _gray(r['path'])
        twin = next((k for k in kept if changed_area(g, k['gray']) < DUPLICATE_AREA), None)
        if twin is not None and r['source'] != 'requested':
            r.update(status='duplicate', reason=f"same screen as {twin['frame_id']}")
            continue
        r['gray'] = g
        kept.append(r)
    return sorted(kept, key=lambda r: r['actual_ms'])


def screen_intervals(screens, duration_ms):
    """Displayed intervals [start, end) from screen-start/initial times (ms)."""
    starts = sorted({round(s['time'] * 1000) for s in screens if s['source'] in ('initial', 'screen-start')} | {0})
    return [(a, b) for a, b in zip(starts, starts[1:] + [duration_ms]) if b > a]


def _interval_of(ms, intervals):
    for a, b in intervals:
        if a <= ms < b:
            return a, b
    return ms, ms + 1


def build(chapter, start_ms, end_ms, screens, video, duration_ms, directory: Path, *, cues=(),
          shortlist=32, tesseract=None, ocr_langs='eng', requested_ms=(), keep_ids=(), log=print):
    """Return (shortlist_rows, all_rows). Writes candidates.csv, reading copies and contact sheets."""
    directory.mkdir(parents=True, exist_ok=True)
    frames_dir = directory / 'frames'
    frames_dir.mkdir(exist_ok=True)
    intervals = screen_intervals(screens, duration_ms)
    planned = [(round(s['time'] * 1000), s['source']) for s in screens if start_ms <= s['time'] * 1000 < end_ms]
    for a, b in intervals:  # heartbeat inside long static screens (small CLI/code changes)
        t = max(a, start_ms) + HEARTBEAT_MS
        while t < min(b, end_ms) - 3000:
            planned.append((t, 'heartbeat'))
            t += HEARTBEAT_MS
    planned += [(int(t), 'requested') for t in requested_ms if start_ms <= int(t) < end_ms]
    if not planned:
        planned = [(min(start_ms + 1000, end_ms - 100), 'chapter-seed')]
    rows, failures, first_error = [], 0, ''
    for ms, source in sorted(planned, key=lambda p: (-SIGNIFICANCE.get(p[1], 0), p[0])):
        ms = min(max(ms, start_ms), end_ms - 100)
        # Only filler samples are dropped by time. Detected screens are kept however close together:
        # a slide shown for about a second (flipped past quickly) is still a distinct screen, and the
        # visual duplicate check below decides what is really the same picture.
        too_close = 1500 if source in ('heartbeat', 'chapter-seed') else 200
        if source != 'requested' and any(abs(r['actual_ms'] - ms) < too_close for r in rows):
            continue
        frame_id = f'{chapter}F{ms:08d}'
        path = frames_dir / f'{frame_id}.jpg'
        try:
            meta = media.extract_frame(video, ms, path, duration_ms)
            q = quality.score(path)
            with Image.open(path) as img:
                area = overlay.content_area(img.convert('L').resize((400, 225)))
        except Exception as error:  # one bad seek is normal; all of them failing is not (checked below)
            failures += 1
            first_error = first_error or str(error)
            continue
        a, b = _interval_of(meta['actual_ms'], intervals)
        spoken = [c['id'] for c in cues if c['end'] > a and c['start'] < b]
        row = dict(frame_id=frame_id, chapter_id=chapter, time=stamp(meta['actual_ms']), source=source,
                   path=str(path), ocr='', ocr_subtitle='', scene_significance=SIGNIFICANCE.get(source, 0.3),
                   screen=f'{stamp(a)}–{stamp(b)}', cues=f'C{spoken[0]}–C{spoken[-1]}' if spoken else '', **meta)
        row.update(quality=round(q['quality'], 3), status='candidate', reason='')
        if q['reject'] and source != 'requested':
            row.update(status='rejected', reason=q['reason'])
        elif area <= BLANK_AREA and source != 'requested':
            row.update(status='rejected', reason='blank')
        rows.append(row)
    if failures and not rows:
        raise RuntimeError(f'{chapter}: all {failures} candidate frames failed ({first_error}); '
                           'this is a systemic problem (decoder, disk or memory), not an image-free chapter')
    rows.sort(key=lambda r: r['actual_ms'])
    live = [r for r in rows if r['status'] == 'candidate']
    if tesseract:
        for r in live:
            cache = Path(r['path']).with_suffix('.ocr.json')
            if not cache.is_file():
                content, subtitle = ocr_mod.ocr_frame(Path(r['path']), tesseract, ocr_langs)
                cache.write_text(json.dumps(dict(ocr=content, ocr_subtitle=subtitle), ensure_ascii=False),
                                 encoding='utf-8')
            r.update(json.loads(cache.read_text(encoding='utf-8')))
    ocr_mod.text_novelty(live)
    for r in live:
        r['time_ms'] = r['actual_ms']
        r['vector'] = select.thumbnail_vector(Path(r['path']))
    # Distinct screens all go to the model unless there are more than `shortlist`; only then
    # does the diversity selector trim. Requested frames and an earlier shortlist always stay.
    distinct = drop_duplicates(live)
    chosen = select.select(distinct, shortlist, end_ms - start_ms) if len(distinct) > shortlist else list(distinct)
    must = [r for r in distinct if (r['source'] == 'requested' or r['frame_id'] in keep_ids) and r not in chosen]
    chosen = sorted(chosen + must, key=lambda r: r['actual_ms'])
    chosen_ids = {r['frame_id'] for r in chosen}
    for r in live:
        r.pop('gray', None)
        if r['frame_id'] in chosen_ids:
            r['status'] = 'shortlisted'
            r['read_path'] = str(reading_copy(Path(r['path'])))
        elif r['status'] != 'duplicate':
            r.update(status='not-shortlisted', reason=f"trimmed by diversity selection (budget {shortlist})")
    for r in rows:
        r['ocr'] = ' '.join(r.get('ocr', '').split())[:300]
        r['text_novelty'] = round(r.get('text_novelty', 0.0), 3)
    with (directory / 'candidates.csv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    contact_sheets(chosen, directory)
    log(f'{chapter}: {len(rows)} candidates ({failures} unreadable), {len(live)} passed quality, '
        f'{len(distinct)} distinct screens, {len(chosen)} shortlisted')
    return chosen, rows
