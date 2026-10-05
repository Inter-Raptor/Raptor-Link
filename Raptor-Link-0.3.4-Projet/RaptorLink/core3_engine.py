"""Raptor Link Core 3 - intentionally small, supervised synchronization engine.

Core 3 keeps only the reliable path:
- Corsair iCUE -> WLED
- WLED autonomous preset
- simple rainbow / solid local source
- global or per-light inactivity
- start with Windows / automatic synchronization
- persistent diagnostics

No screen capture, audio, alarms, profiles, MSI/OpenRGB or animation smoothing
is part of the Core 3 runtime.
"""
from __future__ import annotations

import colorsys
import copy
import ctypes as C
import ipaddress
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import urllib.request

from diagnostics import Diagnostics
from core3_wled import WledWorker


HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))

DEFAULT = {
    "version": 3,
    "settings": {
        "idle_seconds": 300,
        "fps": 12,
        "auto_sync": False,
        "startup": False,
        "check_updates": True,
        "diagnostic_level": "normal",
        "diagnostic_days": 7,
        "diagnostic_max_mb": 50,
    },
    "targets": [],
}


def address(value):
    ip = ipaddress.ip_address(str(value))
    if ip.version != 4 or not (
        ip.is_private
        and not ip.is_loopback
        and not ip.is_unspecified
        and not ip.is_multicast
    ):
        raise ValueError("Une adresse IPv4 locale est nécessaire.")
    return str(ip)


def http(ip, path, payload=None, timeout=2.0):
    ip = address(ip)
    request = urllib.request.Request(
        "http://" + ip + path,
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with HTTP.open(request, timeout=timeout) as response:
        return json.load(response)


def _int(value, low, high, label):
    value = int(value)
    if not low <= value <= high:
        raise ValueError(label + " hors limites")
    return value


def _color(value):
    if not isinstance(value, list) or len(value) != 3:
        return [255, 100, 20]
    return [_int(v, 0, 255, "Couleur") for v in value]


def _legacy_source(target):
    routes = target.get("routes") if isinstance(target.get("routes"), list) else []
    route = routes[0] if routes else {}
    source = str(target.get("source", route.get("source", "icue")))
    if source not in ("icue", "wled_preset", "rainbow", "solid"):
        # Preserve an existing iCUE association when migrating old experimental
        # configurations; otherwise fall back to a harmless static source.
        source = "icue" if (route.get("device") and route.get("ids")) else "solid"
    return source, route


def validate(raw):
    if not isinstance(raw, dict):
        raise ValueError("Configuration invalide")

    cfg = copy.deepcopy(DEFAULT)
    settings = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
    for key in ("idle_seconds", "fps", "auto_sync", "startup", "check_updates",
                "diagnostic_level", "diagnostic_days", "diagnostic_max_mb"):
        if key in settings:
            cfg["settings"][key] = settings[key]

    s = cfg["settings"]
    s["idle_seconds"] = _int(s.get("idle_seconds", 300), 0, 86400, "Inactivité")
    # Old Core 2 configurations allowed up to 40 FPS. Core 3 intentionally
    # caps the migrated value instead of rejecting an otherwise valid config.
    s["fps"] = max(1, min(20, int(s.get("fps", 12))))
    s["auto_sync"] = bool(s.get("auto_sync", False))
    s["startup"] = bool(s.get("startup", False))
    s["check_updates"] = bool(s.get("check_updates", True))
    s["diagnostic_level"] = str(s.get("diagnostic_level", "normal"))
    if s["diagnostic_level"] not in ("off", "normal", "detailed", "trace"):
        s["diagnostic_level"] = "normal"
    s["diagnostic_days"] = _int(s.get("diagnostic_days", 7), 1, 30, "Rétention")
    s["diagnostic_max_mb"] = _int(s.get("diagnostic_max_mb", 50), 5, 500, "Taille journal")

    targets = raw.get("targets", [])
    if not isinstance(targets, list) or len(targets) > 32:
        raise ValueError("32 éclairages maximum")

    seen = set()
    for original in targets:
        if not isinstance(original, dict):
            continue
        source, route = _legacy_source(original)
        ip = address(original.get("ip", ""))
        if ip in seen:
            raise ValueError("Une seule fiche par adresse WLED.")
        seen.add(ip)

        count = _int(original.get("count", 60), 1, 10000, "Nombre de LED")
        port = _int(original.get("port", 21324), 1, 65535, "Port UDP")
        brightness = _int(original.get("brightness", 100), 0, 100, "Luminosité")
        idle = original.get("idle_seconds", None)
        idle = None if idle is None else _int(idle, 0, 86400, "Inactivité")

        ids = original.get("ids", route.get("ids", []))
        if not isinstance(ids, list):
            ids = []
        ids = list(dict.fromkeys(int(v) for v in ids if isinstance(v, (int, float)) and not isinstance(v, bool)))
        ids = [v for v in ids if 0 <= v <= 0xFFFFFFFF][:512]

        mapping = str(original.get("mapping", route.get("mapping", "stretch")))
        if mapping not in ("stretch", "repeat"):
            mapping = "stretch"

        on_stop = str(original.get("on_stop", "off"))
        if on_stop not in ("off", "preset"):
            on_stop = "off"

        target = {
            "name": str(original.get("name", "WLED"))[:70],
            "ip": ip,
            "count": count,
            "port": port,
            "enabled": bool(original.get("enabled", True)),
            "brightness": brightness,
            "idle_seconds": idle,
            "on_stop": on_stop,
            "stop_preset": _int(original.get("preset", original.get("stop_preset", 1)), 1, 250, "Preset arrêt"),
            "source": source,
            "source_preset": _int(original.get("source_preset", route.get("wled_preset", 1)), 1, 250, "Preset source"),
            "device": str(original.get("device", route.get("device", "")))[:128],
            "device_model": str(original.get("device_model", route.get("device_model", "")))[:160],
            "device_serial": str(original.get("device_serial", route.get("device_serial", "")))[:160],
            "ids": ids,
            "mapping": mapping,
            "reverse": bool(original.get("reverse", route.get("reverse", False))),
            "color": _color(original.get("color", route.get("color", [255, 100, 20]))),
        }
        cfg["targets"].append(target)

    return cfg


class IcueBridge:
    """Supervised subprocess around the native Corsair SDK.

    If the SDK blocks, only the child process is killed and restarted.
    The WLED workers keep their last valid frame meanwhile.
    """

    def __init__(self, logger):
        self.log = logger
        self.lock = threading.RLock()
        self.done = threading.Event()
        self.process = None
        self.required = set()
        self.fps = 12
        self.force_scan = True
        self.devices = []
        self.colors = {}
        self.error = ""
        self.busy_since = None
        self.last_progress = time.monotonic()
        self.restarts = 0
        self.thread = threading.Thread(target=self._loop, daemon=True, name="RaptorLink-Core3-iCUE")
        self.thread.start()

    def _command(self):
        root = Path(__file__).resolve().parent
        runtime = root / "runtime" / "python.exe"
        python = str(runtime if runtime.exists() else Path(os.sys.executable))
        return [python, "-u", str(root / "icue_worker.py")]

    def configure(self, required, fps, force_scan=False):
        with self.lock:
            self.required = {str(x) for x in required if x}
            self.fps = max(1, min(20, int(fps)))
            if force_scan:
                self.force_scan = True

    def snapshot(self):
        kill = None
        now = time.monotonic()
        with self.lock:
            stalled = self.busy_since is not None and now - self.busy_since > 4.0
            if stalled and self.process is not None and self.process.poll() is None:
                kill = self.process
            data = {
                "devices": copy.deepcopy(self.devices),
                "colors": copy.deepcopy(self.colors),
                "error": self.error,
                "stalled": stalled,
                "restarts": self.restarts,
                "age": max(0.0, now - self.last_progress),
            }
        if kill is not None:
            try:
                kill.kill()
            except Exception:
                pass
        return data

    def _dispose(self, process):
        if process is None:
            return
        if process.poll() is None:
            try:
                process.kill()
            except Exception:
                pass
        try:
            process.wait(timeout=0.5)
        except Exception:
            pass
        for stream in (process.stdin, process.stdout):
            try:
                if stream:
                    stream.close()
            except Exception:
                pass
        with self.lock:
            if self.process is process:
                self.process = None

    def _start(self):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        process = subprocess.Popen(
            self._command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
            cwd=str(Path(__file__).resolve().parent),
            creationflags=flags,
        )
        with self.lock:
            self.process = process
            self.restarts += 1
        return process

    def _publish(self, data=None, error=None):
        with self.lock:
            if data is not None:
                self.devices = copy.deepcopy(data.get("devices", []))
                self.colors = {
                    str(device): {int(k): tuple(v) for k, v in values.items()}
                    for device, values in data.get("colors", {}).items()
                }
                self.error = str(data.get("error", ""))
            if error is not None:
                self.error = str(error)
            self.last_progress = time.monotonic()

    def _loop(self):
        process = None
        next_start = 0.0
        last_scan = 0.0
        while not self.done.is_set():
            try:
                now = time.monotonic()
                if process is None or process.poll() is not None:
                    if now < next_start:
                        self.done.wait(min(0.2, next_start - now))
                        continue
                    process = self._start()
                    last_scan = 0.0

                with self.lock:
                    required = sorted(self.required)
                    fps = self.fps
                    force_scan = self.force_scan
                    self.force_scan = False
                scan = force_scan or now - last_scan >= 10.0
                request = {"required": required, "scan": scan}

                with self.lock:
                    self.busy_since = time.monotonic()
                process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
                process.stdin.flush()
                line = process.stdout.readline()
                with self.lock:
                    self.busy_since = None
                if not line:
                    raise RuntimeError("pont iCUE interrompu")

                data = json.loads(line)
                if scan:
                    last_scan = time.monotonic()
                self._publish(data=data)
                if not data.get("ok") and data.get("fatal"):
                    raise RuntimeError(data.get("error") or "erreur iCUE")
                self.done.wait(1.0 / max(1, fps))
            except Exception as exc:
                with self.lock:
                    self.busy_since = None
                self._publish(error=exc)
                self._dispose(process)
                process = None
                next_start = time.monotonic() + 1.0
        self._dispose(process)

    def close(self):
        self.done.set()
        with self.lock:
            process = self.process
        self._dispose(process)
        self.thread.join(timeout=2)


class WinIdle:
    """Use Windows' own last-input clock only.

    No mouse-coordinate heuristic and no polling of every key: this is the
    same simple mechanism that proved reliable in the older Raptor Link path.
    """

    class Info(C.Structure):
        _fields_ = [("size", C.c_uint32), ("tick", C.c_uint32)]

    def __init__(self):
        self.user = C.WinDLL("user32", use_last_error=True)
        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        self.user.GetLastInputInfo.argtypes = [C.POINTER(self.Info)]
        self.user.GetLastInputInfo.restype = C.c_int
        self.kernel.GetTickCount.argtypes = []
        self.kernel.GetTickCount.restype = C.c_uint32

    def seconds(self):
        info = self.Info(C.sizeof(self.Info), 0)
        if not self.user.GetLastInputInfo(C.byref(info)):
            raise C.WinError(C.get_last_error())
        return ((self.kernel.GetTickCount() - info.tick) & 0xFFFFFFFF) / 1000.0


def _rainbow(count, seconds):
    phase = seconds * 0.08
    return [
        tuple(round(v * 255) for v in colorsys.hsv_to_rgb((i / max(1, count) + phase) % 1.0, 1.0, 1.0))
        for i in range(count)
    ]


def _gain(frame, brightness):
    factor = max(0, min(100, int(brightness))) / 100.0
    return [tuple(round(v * factor) for v in pixel) for pixel in frame]


def _map_icue(target, colors):
    source = colors.get(target.get("device", ""), {})
    ids = target.get("ids", [])
    if not ids or any(i not in source for i in ids):
        return None
    ordered = [source[i] for i in ids]
    if target.get("reverse"):
        ordered.reverse()
    count = target["count"]
    if target.get("mapping") == "repeat":
        frame = [ordered[i % len(ordered)] for i in range(count)]
    else:
        frame = [
            ordered[min(len(ordered) - 1, i * len(ordered) // count)]
            for i in range(count)
        ]
    return _gain(frame, target["brightness"])


class Engine:
    def __init__(self, path, demo=False):
        self.path = Path(path)
        self.demo = bool(demo)
        self.lock = threading.RLock()
        self.done = threading.Event()
        self.config = validate(DEFAULT)
        self.revision = 0
        self.want_run = False
        self.running = False
        self.run_revision = -1
        self.run_settings = copy.deepcopy(DEFAULT["settings"])
        self.active = []
        self.workers = {}
        self.devices = []
        self.colors = {}
        self.idle = 0.0
        self.keep_awake = False
        self.status = "Démarrage"
        self.target_status = {}
        self.logs = []
        self.last_frames = {}
        self.tests = {}
        self.loop_heartbeat = time.monotonic()
        self.engine_restarts = 0
        self.scan_requested = True

        config_error = ""
        try:
            if self.path.exists():
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                self.config = validate(loaded)
        except Exception as exc:
            config_error = "Configuration non chargée : " + str(exc)

        settings = self.config["settings"]
        self.diag = Diagnostics(
            self.path.parent / "diagnostics",
            settings.get("diagnostic_level", "normal"),
            settings.get("diagnostic_days", 7),
            settings.get("diagnostic_max_mb", 50),
        )
        if config_error:
            self.log(config_error, "CONFIG")
        self.want_run = bool(settings.get("auto_sync"))
        self.icue = None if self.demo else IcueBridge(self.log)
        self.diag.event("normal", "ENGINE", "Raptor Link Core 3 démarré", auto_sync=self.want_run)
        self.thread = threading.Thread(target=self._supervisor, daemon=True, name="RaptorLink-Core3")
        self.thread.start()

    def log(self, message, category="ENGINE", level="normal", **fields):
        with self.lock:
            self.logs.append(time.strftime("%H:%M:%S") + "  " + str(message))
            self.logs = self.logs[-100:]
        try:
            self.diag.event(level, category, str(message), **fields)
        except Exception:
            pass

    def worker(self, target):
        ip = target["ip"]
        worker = self.workers.get(ip)
        if worker is None and not self.demo:
            worker = WledWorker(target, self.log, self.diag)
            self.workers[ip] = worker
            self.log(target["name"] + " : worker Core 3 créé", "WLED-STATE", "detailed", ip=ip)
        elif worker is not None:
            worker.update_target(target)
        return worker

    def snapshot(self):
        with self.lock:
            state = {
                "devices": copy.deepcopy(self.devices),
                "running": self.running,
                "requested": self.want_run,
                "status": self.status,
                "logs": self.logs[-20:],
                "idle": int(self.idle),
                "keep_awake": self.keep_awake,
                "targets": copy.deepcopy(self.target_status),
                "revision": self.revision,
                "engine_age": max(0.0, time.monotonic() - self.loop_heartbeat),
                "engine_restarts": self.engine_restarts,
                "icue": self.icue.snapshot() if self.icue else {},
                "wled_workers": {ip: worker.snapshot() for ip, worker in self.workers.items()},
            }
        return state

    def diagnostic_report(self, minutes=30):
        state = self.snapshot()
        context = {
            "core": "3",
            "running": state["running"],
            "requested": state["requested"],
            "status": state["status"],
            "idle_seconds": state["idle"],
            "keep_awake": state["keep_awake"],
            "engine_restarts": state["engine_restarts"],
            "icue_restarts": state.get("icue", {}).get("restarts", 0),
            "targets": json.dumps(state.get("targets", {}), ensure_ascii=False),
            "wled_workers": json.dumps(state.get("wled_workers", {}), ensure_ascii=False),
        }
        return self.diag.report(int(minutes), context)

    def save(self, config):
        clean = validate(config)
        with self.lock:
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, self.path)
            self.config = clean
            self.revision += 1
        settings = clean["settings"]
        self.diag.configure(
            settings.get("diagnostic_level"),
            settings.get("diagnostic_days"),
            settings.get("diagnostic_max_mb"),
        )
        self.scan_requested = True
        self.log("Configuration Core 3 enregistrée sans arrêter la synchronisation.", "CONFIG")

    def stop(self):
        self.want_run = False

    def start(self):
        self.want_run = True

    def refresh_icue(self):
        self.scan_requested = True

    def identify(self, ip):
        ip = address(ip)
        if not any(t["ip"] == ip for t in self.config["targets"]):
            raise ValueError("Éclairage inconnu.")
        now = time.monotonic()
        self.tests[ip] = (now, now + 5.0)

    def _prepare(self):
        with self.lock:
            config = copy.deepcopy(self.config)
            revision = self.revision
        old = {t["ip"]: t for t in self.active}
        new = [t for t in config["targets"] if t["enabled"]]
        new_ips = {t["ip"] for t in new}

        for ip, old_target in old.items():
            if ip not in new_ips and not self.demo:
                self.worker(old_target).off("Éclairage désactivé")

        self.active = new
        self.run_settings = config["settings"]
        self.run_revision = revision
        self.running = True
        for target in self.active:
            if not self.demo:
                self.worker(target).update_target(target)
        self.log("Core 3 actif : configuration appliquée.", "ENGINE")

    def _stop_active(self):
        for target in list(self.active):
            if self.demo:
                continue
            worker = self.worker(target)
            if target.get("on_stop") == "preset":
                worker.preset(target.get("stop_preset", 1), "Arrêt de la synchronisation")
            else:
                worker.off("Arrêt de la synchronisation")
        self.active = []
        self.running = False
        self.target_status = {}
        self.log("Synchronisation Core 3 arrêtée.", "ENGINE")

    def _supervisor(self):
        while not self.done.is_set():
            try:
                self._loop()
            except Exception as exc:
                self.engine_restarts += 1
                self.status = "Récupération du moteur"
                self.log("Moteur Core 3 relancé : " + str(exc), "ENGINE", "normal", restart=self.engine_restarts)
                self.done.wait(0.5)

    def _loop(self):
        idle_clock = None if self.demo else WinIdle()
        demo_phase = 0.0
        while not self.done.is_set():
            started = time.monotonic()
            self.loop_heartbeat = started

            if self.running and not self.want_run:
                self._stop_active()
            if self.want_run and not self.running:
                self._prepare()
            elif self.running and self.run_revision != self.revision:
                self._prepare()

            if self.demo:
                self.idle = 0.0
                demo_phase += 0.02
                self.devices = [{
                    "id": "demo-keyboard",
                    "model": "Clavier iCUE démonstration",
                    "serial": "demo",
                    "positions": [{"id": i + 1, "x": i, "y": 0, "group": 0} for i in range(20)],
                }]
                self.colors = {
                    "demo-keyboard": {
                        i + 1: tuple(round(v * 255) for v in colorsys.hsv_to_rgb((demo_phase + i / 20) % 1, 1, 1))
                        for i in range(20)
                    }
                }
                icue_state = {"error": "", "stalled": False, "restarts": 0}
            else:
                self.idle = idle_clock.seconds()
                required = {t.get("device", "") for t in self.active if t.get("source") == "icue"}
                self.icue.configure(required, self.run_settings.get("fps", 12), self.scan_requested)
                self.scan_requested = False
                icue_state = self.icue.snapshot()
                self.devices = copy.deepcopy(icue_state.get("devices", []))
                if not icue_state.get("stalled"):
                    self.colors = copy.deepcopy(icue_state.get("colors", {}))

            # Re-associate a device after iCUE restart / USB re-enumeration.
            for target in self.active:
                if target.get("source") != "icue":
                    continue
                current = next((d for d in self.devices if d.get("id") == target.get("device")), None)
                if current is not None:
                    target["device_model"] = current.get("model", "")
                    target["device_serial"] = current.get("serial", "")
                    continue
                matches = [
                    d for d in self.devices
                    if (
                        target.get("device_serial")
                        and d.get("serial") == target.get("device_serial")
                    ) or (
                        not target.get("device_serial")
                        and target.get("device_model")
                        and d.get("model") == target.get("device_model")
                    )
                ]
                if len(matches) == 1:
                    valid_ids = {p.get("id") for p in matches[0].get("positions", [])}
                    if set(target.get("ids", [])) <= valid_ids:
                        target["device"] = matches[0]["id"]

            states = {}
            if self.running:
                for target in self.active:
                    ip = target["ip"]
                    delay = target["idle_seconds"]
                    if delay is None:
                        delay = self.run_settings.get("idle_seconds", 300)
                    on = self.keep_awake or not (delay and self.idle >= delay)
                    worker = None if self.demo else self.worker(target)
                    source = target.get("source", "icue")
                    test_start, test_end = self.tests.get(ip, (0.0, 0.0))
                    testing = test_start <= started < test_end

                    if not on:
                        if worker:
                            worker.off("Inactivité")
                            states[ip] = "Inactivité · " + worker.snapshot()["state"]
                        else:
                            states[ip] = "Inactivité"
                        continue

                    if source == "wled_preset" and not testing:
                        if worker:
                            worker.preset(target.get("source_preset", 1), "Preset WLED")
                            states[ip] = "Preset WLED · " + worker.snapshot()["state"]
                        else:
                            states[ip] = "Preset WLED"
                        continue

                    if testing:
                        test_phase = int(max(0.0, started - test_start))
                        if test_phase == 0:
                            frame = [(255, 0, 0)] * target["count"]
                            reason = "Test rouge"
                        elif test_phase == 1:
                            frame = [(0, 255, 0)] * target["count"]
                            reason = "Test vert"
                        elif test_phase == 2:
                            frame = [(0, 0, 255)] * target["count"]
                            reason = "Test bleu"
                        elif test_phase == 3:
                            frame = [(255, 255, 255)] * target["count"]
                            reason = "Test blanc"
                        else:
                            frame = _rainbow(target["count"], started - test_start)
                            reason = "Test arc-en-ciel"
                        frame = _gain(frame, target["brightness"])
                    elif source == "rainbow":
                        frame = _gain(_rainbow(target["count"], started), target["brightness"])
                        reason = "Arc-en-ciel"
                    elif source == "solid":
                        frame = _gain([tuple(target["color"])] * target["count"], target["brightness"])
                        reason = "Couleur fixe"
                    else:
                        frame = _map_icue(target, self.colors)
                        if frame is None:
                            frame = self.last_frames.get(ip)
                            if frame is None:
                                if worker:
                                    worker.hold("iCUE en attente")
                                    states[ip] = "iCUE en attente"
                                else:
                                    states[ip] = "iCUE en attente"
                                continue
                            reason = "iCUE en reconnexion · image figée"
                        else:
                            self.last_frames[ip] = copy.deepcopy(frame)
                            reason = "iCUE"

                    if worker:
                        worker.stream(frame, self.run_settings.get("fps", 12), reason)
                        states[ip] = reason + " · " + worker.snapshot()["state"]
                    else:
                        states[ip] = reason

            self.target_status = states
            if self.running:
                if any(t.get("source") == "icue" for t in self.active) and icue_state.get("stalled"):
                    self.status = "Synchronisation active · iCUE redémarre"
                elif any(t.get("source") == "icue" for t in self.active) and icue_state.get("error"):
                    self.status = "Synchronisation active · iCUE en reconnexion"
                else:
                    self.status = "Synchronisation active · Core 3"
            else:
                self.status = "Prêt · Core 3"

            fps = self.run_settings.get("fps", 12) if self.running else 5
            self.done.wait(max(0.0, 1.0 / max(1, fps) - (time.monotonic() - started)))

    def shutdown(self):
        self.want_run = False
        if self.running:
            self._stop_active()
        self.done.set()
        self.thread.join(timeout=3)
        for worker in list(self.workers.values()):
            try:
                worker.close()
            except Exception:
                pass
        self.workers.clear()
        if self.icue:
            self.icue.close()
        try:
            self.diag.close()
        except Exception:
            pass


def dns_name(data, offset, depth=0):
    if depth > 16:
        raise ValueError("Boucle DNS")
    parts = []
    while True:
        if offset >= len(data):
            raise ValueError("DNS incomplet")
        size = data[offset]
        offset += 1
        if size == 0:
            break
        if size & 0xC0 == 0xC0:
            pointer = ((size & 63) << 8) | data[offset]
            offset += 1
            part, _ = dns_name(data, pointer, depth + 1)
            parts.append(part)
            break
        if size > 63 or offset + size > len(data):
            raise ValueError("DNS invalide")
        parts.append(data[offset:offset + size].decode(errors="replace"))
        offset += size
    return ".".join(parts), offset


def discover():
    import struct

    label = b"".join(bytes([len(part)]) + part for part in [b"_wled", b"_tcp", b"local"]) + b"\0"
    query = struct.pack("!6H", 0, 0, 1, 0, 0, 0) + label + struct.pack("!HH", 12, 0x8001)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.5)
    found = set()
    try:
        sock.bind(("", 0))
        sock.sendto(query, ("224.0.0.251", 5353))
        end = time.monotonic() + 3.0
        while time.monotonic() < end:
            try:
                data, _ = sock.recvfrom(9000)
                if len(data) < 12:
                    continue
                _, _, qd, an, ns, ar = struct.unpack("!6H", data[:12])
                offset = 12
                for _ in range(qd):
                    _, offset = dns_name(data, offset)
                    offset += 4
                for _ in range(an + ns + ar):
                    _, offset = dns_name(data, offset)
                    typ, _, _, size = struct.unpack("!HHIH", data[offset:offset + 10])
                    offset += 10
                    if typ == 1 and size == 4:
                        found.add(socket.inet_ntoa(data[offset:offset + 4]))
                    offset += size
            except (socket.timeout, ValueError, IndexError, struct.error):
                continue
    finally:
        sock.close()

    results = []
    for ip in sorted(found)[:32]:
        try:
            info = http(ip, "/json/info", timeout=1.2)
            if "leds" in info:
                results.append({
                    "ip": ip,
                    "name": info.get("name", "WLED"),
                    "count": info["leds"]["count"],
                })
        except Exception:
            pass
    return results
