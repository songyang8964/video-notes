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
            p.work.mkdir()
            results = {'C01': dict(text='# A\n\nbody', frames=[])}
            first, _ = p.write_output('T', results)
            self.assertEqual(first.name, '培训笔记.md')
            again, _ = p.write_output('T', results)
            self.assertEqual(again.name, '培训笔记.md')        # unchanged by user: refreshed in place
            first.write_text('user edits', encoding='utf-8')
            third, _ = p.write_output('T', results)
            self.assertNotEqual(third.name, '培训笔记.md')
            self.assertEqual(first.read_text(encoding='utf-8'), 'user edits')



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


if __name__ == '__main__':
    unittest.main()
