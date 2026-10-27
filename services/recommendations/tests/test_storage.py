import tempfile
import unittest
from pathlib import Path
from storage import RecommendationStore


class StorageTests(unittest.TestCase):
    def test_reopen_replacement_isolation_and_older_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'data' / 'results.sqlite3'
            store = RecommendationStore(path)
            store.save(1, 2, 'Comedy', 3, {'recommendations': [{'id': 10}]})
            reopened = RecommendationStore(path)
            self.assertEqual(reopened.latest(1)['prompt'], 'Comedy')
            self.assertIsNone(reopened.latest(2))
            reopened.save(1, 1, 'obsolete', 3, {'recommendations': []})
            self.assertEqual(reopened.latest(1)['profile_revision'], 2)
            reopened.save(1, 3, '', 5, {'recommendations': []})
            self.assertEqual(store.latest(1)['result'], {'recommendations': []})
