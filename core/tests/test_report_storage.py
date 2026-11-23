import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from zhihu_agent.recommend import ReadingStore


class ReportStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ReadingStore(Path(self.temp.name) / 'reading')
        self.report = {'run_id': '20261104T010000Z-1234abcd', 'recommendations': [], 'status': 'original'}

    def partial_write(self):
        original_open = Path.open
        runs = self.store.root / 'runs'

        class BrokenWriter:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()

            def write(self, text):
                self.stream.write(text[:12])
                self.stream.flush()
                raise OSError('Simulated disk failure after partial write')

        def open_file(path, mode='r', *args, **kwargs):
            stream = original_open(path, mode, *args, **kwargs)
            return BrokenWriter(stream) if path.parent == runs and mode in {'w', 'x'} else stream

        return patch.object(Path, 'open', open_file)

    def test_partial_write_preserves_existing_report(self):
        path = self.store.save(self.report)
        before = path.read_bytes()
        with self.partial_write(), self.assertRaises(OSError):
            self.store.save(dict(self.report, status='replacement'))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.store.reports(), [self.report])
        self.assertEqual(list(path.parent.glob('*.tmp')), [])

    def test_failed_new_report_is_not_visible_to_history(self):
        with self.partial_write(), self.assertRaises(OSError):
            self.store.save(self.report)
        self.assertEqual(self.store.reports(), [])
        self.assertEqual(list((self.store.root / 'runs').glob('*.tmp')), [])

    def test_failed_atomic_replace_preserves_old_report(self):
        path = self.store.save(self.report)
        with patch.object(Path, 'replace', side_effect=PermissionError('Simulated locked target')), self.assertRaises(PermissionError):
            self.store.save(dict(self.report, status='replacement'))
        self.assertEqual(json.loads(path.read_text(encoding='utf-8')), self.report)
        self.assertEqual(list(path.parent.glob('*.tmp')), [])

    def test_run_id_cannot_escape_report_directory(self):
        for run_id in ('../outside', 'x/y', 'C:/outside', '', None, 7):
            with self.subTest(run_id=run_id), patch.object(Path, 'open', side_effect=AssertionError('Invalid ID reached filesystem')), self.assertRaises(ValueError):
                self.store.save(dict(self.report, run_id=run_id))

    def test_complete_report_is_visible_only_after_flush_and_replace(self):
        original_replace = Path.replace
        observed = []

        def check_before_replace(temporary, destination):
            observed.append(json.loads(temporary.read_text(encoding='utf-8')))
            self.assertEqual(self.store.reports(), [])
            return original_replace(temporary, destination)

        with patch.object(Path, 'replace', check_before_replace), patch('zhihu_agent.recommend.os.fsync') as sync:
            path = self.store.save(self.report)
        self.assertEqual(observed, [self.report])
        sync.assert_called_once()
        self.assertEqual(self.store.reports(), [self.report])
        self.assertEqual(path.name, self.report['run_id'] + '.json')

    def test_exclusive_open_failure_does_not_remove_another_tempfile(self):
        directory = self.store.root / 'runs'
        directory.mkdir(parents=True)
        temporary = directory / f".{self.report['run_id']}-collision.tmp"
        temporary.write_text('another writer', encoding='utf-8')
        with patch('zhihu_agent.recommend.uuid4', return_value=SimpleNamespace(hex='collision')), self.assertRaises(FileExistsError):
            self.store.save(self.report)
        self.assertEqual(temporary.read_text(encoding='utf-8'), 'another writer')
        self.assertEqual(self.store.reports(), [])


if __name__ == '__main__':
    unittest.main()
