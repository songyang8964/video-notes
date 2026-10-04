import tempfile
import unittest
from pathlib import Path

import numpy as np

from video_notes import cli, render, subtitles
from video_notes.candidates import screen_intervals
from video_notes.pipeline import make_chapters
from video_notes.config import fill, load_context, parse_times
from video_notes.detect import events, overlay
from video_notes.srt import read_srt, stamp
from video_notes.vision import ocr, select


class SrtTests(unittest.TestCase):
    def test_bom_multiline_and_millis(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'a.srt'
            p.write_text('﻿1\r\n00:00:01,5 --> 00:00:02,250\r\nline one\r\nline two\r\n\r\n'
                         '2\r\n01:00:00,000 --> 01:00:01,001\r\nnext\r\n', encoding='utf-8')
            cues = read_srt(p)
        self.assertEqual([(c['start'], c['end']) for c in cues], [(1500, 2250), (3600000, 3601001)])
        self.assertEqual(cues[0]['text'], 'line one line two')
        self.assertEqual(stamp(3601001), '01:00:01.001')


class DiscoveryTests(unittest.TestCase):
    def test_zero_one_many_and_url(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with self.assertRaises(SystemExit) as zero:
                cli.discover(None, root)
            self.assertEqual(zero.exception.code, 2)
            (root / 'A.MP4').write_bytes(b'x')
            self.assertEqual(cli.discover(None, root).name, 'A.MP4')
            (root / 'sub').mkdir()
            (root / 'sub' / 'c.mp4').write_bytes(b'x')  # never recursive
            self.assertEqual(cli.discover(None, root).name, 'A.MP4')
            (root / 'b.mp4').write_bytes(b'x')
            with self.assertRaises(SystemExit) as many:
                cli.discover(None, root)
            self.assertEqual(many.exception.code, 2)
            with self.assertRaises(SystemExit) as url:
                cli.discover('https://example.com/v', root)
            self.assertEqual(url.exception.code, 2)
            self.assertEqual(cli.discover('b.mp4', root).name, 'b.mp4')


class ChapterCheckTests(unittest.TestCase):
    topics = [dict(topic_id='T1', first_cue='1', last_cue='2', kind='teaching'),
              dict(topic_id='T2', first_cue='3', last_cue='3', kind='excluded')]
    frames = [dict(frame_id=f'F{i}', topic_id='T1') for i in range(10)]

    def note(self, images, extra=''):
        return ('# Title\n\n## A\n<!-- cues:1-2 -->\n' + ''.join(f'[[frame:{i}|cap]]\n' for i in images) + extra +
                '<!-- excluded-cues:3-3; reason:break -->\n')

    def test_valid_chapter_passes(self):
        self.assertEqual(render.check_chapter(self.note(['F0', 'F1']), [1, 2, 3], self.topics, self.frames, 8), [])

    def test_repeat_limit_unknown_and_meta_phrases_fail(self):
        for images, extra in ((['F0', 'F0'], ''), ([f'F{i}' for i in range(9)], ''), (['X9'], ''),
                              ([], '讲师在这里强调。\n'), ([], 'FOOBAR\n')):
            problems = render.check_chapter(self.note(images, extra), [1, 2, 3], self.topics, self.frames, 8,
                                            forbidden=['FOOBAR'])
            self.assertTrue(problems, (images, extra))

    def test_cue_gap_fails(self):
        text = '# T\n## A\n<!-- cues:1-1 -->\n<!-- excluded-cues:3-3; reason:x -->'
        self.assertTrue(render.check_chapter(text, [1, 2, 3], self.topics, self.frames, 8))


class PromptAndContextTests(unittest.TestCase):
    def test_fill_does_not_expand_braces_in_values(self):
        self.assertEqual(fill('a {x} {unknown}', x='{y} ${z}', y='bad'), 'a {y} ${z} {unknown}')

    def test_context_front_matter_and_times(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'v.context.md'
            p.write_text('---\ntitle: T\ninclude_times: 00:02:07.797, 90\n---\nUse the term X.\n', encoding='utf-8')
            settings, text = load_context(p)
        self.assertEqual(settings['title'], 'T')
        self.assertEqual(text, 'Use the term X.')
        self.assertEqual(parse_times(settings['include_times']), [127797, 90000])
        self.assertIn('未提供', load_context(None)[1])


class PortedAlgorithmTests(unittest.TestCase):
    def test_text_delta_not_diluted(self):
        long = ' '.join(f'w{i}' for i in range(200))
        self.assertGreaterEqual(ocr.text_delta(long, long + ' changed'), 1 / 3)
        self.assertEqual(ocr.text_delta('A b', 'a  B'), 0.0)

    def test_selector_avoids_duplicates(self):
        v1, v2 = np.array([1.0, 0.0]), np.array([0.0, 1.0])
        cands = [dict(time_ms=0, vector=v1, quality=0.9), dict(time_ms=1000, vector=v1, quality=0.9),
                 dict(time_ms=2000, vector=v2, quality=0.5)]
        picked = select.select(cands, 2, 3000)
        self.assertEqual([c['time_ms'] for c in picked], [0, 2000])

    def test_events_find_cut_and_settle_points(self):
        ts = np.arange(40) / 10
        area = np.zeros(40)
        area[20] = 0.5
        measured = dict(time_series=ts, area_series=area, anchor_series=np.zeros(40), rate_series=np.zeros(40))
        found = events.find(measured, anchor_threshold=0.02, rate_threshold=0.0015, cut_area_threshold=0.002)
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0]['time'], 2.0)
        self.assertLess(found[0]['before_time'], 2.0)
        self.assertGreater(found[0]['after_time'], 2.0)

    def test_overlay_band_only_at_edges(self):
        freq = np.full(36, 0.001)
        freq[-3:] = 0.05
        self.assertEqual(overlay.body_band(freq), (0.0, 33 / 36))
        freq = np.full(36, 0.001)
        freq[15:18] = 0.05  # busy centre is content
        self.assertEqual(overlay.body_band(freq), overlay.FULL)


class SubtitleChoiceTests(unittest.TestCase):
    def test_vtt_rolling_cues_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'v.en.vtt'
            p.write_text('WEBVTT\n\n00:01.000 --> 00:02.000\nhello there\n\n'
                         '00:02.000 --> 00:03.000\nhello there\ngeneral <c>kenobi</c>\n', encoding='utf-8')
            cues = subtitles.read_vtt(p)
        self.assertEqual([c['text'] for c in cues], ['hello there', 'general kenobi'])
        self.assertEqual(cues[1]['start'], 2000)

    def test_refusal_reasons(self):
        cue = lambda i, s, e, t='x': dict(id=i, start=s, end=e, text=t)
        self.assertIn('cues', subtitles.refuse_reason([cue(1, 0, 10)], 600000))
        short = [cue(i, i * 1000, i * 1000 + 900, f't{i}') for i in range(10)]
        self.assertIn('30%', subtitles.refuse_reason(short, 600000))
        good = [cue(i, i * 60000, i * 60000 + 50000, f'line {i}') for i in range(10)]
        self.assertEqual(subtitles.refuse_reason(good, 600000), '')
        rolling = [cue(i, i * 60000, i * 60000 + 50000, 'same start ' + 'x' * i) for i in range(10)]
        self.assertIn('rolling', subtitles.refuse_reason(rolling, 600000))

    def test_sidecar_match_and_explicit_refusal(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            video = root / 'talk.mp4'
            video.write_bytes(b'x')
            for name in ('talk.srt', 'talk.en.srt', 'talkative.srt', 'other.srt'):
                (root / name).write_text('', encoding='utf-8')
            self.assertEqual(sorted(p.name for p in subtitles.sidecars(video)), ['talk.en.srt', 'talk.srt'])
            bad = root / 'bad.srt'
            bad.write_text('1\n00:00:01,000 --> 00:00:02,000\nx\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                subtitles.choose(video, 600000, root / 'w', explicit=bad)


class ChapterAndScreenTests(unittest.TestCase):
    def test_chapter_cut_prefers_longest_pause_near_window_end(self):
        cues = [dict(id=i + 1, start=i * 10000, end=i * 10000 + 9000, text='') for i in range(100)]
        cues[54]['end'] = cues[54]['start'] + 1000   # 9 s pause after cue 55, inside the last quarter
        chapters = make_chapters(cues, 600000)
        self.assertEqual(chapters[0][-1]['id'], 55)
        self.assertEqual([c['id'] for ch in chapters for c in ch], list(range(1, 101)))

    def test_screen_intervals_cover_video(self):
        screens = [dict(time=0.5, source='initial'), dict(time=10.0, source='screen-end'),
                   dict(time=12.0, source='screen-start')]
        self.assertEqual(screen_intervals(screens, 30000), [(0, 500), (500, 12000), (12000, 30000)])


class OutputProtectionTests(unittest.TestCase):
    def test_edited_note_is_not_overwritten(self):
        from video_notes.pipeline import Pipeline
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = Pipeline.__new__(Pipeline)
            p.output_root, p.work, p.video, p.warnings, p.log = root / 'out', root / 'work', root / 'v.mp4', [], lambda m: None
            p.cjk = True
            p.work.mkdir()
            results = {'C01': dict(text='# A\n\nbody', frames=[])}
            first, _ = p.write_output('T', results)
            self.assertEqual(first.name, 'v.md')
            again, _ = p.write_output('T', results)
            self.assertEqual(again.name, 'v.md')        # unchanged by user: refreshed in place
            first.write_text('user edits', encoding='utf-8')
            third, _ = p.write_output('T', results)
            self.assertNotEqual(third.name, 'v.md')
            self.assertEqual(first.read_text(encoding='utf-8'), 'user edits')

    def test_english_note_name_and_caption(self):
        from video_notes.pipeline import Pipeline
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = Pipeline.__new__(Pipeline)
            p.output_root, p.work, p.video, p.warnings, p.log = root / 'out', root / 'work', root / 'v.mp4', [], lambda m: None
            p.cjk = render.is_cjk('English')
            p.work.mkdir()
            image = root / 'f.jpg'
            image.write_bytes(b'x')
            frame = dict(frame_id='C01F00001000', path=str(image), actual_ms=61000)
            note, _ = p.write_output('T', {'C01': dict(text='# A\n\n[[frame:C01F00001000|Topology]]', frames=[frame])})
            self.assertEqual(note.name, 'v.md')
            self.assertTrue(note.with_suffix('.docx').is_file())
            self.assertIn('*Topology (source video 00:01:01.000)*', note.read_text(encoding='utf-8'))


class KnowledgeAndDensityTests(unittest.TestCase):
    knowledge = [dict(knowledge_id='K1', importance='important'), dict(knowledge_id='K2', importance='important'),
                 dict(knowledge_id='K3', importance='supporting')]

    def text(self, mapping):
        return '# T\n\n## 现网拓扑\n<!-- cues:1-2 -->\nbody\n\n<!-- knowledge-map: ' + mapping + ' -->'

    def test_valid_map_passes(self):
        self.assertEqual(render.check_knowledge_map(self.text('K1=现网拓扑; K2=excluded:会议组织'), self.knowledge), [])

    def test_missing_dangling_and_reasonless_exclusion_fail(self):
        for mapping in ('K1=现网拓扑', 'K1=现网拓扑; K2=不存在的小节', 'K1=现网拓扑; K2=excluded', ''):
            self.assertTrue(render.check_knowledge_map(self.text(mapping), self.knowledge), mapping)
        no_comment = '# T\n\n## A\n<!-- cues:1-2 -->\nbody'
        self.assertTrue(render.check_knowledge_map(no_comment, self.knowledge))
        self.assertEqual(render.check_knowledge_map(no_comment, self.knowledge[2:]), [])  # nothing important

    def test_density_flags_summary_but_not_full_explanation(self):
        cue_times = {1: (0, 1000), 2: (240000, 300000)}   # 5 minutes of teaching
        summary = '# T\n\n## A\n<!-- cues:1-2 -->\n' + '要点。' * 20
        full = '# T\n\n## A\n<!-- cues:1-2 -->\n' + '这一步先检查备份路由再拆除链路。' * 60
        self.assertTrue(render.check_density(summary, cue_times, 100))
        self.assertEqual(render.check_density(full, cue_times, 100), [])

    def test_code_blocks_and_images_do_not_count_as_prose(self):
        cue_times = {1: (0, 1000), 2: (120000, 180000)}
        padded = '# T\n\n## A\n<!-- cues:1-2 -->\n短。\n```text\n' + 'x' * 2000 + '\n```\n[[frame:F1|' + 'y' * 50 + ']]'
        self.assertTrue(render.check_density(padded, cue_times, 100))

class DamagedRecordingTests(unittest.TestCase):
    def test_explicit_subtitle_running_past_video_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            srt = Path(d) / 'audio.srt'
            nl = chr(10)
            srt.write_text(''.join(f'{n}{nl}00:{n:02}:00,000 --> 00:{n:02}:05,000{nl}line {n}{nl}{nl}'
                                   for n in range(1, 20)), encoding='utf-8')
            path, cues, source, notes = subtitles.choose(Path(d) / 'v.mp4', 10 * 60000, Path(d) / 'w', srt)
            self.assertEqual((len(cues), source), (19, 'explicit'))
            self.assertTrue(any('runs past the video' in n for n in notes))
            self.assertIn('extends far beyond', subtitles.refuse_reason(cues, 10 * 60000))  # sidecars still refused

    def test_decoder_stops_at_corrupt_tail(self):
        from video_notes.detect import decode

        def frames():
            yield 1
            yield 2
            raise decode.av.InvalidDataError(1094995529, 'Invalid data found when processing input')

        class Container:
            def decode(self, stream):
                return frames()
        self.assertEqual(list(decode._decoded(Container(), None)), [1, 2])


class EnglishDensityTests(unittest.TestCase):
    def test_words_count_for_non_cjk_notes(self):
        section = '## Topology' + chr(10) + '<!-- cues:1-2 -->' + chr(10) + 'OSPF area zero carries the loopbacks. ' * 10
        self.assertEqual(render._prose_chars(section, cjk=False), round(1.5 * 60))
        self.assertGreater(render._prose_chars(section, cjk=True), 250)  # letters: the CJK rule is unchanged

    def test_language_detection(self):
        self.assertTrue(render.is_cjk('中文'))
        self.assertFalse(render.is_cjk('English'))


class WorkDirTests(unittest.TestCase):
    def test_deep_folders_keep_work_out_of_max_path(self):
        import os
        from video_notes import pipeline
        short = pipeline.work_dir(Path('C:/v'), 'abc123def0')
        self.assertEqual(short, Path('C:/v') / '.work' / 'video-notes' / 'abc123def0')
        deep = pipeline.work_dir(Path('C:/' + 'x' * 160), 'abc123def0')
        if os.name == 'nt' and not pipeline._long_paths_enabled():
            self.assertLessEqual(len(str(deep)), pipeline.WORK_PATH_BUDGET)
            self.assertEqual(deep.name, 'abc123def0')


class AssembleTests(unittest.TestCase):
    def test_config_comments_in_code_blocks_are_not_headings(self):
        fence = chr(96) * 3
        text = '\n'.join(['# Chapter', '## Step', fence, 'ospf 1', '# To add: ip ip-prefix x', '#', fence, '# After'])
        out = render.demote_headings(text).split('\n')
        self.assertEqual(out[0], '## Chapter')
        self.assertEqual(out[1], '### Step')
        self.assertEqual(out[4], '# To add: ip ip-prefix x')
        self.assertEqual(out[5], '#')
        self.assertEqual(out[7], '## After')


class StaleAssetTests(unittest.TestCase):
    def test_unreferenced_tool_images_are_removed(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'out'
            (out / 'assets').mkdir(parents=True)
            (out / 'assets' / 'C01F00000001.jpg').write_bytes(b'old')
            (out / 'assets' / 'my-photo.jpg').write_bytes(b'user')
            src = Path(d) / 'f.jpg'
            src.write_bytes(b'x')
            frame = dict(frame_id='C01F00000002', path=str(src), actual_ms=1000)
            render.assemble('T', [dict(text='# A\n\n[[frame:C01F00000002|cap]]', frames=[frame])], out, 'n.md')
            self.assertEqual(sorted(p.name for p in (out / 'assets').iterdir()), ['C01F00000002.jpg', 'my-photo.jpg'])


class DuplicateScreenTests(unittest.TestCase):
    def test_same_screen_merged_but_similar_text_slides_kept(self):
        from PIL import Image, ImageDraw
        from video_notes.candidates import drop_duplicates
        with tempfile.TemporaryDirectory() as d:
            def slide(name, lines, noise=False):
                img = Image.new('L', (1280, 720), 255)
                draw = ImageDraw.Draw(img)
                draw.rectangle((0, 0, 1280, 80), fill=40)            # shared template header
                for i, width in enumerate(lines):                    # text lines at slide size
                    draw.rectangle((100, 150 + i * 70, 100 + width, 190 + i * 70), fill=0)
                if noise:
                    draw.point((5, 700), fill=0)                       # codec-noise-sized change
                path = Path(d) / f'{name}.jpg'
                img.convert('RGB').save(path)
                return path
            a = slide('a', [700, 500, 900])
            b = slide('b', [700, 500, 900], noise=True)
            c = slide('c', [400, 950, 300])
            live = [dict(frame_id='A', path=str(a), source='screen-start', actual_ms=0, status='candidate'),
                    dict(frame_id='B', path=str(b), source='screen-end', actual_ms=5000, status='candidate'),
                    dict(frame_id='C', path=str(c), source='screen-start', actual_ms=9000, status='candidate')]
            kept = [r['frame_id'] for r in drop_duplicates(live)]
        self.assertEqual(kept, ['B', 'C'])              # finished state preferred; different text kept
        self.assertEqual(live[0]['status'], 'duplicate')


class AgentModeSafetyTests(unittest.TestCase):
    def test_run_lock_refuses_second_run_and_clears_stale(self):
        import os
        from video_notes.pipeline import InputError, RunLock
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'run.lock').write_text('999999', encoding='utf-8')  # stale
            with RunLock(Path(d)):
                (Path(d) / 'run.lock').write_text(str(os.getpid()), encoding='utf-8')
                with self.assertRaises(InputError):
                    RunLock(Path(d)).__enter__()
            self.assertFalse((Path(d) / 'run.lock').exists())

    def test_internal_ids_in_prose_are_rejected(self):
        text = '# T\n\n## A\n<!-- cues:1-2 -->\nAs explained in C01 and Chapter 8, see C08F03865397.\n'
        problems = render.check_chapter(text, [1, 2], [dict(topic_id='C01T01', first_cue='1', last_cue='2')], [], 8)
        self.assertTrue(any('internal chapter/frame IDs' in p for p in problems))
        clean = '# T\n\n## A\n<!-- cues:1-2; C01 note -->\nThe R1 and SW-12 switches. [[frame:C01F00000001|cap]]\n'
        self.assertFalse(any('internal' in p for p in render.check_chapter(
            clean, [1, 2], [dict(topic_id='C01T01', first_cue='1', last_cue='2')],
            [dict(frame_id='C01F00000001', topic_id='C01T01')], 8)))


if __name__ == '__main__':
    unittest.main()
