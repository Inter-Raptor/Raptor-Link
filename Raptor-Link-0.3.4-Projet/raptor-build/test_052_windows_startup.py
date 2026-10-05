import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "RaptorLink"
PYTHON = APP / "runtime" / "python.exe"


def request_json(url, token, method="GET"):
    req = urllib.request.Request(
        url,
        data=b"{}" if method == "POST" else None,
        method=method,
        headers={
            "X-Aurora-Token": token,
            "Content-Type": "application/json",
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=2) as response:
        return json.load(response)


def main():
    if os.name != "nt":
        raise SystemExit("Windows smoke test only")

    with tempfile.TemporaryDirectory() as tmp:
        env = os.environ.copy()
        env["LOCALAPPDATA"] = tmp
        log = Path(tmp) / "smoke-stdout.txt"

        with log.open("w", encoding="utf-8") as out:
            process = subprocess.Popen(
                [str(PYTHON), str(APP / "app.py"), "--headless"],
                cwd=str(APP),
                env=env,
                stdout=out,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

        data_dir = Path(tmp) / "AuroraWLED"
        instance = data_dir / "instance.json"
        deadline = time.monotonic() + 20
        info = None

        while time.monotonic() < deadline:
            if process.poll() is not None:
                text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
                error_file = data_dir / "erreur.txt"
                details = error_file.read_text(encoding="utf-8", errors="replace") if error_file.exists() else ""
                raise RuntimeError(
                    f"Raptor Link exited during startup with code {process.returncode}\n{text}\n{details}"
                )
            if instance.exists():
                try:
                    info = json.loads(instance.read_text(encoding="utf-8"))
                    state = request_json(
                        f"http://127.0.0.1:{info['port']}/api/state",
                        info["token"],
                    )
                    if "status" in state and "running" in state:
                        break
                except Exception:
                    pass
            time.sleep(0.2)
        else:
            process.kill()
            raise RuntimeError("Raptor Link did not expose /api/state within 20 seconds")

        request_json(
            f"http://127.0.0.1:{info['port']}/api/quit",
            info["token"],
            "POST",
        )
        process.wait(timeout=15)
        if process.returncode != 0:
            text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
            raise RuntimeError(f"Raptor Link did not close cleanly\n{text}")

        print("Windows startup smoke test: OK")


if __name__ == "__main__":
    main()
