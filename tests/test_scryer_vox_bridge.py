import importlib.util
import io
import json
import pathlib
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "bridge", pathlib.Path(__file__).parents[1] / "scripts/scryer_vox_bridge.py"
)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class BridgeTests(unittest.TestCase):
    def test_unauthorized_operations_never_reach_runtime(self):
        with patch.object(bridge.urllib.request, "urlopen") as transport:
            for command in (
                "restart",
                "pause",
                "disable",
                "dismiss",
                "investigate",
                "shell",
            ):
                with self.assertRaises(ValueError):
                    bridge.request({"command": command})
            transport.assert_not_called()

    def test_export_has_no_topology_or_conversation_content(self):
        snapshot = {
            "state": {"paused": False, "disabled": False, "private": "excluded"},
            "world": {"stamp": "2026-10-10T07:00:00Z", "nodes": ["excluded"]},
            "diagnostics": {"error": "excluded detail"},
            "reports": [],
            "updated_at": 5,
        }
        with patch.object(
            bridge.urllib.request,
            "urlopen",
            return_value=io.BytesIO(json.dumps(snapshot).encode()),
        ):
            result = bridge.request({"command": "list"})
        self.assertEqual(
            set(result),
            {
                "available",
                "reports",
                "inventory_at",
                "paused",
                "disabled",
                "observer_error",
                "fetched_at",
            },
        )
        self.assertNotIn("excluded", json.dumps(result))
