"""Native MSI Mystic Light source management for Raptor Link."""
from __future__ import annotations
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile

OFFICIAL_SDK_URL="https://download.msi.com/uti_exe/Mystic_light_SDK.zip"
DLL_NAME="MysticLight_SDK_x64.dll"

def _candidate_roots(data_dir):
    roots=[Path(data_dir)/"vendor"/"msi",Path(__file__).resolve().parent/"vendor"/"msi"]
    for key in ("ProgramFiles","ProgramFiles(x86)","ProgramData","LOCALAPPDATA"):
        value=os.environ.get(key)
        if value:roots.append(Path(value)/"MSI")
    return roots

def find_msi_sdk(data_dir):
    preferred=Path(data_dir)/"vendor"/"msi"/DLL_NAME
    if preferred.exists():return preferred
    for root in _candidate_roots(data_dir):
        try:
            direct=root/DLL_NAME
            if direct.exists():return direct
            if root.exists():
                for found in root.rglob(DLL_NAME):
                    if found.is_file():return found
        except (OSError,PermissionError):
            continue
    return None

def install_official_sdk(data_dir,timeout=45):
    """Download MSI's official SDK kit and extract only the x64 runtime DLL."""
    request=urllib.request.Request(OFFICIAL_SDK_URL,headers={"User-Agent":"Raptor-Link/0.3.8"})
    with urllib.request.urlopen(request,timeout=timeout) as response:
        data=response.read(32*1024*1024+1)
    if len(data)>32*1024*1024:raise RuntimeError("Le kit SDK MSI téléchargé est anormalement volumineux.")
    try:
        archive=zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise RuntimeError("Le téléchargement officiel MSI n'est pas un ZIP valide.") from exc
    members=[m for m in archive.infolist() if Path(m.filename).name.lower()==DLL_NAME.lower()]
    if not members:raise RuntimeError("MysticLight_SDK_x64.dll est absent du kit officiel MSI.")
    raw=archive.read(members[0])
    if len(raw)<4096 or raw[:2]!=b"MZ":raise RuntimeError("La DLL MSI extraite est invalide.")
    folder=Path(data_dir)/"vendor"/"msi";folder.mkdir(parents=True,exist_ok=True)
    temp=folder/(DLL_NAME+".tmp");target=folder/DLL_NAME
    temp.write_bytes(raw);os.replace(temp,target)
    return target

def sdk_status(data_dir):
    path=find_msi_sdk(data_dir)
    return {
        "available":bool(path),
        "path":str(path) if path else "",
        "official_url":OFFICIAL_SDK_URL,
        "needs_admin_note":True,
    }

class MsiWorker:
    """Run MSI's native SDK in a disposable child process with a watchdog."""
    def __init__(self,logger,data_dir):
        self.log=logger;self.data_dir=Path(data_dir);self.lock=threading.RLock();self.done=threading.Event()
        self.process=None;self.required=set();self.force_scan=True;self.interval=1/12
        self.devices=[];self.colors={};self.error="";self.busy_since=None;self.operation=""
        self.last_progress=time.monotonic();self.last_ok=0.0;self.restarts=0;self.dll_path=find_msi_sdk(self.data_dir)
        self.thread=threading.Thread(target=self.loop,daemon=True,name="RaptorLink-MSI-bridge");self.thread.start()
    def _command(self):
        root=Path(__file__).resolve().parent;runtime=root/"runtime"/"python.exe"
        python=str(runtime if runtime.exists() else Path(sys.executable))
        return [python,"-u",str(root/"msi_worker.py"),str(self.dll_path)]
    def configure(self,required,force_scan=False,fps=12):
        with self.lock:
            self.required={str(x) for x in required if str(x).startswith("msi:")}
            self.interval=1/max(1,min(15,int(fps)))
            if force_scan:self.force_scan=True
    def refresh_sdk(self):
        found=find_msi_sdk(self.data_dir)
        with self.lock:
            changed=str(found or "")!=str(self.dll_path or "")
            self.dll_path=found;self.force_scan=True
        if changed:self._kill()
        return sdk_status(self.data_dir)
    def snapshot(self):
        now=time.monotonic();kill=None
        with self.lock:
            stalled=self.busy_since is not None and now-self.busy_since>8
            if stalled and self.process is not None and self.process.poll() is None:kill=self.process
            return_state={
                "available":bool(self.dll_path),"path":str(self.dll_path) if self.dll_path else "",
                "devices":copy.deepcopy(self.devices),"colors":copy.deepcopy(self.colors),"error":self.error,
                "stalled":stalled,"operation":self.operation if stalled else "",
                "age":max(0,now-self.last_progress),"last_ok":self.last_ok,"restarts":self.restarts,
            }
        if kill is not None:
            try:kill.kill()
            except Exception:pass
        return return_state
    def close(self):
        self.done.set();self._kill();self.thread.join(timeout=1)
    def _dispose(self,p):
        if not p:return
        if p.poll() is None:
            try:p.kill()
            except Exception:pass
        try:p.wait(timeout=.5)
        except Exception:pass
        for stream in (p.stdin,p.stdout):
            try:
                if stream:stream.close()
            except Exception:pass
        with self.lock:
            if self.process is p:self.process=None
    def _kill(self):
        with self.lock:p=self.process
        self._dispose(p)
    def _start(self):
        if not self.dll_path:return None
        flags=getattr(subprocess,"CREATE_NO_WINDOW",0) if os.name=="nt" else 0
        p=subprocess.Popen(self._command(),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                           text=True,encoding="utf-8",bufsize=1,cwd=str(Path(__file__).resolve().parent),creationflags=flags)
        with self.lock:self.process=p;self.restarts+=1
        return p
    def _publish(self,data=None,error=None):
        with self.lock:
            if data is not None:
                self.devices=copy.deepcopy(data.get("devices",[]))
                self.colors={str(dev):{int(k):tuple(v) for k,v in vals.items()} for dev,vals in data.get("colors",{}).items()}
                self.error=str(data.get("error",""))
                if data.get("ok"):self.last_ok=time.monotonic()
            if error is not None:self.error=str(error)
            self.last_progress=time.monotonic()
    def loop(self):
        process=None;last_scan=0.0;retry_at=0.0
        while not self.done.is_set():
            try:
                if not self.dll_path:
                    self._publish(error="SDK MSI officiel non installé dans Raptor Link")
                    self.done.wait(1);self.refresh_sdk();continue
                now=time.monotonic()
                if process is None or process.poll() is not None:
                    if now<retry_at:
                        self.done.wait(min(.25,retry_at-now));continue
                    process=self._start();last_scan=0
                    if process is None:continue
                with self.lock:
                    required=sorted(self.required);force=self.force_scan;self.force_scan=False;interval=self.interval
                do_scan=force or now-last_scan>5
                request={"required":required,"scan":do_scan}
                with self.lock:self.busy_since=time.monotonic();self.operation="détection MSI Mystic Light" if do_scan else "lecture des couleurs MSI"
                process.stdin.write(json.dumps(request,separators=(",",":"))+"\n");process.stdin.flush()
                line=process.stdout.readline()
                if not line:raise RuntimeError("pont MSI Mystic Light interrompu")
                data=json.loads(line)
                with self.lock:self.busy_since=None;self.operation=""
                if do_scan:last_scan=time.monotonic()
                self._publish(data=data)
                if not data.get("ok") and data.get("fatal"):raise RuntimeError(data.get("error","erreur MSI Mystic Light"))
                self.done.wait(interval)
            except Exception as exc:
                with self.lock:self.busy_since=None;self.operation=""
                self._publish(error=exc);self._dispose(process);process=None;retry_at=time.monotonic()+2
        self._dispose(process)
