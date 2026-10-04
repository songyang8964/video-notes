"""Regression tests for problems found in review of the parallel/resumable workflow."""
import hashlib
import tempfile
import threading
import unittest
from pathlib import Path

from video_notes import llm, render
from video_notes.pipeline import review_record_problems


def model(cache, fallback=('codex', 'm2', 'low')):
    m = llm.Model.__new__(llm.Model)
    m.backend, m.model, m.effort, m.exe, m.timeout, m.log = 'claude', 'm1', 'medium', 'claude', 5, lambda s: None
    m.cache, m.calls, m.fallback, m._lock, m.usage = Path(cache), 0, fallback, threading.Lock(), {}
    m.service_tier = None
    m.reuse = ['claude:m1:medium', 'codex:m2:low']
    return m


class ParallelFallbackTests(unittest.TestCase):
    def test_simultaneous_usage_limits_switch_once_and_store_under_the_real_backend(self):
        with tempfile.TemporaryDirectory() as d:
            m = model(d)
            both_failed = threading.Barrier(2)

            def run(prompt, images, target, log_path, state):
                if state[0] == 'claude':
                    log_path.write_text("You've hit your usage limit", encoding='utf-8')
                    both_failed.wait(timeout=5)  # both threads fail on claude before either switches
                    return ''
                return f'{state[0]}:{prompt}'
            m._run = run
            original, llm.resolve = llm.resolve, (lambda backend: backend)
            results, errors = {}, []

            def ask(name):
                try:
                    results[name] = m.ask(name, name)
                except Exception as error:  # the old code raised TypeError: 'NoneType' is not subscriptable
                    errors.append(error)
            try:
                threads = [threading.Thread(target=ask, args=(n,)) for n in ('C01-write', 'C02-write')]
                [t.start() for t in threads]
                [t.join(timeout=20) for t in threads]
            finally:
                llm.resolve = original
            self.assertEqual(errors, [])
            self.assertEqual(results, {'C01-write': 'codex:C01-write', 'C02-write': 'codex:C02-write'})
            for name in results:  # stored under the identity of the backend that produced the reply
                key = hashlib.sha256('\0'.join(['codex:m2:low', name]).encode()).hexdigest()[:16]
                self.assertTrue((Path(d) / f'{name}-{key}.ok').is_file())


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


class FallbackProbeTests(unittest.TestCase):
    def test_probe_uses_the_run_service_tier(self):
        import subprocess
        seen = {}
        original_run, original_resolve = subprocess.run, llm.resolve
        llm.resolve = lambda backend: 'codex'

        def fake(command, **kw):
            seen['command'] = command
            return subprocess.CompletedProcess(command, 0, 'OK', '')
        subprocess.run = fake
        try:
            self.assertEqual(llm.probe('codex', 'm', 'low', service_tier='fast'), (True, 'signed in'))
        finally:
            subprocess.run, llm.resolve = original_run, original_resolve
        self.assertIn('service_tier="fast"', seen['command'])


if __name__ == '__main__':
    unittest.main()
