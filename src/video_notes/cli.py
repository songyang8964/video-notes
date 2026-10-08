"""`video-notes` command line: the tool prepares and checks, an assistant in a chat writes the chapters.

    video-notes [prepare] [video] [--srt subs.srt] [--context ctx.md]   # checks, chapters, frames, briefs
    video-notes assemble [video] [--output DIR]                          # checks the chapters, writes the note
    video-notes setup [--language English] [--tesseract PATH] ...
    video-notes doctor

Exit codes: 0 success, 2 input/argument ambiguity, 1 dependency/processing/check failure,
130 interrupted. Progress and errors go to stderr; the brief folder or note path to stdout.
"""
import argparse
import re
import shutil
import sys
import time
from pathlib import Path

from . import __version__
from .config import config_path, load_config, save_config

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8', errors='replace')

URL = re.compile(r'^[a-z][a-z0-9+.-]*://', re.I)


def log(message):
    print(time.strftime('%H:%M:%S ') + message, file=sys.stderr, flush=True)


def discover(argument, cwd: Path):
    """Resolve the input video per the project rules (top level of cwd only, never recursive)."""
    if argument:
        if URL.match(argument):
            raise SystemExit(_fail(2, 'Online links are not supported yet; download the video first and pass the '
                                      'local file path.'))
        path = (cwd / argument).resolve() if not Path(argument).is_absolute() else Path(argument)
        if not path.is_file():
            raise SystemExit(_fail(2, f'Video not found: {path}'))
        return path
    videos = sorted(p for p in cwd.iterdir() if p.is_file() and p.suffix.lower() == '.mp4')
    if not videos:
        raise SystemExit(_fail(2, f'No .mp4 in {cwd}. Put the video here or pass its path: video-notes <video>'))
    if len(videos) > 1:
        names = '\n  '.join(p.name for p in videos)
        raise SystemExit(_fail(2, f'Several videos found; choose one:\n  {names}\nExample: video-notes "{videos[0].name}"'))
    return videos[0]


def _fail(code, message):
    print('error: ' + message, file=sys.stderr, flush=True)
    return code


def doctor():
    from .transcribe import available_backends
    from .vision.ocr import tesseract_path
    config = load_config()
    rows, fatal = [], False
    for tool in ('ffmpeg', 'ffprobe'):
        ok = bool(shutil.which(tool))
        fatal |= not ok
        rows.append((tool, 'ok' if ok else 'MISSING (required; install FFmpeg and add it to PATH)'))
    for module in ('av', 'numpy', 'PIL', 'skimage'):
        try:
            __import__(module)
            rows.append((f'python:{module}', 'ok'))
        except ImportError:
            fatal = True
            rows.append((f'python:{module}', 'MISSING (pip install -e . in the tool folder)'))
    try:
        __import__('scenedetect')
        rows.append(('python:scenedetect', 'ok (auxiliary detector)'))
    except ImportError:
        rows.append(('python:scenedetect', 'not installed (optional, improves recall)'))
    try:
        import pypandoc
        rows.append(('word:pandoc', f'ok ({pypandoc.get_pandoc_version()})'))
    except (ImportError, OSError):
        rows.append(('word:pandoc', 'not installed (the Word copy is skipped; pip install pypandoc-binary)'))
    tess = tesseract_path(config.get('tesseract'))
    rows.append(('ocr:tesseract', tess or 'not found (optional; OCR text novelty disabled)'))
    asr = available_backends(config)
    rows.append(('asr', ', '.join(asr) if asr else 'none (needed only for videos without subtitles)'))
    rows.append(('config', str(config_path())))
    width = max(len(k) for k, _ in rows)
    for key, value in rows:
        print(f'{key.ljust(width)}  {value}')
    return 1 if fatal else 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ['doctor']:
        return doctor()
    if argv[:1] == ['setup']:
        parser = argparse.ArgumentParser(prog='video-notes setup', description='Save default settings once.')
        parser.add_argument('--language', dest='output_language', help='note language, e.g. 中文 or English')
        parser.add_argument('--max-images', type=int)
        parser.add_argument('--parallel-chapters', type=int, help='chapters prepared at the same time (default 3)')
        parser.add_argument('--tesseract', help='path to tesseract.exe (optional)')
        parser.add_argument('--ocr-langs', help='Tesseract languages, e.g. eng+chi_sim')
        parser.add_argument('--asr-backend', choices=['whisperx', 'faster-whisper', 'openai-whisper'])
        parser.add_argument('--whisperx', help='path to the whisperx executable')
        args = vars(parser.parse_args(argv[1:]))
        path = save_config({k: v for k, v in args.items() if v is not None})
        print(f'saved {path}')
        return doctor()

    mode = 'assemble' if argv[:1] == ['assemble'] else 'prepare'
    if argv[:1] in (['prepare'], ['assemble']):
        argv = argv[1:]
    parser = argparse.ArgumentParser(prog=f'video-notes {mode}', description=(
        'Turn a local lecture/training video into an illustrated note (Markdown and Word) together with an '
        'assistant in a chat. `video-notes prepare` (the default) checks the video and subtitles, splits '
        'chapters, captures candidate frames and writes one brief per chapter; the assistant writes each '
        'chapter from its brief; `video-notes assemble` checks the chapters and writes <video>.md, '
        '<video>.docx and <video>_assets beside the video. Other commands: '
        'video-notes setup, video-notes doctor.'))
    parser.add_argument('video', nargs='?', help='local video file (default: the only .mp4 in this folder)')
    parser.add_argument('--srt', help='existing subtitle file (default: same-name .srt beside the video, '
                                      'otherwise local speech recognition)')
    parser.add_argument('--output', help='folder for <video>.md, <video>.docx and <video>_assets '
                                         '(default: the folder of the video)')
    parser.add_argument('--context', help='per-video context Markdown (default: <video>.context.md)')
    parser.add_argument('--version', action='version', version=f'video-notes {__version__}')
    args = parser.parse_args(argv)
    cwd = Path.cwd()
    video = discover(args.video, cwd)
    if args.srt and not Path(args.srt).is_file():
        return _fail(2, f'Subtitle not found: {args.srt}')
    from .media import ToolError
    from .pipeline import InputError, Pipeline
    from .transcribe import TranscriptionUnavailable
    try:
        root = (cwd / args.output).resolve() if args.output else video.parent
        pipeline = Pipeline(video, root, load_config(), srt=args.srt, context=args.context, cwd=cwd, log=log)
        if mode == 'prepare':
            briefs = pipeline.prepare()
            print(briefs, flush=True)
            log(f'briefs ready: read {briefs / "README.md"}; for each chapter write topics.csv, knowledge.csv, '
                f'chapter.md and review.md next to its brief.md, then run: video-notes assemble'
                + (f' "{video.name}"' if args.video else ''))
            return 0
        note, problems = pipeline.assemble()
    except KeyboardInterrupt:
        log('interrupted; progress is kept, run the same command again to resume')
        return 130
    except InputError as error:
        return _fail(2, str(error))
    except (ToolError, TranscriptionUnavailable, RuntimeError, OSError) as error:
        log('failed; completed stages are cached, rerun to resume')
        return _fail(1, str(error))
    if problems:
        for chapter, items in problems.items():
            log(f'{chapter}: ' + '; '.join(items))
        return _fail(1, f'{len(problems)} chapter(s) did not pass the checks; nothing was assembled')
    print(note, flush=True)
    return 0
