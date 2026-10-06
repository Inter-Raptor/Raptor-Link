"""Persistent, low-overhead diagnostics for Raptor Link Core 2."""
from __future__ import annotations
import datetime as _dt
import json
import os
import queue
import threading
import time
from pathlib import Path

_LEVELS={"off":0,"normal":1,"detailed":2,"trace":3}

class Diagnostics:
    def __init__(self, root: Path, level="normal", keep_days=7, max_mb=50):
        self.root=Path(root)
        self.root.mkdir(parents=True,exist_ok=True)
        self.level=str(level) if str(level) in _LEVELS else "normal"
        self.keep_days=max(1,min(30,int(keep_days)))
        self.max_bytes=max(5,min(500,int(max_mb)))*1024*1024
        self.q=queue.Queue(maxsize=5000)
        self.done=threading.Event()
        self.dropped=0
        self.thread=threading.Thread(target=self._loop,daemon=True,name="RaptorLink-diagnostics")
        self.thread.start()

    def configure(self, level=None, keep_days=None, max_mb=None):
        if level is not None:
            self.level=str(level) if str(level) in _LEVELS else self.level
        if keep_days is not None:
            self.keep_days=max(1,min(30,int(keep_days)))
        if max_mb is not None:
            self.max_bytes=max(5,min(500,int(max_mb)))*1024*1024

    def enabled(self, level="normal"):
        return _LEVELS.get(self.level,1)>=_LEVELS.get(level,1) and self.level!="off"

    def event(self, level, category, message, **fields):
        if not self.enabled(level):
            return
        item={
            "ts":time.time(),
            "level":level,
            "category":str(category)[:40],
            "message":str(message)[:1000],
            "fields":{str(k)[:60]:self._safe(v) for k,v in fields.items()},
        }
        try:self.q.put_nowait(item)
        except queue.Full:self.dropped+=1

    def _safe(self, value):
        if isinstance(value,(str,int,float,bool)) or value is None:return value
        if isinstance(value,(list,tuple)):return [self._safe(v) for v in value[:100]]
        if isinstance(value,dict):return {str(k)[:60]:self._safe(v) for k,v in list(value.items())[:100]}
        return str(value)[:500]

    def _path_for(self, ts):
        day=_dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        return self.root/f"raptor-link-{day}.log"

    def _format(self, item):
        stamp=_dt.datetime.fromtimestamp(item["ts"]).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        fields=" ".join(f"{k}={json.dumps(v,ensure_ascii=False,separators=(',',':'))}" for k,v in item["fields"].items())
        return f"{stamp} [{item['level'].upper()}] [{item['category']}] {item['message']}{(' | '+fields) if fields else ''}\n"

    def _loop(self):
        last_cleanup=0.0
        while not self.done.is_set() or not self.q.empty():
            try:item=self.q.get(timeout=.5)
            except queue.Empty:item=None
            if item is not None:
                try:
                    p=self._path_for(item["ts"])
                    with p.open("a",encoding="utf-8",buffering=1) as f:f.write(self._format(item))
                except OSError:pass
            now=time.time()
            if now-last_cleanup>300:
                last_cleanup=now
                self._cleanup(now)

    def _cleanup(self, now=None):
        now=time.time() if now is None else now
        files=sorted(self.root.glob("raptor-link-*.log"),key=lambda p:p.stat().st_mtime if p.exists() else 0)
        cutoff=now-self.keep_days*86400
        for p in files:
            try:
                if p.stat().st_mtime<cutoff:p.unlink()
            except OSError:pass
        files=sorted(self.root.glob("raptor-link-*.log"),key=lambda p:p.stat().st_mtime if p.exists() else 0)
        total=sum(p.stat().st_size for p in files if p.exists())
        for p in files:
            if total<=self.max_bytes:break
            try:
                size=p.stat().st_size;p.unlink();total-=size
            except OSError:pass

    def recent_lines(self, minutes=30, max_lines=4000):
        if not self.root.exists():return []
        cutoff=0 if minutes<=0 else time.time()-minutes*60
        lines=[]
        for p in sorted(self.root.glob("raptor-link-*.log")):
            try:
                if cutoff and p.stat().st_mtime<cutoff-86400:continue
                with p.open("r",encoding="utf-8",errors="replace") as f:
                    for line in f:
                        if cutoff:
                            try:
                                stamp=line[:23]
                                ts=_dt.datetime.strptime(stamp,"%Y-%m-%d %H:%M:%S.%f").timestamp()
                                if ts<cutoff:continue
                            except Exception:pass
                        lines.append(line.rstrip())
                        if len(lines)>max_lines:lines=lines[-max_lines:]
            except OSError:pass
        return lines[-max_lines:]

    def report(self, minutes=30, context=None):
        context=context or {}
        head=[
            "RAPTOR LINK — RAPPORT DIAGNOSTIC CORE 2",
            "="*44,
            f"Généré : {_dt.datetime.now().isoformat(sep=' ',timespec='seconds')}",
            f"Niveau journal : {self.level}",
            f"Rétention : {self.keep_days} jour(s) / {self.max_bytes//1024//1024} Mo",
            f"Événements perdus (file pleine) : {self.dropped}",
            "",
            "ÉTAT ACTUEL",
            "-"*44,
        ]
        for k,v in context.items():
            head.append(f"{k}: {v}")
        head+=["","CHRONOLOGIE","-"*44]
        return "\n".join(head+self.recent_lines(minutes))

    def close(self):
        self.done.set()
        self.thread.join(timeout=2)
