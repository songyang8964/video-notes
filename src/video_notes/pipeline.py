"""Agent mode: the tool does every step that needs no model, an assistant in a chat writes.

    prepare:  ingest → subtitle choice/transcription → picture end → screen detection → chapters →
              per chapter candidate frames → agent/Cnn/brief.md (rules, transcript, candidate table)
    (agent):  per chapter topics.csv, knowledge.csv, chapter.md, review.md — written in the conversation,
              transcript read once and each image looked at once
    assemble: the same mechanical checks for every chapter (+ the self-review record) → <video>.md,
              <video>.docx and <video>_assets/ beside the video

No model CLI is started. Internal artefacts live in <cwd>/.work/video-notes/<run id>/ (or
~/.video-notes/work/<run id> when that path would be too deep); detection is cached once per video.
One run at a time per work folder.
"""
import csv
import hashlib
import io
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from importlib import resources
from pathlib import Path

import numpy as np

from . import __version__, candidates as cand_mod, render, subtitles
from .config import fill, find_context, home_dir, load_context, parse_times
from .detect.screens import detect_screens, picture_end
from .media import probe, sha256
from .srt import health, read_srt, stamp
from .transcribe import transcribe
from .vision.ocr import tesseract_path


class InputError(ValueError):
    """Ambiguous or invalid user input (exit code 2)."""


def prompt_text(name):
    return resources.files('video_notes.prompts').joinpath(name + '.md').read_text(encoding='utf-8')


def cue_text(cues):
    return '\n'.join(f"C{c['id']} {stamp(c['start'])}–{stamp(c['end'])} {c['text']}" for c in cues)


WORK_PATH_BUDGET = 170  # deepest internal file adds ~80 chars; Windows MAX_PATH is 260 without long paths


def work_dir(cwd: Path, run_id: str):
    """<cwd>/.work/video-notes/<run id>. Deep folders on Windows (e.g. meeting-recorder folders) would
    push internal files past MAX_PATH, so those runs keep their work in ~/.video-notes/work instead —
    not under AppData, which MSIX-packaged apps (Claude, Codex desktop) each redirect privately."""
    work = Path(cwd) / '.work' / 'video-notes' / run_id
    if os.name == 'nt' and len(str(work)) > WORK_PATH_BUDGET and not _long_paths_enabled():
        work = home_dir() / 'work' / run_id
    return work


class RunLock:
    """One run per work folder: two processes writing the same chapters overwrite each other."""

    def __init__(self, work: Path):
        self.path = work / 'run.lock'

    def __enter__(self):
        try:
            handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            other = self.path.read_text(encoding='utf-8').strip()
            if other.isdigit() and _alive(int(other)):
                raise InputError(f'another video-notes run (pid {other}) is using {self.path.parent}; '
                                 'wait for it or stop it first') from None
            self.path.unlink()  # stale lock of a run that was killed
            handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(handle, str(os.getpid()).encode())
        os.close(handle)
        return self

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


def _alive(pid):
    if os.name == 'nt':
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def keep_awake(on):
    """Stop idle sleep during long detection and extraction. Closing the lid still follows the Windows
    power setting."""
    if os.name == 'nt':
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | (0x00000001 if on else 0))


def _long_paths_enabled():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\FileSystem') as key:
            return bool(winreg.QueryValueEx(key, 'LongPathsEnabled')[0])
    except OSError:
        return False


def review_record_problems(record, frames, knowledge):
    """Agent mode has no independent reviewer, so the writer's self-review must be auditable: review.md
    names every inserted image with what was checked against the original, and every important item."""
    problems = []
    lines = {line.split('：')[0].split(':')[0].strip(' -*`'): line for line in record.splitlines() if line.strip()}
    for f in frames:
        line = lines.get(f['frame_id'], '')
        if len(re.sub(r'^\W*' + re.escape(f['frame_id']) + r'\W*', '', line).strip()) < 8:
            problems.append(f"review.md: no verification note for image {f['frame_id']}")
    for k in knowledge:
        if k.get('importance', '').strip() == 'important' and k['knowledge_id'] not in lines:
            problems.append(f"review.md: important item {k['knowledge_id']} not checked")
    return problems


def output_base(out_dir: Path, stem: str):
    """The video's name for <name>.md/.docx and <name>_assets/C01F00000000.jpg, shortened only when the
    longest of those paths would exceed MAX_PATH on Windows without long-path support."""
    longest = lambda base: max(len(str(out_dir / f'{base}_assets' / 'C01F00000000.jpg')),
                               len(str(out_dir / f'{base}.20260101-000000.docx')))
    base = stem
    if os.name == 'nt' and not _long_paths_enabled():
        while len(base) > 12 and longest(base) > 259:
            base = base[:-1].rstrip(' ._-')
    return base


def make_chapters(cues, window_ms, change_ms=()):
    """Chapters of about window_ms, cut in the last quarter of each window at the cue boundary
    with the longest pause, preferring a boundary that coincides with a screen change."""
    changes = sorted(change_ms)
    chapters, i = [], 0
    while i < len(cues):
        start, j = cues[i]['start'], i
        while j + 1 < len(cues) and cues[j + 1]['end'] - start <= window_ms:
            j += 1
        if j + 1 >= len(cues):
            chapters.append(cues[i:])
            break

        def score(k):
            gap = cues[k + 1]['start'] - cues[k]['end']
            lo, hi = cues[k]['end'] - 2000, cues[k + 1]['start'] + 2000
            n = int(np.searchsorted(changes, lo))
            return gap + (5000 if n < len(changes) and changes[n] <= hi else 0)
        lo = i + max(0, (j - i) * 3 // 4)
        cut = max(range(lo, j + 1), key=score)
        chapters.append(cues[i:cut + 1])
        i = cut + 1
    return chapters


class Pipeline:
    def __init__(self, video: Path, output_root: Path, config, *, srt=None, context=None, cwd=None, log=print):
        self.video, self.config, self.log = video.resolve(), config, log
        self.cwd = Path(cwd or Path.cwd())
        self.output_root = output_root
        self.context_path = find_context(self.video, context, self.cwd)
        self.settings, self.context = load_context(self.context_path)
        self.srt_arg = Path(srt).resolve() if srt else None
        self.warnings = []
        # Per-video note language (context front matter `note_language`) overrides the configured one.
        self.language = self.settings.get('note_language') or config['output_language']
        self.cjk = render.is_cjk(self.language)

    def warn(self, message):
        self.warnings.append(message)
        self.log('warning: ' + message)

    def prepare_work(self):
        self.video_hash = sha256(self.video)
        identity = f'{__version__}|{self.video_hash}'
        self.work = work_dir(self.cwd, hashlib.sha256(identity.encode()).hexdigest()[:10])
        self.work.mkdir(parents=True, exist_ok=True)

    def ingest(self):
        """Video, subtitles (or local speech recognition), health checks; no model is called."""
        self.info = probe(self.video)
        self.duration_ms = self.info['duration_ms']
        self.video_hash = sha256(self.video)
        identity = f'{__version__}|{self.video_hash}'
        self.work = work_dir(self.cwd, hashlib.sha256(identity.encode()).hexdigest()[:10])
        self.work.mkdir(parents=True, exist_ok=True)
        try:
            srt, cues, source, notes = subtitles.choose(self.video, self.duration_ms, self.work / 'subtitles',
                                                        self.srt_arg, self.settings.get('language', ''))
        except ValueError as error:
            raise InputError(str(error)) from error
        for note in notes:
            self.log('subtitles: ' + note)
        if srt is None:
            existing = self.work / 'transcript' / (self.video.stem + '.srt')
            srt = existing if existing.is_file() else transcribe(self.video, self.work / 'transcript',
                                                                 self.config, self.settings, self.log)
            cues, source = read_srt(srt), 'transcribed'
            self.warn('no usable subtitle; the transcript comes from speech recognition (check names and terms)')
        self.srt, self.cues, self.srt_source = Path(srt), cues, source
        problems = health(self.cues, self.duration_ms)
        if not self.cues:
            raise InputError(f'subtitle {self.srt.name} has no cues')
        late = [p for p in problems if 'ends after the video' in p]
        if late:  # one line, not one per cue: an audio track longer than the picture is a single fact
            problems = [p for p in problems if p not in late] + [
                f'{len(late)} cues end after the video container ({late[0]} … {late[-1]}); they are written as '
                'text without images']
        for problem in problems:
            self.warn(f'subtitle: {problem}')
        self.srt_hash = sha256(self.srt)
        writer = 'agent (in-conversation)'
        self.tesseract = tesseract_path(self.config.get('tesseract'))
        if not self.tesseract:
            self.warn('Tesseract not found: OCR text novelty is off (candidate ranking uses visuals only)')
        (self.work / 'source.json').write_text(json.dumps(dict(
            version=__version__, video=str(self.video), video_sha256=self.video_hash, srt=str(self.srt),
            srt_sha256=self.srt_hash, srt_source=source, subtitle_notes=notes, subtitle_health=problems,
            context=str(self.context_path) if self.context_path else None, model=writer,
            media=self.info), ensure_ascii=False, indent=2), encoding='utf-8')
        self.log(f'input: {self.video.name}, {stamp(self.duration_ms)}, {len(self.cues)} cues ({source}); '
                 f'writer {writer}; work {self.work}')

    def rules(self):
        return fill(prompt_text('rules'), language=self.language, max_images=self.config['max_images'])

    def values(self, chapter, owned, **extra):
        return dict(context=self.context, rules=self.rules(), chapter_id=chapter, first_cue=owned[0]['id'],
                    last_cue=owned[-1]['id'], start_time=stamp(owned[0]['start']), end_time=stamp(owned[-1]['end']),
                    max_images=self.config['max_images'], cues=cue_text(owned)) | extra

    def check(self, draft, owned, topics, knowledge, frames):
        return render.check_chapter(draft, [c['id'] for c in owned], topics, frames, self.config['max_images'],
                                    self.forbidden(), knowledge=knowledge,
                                    cue_times={c['id']: (c['start'], c['end']) for c in owned},
                                    min_chars_per_minute=self.config['min_chars_per_minute'], cjk=self.cjk)

    def forbidden(self):
        return [w.strip() for w in self.settings.get('forbid', '').split(',') if w.strip()]

    def scan(self):
        """Picture end, screen detection and chapters (no model calls). → (screens, chapters)"""
        self.log('detecting screen changes (first run decodes the whole video; cached afterwards)')
        # Picture end: a damaged recording can decode only part of its container duration.
        self.picture_ms = min(self.duration_ms, round(picture_end(self.video, self.work / 'detect', self.log) * 1000))
        if self.picture_ms < self.duration_ms - 2000:
            self.warn(f'video decodes only until {stamp(self.picture_ms)} of {stamp(self.duration_ms)} (damaged tail); '
                      'no frames are taken after it')
        if self.cues[-1]['end'] > self.picture_ms + 2000:
            self.warn(f'speech continues until {stamp(self.cues[-1]["end"])} after the picture ends at '
                      f'{stamp(self.picture_ms)}; that part is written as text without images')
        screens = detect_screens(self.video, self.work / 'detect', self.picture_ms / 1000,
                                 use_adaptive=self.config['use_adaptive'], log=self.log)
        changes = [round(s['detected_at'] * 1000) for s in screens if s['source'] == 'screen-start']
        chapters = make_chapters(self.cues, self.config['chapter_minutes'] * 60000, changes)
        self.log(f'{len(chapters)} chapters')
        return screens, chapters

    def chapter_window(self, n, chapters):
        """[start, end) where this chapter's frames may come from (clamped to the decodable picture)."""
        start = 0 if n == 1 else chapters[n - 1][0]['start']
        end = min(chapters[n][0]['start'] if n < len(chapters) else self.duration_ms, self.picture_ms)
        return start, end

    def write_output(self, title, results):
        """Never overwrite a note the user edited: compare with the fingerprint of our last write."""
        out_dir = self.output_root  # beside the video by default: <video>.md, <video>.docx, <video>_assets/
        base = output_base(out_dir, self.video.stem)  # named after the video, shortened only to fit MAX_PATH
        if base != self.video.stem:
            self.warn(f'note name shortened to "{base}" to stay within the Windows path length limit')
        target = out_dir / f'{base}.md'
        fingerprint = self.work / 'output.sha256'
        filename = target.name
        if target.is_file() and (not fingerprint.is_file() or fingerprint.read_text().strip() != sha256(target)):
            filename = f'{base}.{time.strftime("%Y%m%d-%H%M%S")}.md'
            self.warn(f'{target} was edited after the last run; the new note is written as {filename}')
        note, inserted = render.assemble(title, [dict(text=r['text'], frames=r['frames']) for r in results.values()],
                                         out_dir, filename, cjk=self.cjk, assets_name=f'{base}_assets')
        if filename == target.name:
            fingerprint.write_text(sha256(note), encoding='utf-8')
        try:
            self.log(f'Word version: {render.to_docx(note)}')
        except render.ConversionUnavailable as error:
            self.warn(f'Word version not written: {error}')
        return note, inserted

    def agent_prepare(self):
        """Media/subtitle checks, detection, chapters and candidate frames; one brief per chapter in
        <work>/agent/. No model calls. → agent folder"""
        self.prepare_work()
        with RunLock(self.work):
            self.ingest()
            screens, chapters = self.scan()
            requested = parse_times(self.settings.get('include_times', ''))
            agent = self.work / 'agent'
            agent.mkdir(exist_ok=True)

            def pack(item):
                n, owned = item
                chapter = f'C{n:02}'
                start, end = self.chapter_window(n, chapters)
                directory = self.work / chapter
                directory.mkdir(exist_ok=True)
                shortlist = [] if end - start < 1000 else cand_mod.build(
                    chapter, start, end, screens, self.video, self.picture_ms, directory / 'candidates', cues=owned,
                    shortlist=self.config['shortlist'], tesseract=self.tesseract,
                    ocr_langs=self.config['ocr_langs'], requested_ms=requested, log=self.log)[0]
                folder = agent / chapter
                folder.mkdir(exist_ok=True)
                first = owned[0]['id'] - 1
                frames = '\n'.join(
                    f"{f['frame_id']} | {f['time']} | 画面显示 {f.get('screen', '')} | 讲解 {f.get('cues', '')} | "
                    f"OCR: {f.get('ocr', '')[:160]} | 原图 {f['path']} | 阅读副本 {f.get('read_path', '')}"
                    for f in shortlist) or '（本章没有可解码的画面：只写文字，不插图）'
                sheets = '\n'.join(str(p) for p in sorted((directory / 'candidates').glob('sheet-*.jpg'))) or '（无）'
                brief = fill(prompt_text('agent'), **self.values(
                    chapter, owned, folder=str(folder), frames=frames, sheets=sheets,
                    surrounding=cue_text(self.cues[max(0, first - 6):first] + self.cues[owned[-1]['id']:owned[-1]['id'] + 5])))
                (folder / 'brief.md').write_text(brief, encoding='utf-8')
                self.log(f'{chapter}: {len(shortlist)} candidate frames; brief {folder / "brief.md"}')
                return dict(chapter=chapter, first_cue=owned[0]['id'], last_cue=owned[-1]['id'],
                            start=stamp(owned[0]['start']), end=stamp(owned[-1]['end']), candidates=len(shortlist))
            with ThreadPoolExecutor(max(1, int(self.config.get('parallel_chapters') or 1))) as pool:
                index = list(pool.map(pack, enumerate(chapters, 1)))
            (agent / 'chapters.json').write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding='utf-8')
            lines = ['# Agent briefs', '', f'video: {self.video}', f'work: {self.work}',
                     f'note language: {self.language}', '',
                     'For each chapter folder below: read brief.md, then write topics.csv, knowledge.csv, chapter.md '
                     'and review.md into the same folder. When every chapter is written, run `video-notes assemble` '
                     'with the same arguments as prepare; failing chapters and the reasons are listed in check.md.',
                     '', 'Warnings:']
            lines += [f'- {w}' for w in self.warnings or ['none']] + ['', '| chapter | time | cues | candidates |',
                                                                      '|---|---|---|---|']
            lines += [f"| {c['chapter']} | {c['start']}–{c['end']} | C{c['first_cue']}–C{c['last_cue']} | "
                      f"{c['candidates']} |" for c in index]
            (agent / 'README.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
            return agent

    def agent_assemble(self):
        """Check the agent-written chapters with the automatic run's checks and assemble the note.
        No model calls. → (note path or None, {chapter: problems})"""
        self.prepare_work()
        with RunLock(self.work):
            self.ingest()
            agent = self.work / 'agent'
            index_path = agent / 'chapters.json'
            if not index_path.is_file():
                raise InputError('no agent briefs here; run `video-notes prepare` first')
            index = json.loads(index_path.read_text(encoding='utf-8'))
            cues = {c['id']: c for c in self.cues}
            results, problems = {}, {}
            for item in index:
                chapter, folder = item['chapter'], agent / item['chapter']
                missing = [n for n in ('topics.csv', 'knowledge.csv', 'chapter.md', 'review.md')
                           if not (folder / n).is_file()]
                if missing:
                    problems[chapter] = ['missing ' + ', '.join(missing)]
                    continue
                read = lambda name: list(csv.DictReader(io.StringIO((folder / name).read_text(encoding='utf-8-sig'))))
                topics, knowledge = read('topics.csv'), read('knowledge.csv')
                text = (folder / 'chapter.md').read_text(encoding='utf-8')
                owned = [cues[i] for i in range(item['first_cue'], item['last_cue'] + 1)]
                frames = self.agent_frames(chapter, text, topics)
                found = self.check(text, owned, topics, knowledge, frames)
                covered = [i for t in topics for i in range(int(t['first_cue']), int(t['last_cue']) + 1)]
                if covered != [c['id'] for c in owned]:
                    found.append(f"topics.csv must cover C{item['first_cue']}–C{item['last_cue']} exactly once in order")
                found += review_record_problems((folder / 'review.md').read_text(encoding='utf-8'), frames, knowledge)
                if found:
                    problems[chapter] = found
                results[chapter] = dict(text=text, frames=frames)
            report = agent / 'check.md'
            report.write_text('# Agent chapter checks\n\n' + ('\n'.join(
                f'- {c}: ' + '; '.join(p) for c, p in problems.items()) or 'all chapters pass') + '\n', encoding='utf-8')
            if problems:
                return None, problems
            note, inserted = self.write_output(self.settings.get('title') or self.video.stem, results)
            if sha256(self.video) != self.video_hash or sha256(self.srt) != self.srt_hash:
                raise RuntimeError('source files changed during assembly')
            self.log(f'assembled {len(results)} chapters, {len(inserted)} images; checks {report}')
            return note, {}

    def agent_frames(self, chapter, text, topics):
        """Frames the agent inserted, with the topic of the section that holds each placeholder."""
        table = self.work / chapter / 'candidates' / 'candidates.csv'
        known = {r['frame_id']: r for r in csv.DictReader(table.open(encoding='utf-8'))} if table.is_file() else {}
        frames = []
        for section in re.split(r'(?=^## )', text, flags=re.M):
            m = re.search(r'<!--\s*cues:\s*(\d+)\s*-\s*(\d+)', section)
            topic = next((t['topic_id'] for t in topics if m and int(t['first_cue']) == int(m[1])
                          and int(t['last_cue']) == int(m[2])), None)
            for frame_id, _ in render.PLACEHOLDER.findall(render.prose(section)):
                if frame_id in known and topic:
                    row = known[frame_id]
                    frames.append(dict(row, topic_id=topic, actual_ms=int(row['actual_ms']),
                                       path=str(self.work / chapter / 'candidates' / 'frames' / f'{frame_id}.jpg')))
        return frames
