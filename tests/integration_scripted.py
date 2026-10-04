"""Plumbing check on a real clip with a scripted model (no CLI login, no cost).

    python tests/integration_scripted.py <folder with one video + srt>

Verifies detection → chapters → candidates → select (+ requested frame) → write → review →
whole-document review → output wiring. It proves mechanics only, not note quality.
"""
import re
import sys
import time
from pathlib import Path

from video_notes import pipeline as pl
from video_notes.config import load_config


class ScriptedModel:
    calls = 0

    def identity(self):
        return 'scripted:test'

    def ask(self, name, prompt, images=()):
        self.calls += 1
        for image in images:
            assert Path(image).is_file(), image
        own = prompt.split('## 本章字幕')[-1]
        cues = [int(x) for x in re.findall(r'^C(\d+) ', own, re.M)]
        found = re.search(r'C\d\d', name)
        chapter = found[0] if found else ''
        if 'prepare' in name:
            first, last = min(cues), max(cues)
            mid = (first + last) // 2
            return ('```csv\ntopic_id,first_cue,last_cue,title,expected_visual,kind\n'
                    f'{chapter}T01,{first},{mid},A,x,teaching\n{chapter}T02,{mid + 1},{last},B,x,teaching\n```\n'
                    '```csv\nknowledge_id,first_cue,last_cue,content,kind,importance\n'
                    f'{chapter}K001,{first},{first},fact,definition,important\n```')
        if '-select' in name:
            frames = re.findall(r'^(C\d\dF\d+) \| ', prompt, re.M)
            topic = re.findall(r'^(C\d\dT\d\d),', prompt, re.M)[0]
            rows = ''.join(f'{f},{"select" if i < 2 else "reject"},{topic if i < 2 else ""},cap {i},r\n'
                           for i, f in enumerate(frames))
            extra = ('\n```csv\ntime,reason\n00:00:30.000,need it\n```' if 'allow_request' not in prompt
                     and '是否允许补截画面：yes' in prompt else '')
            return '```csv\nframe_id,decision,topic_id,caption,reason\n' + rows + '```' + extra
        if name.endswith('write') or 'revise' in name:
            # First draft is deliberately thin and unmapped; the checks must send it back.
            thin = name.endswith('write')
            topics = re.findall(r'^(C\d\dT\d\d),(\d+),(\d+),', prompt, re.M)
            frames = re.findall(r'^(C\d\dF\d+) \| topic=(C\d\dT\d\d)', prompt, re.M)
            knowledge = re.findall(r'^(C\d\dK\d+),\d+,\d+,[^\n]*,important', prompt, re.M)
            parts = ['# Chapter title']
            for tid, a, b in topics:
                parts.append(f'## Topic {a}\n<!-- cues:{a}-{b} -->\n' + ('短。' if thin else '完整解释原因、机制和步骤。' * 40))
                parts += [f'[[frame:{f}|caption]]' for f, t in frames if t == tid]
            if not thin:
                parts.append('<!-- knowledge-map: ' + '; '.join(f'{k}=Topic {topics[0][1]}' for k in knowledge) + ' -->')
            return '\n\n'.join(parts)
        if ('review' in name or 'recheck' in name) and 'global' not in name:
            return 'VERDICT: PASS'
        if name.startswith('global-review'):
            return 'VERDICT: PASS\n```csv\nchapter_id,issue\n```'
        raise AssertionError(name)


def main(folder):
    folder = Path(folder)
    video = next(p for p in folder.iterdir() if p.suffix.lower() == '.mp4')
    config = load_config() | dict(chapter_minutes=2)
    p = pl.Pipeline(video, folder / 'output-fake', config, cwd=folder, log=lambda m: print(m, file=sys.stderr))
    original = p.ingest

    def ingest():
        try:
            original()
        except Exception as error:  # the real CLI may be logged out; plumbing does not need it
            if 'CLI' not in str(error) and 'claude' not in str(error).lower():
                raise
        p.model = ScriptedModel()
    p.ingest = ingest
    started = time.time()
    note, failed, report = p.run()
    text = note.read_text(encoding='utf-8')
    images = re.findall(r'!\[[^\]]*\]\((assets/[^)]+)\)', text)
    assert images and all((note.parent / i).is_file() for i in images), 'images missing'
    assert '[[frame:' not in text and not failed
    print(f'OK {note} | images {len(images)} | model calls {p.model.calls} | {round(time.time() - started)} s')
    print(report.read_text(encoding='utf-8'))


if __name__ == '__main__':
    main(sys.argv[1])
