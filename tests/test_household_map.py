import pathlib
import sys
import unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
from euthernet_cli import household_map

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
