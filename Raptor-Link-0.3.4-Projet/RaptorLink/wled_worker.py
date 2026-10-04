"""Independent WLED output worker used by Raptor Link Core 2.

Each WLED owns one worker. Realtime UDP streaming, autonomous presets and
power transitions therefore cannot block another light or the main engine.
"""
from __future__ import annotations
import copy
import json
import socket
import threading
import time
import urllib.request

def _packets(pixels,timeout=2):
    if len(pixels)<=490:
        return [bytes([2,timeout])+bytes(v for c in pixels for v in c)]
    out=[]
    for start in range(0,len(pixels),480):
        out.append(bytes([4,timeout,start>>8,start&255])+bytes(v for c in pixels[start:start+480] for v in c))
    return out

class WledWorker:
    RELEASE_SETTLE_SECONDS=0.18

    def __init__(self,target,logger,diagnostics=None):
        self.lock=threading.RLock()
        self.done=threading.Event()
        self.log=logger
        self.diag=diagnostics
        self.target=copy.deepcopy(target)
        self.desired={"mode":"hold","reason":"démarrage","frame":None,"preset":None,"fps":25,"transition":0,"restore":None}
        self.desired_rev=0
        self.applied_rev=-1
        self.state="HOLD"
        self.detail="En attente"
        self.last_udp=0.0
        self.last_http_ok=0.0
        self.last_http_error=""
        self.last_udp_error=""
        self.packets_sent=0
        self.transitions=0
        self.retries=0
        self.commanded_off=False
        self.next_http_try=0.0
        self.released_rev=-1
        self.release_packets=0
        self.udp_commands=0
        self.control_path=""
        self.udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        self.udp.settimeout(.2)
        self.http=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.thread=threading.Thread(target=self._loop,daemon=True,name="RaptorLink-WLED-"+self.target["ip"])
        self.thread.start()

    def update_target(self,target):
        with self.lock:self.target=copy.deepcopy(target)

    def _set(self,mode,reason="",**kwargs):
        with self.lock:
            changed=mode!=self.desired.get("mode")
            payload=dict(self.desired)
            payload.update(mode=mode,reason=str(reason)[:200],**kwargs)
            # Frame refreshes should not cause a state transition/revision.
            if mode=="stream" and self.desired.get("mode")=="stream":
                self.desired=payload
                return
            if payload!=self.desired:
                self.desired=payload
                self.desired_rev+=1
                if changed:self.transitions+=1
        if changed:self._event("detailed","WLED-STATE",f"{self.target['name']} -> {mode}",reason=reason,ip=self.target["ip"])

    def stream(self,frame,fps=25,reason="Synchronisé"):
        self._set("stream",reason,frame=[tuple(c) for c in frame],fps=max(1,min(40,int(fps))),preset=None,restore=None)

    def preset(self,preset,reason="Preset WLED",transition=0):
        self._set("preset",reason,preset=max(1,min(250,int(preset))),transition=max(0,min(65,float(transition))),frame=None,restore=None)

    def off(self,reason="Inactivité",transition=0):
        self._set("off",reason,transition=max(0,min(65,float(transition))),frame=None,preset=None,restore=None)

    def hold(self,reason="En attente"):
        self._set("hold",reason,frame=None,preset=None,restore=None)

    def restore(self,state,reason="Restauration"):
        self._set("restore",reason,restore=copy.deepcopy(state or {"on":False}),frame=None,preset=None)

    def snapshot(self):
        with self.lock:
            return {
                "state":self.state,
                "detail":self.detail,
                "desired":self.desired.get("mode"),
                "reason":self.desired.get("reason",""),
                "packets_sent":self.packets_sent,
                "last_udp":self.last_udp,
                "last_http_ok":self.last_http_ok,
                "last_http_error":self.last_http_error,
                "last_udp_error":self.last_udp_error,
                "transitions":self.transitions,
                "retries":self.retries,
                "commanded_off":self.commanded_off,
                "control_path":self.control_path,
                "release_packets":self.release_packets,
                "udp_commands":self.udp_commands,
            }

    def _event(self,level,category,message,**fields):
        if self.diag is not None:
            try:self.diag.event(level,category,message,**fields)
            except Exception:pass

    def _http(self,payload,timeout=2):
        target=copy.deepcopy(self.target)
        req=urllib.request.Request(
            "http://"+target["ip"]+"/json/state",
            data=json.dumps(payload,separators=(",",":")).encode(),
            headers={"Content-Type":"application/json"},
        )
        try:
            with self.http.open(req,timeout=timeout) as r:
                data=json.load(r)
            with self.lock:
                self.last_http_ok=time.time();self.last_http_error="";self.retries=0
            self._event("detailed","WLED-HTTP",f"{target['name']} commande OK",ip=target["ip"],payload=payload)
            return data
        except Exception as exc:
            msg=str(exc)
            with self.lock:
                self.last_http_error=msg;self.retries+=1
            self._event("normal","WLED-HTTP",f"{target['name']} erreur HTTP",ip=target["ip"],error=msg,payload=payload)
            raise

    def _command_payload(self,d):
        transition=max(0,min(650,int(round(float(d.get("transition",0))*10))))
        mode=d["mode"]
        if mode=="off":
            return {"live":False,"on":False,"tt":transition}
        if mode=="preset":
            return {"live":False,"on":True,"ps":int(d["preset"]),"tt":transition}
        if mode=="restore":
            payload=copy.deepcopy(d.get("restore") or {"on":False})
            payload["live"]=False
            return payload
        return {"live":False}

    def _send_frame(self,frame):
        target=copy.deepcopy(self.target)
        try:
            for packet in _packets(frame,2):
                self.udp.sendto(packet,(target["ip"],int(target.get("port",21324))))
                self.packets_sent+=1
            self.last_udp=time.monotonic();self.last_udp_error=""
            return True
        except OSError as exc:
            self.last_udp_error=str(exc)
            self._event("normal","WLED-UDP",f"{target['name']} erreur UDP",ip=target["ip"],error=str(exc))
            return False

    def _release_realtime(self):
        """Force WLED out of UDP realtime mode, even if this process did not start it."""
        target=copy.deepcopy(self.target)
        ok=True
        for _ in range(3):
            try:
                self.udp.sendto(bytes([2,0]),(target["ip"],int(target.get("port",21324))))
                self.release_packets+=1
            except OSError as exc:
                ok=False;self.last_udp_error=str(exc)
            time.sleep(.025)
        self._event("detailed","WLED-UDP",f"{target['name']} sortie realtime demandée",ip=target["ip"],packets=3,ok=ok)
        return ok

    def _udp_state(self,payload):
        """Fallback to WLED's JSON API over the notifier UDP port when HTTP is unavailable."""
        target=copy.deepcopy(self.target)
        data=json.dumps(payload,separators=(",",":"),ensure_ascii=False).encode("utf-8")
        ok=True
        for _ in range(3):
            try:
                self.udp.sendto(data,(target["ip"],int(target.get("port",21324))))
                self.udp_commands+=1
            except OSError as exc:
                ok=False;self.last_udp_error=str(exc)
            time.sleep(.03)
        if ok:
            self.control_path="udp-json-fallback"
            self._event("normal","WLED-UDP",f"{target['name']} commande JSON envoyée par UDP (secours)",ip=target["ip"],payload=payload)
        return ok

    def _loop(self):
        next_udp=0.0
        next_trace=0.0
        previous_mode="hold"
        while not self.done.is_set():
            with self.lock:
                d=copy.deepcopy(self.desired);rev=self.desired_rev
            now=time.monotonic()
            mode=d["mode"]

            if mode=="hold":
                if previous_mode!="hold":
                    self.state="HOLD";self.detail=d.get("reason","En attente")
                previous_mode="hold"
                self.done.wait(.05)
                continue

            if mode=="stream":
                frame=d.get("frame")
                # If Raptor Link itself previously switched this WLED off, wake it.
                # A failed HTTP call must never block realtime output: WLED also accepts
                # JSON state commands over its UDP notifier port.
                if self.commanded_off:
                    try:
                        self.state="WAKING";self.detail="Rallumage WLED"
                        self._release_realtime()
                        self._http({"live":False,"on":True,"tt":0})
                        self.control_path="http"
                    except Exception:
                        self._udp_state({"live":False,"on":True,"tt":0})
                    self.commanded_off=False
                    self.applied_rev=rev
                if frame:
                    interval=1/max(1,int(d.get("fps",25)))
                    if now>=next_udp:
                        if self._send_frame(frame):
                            if self.state!="STREAMING":
                                self._event("detailed","WLED-STATE",f"{self.target['name']} streaming actif",ip=self.target["ip"])
                            self.state="STREAMING";self.detail=d.get("reason","Synchronisé")
                            if now>=next_trace:
                                self._event("trace","WLED-UDP",f"{self.target['name']} flux vivant",ip=self.target["ip"],packets=self.packets_sent,fps=d.get("fps",25),frame_leds=len(frame))
                                next_trace=now+1
                        next_udp=now+interval
                    self.applied_rev=rev
                else:
                    self.state="WAIT_SOURCE";self.detail="Aucune trame disponible"
                previous_mode="stream"
                self.done.wait(.005)
                continue

            # Autonomous preset / OFF / restore: force-release realtime first.
            # This is done even if *this* process did not start realtime mode, which
            # also recovers a WLED left in live mode by an older Raptor Link session.
            if rev!=self.applied_rev and self.released_rev!=rev:
                self.state="RELEASING";self.detail="Sortie forcée du mode temps réel"
                self._release_realtime()
                self.released_rev=rev
                previous_mode=mode
                self.done.wait(self.RELEASE_SETTLE_SECONDS)
                continue

            if rev!=self.applied_rev:
                payload=self._command_payload(d)
                used_fallback=False
                try:
                    self.state="APPLYING";self.detail=d.get("reason","Commande WLED")
                    self._http(payload)
                    self.control_path="http"
                except Exception:
                    # Do not hammer the ESP web server for minutes. WLED supports the
                    # same JSON state object over UDP on the notifier port.
                    used_fallback=self._udp_state(payload)
                    if not used_fallback:
                        self.state="CONTROL_ERROR";self.detail=self.last_http_error or "Commande WLED impossible"
                        self.done.wait(1)
                        previous_mode=mode
                        continue
                self.applied_rev=rev
                if mode=="off":
                    self.commanded_off=True;self.state="OFF_UDP" if used_fallback else "OFF"
                elif mode=="preset":
                    self.commanded_off=False;self.state="AUTONOMOUS_UDP" if used_fallback else "AUTONOMOUS"
                else:
                    self.commanded_off=not bool(payload.get("on",True));self.state="RESTORED_UDP" if used_fallback else "RESTORED"
                self.detail=d.get("reason","")
            previous_mode=mode
            self.done.wait(.05)

        try:self.udp.close()
        except Exception:pass

    def close(self):
        self.done.set()
        self.thread.join(timeout=3)
