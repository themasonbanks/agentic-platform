"""Verify independent connections serialize export and fence expired workers."""
import sqlite3
import tempfile
import unittest
from telemetry.events import EventStore


class ClaimTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = temporary.name + '/events.db'
        self.first_db = sqlite3.connect(path)
        self.addCleanup(self.first_db.close)
        self.second_db = sqlite3.connect(path)
        self.addCleanup(self.second_db.close)
        self.first, self.second = EventStore(self.first_db), EventStore(self.second_db)
        self.first.append('ci', 'event', 'owner/app', 'KEB-130', 'success', 1, {})
        self.worker_a, self.worker_b = 'a' * 32, 'b' * 32

    def test_claim_survives_recreation_and_excludes_other_worker(self):
        self.assertIsNotNone(self.first.claim_next(self.worker_a, 100, 100))
        self.assertIsNone(EventStore(self.second_db).claim_next(self.worker_b, 101, 100))
        self.assertFalse(self.second.acknowledge_claim('ci', 'event', self.worker_b, 102))
        self.assertTrue(self.first.acknowledge_claim('ci', 'event', self.worker_a, 103))
        self.assertEqual(self.second.pending(), [])

    def test_expired_worker_cannot_acknowledge_or_release_new_claim(self):
        self.first.claim_next(self.worker_a, 100, 100)
        self.assertFalse(self.first.acknowledge_claim('ci', 'event', self.worker_a, 200))
        self.assertIsNotNone(self.second.claim_next(self.worker_b, 200, 100))
        self.assertFalse(self.first.acknowledge_claim('ci', 'event', self.worker_a, 201))
        self.first.release_claim('ci', 'event', self.worker_a)
        self.assertIsNone(self.first.claim_next(self.worker_a, 202, 100))
        self.assertTrue(self.second.acknowledge_claim('ci', 'event', self.worker_b, 203))

    def test_failed_export_release_allows_immediate_retry(self):
        self.first.claim_next(self.worker_a, 100, 100)
        self.first.release_claim('ci', 'event', self.worker_a)
        self.assertIsNotNone(self.second.claim_next(self.worker_b, 101, 100))

    def test_lease_validation(self):
        for worker, now, lease in [('bad', 100, 100), (self.worker_a, -1, 100),
                                    (self.worker_a, 100, 0), (self.worker_a, 100, 300_000_000_001)]:
            with self.assertRaises(ValueError):
                self.first.claim_next(worker, now, lease)

    def test_quarantine_fences_expired_worker_and_survives_recreation(self):
        self.first.claim_next(self.worker_a, 100, 100)
        self.assertFalse(self.second.quarantine_claim('ci', 'event', self.worker_b, 101))
        self.assertIsNotNone(self.second.claim_next(self.worker_b, 200, 100))
        self.assertFalse(self.first.quarantine_claim('ci', 'event', self.worker_a, 201))
        self.assertTrue(self.second.quarantine_claim('ci', 'event', self.worker_b, 202))
        self.assertIsNone(EventStore(self.first_db).claim_next(self.worker_a, 1000, 100))
        self.assertEqual(len(self.first.pending()), 1)
        self.assertEqual(self.first_db.execute('SELECT exported FROM engineering_events').fetchone()[0], 0)
        self.assertEqual(self.first_db.execute('SELECT count(*) FROM engineering_export_quarantine').fetchone()[0], 1)
