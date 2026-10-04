"""Choose the subtitle that really is this video's dialogue.

Selection rules:
- candidates: explicit --srt (a demand: refusal stops the run), then same-stem sidecars
  (.srt/.vtt, language-tagged like lecture.en.srt) and embedded text tracks, ranked as one
  list by requested language → untagged → others; then speech recognition.
- a candidate is refused when it looks like something else: fewer than 5 cues, covering
  less than 30% of the running time, extending far past the video, or >30% of cues starting
  with the previous cue's text (rolling auto-captions that were not de-duplicated). An
  explicit --srt that only runs past the video (audio longer than a damaged picture) is kept.
- WebVTT rolling cues are de-duplicated while parsing (each cue repeats the previous lines).
Every refusal is recorded so a poor transcript can be traced to its source decision.
"""
import re
import subprocess
from pathlib import Path

from .srt import read_srt, to_ms, write_srt

SUFFIXES = ('.srt', '.vtt')
VTT_TIME = re.compile(r'(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})')


def read_vtt(path: Path):
    lines = Path(path).read_text(encoding='utf-8-sig', errors='replace').replace('\r', '').split('\n')
    cues, seen_tail = [], []
    i = 0
    while i < len(lines):
        match = VTT_TIME.search(lines[i])
        if not match:
            i += 1
            continue
        g = [x or '0' for x in match.groups()]
        body, i = [], i + 1
        while i < len(lines) and lines[i].strip():
            body.append(re.sub(r'<[^>]+>', '', lines[i]).strip())
            i += 1
        # Rolling captions repeat the previous cue's lines first; keep only new lines.
        new = [line for line in body if line and line not in seen_tail]
        seen_tail = body[-2:]
        if new:
            cues.append(dict(id=len(cues) + 1, index='', start=to_ms(*g[:4]), end=to_ms(*g[4:]), text=' '.join(new)))
    return cues


def read_any(path: Path):
    return read_vtt(path) if Path(path).suffix.lower() == '.vtt' else read_srt(path)


def refuse_reason(cues, duration_ms):
    if len(cues) < 5:
        return f'only {len(cues)} cues'
    if cues[-1]['end'] > duration_ms + 60000:
        return 'extends far beyond the video (cut for a different edit?)'
    covered = sum(max(0, c['end'] - c['start']) for c in cues)
    span = cues[-1]['end'] - cues[0]['start']
    if span < 0.3 * duration_ms and covered < 0.3 * duration_ms:
        return 'covers less than 30% of the running time (forced/partial track?)'
    rolling = sum(1 for a, b in zip(cues, cues[1:]) if a['text'] and b['text'].startswith(a['text'][:40]))
    if rolling > 0.3 * len(cues):
        return 'rolling auto-captions not de-duplicated'
    return ''


def _language(path: Path, stem: str):
    rest = path.name[len(stem):-len(path.suffix)].strip('.')
    return rest.split('.')[0].lower() if rest else ''


def sidecars(video: Path):
    out = []
    for path in video.parent.iterdir():
        if path.suffix.lower() in SUFFIXES and path.name.lower().startswith(video.stem.lower()):
            rest = path.name[len(video.stem):]
            if rest.startswith('.') or rest.lower() in SUFFIXES:
                out.append(path)
    return out


def embedded_tracks(video: Path, work: Path):
    """Extract text subtitle tracks to SRT (bitmap tracks cannot be converted and are skipped)."""
    probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 's', '-show_entries',
                            'stream=index,codec_name:stream_tags=language', '-of', 'csv=p=0', str(video)],
                           capture_output=True, text=True, encoding='utf-8', errors='replace')
    tracks = []
    for n, line in enumerate(filter(None, probe.stdout.splitlines())):
        parts = line.split(',')
        codec, lang = parts[1] if len(parts) > 1 else '', parts[2] if len(parts) > 2 else ''
        if codec in ('hdmv_pgs_subtitle', 'dvd_subtitle', 'dvb_subtitle'):
            continue
        target = work / f'embedded-{n}{("." + lang) if lang else ""}.srt'
        if not target.is_file():
            result = subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(video), '-map', f'0:s:{n}', str(target)],
                                    capture_output=True)
            if result.returncode or not target.is_file():
                continue
        tracks.append((target, lang.lower()))
    return tracks


def choose(video: Path, duration_ms, work: Path, explicit=None, language=''):
    """→ (path or None, cues, source, notes). Explicit subtitles must pass or the run stops."""
    notes = []
    if explicit:
        cues = read_any(explicit)
        reason = refuse_reason(cues, duration_ms)
        if reason.startswith('extends far beyond'):
            # The user vouched for this file: e.g. a meeting recorder's separate audio track that runs
            # past a damaged video. Cues after the picture are kept and written without images.
            notes.append(f'{Path(explicit).name} runs past the video until {cues[-1]["end"] // 1000} s; '
                         'accepted because it was given explicitly')
            reason = ''
        if reason:
            raise ValueError(f'subtitle {Path(explicit).name} refused: {reason}')
        return Path(explicit), cues, 'explicit', notes
    work.mkdir(parents=True, exist_ok=True)
    pool = [(p, _language(p, video.stem), 0) for p in sidecars(video)]
    pool += [(p, lang, 1) for p, lang in embedded_tracks(video, work)]
    want = (language or '').lower()

    def rank(item):
        path, lang, origin = item
        tier = 0 if want and want not in ('auto',) and lang == want else 1 if not lang else 2
        return (tier, origin, 'forced' in path.name.lower(), path.suffix.lower() != '.srt', path.name)
    for path, lang, origin in sorted(pool, key=rank):
        cues = read_any(path)
        reason = refuse_reason(cues, duration_ms)
        if reason:
            notes.append(f'refused {path.name}: {reason}')
            continue
        if path.suffix.lower() == '.vtt':
            converted = work / (path.stem + '.from-vtt.srt')
            write_srt(cues, converted)
            path = converted
        notes.append(f'chose {path.name} ({"embedded track" if origin else "sidecar"}{", " + lang if lang else ""})')
        return path, cues, 'embedded' if origin else 'sidecar', notes
    return None, [], 'none', notes
