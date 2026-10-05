"""Raptor Link 0.5 - Core 3 minimal local application."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent
DEMO = "--demo" in sys.argv
DATA = (Path(os.environ.get("LOCALAPPDATA", str(ROOT))) / "AuroraWLED") if not DEMO else ROOT / "demo-data"
DATA.mkdir(parents=True, exist_ok=True)

if sys.stdout is None:
    sys.stdout = open(DATA / "application.log", "a", encoding="utf-8", buffering=1)
    sys.stderr = sys.stdout

from core3_engine import Engine, http, discover, validate
from support import APP_VERSION, find_update

INSTANCE = DATA / "instance.json"


def request_existing():
    try:
        data = json.loads(INSTANCE.read_text(encoding="utf-8"))
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.AllowSetForegroundWindow(int(data["pid"]))
        request = urllib.request.Request(
            "http://127.0.0.1:" + str(data["port"]) + "/api/open",
            data=b"{}",
            headers={
                "X-Aurora-Token": data["token"],
                "Content-Type": "application/json",
            },
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def startup(enabled):
    if sys.platform != "win32":
        return
    import winreg

    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
    ) as key:
        if enabled:
            launcher = ROOT / "RaptorLink.exe"
            if launcher.exists():
                command = '"' + str(launcher) + '" --background'
            else:
                command = '"' + str(ROOT / "runtime" / "pythonw.exe") + '" "' + str(ROOT / "app.py") + '" --background'
            winreg.SetValueEx(key, "AuroraWLED", 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, "AuroraWLED")
            except FileNotFoundError:
                pass


class App:
    def __init__(self):
        self.token = secrets.token_urlsafe(32)
        self.done = threading.Event()
        self.tray = None
        self.normal_exit_flag = DATA / ("normal-exit-" + str(os.getpid()) + ".flag")
        self.watchdog_state_file = DATA / ("watchdog-state-" + str(os.getpid()) + ".json")
        self.normal_exit_flag.unlink(missing_ok=True)
        self.watchdog_state_file.unlink(missing_ok=True)

        self.engine = Engine(DATA / "config.json", DEMO)
        if "--recover-sync" in sys.argv:
            self.engine.want_run = True
        self.update_info = {"checked": False, "available": False, "error": ""}
        startup(self.engine.config["settings"]["startup"])

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.handler())
        self.url = "http://127.0.0.1:" + str(self.server.server_port) + "/#" + self.token

        from window import WindowController, focus_window
        self.window = WindowController(
            lambda: focus_window("Raptor Link · " + str(self.server.server_port))
            if sys.platform == "win32" and not DEMO
            else False,
            self.launch_window,
        )

        INSTANCE.write_text(
            json.dumps({
                "port": self.server.server_port,
                "token": self.token,
                "pid": os.getpid(),
            }),
            encoding="utf-8",
        )

        self._write_watchdog_state()
        self._start_watchdog()

        if self.engine.config["settings"].get("check_updates", True) and not DEMO:
            threading.Thread(
                target=self.check_for_updates,
                daemon=True,
                name="RaptorLink-update-check",
            ).start()

    def _write_watchdog_state(self):
        if DEMO:
            return
        try:
            temp = self.watchdog_state_file.with_suffix(".tmp")
            temp.write_text(
                json.dumps({
                    "requested": bool(self.engine.want_run),
                    "running": bool(self.engine.running),
                }),
                encoding="utf-8",
            )
            os.replace(temp, self.watchdog_state_file)
        except Exception:
            pass

    def _start_watchdog(self):
        if DEMO or sys.platform != "win32":
            return
        try:
            pythonw = ROOT / "runtime" / "pythonw.exe"
            script = ROOT / "watchdog.py"
            if pythonw.exists() and script.exists():
                subprocess.Popen(
                    [str(pythonw), str(script), str(os.getpid()), str(DATA), str(ROOT)],
                    cwd=str(ROOT),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                self.engine.log("Watchdog Core 3 actif.", "WATCHDOG", "detailed")
        except Exception as exc:
            self.engine.log("Watchdog indisponible : " + str(exc), "WATCHDOG")

    def check_for_updates(self):
        self.update_info = find_update(APP_VERSION)

    def open(self):
        self.window.open()

    def launch_window(self):
        if not DEMO and sys.platform == "win32":
            paths = [
                Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
                Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Microsoft/Edge/Application/msedge.exe",
                Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
            ]
            for executable in paths:
                if executable.exists():
                    subprocess.Popen(
                        [
                            str(executable),
                            "--app=" + self.url,
                            "--window-size=1180,820",
                            "--user-data-dir=" + str(DATA / "window-profile-core3"),
                            "--no-first-run",
                        ],
                        creationflags=0x08000000,
                    )
                    return
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None,
                "Microsoft Edge ou Google Chrome est nécessaire pour ouvrir Raptor Link.",
                "Raptor Link",
                16,
            )
            return
        webbrowser.open(self.url)

    def handler(self):
        app = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, code, data, content_type="application/json; charset=utf-8"):
                body = json.dumps(data, ensure_ascii=False).encode("utf-8") if isinstance(data, (dict, list)) else data
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
                )
                self.end_headers()
                self.wfile.write(body)

            def authorized(self):
                return secrets.compare_digest(
                    self.headers.get("X-Aurora-Token", ""),
                    app.token,
                )

            def do_GET(self):
                if self.path.startswith("/api/"):
                    if not self.authorized():
                        return self.reply(403, {"error": "Accès refusé"})
                    if self.path == "/api/state":
                        state = app.engine.snapshot()
                        state["app"] = {
                            "version": APP_VERSION,
                            "update": copy.deepcopy(app.update_info),
                        }
                        return self.reply(200, state)
                    if self.path == "/api/config":
                        with app.engine.lock:
                            config = copy.deepcopy(app.engine.config)
                        return self.reply(200, config)
                    return self.reply(404, {"error": "Introuvable"})

                mapping = {
                    "/": ("index.html", "text/html; charset=utf-8"),
                    "/style.css": ("style.css", "text/css; charset=utf-8"),
                    "/ui.js": ("ui.js", "text/javascript; charset=utf-8"),
                }
                path = self.path.split("?")[0]
                if path == "/icon.png":
                    return self.reply(200, (ROOT / "icon.png").read_bytes(), "image/png")
                if path not in mapping:
                    return self.reply(404, {"error": "Introuvable"})
                name, mime = mapping[path]
                return self.reply(200, (ROOT / "web" / name).read_bytes(), mime)

            def do_POST(self):
                if not self.authorized():
                    return self.reply(403, {"error": "Accès refusé"})
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 <= size <= 1000000:
                        raise ValueError("Requête trop volumineuse")
                    data = json.loads(self.rfile.read(size) or b"{}")
                    result = {"ok": True}

                    if self.path == "/api/open":
                        threading.Thread(target=app.open, daemon=True).start()
                    elif self.path == "/api/config":
                        previous_startup = app.engine.config["settings"]["startup"]
                        app.engine.save(data)
                        if app.engine.config["settings"]["startup"] != previous_startup:
                            startup(app.engine.config["settings"]["startup"])
                    elif self.path == "/api/validate":
                        result = validate(data)
                    elif self.path == "/api/start":
                        app.engine.start()
                    elif self.path == "/api/stop":
                        app.engine.stop()
                    elif self.path == "/api/awake":
                        app.engine.keep_awake = bool(data.get("enabled"))
                    elif self.path == "/api/refresh":
                        app.engine.refresh_icue()
                    elif self.path == "/api/test":
                        app.engine.identify(data["ip"])
                    elif self.path == "/api/probe":
                        if DEMO:
                            result = {"name": "WLED Démonstration", "count": 160, "version": "demo"}
                        else:
                            info = http(data["ip"], "/json/info", timeout=1.5)
                            result = {
                                "name": info.get("name", "WLED"),
                                "count": info["leds"]["count"],
                                "version": info.get("ver", ""),
                            }
                    elif self.path == "/api/discover":
                        result = discover() if not DEMO else [
                            {"name": "Bureau démo", "ip": "192.168.1.175", "count": 257},
                            {"name": "Logo démo", "ip": "192.168.1.80", "count": 160},
                        ]
                    elif self.path == "/api/open-wled":
                        ip = data["ip"]
                        http(ip, "/json/info", timeout=1.0)
                        webbrowser.open("http://" + str(ip))
                    elif self.path == "/api/window-ready":
                        app.window.ready()
                    elif self.path == "/api/diagnostic-report":
                        minutes = int(data.get("minutes", 30))
                        if minutes not in (5, 30, 120, 1440, 0):
                            raise ValueError("Durée de rapport invalide.")
                        result = {"text": app.engine.diagnostic_report(minutes)}
                    elif self.path == "/api/check-update":
                        if app.engine.config["settings"].get("check_updates", True):
                            app.update_info = find_update(APP_VERSION)
                        else:
                            app.update_info = {"checked": True, "available": False, "error": ""}
                        result = copy.deepcopy(app.update_info)
                    elif self.path == "/api/open-update":
                        url = str(app.update_info.get("url", ""))
                        if not url.startswith("https://github.com/Inter-Raptor/Raptor-Link/releases/"):
                            raise ValueError("Aucune mise à jour disponible.")
                        webbrowser.open(url)
                    elif self.path == "/api/quit":
                        app.done.set()
                    else:
                        return self.reply(404, {"error": "Introuvable"})
                    return self.reply(200, result)
                except Exception as exc:
                    return self.reply(400, {"error": str(exc)})

        return Handler

    def run(self):
        threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
            name="RaptorLink-Core3-HTTP",
        ).start()

        if not DEMO:
            try:
                from PIL import Image
                import pystray

                self.tray = pystray.Icon(
                    "RaptorLink",
                    Image.open(ROOT / "icon.png"),
                    "Raptor Link Core 3",
                    pystray.Menu(
                        pystray.MenuItem("Ouvrir Raptor Link", lambda: self.open(), default=True),
                        pystray.MenuItem("Démarrer", lambda: self.engine.start()),
                        pystray.MenuItem("Arrêter", lambda: self.engine.stop()),
                        pystray.MenuItem(
                            "Ignorer l'inactivité",
                            lambda: setattr(self.engine, "keep_awake", not self.engine.keep_awake),
                            checked=lambda item: self.engine.keep_awake,
                        ),
                        pystray.MenuItem("Quitter", lambda: self.done.set()),
                    ),
                )
                threading.Thread(target=self.tray.run, daemon=True).start()
            except Exception as exc:
                self.engine.log("Icône de notification indisponible : " + str(exc))

        if "--background" not in sys.argv and "--headless" not in sys.argv:
            self.open()

        normal_shutdown = False
        try:
            while not self.done.wait(0.5):
                self._write_watchdog_state()
            self._write_watchdog_state()
            normal_shutdown = True
        except KeyboardInterrupt:
            normal_shutdown = True
        finally:
            # Only an explicit/normal exit suppresses watchdog recovery.
            if normal_shutdown:
                try:
                    self.normal_exit_flag.write_text("normal", encoding="utf-8")
                except Exception:
                    pass
            try:
                self.engine.shutdown()
            finally:
                self.server.shutdown()
                if self.tray:
                    self.tray.stop()
                INSTANCE.unlink(missing_ok=True)
                if normal_shutdown:
                    self.watchdog_state_file.unlink(missing_ok=True)


def main():
    mutex = None
    if sys.platform == "win32" and not DEMO:
        import ctypes as C

        kernel = C.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [C.c_void_p, C.c_int, C.c_wchar_p]
        kernel.CreateMutexW.restype = C.c_void_p
        mutex = kernel.CreateMutexW(None, False, "Local\\AuroraWLED-0.2")
        if not mutex:
            raise C.WinError(C.get_last_error())
        if C.get_last_error() == 183:
            for _ in range(20):
                if request_existing():
                    break
                time.sleep(0.2)
            return

    if not request_existing():
        App().run()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        (DATA / "erreur.txt").write_text(traceback.format_exc(), encoding="utf-8")
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None,
                "Raptor Link a rencontré une erreur. Le watchdog tentera de le relancer.\n"
                "Détails : " + str(DATA / "erreur.txt"),
                "Raptor Link Core 3",
                16,
            )
        else:
            raise
