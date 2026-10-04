"""Backend-independent, content-verified chapter checkpoints for safe handoffs."""
import json
import hashlib
from importlib import resources
from pathlib import Path
from .media import sha256


def evidence(directory):
    files = [directory / n for n in ('chapter.md', 'review.md', 'topics.csv', 'knowledge.csv')]
    files += list(directory.glob('selection*.csv'))
    files += list((directory / 'candidates').glob('*.csv'))
    files += list((directory / 'candidates' / 'frames').glob('*.jpg'))
    return {str(p.relative_to(directory)): sha256(p) for p in sorted(files) if p.is_file()}


def save(directory, source, passed, **extra):
    record = dict(schema=1, source=source, passed=passed, evidence=evidence(directory), templates=templates(), **extra)
    target = directory / 'checkpoint.json'
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(target)


def load(directory, source):
    path = directory / 'checkpoint.json'
    if not path.is_file():
        return None
    record = json.loads(path.read_text(encoding='utf-8'))
    if record.get('schema') != 1 or record.get('source') != source or record.get('evidence') != evidence(directory) or record.get('templates') != templates():
        return None
    return record


def templates():
    root = resources.files('video_notes.prompts')
    return {name: hashlib.sha256(root.joinpath(name + '.md').read_bytes()).hexdigest()
            for name in ('write', 'review', 'revise', 'recheck')}
