"""Regression tests for output naming, image cleanup and the agent self-review record."""
import tempfile
import unittest
from pathlib import Path

from video_notes import render
from video_notes.pipeline import review_record_problems


class PreservedNoteImageTests(unittest.TestCase):
    def test_images_of_a_kept_user_edited_note_are_not_deleted(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'out'
            (out / 'assets').mkdir(parents=True)
            (out / 'assets' / 'C01F00000001.jpg').write_bytes(b'old')
            (out / 'Notes.md').write_text('# edited by the user\n\n![a](assets/C01F00000001.jpg)\n', encoding='utf-8')
            src = Path(d) / 'f.jpg'
            src.write_bytes(b'x')
            frame = dict(frame_id='C01F00000002', path=str(src), actual_ms=1000)
            render.assemble('T', [dict(text='# A\n\n[[frame:C01F00000002|cap]]', frames=[frame])], out,
                            'Notes.20261004-120000.md')
            self.assertTrue((out / 'assets' / 'C01F00000001.jpg').is_file())   # still shown by the kept note
            self.assertTrue((out / 'assets' / 'C01F00000002.jpg').is_file())


class AgentReviewRecordTests(unittest.TestCase):
    frames = [dict(frame_id='C01F00012000')]
    knowledge = [dict(knowledge_id='C01K001', importance='important'), dict(knowledge_id='C01K002', importance='supporting')]

    def test_every_image_and_important_item_needs_a_note(self):
        self.assertEqual(len(review_record_problems('', self.frames, self.knowledge)), 2)
        self.assertEqual(len(review_record_problems('C01F00012000：ok\nC01K001：x', self.frames, self.knowledge)), 1)
        good = ('C01F00012000：opened the original; the prefix list and next hop match the text\n'
                'C01K001：explained in "Root cause", with the policy example')
        self.assertEqual(review_record_problems(good, self.frames, self.knowledge), [])


if __name__ == '__main__':
    unittest.main()


class VideoNamedOutputTests(unittest.TestCase):
    def test_note_named_after_video_with_spaces_and_brackets(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            img = out / 'f.jpg'
            Image.new('RGB', (64, 36), 'white').save(img)
            stem = '【EU DCN】 Course (Part 2) 20261002'
            frame = dict(frame_id='C01F00000002', path=str(img), actual_ms=1000)
            note, _ = render.assemble('T', [dict(text='# A\n\n[[frame:C01F00000002|cap]]', frames=[frame])], out,
                                      f'{stem}.md', assets_name=f'{stem}_assets')
            self.assertIn(f'](<{stem}_assets/C01F00000002.jpg>)', note.read_text(encoding='utf-8'))
            self.assertTrue((out / f'{stem}_assets' / 'C01F00000002.jpg').is_file())
            docx = render.to_docx(note)
            import zipfile
            with zipfile.ZipFile(docx) as z:  # the image is embedded, not linked
                self.assertTrue(any(n.startswith('word/media/') for n in z.namelist()))

    def test_long_names_are_shortened_to_fit_windows_paths(self):
        import os
        from video_notes import pipeline
        base = pipeline.output_base(Path('C:/' + 'x' * 150), 'y' * 100)
        if os.name == 'nt' and not pipeline._long_paths_enabled():
            self.assertLess(len(base), 100)
            self.assertLessEqual(len(str(Path('C:/' + 'x' * 150) / f'{base}_assets' / 'C01F00000000.jpg')), 259)
        self.assertEqual(pipeline.output_base(Path('C:/v'), 'short'), 'short')
