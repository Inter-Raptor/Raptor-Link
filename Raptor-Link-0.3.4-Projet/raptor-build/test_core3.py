import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str((ROOT / "RaptorLink").resolve()))

from core3_engine import Engine, _rainbow, validate
from core3_wled import WledWorker


def target():
    return {
        "name": "Logo",
        "ip": "192.168.1.80",
        "count": 12,
        "port": 21324,
    }


def wait_for(predicate, timeout=1.5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


class Core3Tests(unittest.TestCase):
    def test_old_configuration_migrates_to_one_simple_source(self):
        raw = {
            "settings": {"idle_seconds": 60, "fps": 25},
            "targets": [{
                "name": "Logo",
                "ip": "192.168.1.80",
                "count": 160,
                "port": 21324,
                "enabled": True,
                "brightness": 80,
                "idle_seconds": None,
                "on_stop": "restore",
                "preset": 2,
                "routes": [{
                    "source": "icue",
                    "device": "kbd",
                    "device_model": "K95",
                    "device_serial": "abc",
                    "ids": [1, 2, 3],
                    "mapping": "stretch",
                    "reverse": True,
                    "start": 1,
                    "end": 160,
                }],
            }],
        }
        cfg = validate(raw)
        self.assertEqual(cfg["settings"]["fps"], 20)
        t = cfg["targets"][0]
        self.assertEqual(t["source"], "icue")
        self.assertEqual(t["device"], "kbd")
        self.assertEqual(t["ids"], [1, 2, 3])
        self.assertTrue(t["reverse"])
        self.assertEqual(t["on_stop"], "off")

    def test_old_wled_preset_migrates(self):
        raw = {
            "targets": [{
                "name": "Logo",
                "ip": "192.168.1.80",
                "count": 12,
                "routes": [{
                    "source": "wled_preset",
                    "wled_preset": 17,
                    "device": "",
                    "ids": [],
                }],
            }],
        }
        cfg = validate(raw)
        self.assertEqual(cfg["targets"][0]["source"], "wled_preset")
        self.assertEqual(cfg["targets"][0]["source_preset"], 17)

    def test_rainbow_contains_real_rgb_range(self):
        frame = _rainbow(60, 0)
        self.assertEqual(len(frame), 60)
        self.assertGreater(len(set(frame)), 30)
        self.assertTrue(any(r > 220 and g < 40 and b < 40 for r, g, b in frame))
        self.assertTrue(any(g > 220 and r < 40 and b < 40 for r, g, b in frame))
        self.assertTrue(any(b > 220 and r < 40 and g < 40 for r, g, b in frame))

    def test_static_realtime_frame_is_deduplicated(self):
        sent = []
        with patch.object(WledWorker, "_send", lambda self, data: sent.append(bytes(data)) or setattr(self, "packets_sent", self.packets_sent + 1) or True):
            worker = WledWorker(target(), lambda *a, **k: None, None)
            try:
                worker.HEARTBEAT_SECONDS = 5.0
                worker.WAKE_REINFORCE_SECONDS = 60.0
                worker.stream([(1, 2, 3)] * 12, 20, "test")
                self.assertTrue(wait_for(lambda: worker.snapshot()["frames_sent"] >= 1))
                first = worker.snapshot()["frames_sent"]
                time.sleep(0.25)
                second = worker.snapshot()["frames_sent"]
                self.assertEqual(first, 1)
                self.assertEqual(second, 1)
                self.assertGreater(worker.snapshot()["frames_skipped"], 0)
            finally:
                worker.close()

    def test_changed_frame_is_sent_without_waiting_for_heartbeat(self):
        sent = []
        with patch.object(WledWorker, "_send", lambda self, data: sent.append(bytes(data)) or setattr(self, "packets_sent", self.packets_sent + 1) or True):
            worker = WledWorker(target(), lambda *a, **k: None, None)
            try:
                worker.HEARTBEAT_SECONDS = 10.0
                worker.stream([(1, 2, 3)] * 12, 20, "test")
                self.assertTrue(wait_for(lambda: worker.snapshot()["frames_sent"] >= 1))
                worker.stream([(4, 5, 6)] * 12, 20, "test")
                self.assertTrue(wait_for(lambda: worker.snapshot()["frames_sent"] >= 2))
            finally:
                worker.close()

    def test_off_and_preset_use_small_redundant_udp_commands(self):
        packets = []
        with patch.object(WledWorker, "_send", lambda self, data: packets.append(bytes(data)) or setattr(self, "packets_sent", self.packets_sent + 1) or True):
            worker = WledWorker(target(), lambda *a, **k: None, None)
            try:
                worker.STATE_REINFORCE_SECONDS = 60.0
                worker.off("idle")
                self.assertTrue(wait_for(lambda: worker.snapshot()["state"] == "OFF"))
                off_count = len(packets)
                self.assertGreaterEqual(off_count, 4)
                worker.preset(7, "preset")
                self.assertTrue(wait_for(lambda: worker.snapshot()["state"] == "PRESET"))
                self.assertGreater(len(packets), off_count)
            finally:
                worker.close()

    def test_saving_does_not_cancel_requested_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine(Path(tmp) / "config.json", demo=True)
            try:
                engine.want_run = True
                cfg = validate({
                    "settings": {"idle_seconds": 300, "fps": 12},
                    "targets": [],
                })
                engine.save(cfg)
                self.assertTrue(engine.want_run)
            finally:
                engine.shutdown()


if __name__ == "__main__":
    unittest.main()
