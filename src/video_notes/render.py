"""Chapter validation and final assembly into <output>/<video name>/<title>.md + assets/."""
import re
import shutil
from pathlib import Path

from .srt import stamp

META_PHRASES = ('讲师', '讲者', '老师提到', '视频中提到', '视频里', '根据字幕', '字幕中', '截图中', '本片段',
                '接下来介绍', 'the speaker', 'the lecturer', 'the presenter', 'the instructor', 'the trainer',
                'in this video', 'in the video', 'the transcript', 'the subtitles')
INTERNAL_ID = re.compile(r'\bC\d\d(?:F\d{8}|T\d\d|K\d{3})?\b|\b[Cc]hapter \d+\b|第\s*\d+\s*章')
PLACEHOLDER = re.compile(r'\[\[frame:([A-Za-z0-9]+)\|([^\]\n]+)\]\]')


CJK = re.compile(r'[぀-ヿ㐀-鿿가-힯]')
WORD = re.compile(r"[^\W_]+(?:['’.\-][^\W_]+)*")
WORD_WEIGHT = 1.5  # one CJK character ≈ 0.65 words of the same content in English-like prose


def is_cjk(language):
    """Whether the note language is written in CJK characters (density is then counted per character)."""
    text = str(language or '')
    return bool(CJK.search(text)) or text.strip().lower() in ('chinese', 'zh', 'japanese', 'ja', 'korean', 'ko')


def prose(text):
    return re.sub(r'<!--.*?-->', '', text, flags=re.S)


def _prose_chars(section, cjk=True):
    """Prose size in CJK-character units. Other scripts count words × WORD_WEIGHT so one floor
    serves both (counting Latin letters would let English pass at about a third of the detail)."""
    text = prose(section)
    text = PLACEHOLDER.sub('', text)
    text = re.sub(r'```.*?```', '', text, flags=re.S)        # commands count, but not as prose
    text = re.sub(r'^#+ .*$', '', text, flags=re.M)          # headings are not explanation
    if cjk:
        return len(re.sub(r'\s', '', text))
    words = [w for w in WORD.findall(CJK.sub(' ', text))]
    return round(len(CJK.findall(text)) + WORD_WEIGHT * len(words))


def check_knowledge_map(text, knowledge):
    """Every important knowledge item must map to an existing section or carry an exclusion reason."""
    problems = []
    found = re.search(r'<!--\s*knowledge-map:(.*?)-->', text, re.S)
    important = [k['knowledge_id'] for k in knowledge if k.get('importance', '').strip() == 'important']
    if not important:
        return problems
    if not found:
        return ['missing <!-- knowledge-map: ... --> comment']
    entries = {}
    for part in re.split(r'[;；\n]', found[1]):
        if '=' in part:
            key, value = part.split('=', 1)
            entries[key.strip()] = value.strip()
    headings = [h.strip() for h in re.findall(r'^##+ (.+)$', text, re.M)]
    missing, dangling = [], []
    for kid in important:
        value = entries.get(kid)
        if not value:
            missing.append(kid)
        elif value.lower().startswith('excluded'):
            if len(value.split(':', 1)[-1].strip()) < 2 or ':' not in value:
                dangling.append(f'{kid} (exclusion without a reason)')
        elif not any(value in h or h in value for h in headings):
            dangling.append(f'{kid} -> "{value}" (no such section)')
    if missing:
        problems.append('important knowledge not mapped to the text: ' + ', '.join(missing))
    if dangling:
        problems.append('knowledge-map entries invalid: ' + '; '.join(dangling))
    return problems


def check_density(text, cue_times, min_chapter_cpm, min_section_cpm=50, cjk=True):
    """Detail floor in prose characters per minute of teaching (calibrated on hand-written notes:
    chapters 138–475/min, median ~230; lowest section ~93/min). Catches summaries, not style."""
    problems, total_chars, total_min = [], 0, 0.0
    for section in re.split(r'(?=^## )', text, flags=re.M):
        m = re.search(r'<!--\s*cues:\s*(\d+)\s*-\s*(\d+)', section)
        if not m or int(m[1]) not in cue_times or int(m[2]) not in cue_times:
            continue
        minutes = (cue_times[int(m[2])][1] - cue_times[int(m[1])][0]) / 60000
        chars = _prose_chars(section, cjk)
        total_chars, total_min = total_chars + chars, total_min + minutes
        if minutes >= 1 and chars < min_section_cpm * minutes:
            title = section.splitlines()[0].lstrip('# ').strip()
            problems.append(f'section "{title}" too thin: {chars} chars for {minutes:.1f} min of teaching '
                            f'(need ≥{round(min_section_cpm * minutes)}); explain the reasoning, conditions and examples')
    if total_min >= 1 and total_chars < min_chapter_cpm * total_min:
        problems.append(f'chapter too thin: {total_chars} chars for {total_min:.1f} teaching min '
                        f'(need ≥{round(min_chapter_cpm * total_min)}); this reads as a summary, write the full explanation')
    return problems


def check_chapter(text, owned_ids, topics, frames, max_images, forbidden=(), knowledge=(), cue_times=None,
                  min_chars_per_minute=100, cjk=True):
    """Return a list of mechanical problems (empty = pass)."""
    problems = check_knowledge_map(text, knowledge)
    if cue_times:
        problems += check_density(text, cue_times, min_chars_per_minute, cjk=cjk)
    ranges = re.findall(r'<!--\s*(?:cues|excluded-cues):\s*(\d+)\s*-\s*(\d+)', text)
    mapped = [i for a, b in ranges for i in range(int(a), int(b) + 1)]
    if mapped != list(owned_ids):
        problems.append(f'cue comments must cover {owned_ids[0]}-{owned_ids[-1]} exactly once in order '
                        f'(found {len(mapped)} ids)')
    visible = prose(text)
    ids = PLACEHOLDER.findall(visible)
    used = [i for i, _ in ids]
    if len(used) != len(set(used)):
        problems.append('an image is inserted more than once')
    if len(used) > max_images:
        problems.append(f'{len(used)} images exceed the limit of {max_images}')
    lookup = {f['frame_id']: f for f in frames}
    for frame_id in used:
        if frame_id not in lookup:
            problems.append(f'unknown frame {frame_id}')
    for section in re.split(r'(?=^## )', text, flags=re.M):
        here = PLACEHOLDER.findall(prose(section))
        if not here:
            continue
        m = re.search(r'<!--\s*cues:\s*(\d+)\s*-\s*(\d+)', section)
        topic = next((t for t in topics if m and int(t['first_cue']) == int(m[1]) and int(t['last_cue']) == int(m[2])), None)
        for frame_id, _ in here:
            if frame_id in lookup and (not topic or lookup[frame_id].get('topic_id') != topic['topic_id']):
                problems.append(f'image {frame_id} placed outside its topic section')
    internal = INTERNAL_ID.findall(PLACEHOLDER.sub('', visible))
    if internal:
        problems.append('internal chapter/frame IDs in prose (refer to the section title instead): '
                        + ', '.join(sorted(set(internal))))
    for phrase in tuple(META_PHRASES) + tuple(forbidden):
        if phrase and phrase.lower() in visible.lower():
            problems.append(f'forbidden phrase in prose: {phrase}')
    if re.search(r'!\[[^\]]*\]\(', visible):
        problems.append('direct image path instead of a frame placeholder')
    if visible.count('```') % 2:
        problems.append('unbalanced code fence')
    if not re.search(r'^# \S', text, re.M):
        problems.append('missing chapter title (# ...)')
    return problems


def demote_headings(text):
    """One level deeper for every heading outside code fences. Lines such as `# To add: …` inside a
    device-configuration block are comments of the configuration, not headings."""
    out, fenced = [], False
    for line in text.split('\n'):
        if line.lstrip().startswith('```'):
            fenced = not fenced
        elif not fenced and re.match(r'#{1,5} ', line):
            line = '#' + line
        out.append(line)
    return '\n'.join(out)


def assemble(title, chapters, out_dir: Path, filename='培训笔记.md', cjk=True):
    """chapters: [{'text', 'frames'}] in order. Writes note + assets; returns (note_path, inserted)."""
    assets = out_dir / 'assets'
    assets.mkdir(parents=True, exist_ok=True)
    body, inserted = [], []
    for chapter in chapters:
        text = re.sub(r'<!--.*?-->\n?', '', chapter['text'], flags=re.S).strip()
        if not text:
            continue
        text = demote_headings(text)
        lookup = {f['frame_id']: f for f in chapter['frames']}

        def render(match):
            frame = lookup[match[1]]
            name = match[1] + '.jpg'
            shutil.copy2(frame['path'], assets / name)
            inserted.append(dict(frame, caption=match[2]))
            when = stamp(frame['actual_ms'])
            source = f'（原视频 {when}）' if cjk else f' (source video {when})'
            return f'![{match[2]}](assets/{name})\n\n*{match[2]}{source}*'
        body.append(PLACEHOLDER.sub(render, text))
    note = f'# {title}\n\n' + '\n\n'.join(body) + '\n'
    if '[[frame:' in note:
        raise ValueError('unresolved image placeholder')
    # Images left over from an earlier assembly (e.g. a duplicate removed in review) would ship with the
    # shared folder; only files this tool writes (frame-ID names) are removed, and never one that another
    # note in the folder still shows (a note the user edited is kept beside the new one).
    used = {f"{f['frame_id']}.jpg" for f in inserted}
    for other in out_dir.glob('*.md'):
        if other.name != filename:
            used |= set(re.findall(r'\]\(assets/([^)\s]+)\)', other.read_text(encoding='utf-8', errors='replace')))
    for stale in assets.glob('*.jpg'):
        if re.fullmatch(r'C\d\dF\d+\.jpg', stale.name) and stale.name not in used:
            stale.unlink()
    path = out_dir / filename
    path.write_text(note, encoding='utf-8')
    return path, inserted
