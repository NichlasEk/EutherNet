import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location(
    "reports", pathlib.Path(__file__).parents[1] / "scripts/scryer_reports.py"
)
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def world(status, stamp="2026-10-10T10:00:00Z"):
    return {"nodes": [{"id": "vault", "status": status}], "edges": [], "stamp": stamp}


class ReportTests(unittest.TestCase):
    def test_dedup_ack_recovery_and_recurrence(self):
        values = []
        r.update(values, world("failed"), 1)
        r.control(values, "report-ack", "vault", 2)
        r.update(values, world("failed"), 3)
        self.assertEqual(len(values), 1)
        self.assertTrue(values[0]["acknowledged"])
        r.update(values, world("unknown"), 4)
        self.assertEqual(values[0]["state"], "unconfirmed")
        r.update(values, world("healthy", "2026-10-10T11:00:00Z"), 5)
        self.assertEqual(values[0]["state"], "resolved")
        r.update(values, world("failed", "2026-10-10T12:00:00Z"), 6)
        self.assertEqual(values[0]["episode"], 2)
        self.assertFalse(values[0]["acknowledged"])

    def test_soft_fault_needs_two_snapshots(self):
        values = []
        for _ in range(5):
            r.update(values, world("degraded"), 1)
        self.assertEqual(values[0]["state"], "pending")
        r.update(values, world("degraded", "2026-10-10T11:00:00Z"), 2)
        self.assertEqual(values[0]["state"], "open")

    def test_snooze_persists_and_expires_without_new_issue(self):
        values = []
        w = world("failed")
        r.update(values, w, 1)
        r.control(values, "report-snooze", "vault", 2)
        restored = r.restore(values)
        self.assertFalse(r.public(restored, w, 3)[0]["needs_attention"])
        self.assertTrue(r.public(restored, w, 3600003)[0]["needs_attention"])
        with self.assertRaises(ValueError):
            r.control(values, "restart", "vault", 5)

    def test_removed_node_is_not_claimed_recovered(self):
        values = []
        r.update(values, world("failed"), 1)
        r.update(values, {"nodes": [], "edges": [], "stamp": "x"}, 2)
        self.assertEqual(values[0]["state"], "unconfirmed")

    def test_memory_bounded(self):
        values = []
        w = {
            "nodes": [{"id": f"n{i}", "status": "failed"} for i in range(100)],
            "edges": [],
            "stamp": "2026-10-10T10:00:00Z",
        }
        r.update(values, w, 1)
        self.assertEqual(len(values), 64)
