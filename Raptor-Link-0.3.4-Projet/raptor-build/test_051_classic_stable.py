import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "RaptorLink"
sys.path.insert(0, str(APP))

from engine import Engine, packets, udp_json, validate


def base_target(route):
    return {
        "name": "Logo",
        "ip": "192.168.1.80",
        "count": 160,
        "port": 21324,
        "enabled": True,
        "brightness": 100,
        "idle_seconds": None,
        "on_stop": "off",
        "preset": 1,
        "routes": [route],
    }


def route(source="icue"):
    return {
        "source": source,
        "device": "device-1",
        "device_model": "K95",
        "device_serial": "abc",
        "ids": [1, 2, 3],
        "start": 1,
        "end": 160,
        "mapping": "stretch",
        "reverse": False,
    }


class FakeSocket:
    def __init__(self):
        self.calls = []

    def sendto(self, data, address):
        self.calls.append((bytes(data), address))
        return len(data)


class ClassicStableTests(unittest.TestCase):
    def test_experimental_rgb_is_forced_off(self):
        cfg = validate({
            "settings": {"experimental_rgb": True},
            "targets": [base_target(route("msi"))],
        })
        self.assertFalse(cfg["settings"]["experimental_rgb"])
        migrated = cfg["targets"][0]["routes"][0]
        self.assertEqual(migrated["source"], "icue")
        self.assertEqual(migrated["device"], "")
        self.assertEqual(migrated["ids"], [])

    def test_openrgb_route_is_migrated_to_safe_icue_placeholder(self):
        cfg = validate({
            "targets": [base_target(route("openrgb"))],
        })
        migrated = cfg["targets"][0]["routes"][0]
        self.assertEqual(migrated["source"], "icue")
        self.assertEqual(migrated["device"], "")
        self.assertEqual(migrated["ids"], [])

    def test_normal_wled_sizes_use_one_realtime_udp_packet(self):
        self.assertEqual(len(packets([(1, 2, 3)] * 160, 3)), 1)
        self.assertEqual(len(packets([(1, 2, 3)] * 257, 3)), 1)
        self.assertEqual(len(packets([(1, 2, 3)] * 600, 3)), 2)

    def test_udp_json_repeats_small_state_command(self):
        sock = FakeSocket()
        self.assertTrue(udp_json(sock, "192.168.1.80", 21324, {"on": False}, 2))
        self.assertEqual(len(sock.calls), 2)
        self.assertTrue(all(addr == ("192.168.1.80", 21324) for _, addr in sock.calls))
        self.assertTrue(all(len(data) < 100 for data, _ in sock.calls))

    def test_save_does_not_disable_manual_sync_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(Engine, "loop", lambda self: self.done.wait()):
                engine = Engine(Path(tmp) / "config.json", demo=True)
                try:
                    engine.want_run = True
                    engine.save({"settings": {}, "targets": []})
                    self.assertTrue(engine.want_run)
                finally:
                    engine.shutdown()

    def test_classic_interface_is_kept(self):
        html = (APP / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="page-lights"', html)
        self.assertIn('id="page-automation"', html)
        self.assertIn('id="page-settings"', html)
        self.assertIn("Raptor Link", html)

    def test_experimental_connector_modules_are_removed(self):
        self.assertFalse((APP / "msi_source.py").exists())
        self.assertFalse((APP / "msi_sdk.py").exists())
        self.assertFalse((APP / "msi_worker.py").exists())
        self.assertFalse((APP / "openrgb_source.py").exists())

    def test_engine_has_no_experimental_workers_or_http_health_gate(self):
        source = (APP / "engine.py").read_text(encoding="utf-8")
        self.assertNotIn("OpenRGBWorker", source)
        self.assertNotIn("MsiWorker", source)
        loop = source.split("    def loop(self):", 1)[1]
        self.assertNotIn("probe_target(", loop)
        self.assertIn("heartbeat=start-self.last_send.get(ip,0)>=.9", loop)
        self.assertIn("start-self.last_off.get(ip,0)>=30", loop)


if __name__ == "__main__":
    unittest.main()
