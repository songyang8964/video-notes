"""End-to-end pipeline: ingest → subtitle choice/transcription → screen detection → content-aware
chapters → per chapter (prepare → candidates → select [+ requested frames] → write → check,
review, revise) → whole-document review and fixes → assemble → report.

All internal artefacts live in <cwd>/.work/video-notes/<run id>/ and every stage is resumable:
model calls are cached by content, frames by time, detection signals once per video.
"""
import csv
import hashlib
import io
import json
import re
import time
from importlib import resources
from pathlib import Path

import numpy as np

from . import __version__, candidates as cand_mod, render, subtitles
from .config import fill, find_context, load_context, parse_times
from .detect.screens import detect_screens
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

    def warn(self, message):
        self.warnings.append(message)
        self.log('warning: ' + message)

    # ---- ingest -------------------------------------------------------------------
    def ingest(self):
        self.info = probe(self.video)
        self.duration_ms = self.info['duration_ms']
        self.video_hash = sha256(self.video)
        identity = f'{__version__}|{self.video_hash}'
        self.work = self.cwd / '.work' / 'video-notes' / (self.video.stem[:40].strip() + '-' +
                                                         hashlib.sha256(identity.encode()).hexdigest()[:10])
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
        for problem in problems:
            self.warn(f'subtitle: {problem}')
        self.srt_hash = sha256(self.srt)
        backend = self.config['backend']
        self.model = Model(backend, self.work / 'model-calls', self.config.get(f'{backend}_model'),
                           self.config.get(f'{backend}_effort'), log=self.log)
        self.tesseract = tesseract_path(self.config.get('tesseract'))
        if not self.tesseract:
            self.warn('Tesseract not found: OCR text novelty is off (candidate ranking uses visuals only)')
        (self.work / 'source.json').write_text(json.dumps(dict(
            version=__version__, video=str(self.video), video_sha256=self.video_hash, srt=str(self.srt),
            srt_sha256=self.srt_hash, srt_source=source, subtitle_notes=notes, subtitle_health=problems,
            context=str(self.context_path) if self.context_path else None, model=self.model.identity(),
            media=self.info), ensure_ascii=False, indent=2), encoding='utf-8')
        self.log(f'input: {self.video.name}, {stamp(self.duration_ms)}, {len(self.cues)} cues ({source}); '
                 f'model {self.model.identity()}; work {self.work}')

    # ---- helpers --------------------------------------------------------------------
    def rules(self):
        return fill(prompt_text('rules'), language=self.config['output_language'], max_images=self.config['max_images'])

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
                                    min_chars_per_minute=self.config['min_chars_per_minute'])

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
        """Chapter-level key-image choice on reading copies. The model may ask for up to three
        extra moments once; they are extracted and judged too."""
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

    def write(self, chapter, owned, topics, knowledge, selected, notes, directory):
        table = '\n'.join(f"{f['frame_id']} | topic={f['topic_id']} | {f['time']} | 讲解 {f.get('cues', '')} | "
                          f"{f['caption']}" for f in selected) or '（本章无入选图片）'
        values = self.values(chapter, owned,
                             topics=csv_text(topics, ['topic_id', 'first_cue', 'last_cue', 'title', 'kind']),
                             knowledge=csv_text(knowledge, ['knowledge_id', 'first_cue', 'last_cue', 'content',
                                                            'kind', 'importance']),
                             frames=table, rejected_notes='\n'.join(notes) or '（无）')
        images = [f['path'] for f in selected]  # originals: commands and numbers must be legible
        writing = fill(prompt_text('write'), **values)
        draft = self.model.ask(f'{chapter}-write', writing, images)
        review = ''
        for cycle in range(self.config['review_cycles'] + 1):
            problems = self.check(draft, owned, topics, knowledge, selected)
            review = self.model.ask(f'{chapter}-review{cycle}', fill(prompt_text('review'), **values, draft=draft),
                                    images)
            if review.lstrip().upper().startswith('VERDICT: PASS') and not problems:
                break
            if cycle == self.config['review_cycles']:
                break
            draft = self.model.ask(f'{chapter}-revise{cycle}', fill(
                prompt_text('revise'), writing_prompt=writing, draft=draft, review=review,
                mechanical='\n'.join(problems) or '（无）'), images)
        problems = self.check(draft, owned, topics, knowledge, selected)
        passed = review.lstrip().upper().startswith('VERDICT: PASS')
        (directory / 'chapter.md').write_text(draft, encoding='utf-8')
        (directory / 'review.md').write_text(review, encoding='utf-8')
        return dict(text=draft, frames=selected, problems=problems, review_pass=passed, writing=writing,
                    owned=owned, topics=topics, knowledge=knowledge, images=images)

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
            revised = self.model.ask(f'{chapter}-global-revise', fill(
                prompt_text('revise'), writing_prompt=r['writing'], draft=r['text'],
                review='全文审查发现的问题：\n' + '\n'.join(f'- {i}' for i in items),
                mechanical='（无）'), r['images'])
            problems = self.check(revised, r['owned'], r['topics'], r['knowledge'], r['frames'])
            if problems:
                self.warn(f'{chapter}: whole-document revision rejected by checks ({"; ".join(problems)}); kept previous')
            else:
                r['text'] = revised
                (self.work / chapter / 'chapter.md').write_text(revised, encoding='utf-8')
        return rows, duplicates

    def write_output(self, title, results):
        """Never overwrite a note the user edited: compare with the fingerprint of our last write."""
        out_dir = self.output_root / self.video.stem
        target = out_dir / '培训笔记.md'
        fingerprint = self.work / 'output.sha256'
        filename = '培训笔记.md'
        if target.is_file() and (not fingerprint.is_file() or fingerprint.read_text().strip() != sha256(target)):
            filename = f'培训笔记.{time.strftime("%Y%m%d-%H%M%S")}.md'
            self.warn(f'{target} was edited after the last run; the new note is written as {filename}')
        note, inserted = render.assemble(title, [dict(text=r['text'], frames=r['frames']) for r in results.values()],
                                         out_dir, filename)
        if filename == '培训笔记.md':
            fingerprint.write_text(sha256(note), encoding='utf-8')
        return note, inserted

    # ---- run ----------------------------------------------------------------------------
    def run(self):
        started = time.time()
        self.ingest()
        self.log('detecting screen changes (first run decodes the whole video; cached afterwards)')
        screens = detect_screens(self.video, self.work / 'detect', self.duration_ms / 1000,
                                 use_adaptive=self.config['use_adaptive'], log=self.log)
        changes = [round(s['detected_at'] * 1000) for s in screens if s['source'] == 'screen-start']
        chapters = make_chapters(self.cues, self.config['chapter_minutes'] * 60000, changes)
        self.log(f'{len(chapters)} chapters')
        requested = parse_times(self.settings.get('include_times', ''))
        results = {}
        for n, owned in enumerate(chapters, 1):
            chapter = f'C{n:02}'
            directory = self.work / chapter
            directory.mkdir(exist_ok=True)
            start = 0 if n == 1 else owned[0]['start']
            end = chapters[n][0]['start'] if n < len(chapters) else self.duration_ms
            self.log(f'{chapter} {stamp(start)}–{stamp(end)}: prepare')
            topics, knowledge = self.prepare(chapter, owned, directory)

            def pool(extra=()):
                return cand_mod.build(chapter, start, end, screens, self.video, self.duration_ms,
                                      directory / 'candidates', cues=owned, shortlist=self.config['shortlist'],
                                      tesseract=self.tesseract, ocr_langs=self.config['ocr_langs'],
                                      requested_ms=list(requested) + list(extra), log=self.log)[0]
            shortlist = pool()
            selected, notes, asks = self.select(chapter, owned, topics, shortlist, start, end, directory)
            if asks:
                self.log(f'{chapter}: model asked for {len(asks)} more moment(s); re-judging')
                shortlist = pool(asks)
                selected, notes, _ = self.select(chapter, owned, topics, shortlist, start, end, directory, 1)
            self.log(f'{chapter}: {len(selected)} key images; writing')
            results[chapter] = self.write(chapter, owned, topics, knowledge, selected, notes, directory)
            r = results[chapter]
            self.log(f"{chapter}: {'ok' if r['review_pass'] and not r['problems'] else 'needs attention'}")
        global_rows, duplicates = self.global_review(results) if len(results) > 1 else ([], [])
        title = self.settings.get('title') or self.video.stem
        note, inserted = self.write_output(title, results)
        if sha256(self.video) != self.video_hash or sha256(self.srt) != self.srt_hash:
            raise RuntimeError('source files changed during processing')
        failed = [c for c, r in results.items() if r['problems'] or not r['review_pass']]
        lines = ['# video-notes report', '', f'note: {note}', f'images: {len(inserted)}',
                 f'subtitles: {self.srt_source} ({self.srt.name})', f'model: {self.model.identity()}, '
                 f'calls this run: {self.model.calls}', f'elapsed: {round(time.time() - started)} s', '',
                 '## warnings (degraded or notable)'] + [f'- {w}' for w in self.warnings or ['none']]
        lines += ['', '## chapters'] + [
            f"- {c}: {stamp(r['owned'][0]['start'])}, images {len(r['frames'])}, review "
            f"{'PASS' if r['review_pass'] else 'REVISE (unresolved, see ' + c + '/review.md)'}"
            + (f"; problems: {'; '.join(r['problems'])}" if r['problems'] else '') for c, r in results.items()]
        lines += ['', f'## whole-document review: {len(global_rows)} fix(es) applied']
        lines += [f'- cross-chapter similar images: {d}' for d in duplicates]
        (self.work / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return note, failed, self.work / 'report.md'
