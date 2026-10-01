"""SRT parsing, writing and health checks. All business times are integer milliseconds."""
import re
from pathlib import Path

TIME = re.compile(r'(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})')


def to_ms(h, m, s, frac):
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(frac.ljust(3, '0')[:3])


def stamp(ms):
    ms = int(round(ms))
    return f'{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02}.{ms % 1000:03}'


def srt_stamp(ms):
    return stamp(ms).replace('.', ',')


def read_srt(path: Path):
    """Return cues [{id, index, start, end, text}] in file order. id is 1-based position."""
    # Split on time lines rather than blank lines: real files contain \r\r\n and stray blanks.
    raw = Path(path).read_text(encoding='utf-8-sig', errors='replace').replace('\r\n', '\n').replace('\r', '\n')
    lines = [line.strip() for line in raw.split('\n')]
    marks = [i for i, line in enumerate(lines) if TIME.search(line)]
    cues = []
    for n, i in enumerate(marks):
        stop = marks[n + 1] if n + 1 < len(marks) else len(lines)
        body = [line for line in lines[i + 1:stop] if line]
        if n + 1 < len(marks) and body and body[-1].isdigit():
            body = body[:-1]  # the next cue's sequence number
        g = TIME.search(lines[i]).groups()
        index = next((lines[j] for j in range(i - 1, -1, -1) if lines[j]), '') if i else ''
        cues.append(dict(id=len(cues) + 1, index=index if index.isdigit() else '', start=to_ms(*g[:4]),
                         end=to_ms(*g[4:]), text=' '.join(body)))
    return cues


def write_srt(cues, path: Path):
    path.write_text(''.join(f"{n}\n{srt_stamp(c['start'])} --> {srt_stamp(c['end'])}\n{c['text']}\n\n"
                            for n, c in enumerate(cues, 1)), encoding='utf-8')


def health(cues, duration_ms):
    """Problems worth reporting. Long silences are legal media and are not errors."""
    issues = []
    if len(cues) < 5:
        issues.append(f'only {len(cues)} cues')
    for c in cues:
        if c['end'] < c['start']:
            issues.append(f"cue {c['id']} negative duration")
        if c['end'] > duration_ms + 1000:
            issues.append(f"cue {c['id']} ends after the video ({stamp(c['end'])})")
    disorder = sum(1 for a, b in zip(cues, cues[1:]) if b['start'] < a['start'])
    if disorder:
        issues.append(f'{disorder} cues out of order')
    if cues and duration_ms and (cues[-1]['end'] - cues[0]['start']) < 0.3 * duration_ms:
        issues.append('subtitles cover less than 30% of the video')
    return issues
