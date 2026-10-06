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
    HTTP_TIMEOUT_SECONDS=1.2
    HTTP_RETRY_BASE_SECONDS=15.0
    HTTP_RETRY_MAX_SECONDS=60.0
    HEALTH_INTERVAL_SECONDS=15.0
    HEALTH_OFFLINE_SECONDS=5.0
    HEALTH_REBOOT_SECONDS=2.0
    HEALTH_TIMEOUT_SECONDS=0.7
    RESYNC_MIN_INTERVAL_SECONDS=12.0

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
        self.pending_confirmation=False
        self.fallback_batches=0
        self.released_rev=-1
        self.release_packets=0
        self.udp_commands=0
        self.control_path=""
        self.health_online=None
        self.health_error=""
        self.health_failures=0
        self.last_health_ok=0.0
        self.health_uptime=None
        self.health_live=None
        self.health_lm=None
        self.health_lip=""
        self.reboots_detected=0
        self.recoveries=0
        self.resync_requested=False
        self.last_resync=0.0
        self.reboot_requested=False
        self.reboot_pending=False
        self.reboot_requested_at=0.0
        self.reboot_reason=""
        self.last_frame_signature=None
        self.frame_changes=0
        self.last_frame_change=0.0
        self.health_wake=threading.Event()
        self.udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        self.udp.settimeout(.2)
        self.http=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.health_http=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.thread=threading.Thread(target=self._loop,daemon=True,name="RaptorLink-WLED-"+self.target["ip"])
        self.health_thread=threading.Thread(target=self._health_loop,daemon=True,name="RaptorLink-WLED-health-"+self.target["ip"])
        self.thread.start();self.health_thread.start()

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
                self.pending_confirmation=False
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
                "http_retry_in":round(max(0.0,self.next_http_try-time.monotonic()),2),
                "pending_confirmation":self.pending_confirmation,
                "fallback_batches":self.fallback_batches,
                "release_packets":self.release_packets,
                "udp_commands":self.udp_commands,
                "health_online":self.health_online,
                "health_error":self.health_error,
                "health_failures":self.health_failures,
                "health_seen_ago":None if not self.last_health_ok else round(max(0.0,time.time()-self.last_health_ok),1),
                "wled_uptime":self.health_uptime,
                "realtime_live":self.health_live,
                "realtime_mode":self.health_lm,
                "realtime_source":self.health_lip,
                "reboots_detected":self.reboots_detected,
                "recoveries":self.recoveries,
                "reboot_pending":self.reboot_pending,
                "resync_requested":self.resync_requested,
                "frame_changes":self.frame_changes,
                "frame_change_age":None if not self.last_frame_change else round(max(0.0,time.monotonic()-self.last_frame_change),2),
                "frame_sample":list(self.last_frame_signature[1]) if self.last_frame_signature else [],
            }

    def _event(self,level,category,message,**fields):
        if self.diag is not None:
            try:self.diag.event(level,category,message,**fields)
            except Exception:pass

    def reboot(self,reason="Redémarrage manuel"):
        """Queue a WLED reboot without blocking the UI or another controller."""
        with self.lock:
            if self.reboot_pending or self.reboot_requested:return False
            self.reboot_requested=True
            self.reboot_pending=True
            self.reboot_requested_at=time.monotonic()
            self.reboot_reason=str(reason)[:200]
        self.health_wake.set()
        self._event("normal","WLED-STATE",f"{self.target['name']} redémarrage demandé",ip=self.target["ip"])
        return True

    def _health_probe(self):
        """Probe WLED out-of-band so realtime UDP can never be blocked by HTTP."""
        target=copy.deepcopy(self.target)
        req=urllib.request.Request("http://"+target["ip"]+"/json/info")
        try:
            with self.health_http.open(req,timeout=self.HEALTH_TIMEOUT_SECONDS) as r:
                info=json.load(r)
            if not isinstance(info,dict):info={}
            now=time.monotonic()
            uptime=info.get("uptime")
            try:uptime=int(uptime) if uptime is not None else None
            except Exception:uptime=None
            live=info.get("live")
            live=bool(live) if live is not None else None
            lm=info.get("lm")
            lip=str(info.get("lip") or "")
            with self.lock:
                previous_online=self.health_online
                previous_uptime=self.health_uptime
                was_pending=self.reboot_pending
                self.health_online=True
                self.health_error=""
                self.health_failures=0
                self.last_health_ok=time.time()
                self.health_uptime=uptime
                self.health_live=live
                self.health_lm=lm
                self.health_lip=lip
                desired_mode=self.desired.get("mode")
                rebooted=previous_uptime is not None and uptime is not None and uptime+2<previous_uptime
                if rebooted:
                    self.reboots_detected+=1
                recovered=previous_online is False
                manual_ready=was_pending and now-self.reboot_requested_at>=1.0 and (rebooted or recovered or (uptime is not None and uptime<60))
                need_live_repair=(desired_mode=="stream" and live is False and self.last_udp and now-self.last_udp<5 and now-self.last_resync>=self.RESYNC_MIN_INTERVAL_SECONDS)
                if rebooted or manual_ready or need_live_repair:
                    self.resync_requested=desired_mode=="stream"
                    self.last_resync=now
                if manual_ready:
                    self.reboot_pending=False
                    self.recoveries+=1
            if rebooted:
                self._event("normal","WLED-STATE",f"{target['name']} redémarrage détecté",ip=target["ip"],uptime=uptime)
            elif previous_online is False:
                self._event("normal","WLED-STATE",f"{target['name']} revenu sur le réseau",ip=target["ip"],uptime=uptime)
            if need_live_repair:
                self._event("normal","WLED-STATE",f"{target['name']} mode realtime absent malgré le flux ; resynchronisation",ip=target["ip"],live=live,lm=lm,lip=lip)
            return True
        except Exception as exc:
            msg=str(exc)
            with self.lock:
                previous_online=self.health_online
                self.health_failures+=1
                self.health_error=msg
                # One timeout is tolerated because some ESPs have a fragile web
                # server while UDP realtime remains perfectly healthy.
                if self.health_failures>=2 or self.reboot_pending:self.health_online=False
            if previous_online is not False and (self.health_failures>=2 or self.reboot_pending):
                self._event("normal","WLED-STATE",f"{target['name']} contrôle HTTP indisponible ; flux UDP maintenu",ip=target["ip"],error=msg)
            return False

    def _health_loop(self):
        # Do not add load during application startup; then use a very light
        # independent probe. Reboot recovery gets a short temporary cadence.
        if self.health_wake.wait(2.0):
            self.health_wake.clear()
        while not self.done.is_set():
            self._health_probe()
            with self.lock:
                pending=self.reboot_pending
                online=self.health_online
                mode=self.desired.get("mode")
            interval=self.HEALTH_REBOOT_SECONDS if pending else self.HEALTH_OFFLINE_SECONDS if online is False else 30.0 if mode=="hold" else self.HEALTH_INTERVAL_SECONDS
            self.health_wake.wait(interval);self.health_wake.clear()

    def _perform_reboot(self,reason):
        target=copy.deepcopy(self.target)
        self.state="REBOOTING";self.detail=reason or "Redémarrage WLED"
        self._release_realtime()
        accepted=False
        try:
            self._http({"rb":True},timeout=.8)
            accepted=True;self.control_path="http-reboot"
        except Exception:
            # A reboot can cut the HTTP response before it reaches the PC. Send
            # the same command over WLED's JSON UDP notifier as a best effort.
            accepted=self._udp_state({"rb":True})
            self.control_path="udp-reboot" if accepted else self.control_path
        with self.lock:
            self.health_online=False
            self.health_failures=max(2,self.health_failures)
            self.health_error="Redémarrage en cours"
            self.reboot_requested_at=time.monotonic()
        self.health_wake.set()
        self._event("normal","WLED-STATE",f"{target['name']} redémarrage envoyé",ip=target["ip"],accepted=accepted,path=self.control_path)

    def _resync_stream(self):
        target=copy.deepcopy(self.target)
        self.state="RECOVERING";self.detail="Réinitialisation du mode temps réel"
        self._release_realtime()
        # Raptor Link scales its own RGB values, so WLED must not remain at
        # brightness 0 after a reboot/power cut.
        self._udp_state({"live":False,"on":True,"bri":255,"tt":0})
        with self.lock:
            self.resync_requested=False
            self.commanded_off=False
            self.pending_confirmation=False
            self.recoveries+=1
            self.last_resync=time.monotonic()
        self._event("normal","WLED-STATE",f"{target['name']} resynchronisé après redémarrage/perte realtime",ip=target["ip"])

    def _http_success(self):
        with self.lock:
            self.last_http_ok=time.time()
            self.last_http_error=""
            self.retries=0
            self.next_http_try=0.0

    def _http_failure(self,exc):
        msg=str(exc)
        with self.lock:
            self.last_http_error=msg
            self.retries+=1
            delay=min(self.HTTP_RETRY_MAX_SECONDS,self.HTTP_RETRY_BASE_SECONDS*(2**min(8,self.retries-1)))
            self.next_http_try=time.monotonic()+delay
        return msg,delay

    def _http(self,payload,timeout=None):
        target=copy.deepcopy(self.target)
        timeout=self.HTTP_TIMEOUT_SECONDS if timeout is None else max(.2,float(timeout))
        req=urllib.request.Request(
            "http://"+target["ip"]+"/json/state",
            data=json.dumps(payload,separators=(",",":")).encode(),
            headers={"Content-Type":"application/json"},
        )
        try:
            with self.http.open(req,timeout=timeout) as r:
                data=json.load(r)
            self._http_success()
            self._event("detailed","WLED-HTTP",f"{target['name']} commande OK",ip=target["ip"],payload=payload)
            return data
        except Exception as exc:
            msg,delay=self._http_failure(exc)
            self._event("normal","WLED-HTTP",f"{target['name']} erreur HTTP",ip=target["ip"],error=msg,payload=payload,retry_in=round(delay,1))
            raise

    def _http_state(self,timeout=.8):
        """Lightweight recovery probe used only after the HTTP circuit cooldown."""
        target=copy.deepcopy(self.target)
        req=urllib.request.Request("http://"+target["ip"]+"/json/state")
        try:
            with self.http.open(req,timeout=max(.2,float(timeout))) as r:
                data=json.load(r)
            self._http_success()
            return data if isinstance(data,dict) else {}
        except Exception as exc:
            msg,delay=self._http_failure(exc)
            self._event("normal","WLED-HTTP",f"{target['name']} toujours indisponible",ip=target["ip"],error=msg,retry_in=round(delay,1))
            raise

    def _matches_desired(self,mode,payload,state):
        if not isinstance(state,dict):return False
        if mode=="off":
            return state.get("on") is False
        if mode=="preset":
            try:return bool(state.get("on")) and int(state.get("ps",-1))==int(payload.get("ps",-2))
            except Exception:return False
        if mode=="restore":
            if "on" in payload and bool(state.get("on"))!=bool(payload.get("on")):return False
            if "bri" in payload:
                try:
                    if int(state.get("bri",-1))!=int(payload.get("bri")):return False
                except Exception:return False
            # Segment/transition restoration is richer than a cheap equality test.
            return "seg" not in payload
        return False

    def _apply_control(self,payload):
        """Apply one state change without ever hammering a sick WLED web server."""
        if time.monotonic()>=self.next_http_try:
            try:
                self._http(payload)
                self.control_path="http"
                return "http"
            except Exception:
                # Keep the circuit open even if a future/custom HTTP transport
                # raises before _http_failure() can record the failure.
                with self.lock:
                    if self.next_http_try<=time.monotonic():
                        self.next_http_try=time.monotonic()+self.HTTP_RETRY_BASE_SECONDS
                pass
        if self._udp_state(payload):
            self.control_path="udp-json-fallback"
            return "udp"
        return ""

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
        # Keep a tiny RGB fingerprint in diagnostics. It tells us whether the
        # source itself is moving even when the physical strip is not.
        step=max(1,len(frame)//4)
        sample=tuple(tuple(x) for x in frame[::step][:5])
        signature=(len(frame),sample)
        if signature!=self.last_frame_signature:
            self.last_frame_signature=signature
            self.frame_changes+=1
            self.last_frame_change=time.monotonic()
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
        self.fallback_batches+=1
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
            with self.lock:
                reboot_now=self.reboot_requested
                if reboot_now:self.reboot_requested=False
                resync_now=self.resync_requested
            if reboot_now:
                self._perform_reboot(self.reboot_reason)
                previous_mode=mode
                self.done.wait(.15)
                continue
            if mode=="stream" and resync_now:
                self._resync_stream()
                previous_mode="hold"
                self.done.wait(.12)
                continue

            if mode=="hold":
                if previous_mode!="hold":
                    self.state="HOLD";self.detail=d.get("reason","En attente")
                previous_mode="hold"
                self.done.wait(.05)
                continue

            if mode=="stream":
                frame=d.get("frame")
                # Entering realtime always prepares WLED once, even when the ESP
                # rebooted outside Raptor Link. This fixes the case where WLED
                # comes back with on=false or brightness=0 and UDP frames merely
                # produce a faint flicker.
                if self.commanded_off or previous_mode!="stream":
                    self.state="WAKING";self.detail="Préparation du mode temps réel"
                    self._release_realtime()
                    self._udp_state({"live":False,"on":True,"bri":255,"tt":0})
                    self.pending_confirmation=False
                    self.commanded_off=False
                    self.applied_rev=rev
                if frame:
                    interval=1/max(1,int(d.get("fps",25)))
                    if now>=next_udp:
                        if self._send_frame(frame):
                            if self.state!="STREAMING":
                                self._event("detailed","WLED-STATE",f"{self.target['name']} streaming actif",ip=self.target["ip"])
                            with self.lock:reboot_pending=self.reboot_pending
                            self.state="REBOOTING" if reboot_pending else "STREAMING"
                            self.detail="Redémarrage WLED en cours" if reboot_pending else d.get("reason","Synchronisé")
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
                self.state="APPLYING";self.detail=d.get("reason","Commande WLED")
                path=self._apply_control(payload)
                if not path:
                    self.state="CONTROL_ERROR";self.detail=self.last_http_error or "Commande WLED impossible"
                    self.done.wait(1)
                    previous_mode=mode
                    continue
                used_fallback=path!="http"
                self.pending_confirmation=used_fallback
                self.applied_rev=rev
                if mode=="off":
                    self.commanded_off=True;self.state="OFF_UDP" if used_fallback else "OFF"
                elif mode=="preset":
                    self.commanded_off=False;self.state="AUTONOMOUS_UDP" if used_fallback else "AUTONOMOUS"
                else:
                    self.commanded_off=not bool(payload.get("on",True));self.state="RESTORED_UDP" if used_fallback else "RESTORED"
                self.detail=d.get("reason","")

            # UDP fallback is fire-and-forget. If the controller was actually offline,
            # resend the desired state only after an exponential cooldown and confirm
            # it through HTTP when the web server becomes healthy again.
            if rev==self.applied_rev and self.pending_confirmation and mode in ("off","preset","restore") and now>=self.next_http_try:
                payload=self._command_payload(d)
                try:
                    self.state="VERIFYING";self.detail="Vérification de la récupération WLED"
                    remote=self._http_state(.8)
                    if not self._matches_desired(mode,payload,remote):
                        self._http(payload)
                    self.pending_confirmation=False
                    self.control_path="http-recovered"
                    if mode=="off":self.state="OFF"
                    elif mode=="preset":self.state="AUTONOMOUS"
                    else:self.state="RESTORED"
                    self.detail=d.get("reason","")
                    self._event("normal","WLED-HTTP",f"{self.target['name']} communication HTTP rétablie",ip=self.target["ip"])
                except Exception:
                    # One UDP refresh per cooldown keeps the desired state convergent
                    # without recreating the old 2-second HTTP retry storm.
                    self._udp_state(payload)
                    self.control_path="udp-json-fallback"
                    if mode=="off":self.state="OFF_UDP"
                    elif mode=="preset":self.state="AUTONOMOUS_UDP"
                    else:self.state="RESTORED_UDP"
                    self.detail=d.get("reason","")+" · secours UDP"
            previous_mode=mode
            self.done.wait(.05)

        try:self.udp.close()
        except Exception:pass

    def close(self):
        self.done.set();self.health_wake.set()
        self.thread.join(timeout=3);self.health_thread.join(timeout=2)
