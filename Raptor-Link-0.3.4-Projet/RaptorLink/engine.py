import datetime
from concurrent.futures import ThreadPoolExecutor
from features import extras, gate, window, effect, render, Fade, clock, due_alarm
from capture import Capture
from screens import screen_key
from activity import Presence
import ctypes as C
import copy
import ipaddress
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import urllib.request

from cue_base import Cue, Device, Filter, Position, Color, check, Idle, sources
from openrgb_source import OpenRGBWorker
from msi_source import MsiWorker, install_official_sdk, sdk_status
from wled_worker import WledWorker
from diagnostics import Diagnostics

HTTP=urllib.request.build_opener(urllib.request.ProxyHandler({}))
DEFAULT={'version':1,'settings':{'idle_seconds':300,'fps':25,'lock_off':True,'auto_sync':False,'startup':False,'language':'fr','check_updates':True,'experimental_rgb':False,'diagnostic_level':'normal','diagnostic_days':7,'diagnostic_max_mb':50},'targets':[]}

def address(value):
    ip=ipaddress.ip_address(value)
    if ip.version!=4 or not (ip.is_private and not ip.is_loopback and not ip.is_unspecified and not ip.is_multicast):
        raise ValueError('Une adresse IPv4 locale est nécessaire.')
    return str(ip)

def http(ip,path,payload=None):
    address(ip)
    req=urllib.request.Request('http://'+ip+path,data=None if payload is None else json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
    with HTTP.open(req,timeout=2) as r:
        return json.load(r)

def validate(raw):
    if not isinstance(raw,dict): raise ValueError('Configuration invalide')
    cfg=copy.deepcopy(DEFAULT)
    cfg['settings'].update(raw.get('settings',{}))
    s=cfg['settings']
    for key,lo,hi in [('fps',1,40),('idle_seconds',0,86400)]:
        s[key]=int(s[key])
        if not lo<=s[key]<=hi: raise ValueError('Réglage hors limites : '+key)
    for k in ['lock_off','auto_sync','startup','check_updates','experimental_rgb']: s[k]=bool(s.get(k,True if k=='check_updates' else False))
    if s['language'] not in ['fr','en']: s['language']='fr'
    s['diagnostic_level']=str(s.get('diagnostic_level','normal'))
    if s['diagnostic_level'] not in ['off','normal','detailed','trace']:s['diagnostic_level']='normal'
    s['diagnostic_days']=max(1,min(30,int(s.get('diagnostic_days',7))))
    s['diagnostic_max_mb']=max(5,min(500,int(s.get('diagnostic_max_mb',50))))
    targets=raw.get('targets',[])
    if not isinstance(targets,list) or len(targets)>32: raise ValueError('32 éclairages maximum')
    seen=set()
    for raw_t in targets:
        t=copy.deepcopy(raw_t)
        t['ip']=address(t['ip'])
        if t['ip'] in seen: raise ValueError('Une seule fiche par adresse WLED. Ajoutez plusieurs associations dans sa fiche.')
        seen.add(t['ip'])
        t['name']=str(t.get('name','WLED'))[:70]
        t['count']=int(t['count']);t['port']=int(t.get('port',21324))
        if not 1<=t['count']<=10000 or not 1<=t['port']<=65535: raise ValueError('Longueur ou port invalide')
        t['enabled']=bool(t.get('enabled',True))
        t['brightness']=int(t.get('brightness',100))
        if not 0<=t['brightness']<=100: raise ValueError('Luminosité invalide')
        t['idle_seconds']=None if t.get('idle_seconds') is None else int(t['idle_seconds'])
        if t['idle_seconds'] is not None and not 0<=t['idle_seconds']<=86400: raise ValueError('Délai invalide')
        t['on_stop']=t.get('on_stop','off')
        if t['on_stop'] not in ['off','restore','preset']: raise ValueError('Action de sortie invalide')
        t['preset']=int(t.get('preset',1))
        if not 1<=t['preset']<=250: raise ValueError('Preset : 1 à 250')
        occupied=set()
        routes=t.get('routes',[])
        if len(routes)>64: raise ValueError('64 associations maximum')
        for r in routes:
            r['start']=int(r['start']);r['end']=int(r['end'])
            if not 1<=r['start']<=r['end']<=t['count']: raise ValueError('Plage LED hors du ruban')
            indexes=set(range(r['start'],r['end']+1))
            if occupied & indexes: raise ValueError('Deux associations se chevauchent sur '+t['name'])
            occupied|=indexes
            r['device']=str(r.get('device',''))[:128]
            ids=r.get('ids',[])
            if not isinstance(ids,list) or len(ids)>512: raise ValueError('Sélection source invalide')
            r['ids']=list(dict.fromkeys(int(i) for i in ids))
            if any(not 0<=i<=0xffffffff for i in r['ids']): raise ValueError('Identifiant LED invalide')
            r['reverse']=bool(r.get('reverse',False))
            r['mapping']=r.get('mapping','stretch')
            if r['mapping'] not in ['stretch','repeat']: raise ValueError('Répartition invalide')
        t['routes']=routes
        cfg['targets'].append(t)
    cfg=extras(cfg,raw)
    for p in cfg['profiles']:
        p['targets']=validate({'settings':cfg['settings'],'targets':p['targets']})['targets']
    return cfg

def render_target(target,colors):
    pixels=[(0,0,0)]*target['count']
    for r in target['routes']:
        src=colors.get(r['device'],{})
        ids=r['ids']
        if not ids or any(i not in src for i in ids): continue
        n=r['end']-r['start']+1
        ordered=list(reversed(ids)) if r.get('reverse') else ids
        for j in range(n):
            index=j%len(ordered) if r.get('mapping')=='repeat' else min(len(ordered)-1,j*len(ordered)//n)
            pixels[r['start']-1+j]=src[ordered[index]]
    gain=target['brightness']/100
    return [tuple(round(v*gain) for v in c) for c in pixels]

def packets(pixels,timeout=5):
    if len(pixels)<=490:
        return [bytes([2,timeout])+bytes(v for c in pixels for v in c)]
    output=[]
    for start in range(0,len(pixels),480):
        output.append(bytes([4,timeout,start>>8,start&255])+bytes(v for c in pixels[start:start+480] for v in c))
    return output


def probe_target(t,initialize=False,demo=False):
    if demo:return {} if initialize else None
    info=http(t['ip'],'/json/info')
    if info.get('leds',{}).get('count')!=t['count']:raise ValueError('Nombre de LED différent : utilisez Vérifier.')
    if not initialize:return None
    # Capture the state for a later restore, but never switch WLED off while
    # probing. At Windows startup the network can be late and repeated probes
    # must remain read-only or the strip visibly blinks.
    st=http(t['ip'],'/json/state')
    return {k:st[k] for k in ['on','bri','transition','mainseg','seg'] if k in st}

class GenericCue(Cue):
    def scan(self):
        ds,n=(Device*64)(),C.c_int()
        check(self.dll.CorsairGetDevices(C.byref(Filter(-1)),64,ds,C.byref(n)))
        out=[]
        for d in ds[:n.value]:
            try:
                ps,np=(Position*512)(),C.c_int()
                check(self.dll.CorsairGetLedPositions(d.id,512,ps,C.byref(np)))
                out.append({'id':d.id.decode(errors='replace'),'model':d.model.decode(errors='replace'),'serial':d.serial.decode(errors='replace'),'type':d.type,
                            'positions':[{'id':p.id,'x':p.x,'y':p.y,'group':p.id>>16} for p in ps[:np.value]]})
            except RuntimeError: continue
        self.devices=out
        return out
    def read_device(self,d):
        cols=(Color*len(d['positions']))()
        for c,p in zip(cols,d['positions']): c.id=p['id']
        check(self.dll.CorsairGetLedColors(d['id'].encode(),len(cols),cols))
        return {c.id:(c.r,c.g,c.b) for c in cols}

class IcueWorker:
    """Run the native Corsair SDK in a disposable child process.

    A native SDK call may stop returning after a long session or a Windows
    sleep/resume. Keeping it out of the engine process means the watchdog can
    kill and restart only the iCUE bridge without freezing presence detection,
    WLED output, the web UI or the Stop command.
    """
    def __init__(self,logger,command=None):
        self.log=logger;self.lock=threading.RLock();self.done=threading.Event();self.process=None
        self.required=set();self.force_scan=True;self.interval=1/25;self.devices=[];self.colors={};self.error=''
        self.busy_since=None;self.operation='';self.last_progress=time.monotonic();self.last_ok=0;self.restarts=0
        self.color_updates=0;self.color_changes=0;self.last_color_change=0.0
        self.command=command or self._default_command()
        self.thread=threading.Thread(target=self.loop,daemon=True,name='RaptorLink-iCUE-bridge');self.thread.start()
    def _default_command(self):
        root=Path(__file__).resolve().parent
        runtime=root/'runtime'/'python.exe'
        python=str(runtime if runtime.exists() else Path(sys.executable))
        return [python,'-u',str(root/'icue_worker.py')]
    def configure(self,required,force_scan=False,fps=25):
        with self.lock:
            self.required={str(x) for x in required if x}
            self.interval=1/max(1,min(40,int(fps)))
            if force_scan:self.force_scan=True
    def snapshot(self):
        now=time.monotonic();kill=None
        with self.lock:
            stalled=self.busy_since is not None and now-self.busy_since>4
            if stalled and self.process is not None and self.process.poll() is None:
                kill=self.process
            state={'devices':copy.deepcopy(self.devices),'colors':copy.deepcopy(self.colors),'error':self.error,
                   'stalled':stalled,'operation':self.operation if stalled else '',
                   'age':max(0,now-self.last_progress),'last_ok':self.last_ok,'restarts':self.restarts,
                   'color_updates':self.color_updates,'color_changes':self.color_changes,
                   'color_change_age':None if not self.last_color_change else max(0,now-self.last_color_change)}
        if kill is not None:
            try:kill.kill()
            except Exception:pass
        return state
    def close(self):
        self.done.set();self._kill();self.thread.join(timeout=1)
    def _dispose(self,p):
        if p is None:return
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
        flags=getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0
        p=subprocess.Popen(self.command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                           text=True,encoding='utf-8',bufsize=1,cwd=str(Path(__file__).resolve().parent),creationflags=flags)
        with self.lock:self.process=p;self.restarts+=1
        return p
    def _publish(self,data=None,error=None):
        with self.lock:
            if data is not None:
                self.devices=copy.deepcopy(data.get('devices',[]))
                new_colors={str(dev):{int(k):tuple(v) for k,v in vals.items()} for dev,vals in data.get('colors',{}).items()}
                if data.get('ok'):
                    self.color_updates+=1
                    if new_colors and new_colors!=self.colors:
                        self.color_changes+=1;self.last_color_change=time.monotonic()
                self.colors=new_colors
                self.error=str(data.get('error',''));self.last_ok=time.monotonic() if data.get('ok') else self.last_ok
            if error is not None:self.error=str(error)
            self.last_progress=time.monotonic()
    def loop(self):
        process=None;last_scan=0;retry_at=0
        while not self.done.is_set():
            try:
                now=time.monotonic()
                if process is None or process.poll() is not None:
                    if now<retry_at:
                        self.done.wait(min(.2,retry_at-now));continue
                    process=self._start();last_scan=0
                with self.lock:
                    required=sorted(self.required);force=self.force_scan;self.force_scan=False;interval=self.interval
                do_scan=force or now-last_scan>5
                request={'required':required,'scan':do_scan}
                with self.lock:self.busy_since=time.monotonic();self.operation='détection iCUE' if do_scan else 'lecture des couleurs iCUE'
                process.stdin.write(json.dumps(request,separators=(',',':'))+'\n');process.stdin.flush()
                line=process.stdout.readline()
                if not line:raise RuntimeError('pont iCUE interrompu')
                data=json.loads(line)
                with self.lock:self.busy_since=None;self.operation=''
                if do_scan:last_scan=time.monotonic()
                self._publish(data=data)
                if not data.get('ok') and data.get('fatal'):
                    raise RuntimeError(data.get('error','erreur iCUE'))
                self.done.wait(interval)
            except Exception as e:
                with self.lock:self.busy_since=None;self.operation=''
                self._publish(error=e)
                self._dispose(process)
                process=None;retry_at=time.monotonic()+1
        self._dispose(process)

class WinIdle(Presence):
    def __init__(self):
        super().__init__()
        self.user.OpenInputDesktop.argtypes=[C.c_uint32,C.c_int,C.c_uint32]
        self.user.OpenInputDesktop.restype=C.c_void_p
        self.user.CloseDesktop.argtypes=[C.c_void_p]
        self.user.SwitchDesktop.argtypes=[C.c_void_p]
        self.user.SwitchDesktop.restype=C.c_int
    def locked(self):
        handle=self.user.OpenInputDesktop(0,False,0x0100)
        if not handle: return True
        try: return not bool(self.user.SwitchDesktop(handle))
        finally: self.user.CloseDesktop(handle)

class Engine:
    def __init__(self,path,demo=False):
        self.path=path;self.demo=demo;self.lock=threading.RLock();self.done=threading.Event()
        self.config=validate(DEFAULT);self.devices=[];self.colors={};self.logs=[];self.active=[];self.original={}
        self.want_run=False;self.running=False;self.keep_awake=False;self.status='Démarrage';self.icue=None;self.msi=None;self.openrgb=None;self.icue_notice=False;self.msi_notice=False;self.openrgb_notice=False;self.loop_heartbeat=time.monotonic()
        self.plan_ip='';self.plan_frame=[];self.preview_device='';self.idle=0;self.is_locked=False;self.target_status={};self.revision=0;self.scan_requested=True
        config_error=''
        try:
            if path.exists(): self.config=validate(json.loads(path.read_text(encoding='utf-8')))
        except Exception as e: config_error='Configuration non chargée : '+str(e)
        ds=self.config['settings']
        self.diag=Diagnostics(path.parent/'diagnostics',ds.get('diagnostic_level','normal'),ds.get('diagnostic_days',7),ds.get('diagnostic_max_mb',50))
        self.wled_workers={}
        self.want_run=self.config['settings']['auto_sync']
        self.tests={};self.color_tests={};self.fades={};self.history={};self.frames={};self.alarm_seen={};self.alarms={};self.previews={};self.gate_states={}
        if config_error:self.log(config_error,'CONFIG')
        try:self.alarm_seen=json.loads(path.with_name('alarms-fired.json').read_text())
        except Exception:pass
        self.diag.event('normal','ENGINE','Raptor Link Core 2 démarré',demo=self.demo,auto_sync=self.want_run)
        self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start()
    def log(self,msg,category='ENGINE',level='normal',**fields):
        with self.lock:
            self.logs.append(time.strftime('%H:%M:%S')+'  '+str(msg));self.logs=self.logs[-80:]
        try:self.diag.event(level,category,str(msg),**fields)
        except Exception:pass
    def worker(self,target):
        ip=target['ip']
        w=self.wled_workers.get(ip)
        if w is None and not self.demo:
            w=WledWorker(target,self.log,self.diag);self.wled_workers[ip]=w
            self.log(target['name']+' : worker WLED Core 2 créé','WLED-STATE','detailed',ip=ip)
        elif w is not None:w.update_target(target)
        return w
    def snapshot(self):
        with self.lock:
            workers={ip:w.snapshot() for ip,w in self.wled_workers.items()}
            return copy.deepcopy({'devices':self.devices,'colors':self.colors,'running':self.running,'requested':self.want_run,
              'status':self.status,'logs':self.logs[-15:],'idle':int(self.idle),'locked':self.is_locked,'keep_awake':self.keep_awake,
              'plan_ip':self.plan_ip,'plan_frame':self.plan_frame,'targets':self.target_status,'demo':self.demo,'revision':self.revision,'audio_level':self.capture.level if hasattr(self,'capture') else 0,'frames':self.previews,'engine_age':max(0,time.monotonic()-self.loop_heartbeat),'icue':self.icue.snapshot() if self.icue else {},'msi':self.msi.snapshot() if self.msi else {},'openrgb':self.openrgb.snapshot() if self.openrgb else {},'wled_workers':workers,'capture_error':' ; '.join(filter(None,[getattr(getattr(self,'capture',None),'error',''),getattr(getattr(self,'capture',None),'screen_error','')]))})
    def diagnostic_report(self,minutes=30):
        state=self.snapshot()
        context={
            'running':state['running'],
            'requested':state['requested'],
            'status':state['status'],
            'idle_seconds':state['idle'],
            'locked':state['locked'],
            'keep_awake':state['keep_awake'],
            'icue_stalled':bool(state.get('icue',{}).get('stalled')),
            'icue_restarts':state.get('icue',{}).get('restarts',0),
            'targets':json.dumps(state.get('targets',{}),ensure_ascii=False),
            'wled_workers':json.dumps(state.get('wled_workers',{}),ensure_ascii=False),
        }
        return self.diag.report(int(minutes),context)
    def save(self,cfg):
        cfg=validate(cfg)
        with self.lock:
            self.want_run=False
            temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps(cfg,indent=2,ensure_ascii=False),encoding='utf-8');os.replace(temp,self.path)
            self.config=cfg;self.revision+=1
        ds=cfg['settings'];self.diag.configure(ds.get('diagnostic_level'),ds.get('diagnostic_days'),ds.get('diagnostic_max_mb'))
        self.log('Configuration enregistrée. Relancez la synchronisation pour appliquer les associations.','CONFIG')
    def msi_status(self):
        base=sdk_status(self.path.parent)
        if self.msi:
            state=self.msi.snapshot()
            base.update({
                'worker_available':bool(state.get('available')),
                'connected':bool(state.get('last_ok')),
                'error':state.get('error',''),
                'stalled':bool(state.get('stalled')),
                'devices':len(state.get('devices',[])),
                'diagnostic':copy.deepcopy(state.get('diagnostic',{})),
                'restarts':state.get('restarts',0),
                'last_ok':state.get('last_ok',0),
            })
        else:
            base.update({'worker_available':False,'connected':False,'error':'','stalled':False,'devices':0,'diagnostic':{},'restarts':0,'last_ok':0})
        return base

    def install_msi_sdk(self):
        if self.msi:
            self.msi.close()
            self.msi=None
            time.sleep(.15)
        try:
            path=install_official_sdk(self.path.parent)
        finally:
            if not self.demo and self.config['settings'].get('experimental_rgb') and self.msi is None:self.msi=MsiWorker(self.log,self.path.parent)
        self.scan_requested=True
        self.log('SDK MSI Mystic Light officiel installé pour Raptor Link : '+str(path))
        return self.msi_status()

    def stop(self):
        self.want_run=False
    def shutdown(self):
        self.want_run=False;self.done.set();self.thread.join(timeout=15)
        # restore() is requested by the engine loop during shutdown. Give the
        # per-WLED workers enough time to leave realtime mode and apply OFF /
        # preset / restore before terminating them.
        if self.wled_workers:time.sleep(2.4)
        for w in list(self.wled_workers.values()):
            try:w.close()
            except Exception:pass
        self.wled_workers.clear()
        try:self.diag.close()
        except Exception:pass
    def restore(self):
        now=datetime.datetime.now()
        for t in self.active:
            if self.demo:continue
            try:
                w=self.worker(t)
                if not window(t['schedule'],now):
                    w.off('Hors horaires à l’arrêt',0)
                elif t['on_stop']=='restore':
                    w.restore(self.original.get(t['ip'],{'on':False}),'Rétablissement de l’état initial')
                elif t['on_stop']=='preset':
                    w.preset(t['preset'],'Preset WLED à l’arrêt',0)
                else:
                    w.off('Arrêt de la synchronisation',0)
            except Exception as e:self.log(t['name']+' : restauration impossible ('+str(e)+')','WLED-STATE')
        self.active=[];self.original={};self.running=False;self.target_status={};self.gate_states={}
    def prepare(self):
        with self.lock:
            cfg=copy.deepcopy(self.config); revision=self.revision
        self.active=[t for t in cfg['targets'] if t['enabled']]
        self.run_revision=revision;self.run_settings=cfg['settings'];self.running=True
        self.fades={};self.history={};self.frames={};self.target_status={};self.alarms={};self.gate_states={}
        for t in self.active:
            if not self.demo:self.worker(t).hold('Préparation de la synchronisation')
        self.log('Synchronisation Core 2 démarrée : un worker indépendant par WLED.','ENGINE')
    def reboot_wled(self,ip,reason="Redémarrage manuel"):
        with self.lock:t=next((copy.deepcopy(t) for t in self.config['targets'] if t['ip']==ip),None)
        if not t:raise ValueError('WLED inconnu.')
        if self.demo:return {'ok':True,'queued':True,'name':t['name']}
        queued=self.worker(t).reboot(reason)
        self.log(t['name']+' : redémarrage WLED demandé','WLED-STATE','normal',ip=t['ip'])
        return {'ok':True,'queued':bool(queued),'name':t['name']}

    def reboot_all_wled(self):
        with self.lock:targets=copy.deepcopy(self.config['targets'])
        if self.demo:return {'ok':True,'count':len(targets)}
        def sequence():
            for t in targets:
                if self.done.is_set():break
                try:self.worker(t).reboot('Redémarrage global demandé')
                except Exception as e:self.log(t['name']+' : redémarrage impossible ('+str(e)+')','WLED-STATE')
                self.done.wait(.6)
        threading.Thread(target=sequence,daemon=True,name='RaptorLink-WLED-reboot-all').start()
        self.log('Redémarrage de tous les WLED demandé ('+str(len(targets))+')','WLED-STATE')
        return {'ok':True,'count':len(targets)}

    def color_diagnostic(self,ip):
        t=next((t for t in self.active if t['ip']==ip),None)
        if not self.running or not t:raise ValueError('Démarrez la synchronisation avant le diagnostic couleurs.')
        now=time.monotonic();self.color_tests[ip]=(now,now+8.0)
        self.log(t['name']+' : diagnostic couleurs rouge/vert/bleu/blanc démarré','WLED-STATE','normal',ip=ip)
        return {'ok':True,'duration':8}

    def identify(self,ip):
        t=next((t for t in self.config['targets'] if t['ip']==ip),None)
        if not t:raise ValueError('Enregistrez cet éclairage avant le test.')
        if not window(t['schedule'],datetime.datetime.now()):raise ValueError('Test refusé : cet éclairage est hors de sa plage horaire.')
        if self.running:self.tests[ip]=time.monotonic()+3;return
        if getattr(self,'test_busy',False):raise ValueError('Un test est déjà en cours.')
        self.test_busy=True
        def test():
            state=None
            try:
                if self.demo:return
                state=http(ip,'/json/state')
                http(ip,'/json/state',{'on':False,'transition':0})
                for i in range(12):
                    if self.done.is_set() or self.want_run:break
                    allowed=window(t['schedule'],datetime.datetime.now())
                    for p in packets([(80,80,80) if i%2 and allowed else (0,0,0)]*t['count']):self.udp.sendto(p,(ip,t['port']))
                    self.done.wait(.25)
            except Exception as e:self.log('Test : '+str(e))
            finally:
                if state is not None and not self.want_run:
                    try:
                        self.udp.sendto(bytes([2,0]),(ip,t['port']))
                        http(ip,'/json/state',{k:state[k] for k in ['on','bri','seg'] if k in state} if window(t['schedule'],datetime.datetime.now()) else {'on':False})
                    except Exception:pass
                self.test_busy=False
        threading.Thread(target=test,daemon=True).start()
    def select_profile(self,index):
        cfg=copy.deepcopy(self.config);p=cfg['profiles'][int(index)];was=self.want_run
        cfg['targets']=copy.deepcopy(p['targets']);self.save(cfg);self.want_run=was
        self.log('Profil chargé : '+p['name'])
    def demo_scan(self):
        key=[]
        for row in range(6):
            for col in range(20): key.append({'id':row*20+col+1,'x':col*19,'y':row*19+25,'group':0})
        key += [{'id':131073+i,'x':i*21,'y':0,'group':2} for i in range(19)]
        self.devices=[{'id':'demo-keyboard','model':'K95 RGB PLATINUM · Démonstration','type':1,'positions':key},
         {'id':'demo-ram','model':'VENGEANCE RGB DDR5 · Démonstration','type':128,'positions':[{'id':524289+i,'x':0,'y':i*10,'group':8} for i in range(10)]},
         {'id':'demo-fan','model':'iCUE LINK QX RGB · Démonstration','type':32,'positions':[{'id':720897+i,'x':math.cos(i*math.tau/16)*40,'y':math.sin(i*math.tau/16)*40,'group':11} for i in range(16)]}]
    def loop(self):
        import colorsys
        self.udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);self.udp.settimeout(.2)
        pool=ThreadPoolExecutor(max_workers=4);pending={};restore_probe_done=set()
        self.capture=Capture();last_frame=time.monotonic();last_error=''
        if not self.demo:
            self.icue=IcueWorker(self.log)
            if self.config['settings'].get('experimental_rgb'):
                self.msi=MsiWorker(self.log,self.path.parent)
                self.openrgb=OpenRGBWorker(self.log)
        idle=None
        try:
            if not self.demo:idle=WinIdle()
            else:self.demo_scan()
            while not self.done.is_set():
                start=time.monotonic();self.loop_heartbeat=start;dt=min(.2,start-last_frame);last_frame=start;now=datetime.datetime.now()
                try:
                    experimental=bool(self.config['settings'].get('experimental_rgb',False))
                    if not self.demo:
                        if experimental and self.msi is None:self.msi=MsiWorker(self.log,self.path.parent)
                        if experimental and self.openrgb is None:self.openrgb=OpenRGBWorker(self.log)
                        if not experimental and self.msi is not None:
                            self.msi.close();self.msi=None;self.msi_notice=False
                        if not experimental and self.openrgb is not None:
                            self.openrgb.close();self.openrgb=None;self.openrgb_notice=False
                    if self.running and (not self.want_run or self.run_revision!=self.revision):
                        for f in pending.values():f.cancel()
                        pending={};restore_probe_done=set();self.restore()
                    cue_state={};msi_state={};openrgb_state={}
                    if not self.demo:
                        self.idle=idle.seconds();self.is_locked=idle.locked()
                        cue_state=self.icue.snapshot() if self.icue else {}
                        msi_state=self.msi.snapshot() if self.msi else {}
                        openrgb_state=self.openrgb.snapshot() if self.openrgb else {}
                        cue_devices=copy.deepcopy(cue_state.get('devices',[]))
                        for d in cue_devices:d['provider']='icue'
                        msi_devices=copy.deepcopy(msi_state.get('devices',[]))
                        for d in msi_devices:d['provider']='msi'
                        openrgb_devices=copy.deepcopy(openrgb_state.get('devices',[]))
                        self.devices=cue_devices+msi_devices+openrgb_devices
                    for t in self.active:
                        for r in t['routes']:
                            if r['source'] not in ('icue','msi','openrgb'):continue
                            expected_provider={'icue':'icue','msi':'msi','openrgb':'openrgb'}[r['source']]
                            current=next((d for d in self.devices if d['id']==r['device'] and d.get('provider','icue')==expected_provider),None)
                            if current:r['device_model']=current['model'];r['device_serial']=current.get('serial','')
                            else:
                                candidates=[d for d in self.devices if d.get('provider','icue')==expected_provider]
                                matches=[d for d in candidates if (r.get('device_serial') and d.get('serial')==r['device_serial']) or (not r.get('device_serial') and r.get('device_model')==d['model'])]
                                if len(matches)==1 and set(r['ids'])<={p['id'] for p in matches[0]['positions']}:r['device']=matches[0]['id']
                    required={self.preview_device}|{r['device'] for t in self.config['targets']+self.active for r in t['routes'] if r.get('source')=='icue' or (experimental and r.get('source') in ('msi','openrgb'))}
                    if self.demo:
                        colors={}
                        for d in self.devices:
                            if d['id'] in required:
                                colors[d['id']]={p['id']:tuple(round(x*255) for x in colorsys.hsv_to_rgb((start*.1+p['x']/600+p['y']/600)%1,.85,1)) for p in d['positions']}
                    else:
                        force_scan=self.scan_requested
                        if self.icue:self.icue.configure({x for x in required if x and not str(x).startswith(('msi:','openrgb:'))},force_scan,self.config['settings']['fps'])
                        if self.msi:self.msi.configure({x for x in required if str(x).startswith('msi:')},force_scan,min(15,self.config['settings']['fps']))
                        if self.openrgb:self.openrgb.configure({x for x in required if str(x).startswith('openrgb:')},force_scan,min(25,self.config['settings']['fps']))
                        self.scan_requested=False
                        cue_state=self.icue.snapshot() if self.icue else {}
                        msi_state=self.msi.snapshot() if self.msi else {}
                        openrgb_state=self.openrgb.snapshot() if self.openrgb else {}
                        cue_devices=copy.deepcopy(cue_state.get('devices',[]))
                        for d in cue_devices:d['provider']='icue'
                        msi_devices=copy.deepcopy(msi_state.get('devices',[]))
                        for d in msi_devices:d['provider']='msi'
                        self.devices=cue_devices+msi_devices+copy.deepcopy(openrgb_state.get('devices',[]))
                        colors={}
                        if not cue_state.get('stalled'):colors.update(cue_state.get('colors',{}))
                        if not msi_state.get('stalled'):colors.update(msi_state.get('colors',{}))
                        colors.update(openrgb_state.get('colors',{}))
                        if cue_state.get('stalled') and not self.icue_notice:
                            self.log('iCUE ne répond plus pendant '+cue_state.get('operation','une opération')+' ; le moteur WLED reste actif.')
                            self.icue_notice=True
                        elif not cue_state.get('stalled') and self.icue_notice:
                            self.log('iCUE : communication rétablie.')
                            self.icue_notice=False
                        uses_msi=experimental and any(r.get('source')=='msi' for t in self.config['targets']+self.active for r in t.get('routes',[]))
                        if uses_msi and (not msi_state.get('available') or msi_state.get('error')) and not self.msi_notice:
                            detail=msi_state.get('error') or 'SDK MSI non installé'
                            self.log('MSI Mystic Light natif (Bêta) : '+str(detail))
                            self.msi_notice=True
                        elif uses_msi and msi_state.get('available') and not msi_state.get('error') and self.msi_notice:
                            self.log('MSI Mystic Light natif (Bêta) : communication rétablie.')
                            self.msi_notice=False
                        uses_openrgb=experimental and any(r.get('source')=='openrgb' for t in self.config['targets']+self.active for r in t.get('routes',[]))
                        if uses_openrgb and not openrgb_state.get('connected') and not self.openrgb_notice:
                            detail=openrgb_state.get('error') or 'serveur SDK indisponible'
                            self.log('OpenRGB (Bêta) : '+str(detail)+' — démarrez le serveur SDK local sur le port 6742.')
                            self.openrgb_notice=True
                        elif openrgb_state.get('connected') and self.openrgb_notice:
                            self.log('OpenRGB (Bêta) : connexion rétablie.')
                            self.openrgb_notice=False
                    self.colors=colors
                    if self.want_run and not self.running and not getattr(self,'test_busy',False):self.prepare()
                    routes=[r for t in self.active for r in t['routes']] if self.running else []
                    self.capture.screen_requests=tuple(set(screen_key(r) for r in routes if r['source']=='screen'))
                    self.capture.screen_enabled=not self.demo and not self.is_locked and any(r['source']=='screen' for r in routes)
                    self.capture.audio_mode=self.config['settings'].get('audio_mode','output')
                    self.capture.audio_output=self.config['settings'].get('audio_output','default') if any(r['source']=='audio' for r in routes) and not self.demo else None
                    self.capture.audio_device=int(self.config['settings'].get('audio_device',-1)) if any(r['source']=='audio' for r in routes) and not self.demo else -1
                    states={};frames={};self.plan_frame=[]
                    if self.running:
                        for t in self.active:
                            ip=t['ip'];worker=None if self.demo else self.worker(t)

                            # The normal realtime path performs no periodic HTTP health
                            # polling. Only targets configured to restore their previous
                            # state get one best-effort snapshot at the beginning.
                            if t['on_stop']=='restore' and ip in pending and pending[ip].done():
                                try:
                                    original=pending.pop(ip).result()
                                    if original is not None:self.original[ip]=original
                                    self.log(t['name']+' : état initial mémorisé','WLED-HTTP','detailed',ip=ip)
                                except Exception as e:
                                    pending.pop(ip,None)
                                    self.log(t['name']+' : état initial indisponible ('+str(e)+')','WLED-HTTP','detailed',ip=ip)
                            if not self.demo and t['on_stop']=='restore' and ip not in self.original and ip not in pending and ip not in restore_probe_done:
                                restore_probe_done.add(ip);pending[ip]=pool.submit(probe_target,t,True,False)

                            errors=[];source_missing=False
                            for i,r in enumerate(t['routes']):
                                if r['source'] in ('msi','openrgb') and not experimental:
                                    errors.append('Association '+str(i+1)+' : source RGB expérimentale désactivée');source_missing=True
                                elif r['source'] in ('icue','msi','openrgb') and (not r['ids'] or any(x not in colors.get(r['device'],{}) for x in r['ids'])):
                                    errors.append('Association '+str(i+1)+' : appareil ou LED indisponibles');source_missing=True
                            if not self.demo and on_screen_missing(t,self.capture):
                                errors.append('Écran indisponible : vérifiez la sélection dans la zone');source_missing=True
                            if not t['routes']:errors.append('Ajoutez une association')

                            due=due_alarm(t,now,self.alarm_seen)
                            if due is not None:
                                try:
                                    alarm_file=self.path.with_name('alarms-fired.json');tmp=alarm_file.with_suffix('.tmp');tmp.write_text(json.dumps(self.alarm_seen));os.replace(tmp,alarm_file)
                                except OSError as e:self.log('Mémorisation du réveil : '+str(e),'AUTOMATION')
                                if window(t['schedule'],now):self.alarms[ip]=(start+due['duration'],copy.deepcopy(due))
                            alarm_end,alarm=self.alarms.get(ip,(0,None));alarm_on=start<alarm_end
                            on,label=gate(t,self.run_settings,now,self.idle,self.is_locked,self.keep_awake,alarm_on)
                            gate_state=(bool(on),label)
                            if self.gate_states.get(ip)!=gate_state:
                                previous=self.gate_states.get(ip);self.gate_states[ip]=gate_state
                                self.log(t['name']+' : '+label,'PRESENCE','detailed',ip=ip,on=bool(on),idle=round(self.idle,1),locked=self.is_locked,previous=previous)

                            testing=start<self.tests.get(ip,0) and on
                            color_test=self.color_tests.get(ip)
                            color_testing=bool(color_test and start<color_test[1] and on)
                            autonomous=len(t['routes'])==1 and t['routes'][0].get('source')=='wled_preset'

                            # A source that is still booting never sends a black frame.
                            # The independent WLED worker keeps its previous output.
                            if source_missing and on and not alarm_on and not testing and not color_testing:
                                if ip not in self.frames:
                                    if worker:worker.hold('En attente de la source')
                                    states[ip]=('; '.join(errors)+' · ' if errors else '')+'En attente de la source…'
                                    frames[ip]=[]
                                    continue
                                frame=copy.deepcopy(self.frames[ip]);label='Source en reconnexion'
                                autonomous=False
                            elif autonomous and not alarm_on and not testing:
                                preset=t['routes'][0].get('wled_preset',1)
                                if self.demo:
                                    states[ip]=label+' · Preset WLED '+str(preset);frames[ip]=[]
                                elif on:
                                    worker.preset(preset,label,self.run_settings.get('fade_in',0))
                                    ws=worker.snapshot();states[ip]=label+' · '+ws['state']+' · preset '+str(preset);frames[ip]=[]
                                else:
                                    worker.off(label,self.run_settings.get('fade_out',0))
                                    ws=worker.snapshot();states[ip]=label+' · '+ws['state'];frames[ip]=[]
                                continue
                            else:
                                frame=render(t,colors,start,self.capture.screen,self.capture.level,self.history,dt)

                            if alarm_on and on:
                                frame=effect(alarm['effect'],t['count'],start-(alarm_end-alarm['duration']))
                                frame=[tuple(round(c*t['brightness']/100) for c in x) for x in frame];label='Réveil : '+alarm['name']
                            if testing:
                                frame=[(100,100,100) if int(start*4)%2 else (0,0,0)]*t['count'];label='Identification'
                            if color_testing:
                                elapsed=max(0,start-color_test[0])
                                palette=[((255,0,0),'ROUGE'),((0,255,0),'VERT'),((0,0,255),'BLEU'),((255,255,255),'BLANC')]
                                color,name=palette[min(3,int(elapsed//2))]
                                frame=[color]*t['count'];label='Diagnostic RGB : '+name

                            fade=self.fades.setdefault(ip,Fade())
                            if label=='Hors horaires':fade.value=0
                            gain=fade.step(on,dt,self.run_settings['fade_in'],self.run_settings['fade_out'])
                            if on:self.frames[ip]=frame
                            elif gain>0:frame=self.frames.get(ip,frame)
                            frame=[tuple(round(v*gain) for v in c) for c in frame]

                            if worker:
                                if on or gain>0:
                                    worker.stream(frame,self.run_settings.get('fps',25),label)
                                else:
                                    worker.off(label,0)
                                ws=worker.snapshot()
                                states[ip]=('; '.join(errors) if errors and on and not alarm_on else label)+' · '+ws['state']
                            else:
                                states[ip]=('; '.join(errors) if errors and on and not alarm_on else label)

                            if ip==self.plan_ip:self.plan_frame=frame
                            frames[ip]=frame[::max(1,len(frame)//200)]
                    self.target_status=states;self.previews=frames
                    base_status=('Synchronisation active' if self.active else 'Activez au moins un éclairage') if self.running else 'Prêt · choisissez vos associations'
                    uses_icue=any(r.get('source')=='icue' for t in self.active for r in t.get('routes',[]))
                    uses_msi=experimental and any(r.get('source')=='msi' for t in self.active for r in t.get('routes',[]))
                    uses_openrgb=experimental and any(r.get('source')=='openrgb' for t in self.active for r in t.get('routes',[]))
                    if uses_icue and cue_state.get('stalled'):self.status=base_status+' · iCUE ne répond plus'
                    elif uses_msi and not msi_state.get('available'):self.status=base_status+' · SDK MSI à installer'
                    elif uses_msi and (msi_state.get('stalled') or msi_state.get('error')):self.status=base_status+' · MSI Mystic Light Bêta indisponible'
                    elif uses_openrgb and not openrgb_state.get('connected'):self.status=base_status+' · OpenRGB Bêta non connecté'
                    else:self.status=base_status
                    last_error=''
                except Exception as exc:
                    if str(exc)!=last_error:self.log(str(exc));last_error=str(exc)
                    self.status=str(exc)
                fps=self.config['settings']['fps'] if self.running else 8
                self.done.wait(max(0,1/fps-(time.monotonic()-start)))
        finally:
            pool.shutdown(wait=False,cancel_futures=True);self.restore();self.capture.close();self.udp.close()
            if self.icue:self.icue.close()
            if self.msi:self.msi.close()
            if self.openrgb:self.openrgb.close()

# Découverte mDNS : requête PTR, lecture des enregistrements A supplémentaires.
def dns_name(data,offset,depth=0):
    if depth>16: raise ValueError('Boucle DNS')
    parts=[];end=None
    while True:
        if offset>=len(data): raise ValueError('DNS incomplet')
        n=data[offset];offset+=1
        if n==0: break
        if n&0xc0==0xc0:
            ptr=((n&63)<<8)|data[offset];offset+=1
            part,_=dns_name(data,ptr,depth+1);parts.append(part);break
        if n>63 or offset+n>len(data):raise ValueError('DNS invalide')
        parts.append(data[offset:offset+n].decode(errors='replace'));offset+=n
    return '.'.join(parts),offset

def discover():
    import struct
    label=b''.join(bytes([len(p)])+p for p in [b'_wled',b'_tcp',b'local'])+b'\0'
    query=struct.pack('!6H',0,0,1,0,0,0)+label+struct.pack('!HH',12,0x8001)
    sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);sock.settimeout(.5)
    found=set()
    try:
        sock.bind(('',0));sock.sendto(query,('224.0.0.251',5353))
        end=time.monotonic()+4
        while time.monotonic()<end:
            try:
                data,remote=sock.recvfrom(9000)
                if len(data)<12:continue
                _,_,qd,an,ns,ar=struct.unpack('!6H',data[:12]);offset=12
                for _ in range(qd): _,offset=dns_name(data,offset);offset+=4
                for _ in range(an+ns+ar):
                    name,offset=dns_name(data,offset)
                    typ,cls,ttl,size=struct.unpack('!HHIH',data[offset:offset+10]);offset+=10
                    if typ==1 and size==4: found.add(socket.inet_ntoa(data[offset:offset+4]))
                    offset+=size
            except (socket.timeout,ValueError,IndexError,struct.error):continue
    finally:sock.close()
    results=[]
    for ip in sorted(found)[:32]:
        try:
            j=http(ip,'/json/info')
            if 'leds' in j and 'ver' in j: results.append({'ip':ip,'name':j.get('name','WLED'),'count':j['leds']['count']})
        except Exception:pass
    return results


def on_screen_missing(t,capture):
    return capture.screen_enabled and any(r['source']=='screen' and screen_key(r) not in capture.screen for r in t['routes'])
