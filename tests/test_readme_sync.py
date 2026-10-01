"""The README exists in Chinese, English and Hungarian; every change must land in all three.

This compares structure (not wording): headings per level, diagrams, code blocks, table rows,
links to repository files and the language switcher. A change made in only one language makes
the counts diverge and the test fail.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = {'zh': ROOT / 'README.md', 'en': ROOT / 'README.en.md', 'hu': ROOT / 'README.hu.md'}


def structure(text):
    fences = re.findall(r'^```(\w*)', text, re.M)
    body = re.sub(r'^```.*?^```', '', text, flags=re.S | re.M)   # headings inside code do not count
    headings = [len(m) for m in re.findall(r'^(#{1,6}) ', body, re.M)]
    return dict(
        headings_per_level={level: headings.count(level) for level in range(1, 5)},
        fences=len(fences),
        diagrams=fences.count('mermaid'),
        table_rows=len(re.findall(r'^\|.*\|\s*$', body, re.M)),
        repo_links=sorted(set(re.findall(r'\]\(((?:colab|tests|src|NOTICE|install)[^)#]*)\)', body))),
        numbered_steps=len(re.findall(r'^\s*\d+\. ', body, re.M)),
    )


class ReadmeSyncTests(unittest.TestCase):
    def test_all_three_languages_exist_and_link_each_other(self):
        for lang, path in FILES.items():
            self.assertTrue(path.is_file(), path)
            head = path.read_text(encoding='utf-8')[:400]
            for target in ('README.md', 'README.en.md', 'README.hu.md'):
                if target != path.name:
                    self.assertIn(f']({target})', head, f'{path.name} lacks a link to {target}')

    def test_structure_matches_across_languages(self):
        reference = structure(FILES['zh'].read_text(encoding='utf-8'))
        for lang in ('en', 'hu'):
            with self.subTest(language=lang):
                self.assertEqual(structure(FILES[lang].read_text(encoding='utf-8')), reference)


if __name__ == '__main__':
    unittest.main()
