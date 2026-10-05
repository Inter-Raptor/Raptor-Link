"""Small Windows watchdog for Raptor Link Core 3.

It watches one application PID. A normal Raptor Link shutdown writes a per-PID
flag; an unexpected exit does not, so the watchdog relaunches app.py once.
"""
from __future__ import annotations

import ctypes as C
import json
import os
from pathlib import Path
import subprocess
import sys
import time


SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0
INFINITE = 0xFFFFFFFF


def _wait_process(pid):
    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [C.c_uint32, C.c_int, C.c_uint32]
    kernel.OpenProcess.restype = C.c_void_p
    kernel.WaitForSingleObject.argtypes = [C.c_void_p, C.c_uint32]
    kernel.WaitForSingleObject.restype = C.c_uint32
    kernel.CloseHandle.argtypes = [C.c_void_p]
    handle = kernel.OpenProcess(SYNCHRONIZE, False, int(pid))
    if not handle:
        return
    try:
        kernel.WaitForSingleObject(handle, INFINITE)
    finally:
        kernel.CloseHandle(handle)


def _allow_restart(data):
    path = data / "watchdog.json"
    now = time.time()
    history = []
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        history = [float(x) for x in loaded.get("restarts", [])]
    except Exception:
        pass
    history = [x for x in history if now - x < 3600]
    if len(history) >= 5:
        return False
    history.append(now)
    try:
        path.write_text(json.dumps({"restarts": history}), encoding="utf-8")
    except Exception:
        pass
    return True


def main():
    if os.name != "nt" or len(sys.argv) < 4:
        return
    pid = int(sys.argv[1])
    data = Path(sys.argv[2])
    root = Path(sys.argv[3])
    normal = data / ("normal-exit-" + str(pid) + ".flag")
    normal.unlink(missing_ok=True)

    _wait_process(pid)

    if normal.exists():
        normal.unlink(missing_ok=True)
        return
    if not _allow_restart(data):
        return

    time.sleep(2.0)
    pythonw = root / "runtime" / "pythonw.exe"
    app = root / "app.py"
    if pythonw.exists() and app.exists():
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            [str(pythonw), str(app), "--background", "--recovered"],
            cwd=str(root),
            creationflags=flags,
        )


if __name__ == "__main__":
    main()
