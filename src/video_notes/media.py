"""Subprocess helpers, ffprobe and original-resolution frame extraction with real PTS."""
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path


class ToolError(RuntimeError):
    pass


def run(args, timeout=None, **kwargs):
    """Run an argument array (never a shell string)."""
    result = subprocess.run([str(a) for a in args], capture_output=True, encoding='utf-8', errors='replace',
                            timeout=timeout, **kwargs)
    if result.returncode:
        raise ToolError(f'{Path(str(args[0])).name} exit {result.returncode}: {result.stderr[-1200:]}')
    return result


def which(name):
    return shutil.which(name)


def sha256(path: Path, block=1 << 20):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        while chunk := handle.read(block):
            digest.update(chunk)
    return digest.hexdigest()


def probe(path: Path):
    data = json.loads(run(['ffprobe', '-v', 'error', '-show_entries',
                           'format=duration,size:stream=codec_type,codec_name,width,height,r_frame_rate',
                           '-of', 'json', path]).stdout)
    video = next((s for s in data['streams'] if s['codec_type'] == 'video'), None)
    if video is None:
        raise ToolError('no video stream')
    return dict(duration_ms=round(float(data['format']['duration']) * 1000), size=int(data['format']['size']),
                width=video.get('width'), height=video.get('height'), codec=video.get('codec_name'),
                has_audio=any(s['codec_type'] == 'audio' for s in data['streams']))


def extract_frame(video: Path, ms: int, target: Path, duration_ms: int):
    """Extract one original-resolution JPEG at ms and record the actual frame PTS.

    Returns {'actual_ms', 'requested_ms', 'pts_ticks'}; cached beside the image."""
    meta = target.with_suffix('.frame.json')
    if not (meta.is_file() and target.is_file()):
        result = run(['ffmpeg', '-hide_banner', '-ss', f'{ms / 1000:.3f}', '-copyts', '-i', video, '-an',
                      '-frames:v', '1', '-vf', 'showinfo', '-fps_mode', 'vfr', '-q:v', '2', '-update', '1', '-y',
                      target], timeout=120)
        first = re.search(r'\bn:\s*0\s+pts:\s*(\d+)\s+pts_time:([\d.]+)', result.stderr)
        if not first or not target.is_file():
            raise ToolError(f'frame extraction failed at {ms} ms')
        meta.write_text(json.dumps(dict(actual_ms=round(float(first[2]) * 1000), pts_ticks=int(first[1]),
                                        requested_ms=int(ms))), encoding='utf-8')
    data = json.loads(meta.read_text(encoding='utf-8'))
    if not 0 <= data['actual_ms'] < duration_ms:
        raise ToolError('frame PTS outside the source')
    return data
