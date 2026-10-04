"""prepare → written chapter → assemble on a generated 6-second video (real FFmpeg and pandoc, no model).

Regression tests for review findings: stale chapters after an input change, a user-edited Word file,
an incomplete chapter index, and keep-awake during prepare."""
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from video_notes import pipeline as pl
from video_notes.config import DEFAULTS

CUES = ['Router R1 peers with AS {asn} over eBGP.', 'The session uses the loopback address.',
        'Routes are exchanged after the session is up.', 'The policy filters the private prefixes.',
        'Finally the routes are verified with display commands.']


def write_srt(path, asn):
    path.write_text(''.join(f'{n}\n00:00:0{n - 1},100 --> 00:00:0{n - 1},900\n{text.format(asn=asn)}\n\n'
                            for n, text in enumerate(CUES, 1)), encoding='utf-8')


def write_chapter(folder, asn):
    (folder / 'topics.csv').write_text('topic_id,first_cue,last_cue,title,expected_visual,kind\n'
                                       'C01T01,1,5,eBGP peering,none,teaching\n', encoding='utf-8')
    (folder / 'knowledge.csv').write_text('knowledge_id,first_cue,last_cue,content,kind,importance\n'
                                          f'C01K001,1,1,R1 peers with AS {asn},fact,important\n', encoding='utf-8')
    (folder / 'chapter.md').write_text(f'# eBGP\n\n## eBGP peering\n<!-- cues:1-5 -->\nRouter R1 peers with AS {asn} '
                                       'over eBGP using its loopback address.\n\n'
                                       '<!-- knowledge-map: C01K001=eBGP peering -->\n', encoding='utf-8')
    (folder / 'review.md').write_text(f'C01K001：explained in "eBGP peering", AS {asn} checked against the subtitle\n',
                                      encoding='utf-8')


class PrepareAssembleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.video = self.dir / 'talk.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc=duration=6:size=320x180:rate=10',
                        '-f', 'lavfi', '-i', 'sine=duration=6', '-shortest', str(self.video)], check=True)
        write_srt(self.dir / 'talk.srt', 65001)
        self.awake = []
        self.original_awake, pl.keep_awake = pl.keep_awake, self.awake.append

    def tearDown(self):
        pl.keep_awake = self.original_awake
        self.tmp.cleanup()

    def run_stage(self, stage):
        config = dict(DEFAULTS, use_adaptive=False)
        return getattr(pl.Pipeline(self.video, self.dir, config, cwd=self.dir, log=lambda m: None), stage)()

    def test_full_round_trip_and_keep_awake(self):
        briefs = self.run_stage('prepare')
        self.assertEqual(self.awake, [True, False])
        write_chapter(briefs / 'C01', 65001)
        note, problems = self.run_stage('assemble')
        self.assertEqual(problems, {})
        self.assertIn('AS 65001', note.read_text(encoding='utf-8'))
        self.assertTrue(note.with_suffix('.docx').is_file())

    def test_changed_subtitles_make_old_chapters_stale(self):
        briefs = self.run_stage('prepare')
        write_chapter(briefs / 'C01', 65001)
        self.assertEqual(self.run_stage('assemble')[1], {})
        write_srt(self.dir / 'talk.srt', 65002)                 # same cue IDs and times, corrected content
        with self.assertRaises(pl.InputError) as refused:
            self.run_stage('assemble')
        self.assertIn('subtitles', str(refused.exception))
        time.sleep(0.05)
        self.run_stage('prepare')                              # the brief now carries AS 65002
        note, problems = self.run_stage('assemble')
        self.assertIsNone(note)
        self.assertIn('brief.md changed after review.md', problems['C01'][0])
        write_chapter(briefs / 'C01', 65002)                   # re-checked against the new brief
        note, problems = self.run_stage('assemble')
        self.assertEqual(problems, {})
        self.assertIn('AS 65002', note.read_text(encoding='utf-8'))

    def test_rerunning_prepare_with_same_inputs_keeps_chapters_valid(self):
        briefs = self.run_stage('prepare')
        write_chapter(briefs / 'C01', 65001)
        time.sleep(0.05)
        self.run_stage('prepare')                              # e.g. resuming after an interruption
        self.assertEqual(self.run_stage('assemble')[1], {})

    def test_user_edited_word_file_is_kept(self):
        briefs = self.run_stage('prepare')
        write_chapter(briefs / 'C01', 65001)
        note, _ = self.run_stage('assemble')
        word = note.with_suffix('.docx')
        word.write_bytes(word.read_bytes() + b'user edit')
        edited = word.read_bytes()
        self.run_stage('assemble')
        self.assertEqual(word.read_bytes(), edited)
        self.assertEqual(len(list(self.dir.glob('talk.*.docx'))), 1)  # the new copy has a timestamped name

    def test_incomplete_chapter_index_is_refused(self):
        briefs = self.run_stage('prepare')
        write_chapter(briefs / 'C01', 65001)
        index = json.loads((briefs / 'chapters.json').read_text(encoding='utf-8'))
        for chapters in ([], [dict(index['chapters'][0], last_cue=3)]):
            (briefs / 'chapters.json').write_text(json.dumps(dict(index, chapters=chapters)), encoding='utf-8')
            with self.assertRaises(pl.InputError):
                self.run_stage('assemble')
        self.assertFalse((self.dir / 'talk.md').exists())


class ChapterIndexTests(unittest.TestCase):
    def test_index_must_cover_every_cue_once_in_order(self):
        good = [dict(chapter='C01', first_cue=1, last_cue=3), dict(chapter='C02', first_cue=4, last_cue=5)]
        self.assertEqual(pl.chapter_index_problem(good, [1, 2, 3, 4, 5]), '')
        self.assertIn('empty', pl.chapter_index_problem([], [1]))
        self.assertTrue(pl.chapter_index_problem(good[:1], [1, 2, 3, 4, 5]))
        self.assertTrue(pl.chapter_index_problem([good[1], good[0]], [1, 2, 3, 4, 5]))


if __name__ == '__main__':
    unittest.main()
