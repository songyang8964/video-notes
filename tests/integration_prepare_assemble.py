"""prepare → written chapters → assemble on a real clip (no model, no login, no cost).

    python tests/integration_prepare_assemble.py <folder with one video + srt>

prepare → a scripted writer produces topics.csv, knowledge.csv, chapter.md and review.md per chapter → assemble.
A first, deliberately thin chapter must be refused by the checks; the corrected one must assemble.
It proves wiring and checks only, not note quality.
"""
import csv
import json
import re
import sys
from pathlib import Path

from video_notes import pipeline as pl
from video_notes.config import load_config


def write_chapter(folder, item, frames, thin):
    a, b = item['first_cue'], item['last_cue']
    mid = (a + b) // 2
    topics = [(f"{item['chapter']}T01", a, mid), (f"{item['chapter']}T02", mid + 1, b)]
    with (folder / 'topics.csv').open('w', encoding='utf-8', newline='') as handle:
        w = csv.writer(handle)
        w.writerow(['topic_id', 'first_cue', 'last_cue', 'title', 'expected_visual', 'kind'])
        w.writerows([(t, x, y, f'Topic {x}', 'slide', 'teaching') for t, x, y in topics])
    with (folder / 'knowledge.csv').open('w', encoding='utf-8', newline='') as handle:
        w = csv.writer(handle)
        w.writerow(['knowledge_id', 'first_cue', 'last_cue', 'content', 'kind', 'importance'])
        w.writerow([f"{item['chapter']}K001", a, a, 'fact', 'definition', 'important'])
    parts = ['# Chapter title']
    for i, (_, x, y) in enumerate(topics):
        parts.append(f'## Topic {x}\n<!-- cues:{x}-{y} -->\n' + ('短。' if thin else '完整解释原因、机制和步骤。' * 60))
        if i < len(frames):
            parts.append(f'[[frame:{frames[i]}|caption {i}]]')
    if not thin:
        parts.append(f"<!-- knowledge-map: {item['chapter']}K001=Topic {topics[0][1]} -->")
    (folder / 'chapter.md').write_text('\n\n'.join(parts), encoding='utf-8')
    record = [] if thin else [f'{f}：opened the original, caption and text match it' for f in frames[:len(topics)]]
    record += [] if thin else [f"{item['chapter']}K001：explained in Topic {topics[0][1]}"]
    (folder / 'review.md').write_text('\n'.join(record), encoding='utf-8')


def main(folder):
    folder = Path(folder)
    video = next(p for p in folder.iterdir() if p.suffix.lower() == '.mp4')
    log = lambda m: print(m, file=sys.stderr)
    make = lambda: pl.Pipeline(video, folder / 'output-test', load_config(), cwd=folder, log=log)
    briefs = make().prepare()
    index = json.loads((briefs / 'chapters.json').read_text(encoding='utf-8'))['chapters']
    for thin in (True, False):
        for item in index:
            brief = (briefs / item['chapter'] / 'brief.md').read_text(encoding='utf-8')
            frames = re.findall(r'^(C\d\dF\d+) \| ', brief, re.M)[:2]
            write_chapter(briefs / item['chapter'], item, frames, thin)
        note, problems = make().assemble()
        if thin:
            assert note is None and problems, 'thin chapters must be refused'
            print('refused as expected:', problems, file=sys.stderr)
    assert note and not problems, problems
    text = note.read_text(encoding='utf-8')
    images = re.findall(r'!\[[^\]]*\]\(<?([^)<>]*_assets/C\d\dF\d+\.jpg)>?\)', text)
    assert note.with_suffix('.docx').is_file(), 'Word copy missing'
    assert images and all((note.parent / i).is_file() for i in images), 'images missing'
    assert '[[frame:' not in text
    assert not (briefs.parent / 'model-calls').exists() or not any((briefs.parent / 'model-calls').iterdir()), \
        'prepare/assemble must not call a model'
    print(f'OK {note} | chapters {len(index)} | images {len(images)} | no model calls')


if __name__ == '__main__':
    main(sys.argv[1])
