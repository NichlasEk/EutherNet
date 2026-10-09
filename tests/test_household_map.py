import pathlib
import sys
import unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
from euthernet_cli import household_map, map_sync_status

class HouseholdMapTests(unittest.TestCase):
    def test_failed_sync_does_not_claim_robot_offline_or_vault_protected(self):
        snapshot = {'collectors': {'systemd_user': {
            'running_services': {'stdout': 'eutherwash.service loaded active running Wash'},
            'failed_services': {'stdout': '● eutherwash-vacuum-map-sync.service loaded failed failed Sync'},
        }}}
        nodes, edges = household_map(snapshot)
        by_id = {n['id']: n for n in nodes}
        self.assertEqual(by_id['eutherwash']['status'], 'running')
        self.assertEqual(by_id['ebba-map-sync']['status'], 'failed')
        self.assertEqual(by_id['ebba']['status'], 'configured')
        self.assertEqual(by_id['euthervault']['status'], 'unknown')
        self.assertFalse(any(e['type'] == 'backup' for e in edges))

    def test_missing_inventory_is_unknown(self):
        nodes, _ = household_map({})
        self.assertEqual(next(n for n in nodes if n['id'] == 'eutherwash')['status'], 'unknown')

class MapSyncStatusTests(unittest.TestCase):
    def status(self, **props):
        values = dict(LoadState="loaded", ActiveState="inactive", Result="success", ExecMainStatus="0", ExecMainExitTimestamp="Fri 2026-10-09 20:00:00 CEST")
        values.update(props)
        return map_sync_status({"collectors": {"systemd_user": {"map_sync_status": {"ok": True, "stdout": "\n".join(f"{k}={v}" for k,v in values.items())}}}})

    def test_successful_oneshot_is_healthy_while_inactive(self):
        self.assertEqual(self.status(), "healthy")

    def test_failed_import_is_failed(self):
        self.assertEqual(self.status(ActiveState="failed", Result="exit-code", ExecMainStatus="1"), "failed")

    def test_in_flight_and_unobserved_are_not_success(self):
        self.assertEqual(self.status(ActiveState="activating"), "observed")
        self.assertEqual(self.status(ExecMainExitTimestamp=""), "unknown")
        self.assertEqual(self.status(LoadState="not-found"), "unknown")
        self.assertEqual(map_sync_status({}), "unknown")
