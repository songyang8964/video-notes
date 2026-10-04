"""`video-notes` command line.

    video-notes                      # the only .mp4 in the current folder
    video-notes lecture.mp4 [--srt subs.srt] [--output DIR] [--context ctx.md] [--backend claude|codex]
    video-notes setup [--backend claude|codex] [--model NAME] [--tesseract PATH] ...
    video-notes doctor
    video-notes prepare [video] / video-notes assemble [video]   # agent mode: no model calls

Exit codes: 0 success, 2 input/argument ambiguity, 1 dependency/model/processing/acceptance
failure, 130 interrupted. Progress and errors go to stderr; the final note path to stdout.
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
    from .llm import ModelError, candidates, probe, resolve
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
    for backend in ('claude', 'codex'):
        selected = config['backend'] == backend
        try:
            exe = resolve(backend)
            origin = next(o for p, o in candidates(backend) if str(p) == exe)
        except ModelError:
            fatal |= selected
            rows.append((f'model:{backend}', 'not found' + (' — SELECTED BACKEND MISSING' if selected else '')))
            continue
        if selected:
            ok, detail = probe(backend, config.get(f'{backend}_model'), config.get(f'{backend}_effort'))
            fatal |= not ok
            if 'unrecognized_model' in detail or 'unknown model' in detail.lower():
                fix = f'this CLI version does not know the model; update it or `video-notes setup --model <name>`'
            else:
                fix = f'sign in once: "{exe}" then /login' if backend == 'claude' else f'sign in once: "{exe}" login'
            chosen = f"{config.get(f'{backend}_model') or 'default'} / {config.get(f'{backend}_effort') or 'default'}"
            rows.append((f'model:{backend}', (f'ok, signed in, {chosen} (selected) — {origin}: {exe}' if ok
                         else f'SELECTED BUT NOT USABLE: {detail} — {fix} [{origin}: {exe}]')))
        else:
            rows.append((f'model:{backend}', f'installed — {origin}: {exe}'))
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
        parser.add_argument('--backend', choices=['claude', 'codex'])
        parser.add_argument('--model', help='model for the backend being configured, e.g. claude-opus-5-5 or gpt-6.1-sol')
        parser.add_argument('--effort', help='reasoning effort for that backend, e.g. medium')
        parser.add_argument('--claude-path', help='claude executable, e.g. the copy inside the Claude desktop app')
        parser.add_argument('--codex-path', help='codex executable, e.g. the copy inside the Codex desktop app')
        parser.add_argument('--language', dest='output_language', help='note language, e.g. 中文 or English')
        parser.add_argument('--max-images', type=int)
        parser.add_argument('--fallback-backend', choices=['claude', 'codex'],
                            help='backend that takes over automatically when the main one reaches its usage limit')
        parser.add_argument('--parallel-chapters', type=int, help='chapters processed at the same time (default 3)')
        parser.add_argument('--tesseract', help='path to tesseract.exe (optional)')
        parser.add_argument('--ocr-langs', help='Tesseract languages, e.g. eng+chi_sim')
        parser.add_argument('--asr-backend', choices=['whisperx', 'faster-whisper', 'openai-whisper'])
        parser.add_argument('--whisperx', help='path to the whisperx executable')
        args = vars(parser.parse_args(argv[1:]))
        target = args.get('backend') or load_config()['backend']
        for key in ('model', 'effort'):  # stored per backend so switching backends keeps both choices
            if args.get(key) is not None:
                args[f'{target}_{key}'] = args[key]
            args.pop(key, None)
        changes = {k: v for k, v in args.items() if v is not None}
        path = save_config(changes)
        print(f'saved {path}')
        return doctor()

    mode = argv[0] if argv[:1] in (['prepare'], ['assemble']) else None
    if mode:
        argv = argv[1:]
    parser = argparse.ArgumentParser(prog='video-notes' + (f' {mode}' if mode else ''), description=(
        'Turn a local lecture/training video into an illustrated Markdown note. Run it in the folder that '
        'holds the video. Other commands: video-notes setup, video-notes doctor, and the agent mode '
        '`video-notes prepare` / `video-notes assemble` (an assistant in a chat writes the chapters itself; '
        'the tool runs no model).'))
    parser.add_argument('video', nargs='?', help='local video file (default: the only .mp4 in this folder)')
    parser.add_argument('--srt', help='existing subtitle file (default: same-name .srt beside the video, '
                                      'otherwise automatic transcription)')
    parser.add_argument('--output', default='output', help='output root (default: ./output)')
    parser.add_argument('--context', help='per-video context Markdown (default: <video>.context.md)')
    parser.add_argument('--backend', choices=['claude', 'codex'], help='override the configured model backend')
    parser.add_argument('--fallback-backend', choices=['claude', 'codex'], help='automatically continue unfinished calls with this backend on a usage limit')
    parser.add_argument('--resume-review', action='store_true', help='review persisted chapters only; keep verified PASS checkpoints, without extracting frames or rewriting initial drafts')
    parser.add_argument('--codex-service-tier', choices=['fast', 'flex'], help='override the Codex service tier for this process only')
    parser.add_argument('--version', action='version', version=f'video-notes {__version__}')
    args = parser.parse_args(argv)
    cwd = Path.cwd()
    video = discover(args.video, cwd)
    if args.srt and not Path(args.srt).is_file():
        return _fail(2, f'Subtitle not found: {args.srt}')
    config = load_config()
    if args.backend:
        config['backend'] = args.backend
    if args.fallback_backend:
        config['fallback_backend'] = args.fallback_backend
    if args.codex_service_tier:
        config['codex_service_tier'] = args.codex_service_tier
    from .llm import ModelError
    from .media import ToolError
    from .pipeline import InputError, Pipeline
    from .transcribe import TranscriptionUnavailable
    try:
        pipeline = Pipeline(video, (cwd / args.output).resolve(), config, srt=args.srt, context=args.context,
                            cwd=cwd, log=log)
        if mode == 'prepare':
            agent = pipeline.agent_prepare()
            print(agent, flush=True)
            log(f'agent briefs ready in {agent}; write each chapter there, then run video-notes assemble')
            return 0
        if mode == 'assemble':
            note, problems = pipeline.agent_assemble()
            if problems:
                for chapter, items in problems.items():
                    log(f'{chapter}: ' + '; '.join(items))
                return _fail(1, f'{len(problems)} chapter(s) did not pass the checks; nothing was assembled')
            print(note, flush=True)
            return 0
        note, failed, report = pipeline.resume_review() if args.resume_review else pipeline.run()
    except KeyboardInterrupt:
        log('interrupted; progress is kept, run the same command again to resume')
        return 130
    except InputError as error:
        return _fail(2, str(error))
    except (ModelError, ToolError, TranscriptionUnavailable, RuntimeError, OSError) as error:
        log('failed; completed stages are cached, rerun to resume')
        return _fail(1, str(error))
    print(note, flush=True)
    if failed:
        log(f"note written, but {len(failed)} chapter(s) did not pass review: {', '.join(failed)}; see {report}")
        return 1
    log(f'done; report: {report}')
    return 0
