import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location(
    "mirror", pathlib.Path(__file__).parents[1] / "scripts/mirror_health.py"
)
mirror = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mirror)


class MirrorHealthTests(unittest.TestCase):
    def report(self):
        return {
            "ok": True,
            "all_checksums_ok": True,
            "timer_enabled": True,
            "timer_active": True,
            "directory": "PRIVATE",
            "latest_file": "PRIVATE",
            "errors": ["PRIVATE"],
            "datasets": {
                k: {"latest_epoch": 99000, "backup_count": 2, "latest_file": "PRIVATE"}
                for k in ("accounts", "state", "media")
            },
        }

    def test_projects_only_public_metadata(self):
        value = mirror.summarize(self.report(), 99999, 100000)
        self.assertEqual(value["status"], "healthy")
        self.assertNotIn("PRIVATE", str(value))

    def test_missing_and_stale_check_are_unknown(self):
        self.assertEqual(mirror.summarize(None, 0, 100000)["status"], "unknown")
        self.assertEqual(
            mirror.summarize(self.report(), 50000, 100000)["status"], "unknown"
        )
        self.assertEqual(
            mirror.summarize(self.report(), 100100, 100000)["status"], "unknown"
        )

    def test_checksums_and_backup_age_fail(self):
        value = self.report()
        value["all_checksums_ok"] = False
        self.assertEqual(mirror.summarize(value, 99999, 100000)["status"], "failed")
        value = self.report()
        value["datasets"]["state"]["latest_epoch"] = 1
        self.assertEqual(mirror.summarize(value, 199999, 200000)["status"], "failed")
