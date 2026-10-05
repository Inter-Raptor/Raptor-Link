"""Raptor Link Core 3 - resilient, low-traffic WLED worker.

The realtime path is UDP-only. HTTP is deliberately excluded from the hot path:
a slow WLED web server must never freeze RGB streaming or presence handling.
"""
from __future__ import annotations

import copy
import json
import socket
import threading
import time


def _frame_bytes(frame):
    return bytes(v for pixel in frame for v in pixel)


def _packets(frame, timeout=3):
    raw = _frame_bytes(frame)
    if len(frame) <= 490:
        return [bytes([2, timeout]) + raw]
    output = []
    for start in range(0, len(frame), 480):
        chunk = frame[start:start + 480]
        output.append(
            bytes([4, timeout, start >> 8, start & 255])
            + bytes(v for pixel in chunk for v in pixel)
        )
    return output


class WledWorker:
    """One self-recovering worker per WLED.

    Rules:
    - realtime RGB is sent only when the image changes;
    - one heartbeat keeps WLED realtime alive when the image is static;
    - OFF/preset commands are UDP JSON and are reinforced at a very low rate;
    - every exception is contained inside this worker, never in the main engine.
    """

    HEARTBEAT_SECONDS = 0.90
    STATE_REINFORCE_SECONDS = 10.0
    WAKE_REINFORCE_SECONDS = 10.0
    RELEASE_REPEAT = 2
    CONTROL_REPEAT = 2

    def __init__(self, target, logger, diagnostics=None):
        self.lock = threading.RLock()
        self.done = threading.Event()
        self.log = logger
        self.diag = diagnostics
        self.target = copy.deepcopy(target)
        self.desired = {
            "mode": "hold",
            "reason": "démarrage",
            "frame": None,
            "fps": 12,
            "preset": 1,
        }
        self.mode_revision = 0
        self.state = "HOLD"
        self.detail = "En attente"
        self.packets_sent = 0
        self.control_packets = 0
        self.frames_sent = 0
        self.frames_skipped = 0
        self.restarts = 0
        self.last_udp = 0.0
        self.last_error = ""
        self.last_frame_sample = []
        self._socket = None
        self.thread = threading.Thread(
            target=self._supervisor,
            daemon=True,
            name="RaptorLink-Core3-WLED-" + str(target.get("ip", "")),
        )
        self.thread.start()

    def _event(self, level, category, message, **fields):
        if self.diag is not None:
            try:
                self.diag.event(level, category, message, **fields)
            except Exception:
                pass

    def update_target(self, target):
        with self.lock:
            self.target = copy.deepcopy(target)

    def _set(self, mode, reason="", **values):
        with self.lock:
            changed = mode != self.desired.get("mode")
            current = dict(self.desired)
            current.update(mode=mode, reason=str(reason)[:200], **values)
            if mode == "stream" and self.desired.get("mode") == "stream":
                # Frames change continuously; that is not a state transition.
                self.desired = current
                return
            if current != self.desired:
                self.desired = current
                self.mode_revision += 1
        if changed:
            self._event(
                "detailed",
                "WLED-STATE",
                self.target.get("name", "WLED") + " -> " + mode,
                ip=self.target.get("ip", ""),
                reason=reason,
            )

    def hold(self, reason="En attente"):
        self._set("hold", reason, frame=None)

    def stream(self, frame, fps=12, reason="Synchronisé"):
        clean = [
            tuple(max(0, min(255, int(v))) for v in pixel[:3])
            for pixel in frame
        ]
        self._set(
            "stream",
            reason,
            frame=clean,
            fps=max(1, min(20, int(fps))),
            preset=None,
        )

    def off(self, reason="Inactivité"):
        self._set("off", reason, frame=None, preset=None)

    def preset(self, preset, reason="Preset WLED"):
        self._set(
            "preset",
            reason,
            frame=None,
            preset=max(1, min(250, int(preset))),
        )

    def snapshot(self):
        with self.lock:
            return {
                "state": self.state,
                "detail": self.detail,
                "desired": self.desired.get("mode"),
                "reason": self.desired.get("reason", ""),
                "packets_sent": self.packets_sent,
                "control_packets": self.control_packets,
                "frames_sent": self.frames_sent,
                "frames_skipped": self.frames_skipped,
                "restarts": self.restarts,
                "last_udp": self.last_udp,
                "last_error": self.last_error,
                "frame_sample": copy.deepcopy(self.last_frame_sample),
            }

    def _open_socket(self):
        if self._socket is not None:
            try:
                self._socket.close()
            except Exception:
                pass
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.settimeout(0.2)

    def _send(self, data):
        target = copy.deepcopy(self.target)
        try:
            if self._socket is None:
                self._open_socket()
            self._socket.sendto(
                data,
                (target["ip"], int(target.get("port", 21324))),
            )
            self.last_udp = time.monotonic()
            self.last_error = ""
            self.packets_sent += 1
            return True
        except OSError as exc:
            self.last_error = str(exc)
            self._event(
                "normal",
                "WLED-UDP",
                target.get("name", "WLED") + " erreur UDP",
                ip=target.get("ip", ""),
                error=str(exc),
            )
            try:
                self._open_socket()
            except Exception:
                pass
            return False

    def _control(self, payload, repeat=None):
        repeat = self.CONTROL_REPEAT if repeat is None else max(1, int(repeat))
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ok = False
        for index in range(repeat):
            ok = self._send(raw) or ok
            self.control_packets += 1
            if index + 1 < repeat:
                self.done.wait(0.035)
        return ok

    def _release(self):
        ok = False
        for index in range(self.RELEASE_REPEAT):
            ok = self._send(bytes([2, 0])) or ok
            self.control_packets += 1
            if index + 1 < self.RELEASE_REPEAT:
                self.done.wait(0.025)
        return ok

    def _wake(self):
        # JSON-over-UDP is non-blocking and works even if /json/state is slow.
        return self._control({"live": False, "on": True, "tt": 0}, repeat=1)

    def _apply_off(self):
        self._release()
        self.done.wait(0.08)
        self._control({"live": False, "on": False, "tt": 0})

    def _apply_preset(self, preset):
        self._release()
        self.done.wait(0.08)
        self._control({"live": False, "on": True, "ps": int(preset), "tt": 0})

    def _sample(self, frame):
        if not frame:
            return []
        points = sorted(set([0, len(frame) // 4, len(frame) // 2, (3 * len(frame)) // 4, len(frame) - 1]))
        return [{"led": i + 1, "rgb": list(frame[i])} for i in points]

    def _supervisor(self):
        while not self.done.is_set():
            try:
                self._open_socket()
                self._run()
            except Exception as exc:
                self.restarts += 1
                self.last_error = str(exc)
                self.state = "RECOVERING"
                self.detail = "Récupération du worker"
                self._event(
                    "normal",
                    "WLED-WORKER",
                    self.target.get("name", "WLED") + " worker relancé",
                    ip=self.target.get("ip", ""),
                    error=str(exc),
                    restart=self.restarts,
                )
                self.done.wait(0.5)
        try:
            if self._socket is not None:
                self._socket.close()
        except Exception:
            pass

    def _run(self):
        applied_revision = -1
        previous_mode = None
        last_frame_raw = None
        next_frame_allowed = 0.0
        next_heartbeat = 0.0
        next_control = 0.0
        next_trace = 0.0

        while not self.done.is_set():
            with self.lock:
                desired = copy.deepcopy(self.desired)
                revision = self.mode_revision
            now = time.monotonic()
            mode = desired["mode"]

            if mode != previous_mode:
                last_frame_raw = None
                next_frame_allowed = 0.0
                next_heartbeat = 0.0
                next_control = 0.0
                previous_mode = mode

            if mode == "hold":
                self.state = "HOLD"
                self.detail = desired.get("reason", "En attente")
                applied_revision = revision
                self.done.wait(0.05)
                continue

            if mode == "off":
                if revision != applied_revision or now >= next_control:
                    self.state = "APPLYING_OFF"
                    self._apply_off()
                    applied_revision = revision
                    next_control = time.monotonic() + self.STATE_REINFORCE_SECONDS
                self.state = "OFF"
                self.detail = desired.get("reason", "Éteint")
                self.done.wait(0.05)
                continue

            if mode == "preset":
                if revision != applied_revision or now >= next_control:
                    self.state = "APPLYING_PRESET"
                    self._apply_preset(desired.get("preset", 1))
                    applied_revision = revision
                    next_control = time.monotonic() + self.STATE_REINFORCE_SECONDS
                self.state = "PRESET"
                self.detail = desired.get("reason", "Preset WLED")
                self.done.wait(0.05)
                continue

            if mode != "stream":
                raise RuntimeError("Mode WLED inconnu: " + str(mode))

            frame = desired.get("frame") or []
            if not frame:
                self.state = "WAIT_SOURCE"
                self.detail = "Source indisponible"
                self.done.wait(0.05)
                continue

            if revision != applied_revision or now >= next_control:
                self._wake()
                applied_revision = revision
                next_control = time.monotonic() + self.WAKE_REINFORCE_SECONDS

            raw = _frame_bytes(frame)
            changed = raw != last_frame_raw
            due_heartbeat = now >= next_heartbeat
            if now >= next_frame_allowed and (changed or due_heartbeat):
                ok = True
                for packet in _packets(frame, 3):
                    ok = self._send(packet) and ok
                if ok:
                    self.frames_sent += 1
                    self.last_frame_sample = self._sample(frame)
                    last_frame_raw = raw
                    next_heartbeat = time.monotonic() + self.HEARTBEAT_SECONDS
                    self.state = "STREAMING"
                    self.detail = desired.get("reason", "Synchronisé")
                    if now >= next_trace:
                        self._event(
                            "trace",
                            "WLED-UDP",
                            self.target.get("name", "WLED") + " flux Core 3",
                            ip=self.target.get("ip", ""),
                            frame_leds=len(frame),
                            fps_limit=desired.get("fps", 12),
                            sample=self.last_frame_sample,
                            packets=self.packets_sent,
                            skipped=self.frames_skipped,
                        )
                        next_trace = now + 2.0
                next_frame_allowed = time.monotonic() + 1.0 / max(1, int(desired.get("fps", 12)))
            elif not changed:
                self.frames_skipped += 1

            self.done.wait(0.005)

    def close(self):
        self.done.set()
        self.thread.join(timeout=3)
