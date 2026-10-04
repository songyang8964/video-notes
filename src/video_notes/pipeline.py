"""End-to-end pipeline: ingest → subtitle choice/transcription → screen detection → content-aware
chapters → [parallel] per chapter prepare → candidates → select [+ requested frames] → [parallel] per
chapter write → check + review → revise ↔ re-check of the blocking issues → whole-document review and
fixes → assemble → report.

All internal artefacts live in <cwd>/.work/video-notes/<run id>/ (or ~/.video-notes/work/<run id> when
that path would be too deep) and every stage is resumable: model calls are cached by content (reused
across backends), frames by time, detection signals once per video. One run at a time per work folder.
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

from . import __version__, candidates as cand_mod, render, subtitles, checkpoint
from .config import fill, find_context, home_dir, load_context, parse_times
from .detect.screens import detect_screens, picture_end
from .llm import Model
from .media import probe, sha256
from .srt import health, read_srt, stamp
from .transcribe import transcribe
from .vision.ocr import tesseract_path
from .vision.select import thumbnail_vector


class InputError(ValueError):
    """Ambiguous or invalid user input (exit code 2)."""


def prompt_text(name):
    return resources.files('video_notes.prompts').joinpath(name + '.md').read_text(encoding='utf-8')


def csv_blocks(text):
    blocks = re.findall(r'```(?:csv)?\s*\n(.*?)\n```', text, re.S)
    return [list(csv.DictReader(io.StringIO(b))) for b in blocks]


def csv_text(rows, fields):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


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
    """Stop idle sleep during a run (a sleeping laptop drops model connections). Closing the lid still
    follows the Windows power setting."""
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


def preflight(chapters, picture_ms, config):
    """Expected model calls before any is made (cached calls cost nothing). Per chapter: prepare, select,
    write, review, plus revise + re-check when the review finds blocking issues; then the whole-document
    review and revisions of the chapters it flags. Real token use is in the report afterwards."""
    n = len(chapters)
    with_picture = sum(1 for owned in chapters if owned[0]['start'] < picture_ms)
    fewest = 3 * n + with_picture + 1                          # nothing needs revising
    most = fewest + 2 * config['review_cycles'] * n + n        # every round revises, every chapter flagged globally
    return (f'preflight: {n} chapters ({n - with_picture} without picture); model calls between {fewest} and '
            f'{most} (≤{config["shortlist"]} candidate images per select; originals for writing and review)')


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


def usage_text(usage):
    if not usage:
        return 'not reported (cached responses or a backend without usage data)'
    fresh = usage.get('input_tokens', 0) + usage.get('cache_creation_input_tokens', 0)
    return (f"input {fresh:,} + cache-read {usage.get('cache_read_input_tokens', 0):,}, "
            f"output {usage.get('output_tokens', 0):,}, list price ${usage.get('cost_usd', 0):.2f}")


def verdict_pass(review):
    return review.lstrip().upper().startswith('VERDICT: PASS')


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

    # ---- ingest -------------------------------------------------------------------
    def prepare_work(self):
        self.video_hash = sha256(self.video)
        identity = f'{__version__}|{self.video_hash}'
        self.work = work_dir(self.cwd, hashlib.sha256(identity.encode()).hexdigest()[:10])
        self.work.mkdir(parents=True, exist_ok=True)

    def ingest(self, models=True):
        """models=False (agent mode): no model CLI is resolved, probed or called."""
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
        if models:
            backend, other = self.config['backend'], self.config.get('fallback_backend')
            fallback = ((other, self.config.get(f'{other}_model'), self.config.get(f'{other}_effort'))
                        if other and other != backend else None)
            if fallback:  # checked now (one tiny call), not discovered broken when the main backend runs out
                from .llm import probe as probe_backend  # `probe` is the media probe in this module
                ok, detail = probe_backend(*fallback, service_tier=self.config.get('codex_service_tier'))
                if not ok:
                    self.warn(f'fallback backend {other} is not usable ({detail[-160:]}); a usage limit will stop '
                              'the run instead (progress is kept)')
                    fallback = None
            self.model = Model(backend, self.work / 'model-calls', self.config.get(f'{backend}_model'),
                               self.config.get(f'{backend}_effort'), log=self.log, fallback=fallback,
                               service_tier=self.config.get('codex_service_tier'),
                               reuse_backends=[(b, self.config.get(f'{b}_model'), self.config.get(f'{b}_effort'))
                                               for b in ('claude', 'codex')])
        writer = self.model.identity() if models else 'agent (in-conversation)'
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

    # ---- helpers --------------------------------------------------------------------
    def rules(self):
        return fill(prompt_text('rules'), language=self.language, max_images=self.config['max_images'])

    def values(self, chapter, owned, **extra):
        return dict(context=self.context, rules=self.rules(), chapter_id=chapter, first_cue=owned[0]['id'],
                    last_cue=owned[-1]['id'], start_time=stamp(owned[0]['start']), end_time=stamp(owned[-1]['end']),
                    max_images=self.config['max_images'], cues=cue_text(owned)) | extra

    def ask_csv(self, name, prompt, images, validate, blocks):
        for attempt in range(3):
            text = self.model.ask(name if not attempt else f'{name}-fix{attempt}', prompt, images)
            try:
                parsed = csv_blocks(text)
                if len(parsed) not in blocks:
                    raise ValueError(f'expected {" or ".join(map(str, blocks))} CSV block(s), got {len(parsed)}')
                validate(*parsed)
                return parsed
            except (ValueError, KeyError) as error:
                prompt = prompt + f'\n\n上次输出未通过程序校验：{error}\n请完整重新输出。'
        raise RuntimeError(f'{name}: model output repeatedly invalid')

    def check(self, draft, owned, topics, knowledge, frames):
        return render.check_chapter(draft, [c['id'] for c in owned], topics, frames, self.config['max_images'],
                                    self.forbidden(), knowledge=knowledge,
                                    cue_times={c['id']: (c['start'], c['end']) for c in owned},
                                    min_chars_per_minute=self.config['min_chars_per_minute'], cjk=self.cjk)

    def forbidden(self):
        return [w.strip() for w in self.settings.get('forbid', '').split(',') if w.strip()]

    # ---- chapter stages ---------------------------------------------------------------
    def prepare(self, chapter, owned, directory):
        ids = [c['id'] for c in owned]
        first = ids[0] - 1
        surrounding = cue_text(self.cues[max(0, first - 6):first] + self.cues[ids[-1]:ids[-1] + 5])

        def validate(topics, knowledge):
            covered = [i for t in topics for i in range(int(t['first_cue']), int(t['last_cue']) + 1)]
            if covered != ids:
                raise ValueError('topics must cover every chapter cue exactly once in order')
            if any(t['kind'] not in ('teaching', 'excluded') for t in topics):
                raise ValueError('topic kind must be teaching or excluded')
            for k in knowledge:
                if not (ids[0] <= int(k['first_cue']) <= int(k['last_cue']) <= ids[-1]):
                    raise ValueError(f"knowledge {k['knowledge_id']} cites cues outside the chapter")
        prompt = fill(prompt_text('prepare'), **self.values(chapter, owned, surrounding=surrounding))
        topics, knowledge = self.ask_csv(f'{chapter}-prepare', prompt, (), validate, (2,))
        (directory / 'topics.csv').write_text(csv_text(topics, list(topics[0])), encoding='utf-8')
        (directory / 'knowledge.csv').write_text(
            csv_text(knowledge, ['knowledge_id', 'first_cue', 'last_cue', 'content', 'kind', 'importance']),
            encoding='utf-8')
        return topics, knowledge

    def select(self, chapter, owned, topics, shortlist, start, end, directory, round_no=0):
        """Chapter-level key-image choice on per-candidate reading copies (1280 px). The model may ask
        for up to three extra moments once; they are extracted and judged too.

        Contact-sheet screening was tried and rejected (2026-10-04): on four real chapters it kept 1 of
        12 key images of the reviewed note and missed every change-plan table in one chapter, because
        table and CLI text is unreadable on thumbnails."""
        if not shortlist or not any(t['kind'] == 'teaching' for t in topics):
            return [], [], []
        teaching = {t['topic_id'] for t in topics if t['kind'] == 'teaching'}
        ids = {f['frame_id'] for f in shortlist}

        def validate(rows, *request):
            if {r['frame_id'] for r in rows} != ids or len(rows) != len(ids):
                raise ValueError('classify every candidate frame exactly once')
            chosen = [r for r in rows if r['decision'] == 'select']
            if len(chosen) > self.config['max_images']:
                raise ValueError(f"at most {self.config['max_images']} images per chapter")
            if any(r['topic_id'] not in teaching for r in chosen):
                raise ValueError('selected frames must belong to a teaching topic')
            for r in (request[0] if request else []):
                parse_times(r['time'])
        table = '\n'.join(f"{f['frame_id']} | {f['time']} | {f['source']} | 画面显示 {f.get('screen', '')} "
                          f"| 讲解 {f.get('cues', '')} | OCR: {f.get('ocr', '')[:160]}" for f in shortlist)
        prompt = fill(prompt_text('select'), **self.values(
            chapter, owned, topics=csv_text(topics, ['topic_id', 'first_cue', 'last_cue', 'title', 'kind']),
            frames=table, allow_request='yes' if round_no == 0 else 'no'))
        parsed = self.ask_csv(f'{chapter}-select{round_no or ""}', prompt,
                              [f.get('read_path') or f['path'] for f in shortlist], validate, (1, 2))
        rows = parsed[0]
        requests = [t for r in (parsed[1] if len(parsed) > 1 else []) for t in parse_times(r['time'])
                    if start <= t < end][:3] if round_no == 0 else []
        by_id = {f['frame_id']: f for f in shortlist}
        selected = [dict(by_id[r['frame_id']], topic_id=r['topic_id'], caption=r['caption'])
                    for r in rows if r['decision'] == 'select']
        notes = [f"{r['frame_id']} {by_id[r['frame_id']]['time']}: {r['reason']}" for r in rows if r['decision'] != 'select']
        (directory / f'selection{round_no or ""}.csv').write_text(
            csv_text(rows, ['frame_id', 'decision', 'topic_id', 'caption', 'reason']), encoding='utf-8')
        return sorted(selected, key=lambda f: f['actual_ms']), notes, requests

    def chapter_values(self, chapter, owned, topics, knowledge, selected, notes=(), earlier=''):
        table = '\n'.join(f"{f['frame_id']} | topic={f['topic_id']} | {f['time']} | 讲解 {f.get('cues', '')} | "
                          f"{f['caption']}" for f in selected) or '（本章无入选图片）'
        return self.values(chapter, owned,
                           topics=csv_text(topics, ['topic_id', 'first_cue', 'last_cue', 'title', 'kind']),
                           knowledge=csv_text(knowledge, ['knowledge_id', 'first_cue', 'last_cue', 'content',
                                                          'kind', 'importance']),
                           frames=table, rejected_notes='\n'.join(notes) or '（无）', earlier_frames=earlier or '（无）')

    def write(self, chapter, owned, topics, knowledge, selected, notes, directory, earlier=''):
        values = self.chapter_values(chapter, owned, topics, knowledge, selected, notes, earlier)
        images = [f['path'] for f in selected]  # originals: commands and numbers must be legible
        writing = fill(prompt_text('write'), **values)
        draft = self.model.ask(f'{chapter}-write', writing, images)
        draft, review, problems, passed = self.review_loop(chapter, draft, writing, values, owned, topics,
                                                           knowledge, selected)
        (directory / 'chapter.md').write_text(draft, encoding='utf-8')
        (directory / 'review.md').write_text(review, encoding='utf-8')
        return dict(text=draft, frames=selected, problems=problems, review_pass=passed, writing=writing,
                    owned=owned, topics=topics, knowledge=knowledge, images=images)

    def review_loop(self, chapter, draft, writing, values, owned, topics, knowledge, selected, tag='', initial_review=None, persist=None):
        """One full review, then at most review_cycles × (revise → re-check). A re-check verifies only the
        blocking issues it was asked to fix and errors the revision introduced: a fresh full review every
        round never converged (each round found new minor points) and used most of a run's model budget.
        → (draft, review, mechanical problems, passed)."""
        images = [f['path'] for f in selected]  # originals also for review: small CLI text and table digits
        problems = self.check(draft, owned, topics, knowledge, selected)
        review = initial_review or self.model.ask(f'{chapter}-{tag}review', fill(prompt_text('review'), **values, draft=draft), images)
        if persist:
            persist(draft, review, 'reviewed')
        for cycle in range(self.config['review_cycles']):
            if verdict_pass(review) and not problems:
                break
            draft = self.model.ask(f'{chapter}-{tag}revise{cycle}', fill(
                prompt_text('revise'), writing_prompt=writing, draft=draft, review=review,
                mechanical='\n'.join(problems) or '（无）'), images)
            problems = self.check(draft, owned, topics, knowledge, selected)
            if persist:
                persist(draft, review, 'needs-recheck')
            review = self.model.ask(f'{chapter}-{tag}recheck{cycle}', fill(
                prompt_text('recheck'), **values, draft=draft, previous=review,
                mechanical='\n'.join(problems) or '（无）'), images)
            if persist:
                persist(draft, review, 'reviewed')
        return draft, review, problems, verdict_pass(review) and not problems

    def checkpoint_source(self):
        """What an accepted chapter depends on besides its own files (checked by checkpoint.load)."""
        return dict(video=self.video_hash, srt=self.srt_hash, context=hashlib.sha256(self.context.encode()).hexdigest())

    def kept_chapter(self, plan, source):
        """An accepted chapter whose checkpoint still matches its files, prompts and sources (else None)."""
        directory = plan['directory']
        record = checkpoint.load(directory, source)
        if not (record and record['passed'] and (directory / 'chapter.md').is_file()):
            return None
        text = (directory / 'chapter.md').read_text(encoding='utf-8')
        if self.check(text, plan['owned'], plan['topics'], plan['knowledge'], plan['selected']):
            return None
        self.log(f"{plan['chapter']}: keeping verified PASS checkpoint")
        return dict(text=text, frames=plan['selected'], problems=[], review_pass=True, owned=plan['owned'],
                    topics=plan['topics'], knowledge=plan['knowledge'], images=[f['path'] for f in plan['selected']],
                    writing=fill(prompt_text('write'), **self.chapter_values(
                        plan['chapter'], plan['owned'], plan['topics'], plan['knowledge'], plan['selected'],
                        plan['notes'])))

    def resume_review(self):
        """Finish existing drafts without re-entering extraction, selection or initial writing."""
        self.prepare_work()
        results, failed = {}, []
        keep_awake(True)
        try:
            with RunLock(self.work):
                self.ingest()
                source = self.checkpoint_source()
                directories = sorted(self.work.glob('C[0-9][0-9]'))
                if not directories:
                    raise InputError('no persisted chapters to resume; run without --resume-review first')
                for directory in directories:
                    def rows(name):
                        if not (directory / name).is_file() and (name.startswith('candidates/') or name.startswith('selection')):
                            return []  # legitimate text-only chapters have no frame stage
                        with (directory / name).open(encoding='utf-8', newline='') as f:
                            return list(csv.DictReader(f))
                    topics, knowledge = rows('topics.csv'), rows('knowledge.csv')
                    owned = [c for c in self.cues if min(int(t['first_cue']) for t in topics) <= c['id'] <= max(int(t['last_cue']) for t in topics)]
                    candidates = {f['frame_id']: f for f in rows('candidates/candidates.csv')}
                    selections = rows('selection1.csv' if (directory / 'selection1.csv').is_file() else 'selection.csv')
                    selected = [dict(candidates[r['frame_id']], topic_id=r['topic_id'], caption=r['caption']) for r in selections if r['decision'] == 'select']
                    for frame in selected:
                        frame['actual_ms'] = int(frame['actual_ms'])
                        frame['path'] = str(directory / 'candidates' / 'frames' / (frame['frame_id'] + '.jpg'))
                    draft = (directory / 'chapter.md').read_text(encoding='utf-8')
                    record = checkpoint.load(directory, source)
                    problems = self.check(draft, owned, topics, knowledge, selected)
                    if record and record['passed'] and not problems:
                        self.log(f'{directory.name}: keeping verified PASS checkpoint')
                        passed = True
                    else:
                        # Back-references only need the earlier section titles and inserted images; sending the
                        # whole earlier note (~130k characters for a 90-minute video) with every call did not.
                        headings = '\n'.join(h for r in results.values()
                                             for h in re.findall(r'^#{1,3} .+$', r['text'], re.M))
                        shown = '\n'.join(f"{f['time']}：{f['caption']}" for r in results.values()
                                          for f in r['frames'] if f['frame_id'] in
                                          {m[0] for m in render.PLACEHOLDER.findall(render.prose(r['text']))})
                        values = self.chapter_values(directory.name, owned, topics, knowledge, selected, earlier=shown)
                        values['context'] += '\n\n## 前面章节的小节标题（引用前文时使用）\n' + (headings or '（无）')
                        initial = (directory / 'review.md').read_text(encoding='utf-8') if record else None
                        if record and record.get('phase') == 'needs-recheck':
                            initial = self.model.ask(f'{directory.name}-resume-recheck', fill(prompt_text('recheck'), **values, draft=draft, previous=initial, mechanical='\n'.join(problems) or '(None)'), [f['path'] for f in selected])
                        def persist(text, review, phase):
                            (directory / 'chapter.md').write_text(text, encoding='utf-8')
                            (directory / 'review.md').write_text(review, encoding='utf-8')
                            checkpoint.save(directory, source, False, phase=phase, model=self.model.identity())
                        draft, review, problems, passed = self.review_loop(directory.name, draft, fill(prompt_text('write'), **values), values, owned, topics, knowledge, selected, tag='resume-', initial_review=initial, persist=persist)
                        checkpoint.save(directory, source, passed, phase='reviewed', model=self.model.identity())
                    results[directory.name] = dict(text=draft, frames=selected, problems=problems, review_pass=passed)
                    if not passed:
                        failed.append(directory.name)
                if failed:
                    raise RuntimeError('saved unfinished reviews: ' + ', '.join(failed))
                note, inserted = self.write_output(self.settings.get('title') or self.video.stem, results)
                if sha256(self.video) != self.video_hash or sha256(self.srt) != self.srt_hash:
                    raise RuntimeError('source files changed during review')
                report = self.work / 'report.md'
                with report.open('a', encoding='utf-8') as f:
                    f.write('\n## Resumed chapter acceptance\n\n' + '\n'.join(f'- {c}: PASS' for c in results) + f'\n\nNote: {note}\nImages: {len(inserted)}\nWarnings: {self.warnings}\n')
                return note, [], report
        finally:
            keep_awake(False)

    # ---- whole document -------------------------------------------------------------------
    def cross_chapter_duplicates(self, results):
        items = []
        for chapter, r in results.items():
            used = {m[0] for m in render.PLACEHOLDER.findall(render.prose(r['text']))}
            items += [(chapter, f) for f in r['frames'] if f['frame_id'] in used]
        vectors = [thumbnail_vector(Path(f['path'])) for _, f in items]
        pairs = []
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if items[i][0] != items[j][0] and float(np.dot(vectors[i], vectors[j])) > 0.97:
                    pairs.append(f'{items[i][1]["frame_id"]}（{items[i][0]}）≈ {items[j][1]["frame_id"]}（{items[j][0]}）')
        return pairs

    def global_review(self, results):
        duplicates = self.cross_chapter_duplicates(results)
        draft = '\n\n'.join(f'===== {c} =====\n{render.prose(r["text"]).strip()}' for c, r in results.items())
        knowledge = '\n'.join(f"{c} {k['knowledge_id']}: {k['content']}" for c, r in results.items()
                              for k in r['knowledge'] if k.get('importance') == 'important')
        prompt = fill(prompt_text('global'), context=self.context, rules=self.rules(), draft=draft,
                      knowledge=knowledge or '（无）', duplicates='\n'.join(duplicates) or '（无）')

        def validate(rows):
            if any(r['chapter_id'] not in results for r in rows):
                raise ValueError('chapter_id must be one of ' + ', '.join(results))
        (rows,) = self.ask_csv('global-review', prompt, (), validate, (1,))
        issues = {}
        for r in rows:
            issues.setdefault(r['chapter_id'], []).append(r['issue'])
        (self.work / 'global-review.csv').write_text(csv_text(rows, ['chapter_id', 'issue']), encoding='utf-8')
        for chapter, items in issues.items():
            r = results[chapter]
            self.log(f'{chapter}: whole-document fixes ({len(items)})')
            values = self.chapter_values(chapter, r['owned'], r['topics'], r['knowledge'], r['frames'])
            revised, review, problems, passed = self.review_loop(
                chapter, r['text'], r['writing'], values, r['owned'], r['topics'], r['knowledge'], r['frames'],
                tag='global-', initial_review='VERDICT: REVISE\n' + '\n'.join(f'【阻断】{i}' for i in items))
            if not passed:
                self.warn(f'{chapter}: whole-document revision not accepted; kept previous draft')
                r['review_pass'] = False
                (self.work / chapter / 'review.md').write_text(review, encoding='utf-8')
            else:
                r['text'] = revised
                (self.work / chapter / 'chapter.md').write_text(revised, encoding='utf-8')
                (self.work / chapter / 'review.md').write_text(review, encoding='utf-8')
        return rows, duplicates

    def write_output(self, title, results):
        """Never overwrite a note the user edited: compare with the fingerprint of our last write."""
        out_dir = self.output_root / self.video.stem
        base = '培训笔记' if self.cjk else 'Training Notes'
        target = out_dir / f'{base}.md'
        fingerprint = self.work / 'output.sha256'
        filename = target.name
        if target.is_file() and (not fingerprint.is_file() or fingerprint.read_text().strip() != sha256(target)):
            filename = f'{base}.{time.strftime("%Y%m%d-%H%M%S")}.md'
            self.warn(f'{target} was edited after the last run; the new note is written as {filename}')
        note, inserted = render.assemble(title, [dict(text=r['text'], frames=r['frames']) for r in results.values()],
                                         out_dir, filename, cjk=self.cjk)
        if filename == target.name:
            fingerprint.write_text(sha256(note), encoding='utf-8')
        return note, inserted

    # ---- agent mode -----------------------------------------------------------------------
    # An agent in a chat (Claude/Codex app) does the model stages itself — read the transcript once,
    # look at the frames once, write — so no `claude -p`/`codex exec` subprocess re-sends the same context.
    # The tool does everything else with the same code and checks as the automatic run.

    def agent_prepare(self):
        """Media/subtitle checks, detection, chapters and candidate frames; one brief per chapter in
        <work>/agent/. No model calls. → agent folder"""
        self.prepare_work()
        with RunLock(self.work):
            self.ingest(models=False)
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
                     'For each chapter read brief.md, write topics.csv, knowledge.csv and chapter.md into the same '
                     'folder, then run `video-notes assemble` with the same arguments. Warnings:']
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
            self.ingest(models=False)
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

    # ---- run ----------------------------------------------------------------------------
    def run(self):
        started = time.time()
        self.prepare_work()
        keep_awake(True)
        try:
            with RunLock(self.work):
                self.ingest()
                return self._run(started)
        finally:
            keep_awake(False)

    def plan_chapter(self, n, owned, chapters, screens, requested):
        """prepare → candidates → select for one chapter (independent of other chapters)."""
        chapter = f'C{n:02}'
        directory = self.work / chapter
        directory.mkdir(exist_ok=True)
        start, end = self.chapter_window(n, chapters)
        self.log(f'{chapter} {stamp(owned[0]["start"])}–{stamp(owned[-1]["end"])}: prepare')
        topics, knowledge = self.prepare(chapter, owned, directory)

        def pool(extra=(), keep=()):
            if end - start < 1000:  # chapter lies after the decodable picture: text only
                self.log(f'{chapter}: no decodable picture in this range; text only')
                return []
            return cand_mod.build(chapter, start, end, screens, self.video, self.picture_ms,
                                  directory / 'candidates', cues=owned, shortlist=self.config['shortlist'],
                                  tesseract=self.tesseract, ocr_langs=self.config['ocr_langs'],
                                  requested_ms=list(requested) + list(extra), keep_ids=keep, log=self.log)[0]
        shortlist = pool()
        selected, notes, asks = self.select(chapter, owned, topics, shortlist, start, max(start, end), directory)
        if asks:
            self.log(f'{chapter}: model asked for {len(asks)} more moment(s); re-judging')
            shortlist = pool(asks, keep={f['frame_id'] for f in shortlist})
            selected, notes, _ = self.select(chapter, owned, topics, shortlist, start, end, directory, 1)
        self.log(f'{chapter}: {len(selected)} key images')
        return dict(chapter=chapter, owned=owned, topics=topics, knowledge=knowledge, selected=selected,
                    notes=notes, directory=directory)

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

    def _run(self, started):
        screens, chapters = self.scan()
        self.log(preflight(chapters, self.picture_ms, self.config))
        requested = parse_times(self.settings.get('include_times', ''))
        workers = max(1, int(self.config.get('parallel_chapters') or 1))
        # Chapters are independent until the whole-document review, so they run concurrently: first every
        # chapter's plan (prepare/candidates/select), then writing, which needs the earlier chapters' key
        # images to avoid inserting the same slide twice.
        with ThreadPoolExecutor(workers) as pool:
            plans = list(pool.map(lambda item: self.plan_chapter(item[0], item[1], chapters, screens, requested),
                                  enumerate(chapters, 1)))

            def earlier(index):
                return '\n'.join(f"{f['time']}：{f['caption']}" for p in plans[:index] for f in p['selected'])

            source = self.checkpoint_source()

            def write(index):
                p = plans[index]
                kept = self.kept_chapter(p, source)
                if kept:  # same checkpoints as --resume-review: an accepted chapter is never rewritten
                    return kept
                r = self.write(p['chapter'], p['owned'], p['topics'], p['knowledge'], p['selected'], p['notes'],
                               p['directory'], earlier(index))
                checkpoint.save(p['directory'], source, r['review_pass'] and not r['problems'], phase='reviewed',
                                model=self.model.identity())
                self.log(f"{p['chapter']}: {'ok' if r['review_pass'] else 'needs attention'}")
                return r
            results = {p['chapter']: r for p, r in zip(plans, pool.map(write, range(len(plans))))}
        global_rows, duplicates = self.global_review(results) if len(results) > 1 else ([], [])
        title = self.settings.get('title') or self.video.stem
        note, inserted = self.write_output(title, results)
        if sha256(self.video) != self.video_hash or sha256(self.srt) != self.srt_hash:
            raise RuntimeError('source files changed during processing')
        failed = [c for c, r in results.items() if r['problems'] or not r['review_pass']]
        lines = ['# video-notes report', '', f'note: {note}', f'images inserted: {len(inserted)}',
                 f'subtitles: {self.srt_source} ({self.srt.name})', f'model: {self.model.identity()}, '
                 f'calls this run: {self.model.calls}', f'tokens this run: {usage_text(getattr(self.model, "usage", {}))}',
                 f'elapsed: {round(time.time() - started)} s', '',
                 '## warnings (degraded or notable)'] + [f'- {w}' for w in self.warnings or ['none']]
        lines += ['', '## chapters'] + [
            f"- {c}: {stamp(r['owned'][0]['start'])}, images selected {len(r['frames'])}, inserted "
            f"{len({m[0] for m in render.PLACEHOLDER.findall(render.prose(r['text']))})}, review "
            f"{'PASS' if r['review_pass'] else 'REVISE (unresolved, see ' + c + '/review.md)'}"
            + (f"; problems: {'; '.join(r['problems'])}" if r['problems'] else '') for c, r in results.items()]
        lines += ['', f'## whole-document review: {len(global_rows)} issue(s) identified; acceptance is recorded per chapter']
        lines += [f'- cross-chapter similar images: {d}' for d in duplicates]
        (self.work / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return note, failed, self.work / 'report.md'
