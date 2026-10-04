"""User configuration (set once by `video-notes setup`) and per-video context.

Config: ~/.video-notes/config.json (%USERPROFILE%; the old %APPDATA% location is still read). No secrets.
Not under AppData on purpose: desktop apps packaged as MSIX (Claude, Codex) each get a private
redirected AppData, so a tool started from different apps would otherwise see different settings.
Per-video context (optional): `<video stem>.context.md` beside the video, `video-notes.context.md`
in the working folder, or --context. Free Markdown injected into every prompt (subject, scope,
confirmed terminology, what to exclude), with an optional front-matter block for settings:

    ---
    title: My course notes
    asr_preset: networking
    language: auto            # spoken language (subtitle choice and speech recognition)
    note_language: English    # language of the note (default: the configured output_language)
    include_times: 00:02:07.797, 01:10:00
    ---
"""
import json
import os
import re
from pathlib import Path

DEFAULTS = dict(
    backend='claude',          # claude | codex
    claude_model='claude-opus-5-5',  # model passed to `claude --model`
    claude_effort='medium',          # `claude --effort`: low | medium | high | xhigh | max
    codex_model='gpt-6.1-sol',       # model passed to `codex exec -m`
    codex_effort='medium',           # codex model_reasoning_effort: minimal | low | medium | high
    codex_service_tier=None,          # optional process override; never edits ~/.codex/config.toml
    claude_path=None,          # explicit claude executable (default: PATH, then the desktop app bundle)
    codex_path=None,           # explicit codex executable (default: PATH, then the desktop app bundle)
    output_language='中文',
    max_images=8,
    shortlist=32,              # max distinct candidate screens shown to the vision model per chapter
    chapter_minutes=10,
    use_adaptive=True,         # PySceneDetect auxiliary detector (slower, better recall)
    review_cycles=2,           # revise → re-check rounds after the first review (blocking issues only)
    parallel_chapters=3,       # chapters processed concurrently (model calls and frame extraction)
    fallback_backend=None,     # claude | codex: taken over automatically when the backend hits a usage limit
    min_chars_per_minute=100,  # detail floor (prose chars per teaching minute); see render.check_density
    tesseract=None,
    ocr_langs='eng',
    asr_backend=None,          # whisperx | faster-whisper | openai-whisper | None=auto
    asr_model='large-v3',
    whisperx=None,
)


def home_dir():
    """Per-user tool folder shared by every app that starts the tool (see the module note)."""
    return Path.home() / '.video-notes'


def config_path():
    return home_dir() / 'config.json'


def _legacy_config_path():
    base = os.environ.get('APPDATA') or str(Path.home() / '.config')
    return Path(base) / 'video-notes' / 'config.json'


def load_config():
    path = config_path() if config_path().is_file() else _legacy_config_path()
    data = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    return DEFAULTS | {k: v for k, v in data.items() if k in DEFAULTS}


def save_config(values):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    current = load_config() | values
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def find_context(video: Path, explicit=None, cwd=None):
    if explicit:
        return Path(explicit)
    for candidate in (video.with_suffix('.context.md'), Path(cwd or Path.cwd()) / 'video-notes.context.md'):
        if candidate.is_file():
            return candidate
    return None


def load_context(path):
    """(settings dict, markdown text). Missing file → ({}, default text)."""
    if not path:
        return {}, '（未提供视频上下文。请根据字幕自行判断主题、范围与术语。）'
    text = Path(path).read_text(encoding='utf-8-sig')
    settings = {}
    match = re.match(r'^---\s*\n(.*?)\n---\s*\n', text, re.S)
    if match:
        for line in match[1].splitlines():
            if ':' in line:
                key, value = line.split(':', 1)
                settings[key.strip()] = value.strip()
        text = text[match.end():]
    return settings, text.strip() or '（视频上下文为空。）'


def parse_times(value):
    """'00:02:07.797, 3600.5' → [127797, 3600500]."""
    out = []
    for part in filter(None, (p.strip() for p in str(value or '').split(','))):
        if ':' in part:
            h, m, s = (part.split(':') + ['0', '0'])[:3] if part.count(':') == 2 else ['0'] + part.split(':')
            out.append(round(((int(h) * 60 + int(m)) * 60 + float(s)) * 1000))
        else:
            out.append(round(float(part) * 1000))
    return out


def fill(template, **values):
    """Single-pass replacement of {known_key}; braces in values are never expanded."""
    return re.sub(r'\{(\w+)\}', lambda m: str(values[m[1]]) if m[1] in values else m[0], template)
