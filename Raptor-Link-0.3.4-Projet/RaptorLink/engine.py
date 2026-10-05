import datetime
from concurrent.futures import ThreadPoolExecutor
from features import extras, gate, window, effect, render, Fade, clock, due_alarm
from capture import Capture
from screens import screen_key
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

HTTP=urllib.request.build_opener(urllib.request.ProxyHandler({}))
DEFAULT={'version':1,'settings':{'idle_seconds':300,'fps':25,'lock_off':True,'auto_sync':False,'startup':False,'language':'fr','check_updates':True,'experimental_rgb':False},'targets':[]}

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
    for k in ['lock_off','auto_sync','startup','check_updates']: s[k]=bool(s.get(k,True if k=='check_updates' else False))
    s['experimental_rgb']=False
    if s['language'] not in ['fr','en']: s['language']='fr'
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

def packets(pixels,timeout=2):
    if len(pixels)<=490:
        return [bytes([2,timeout])+bytes(v for c in pixels for v in c)]
    output=[]
    for start in range(0,len(pixels),480):
        output.append(bytes([4,timeout,start>>8,start&255])+bytes(v for c in pixels[start:start+480] for v in c))
    return output


def udp_json(sock,ip,port,payload,repeat=2):
    raw=json.dumps(payload,separators=(',',':'),ensure_ascii=False).encode('utf-8')
    ok=False
    for i in range(max(1,int(repeat))):
        try:
            sock.sendto(raw,(ip,port));ok=True
        except OSError:
            pass
        if i+1<repeat:time.sleep(.025)
    return ok

def probe_target(t,initialize=False,demo=False):
    if demo:return {} if initialize else None
    info=http(t['ip'],'/json/info')
    if info.get('leds',{}).get('count')!=t['count']:raise ValueError('Nombre de LED différent : utilisez Vérifier.')
    if not initialize:return None
    st=http(t['ip'],'/json/state')
    http(t['ip'],'/json/state',{'on':False,'transition':0})
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
                   'age':max(0,now-self.last_progress),'last_ok':self.last_ok,'restarts':self.restarts}
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
                self.colors={str(dev):{int(k):tuple(v) for k,v in vals.items()} for dev,vals in data.get('colors',{}).items()}
                self.error=str(data.get('error',''));self.last_ok=time.monotonic() if data.get('ok') else self.last_ok
            if error is not None:self.error=str(error)
            self.last_progress=time.monotonic()
    def loop(self):
        import colorsys
        self.udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);self.udp.settimeout(.2)
        self.capture=Capture();last_frame=time.monotonic();last_error=''
        if not self.demo:self.icue=IcueWorker(self.log)
        idle=None
        try:
            if not self.demo:idle=WinIdle()
            else:self.demo_scan()
            while not self.done.is_set():
                start=time.monotonic();self.loop_heartbeat=start;dt=min(.2,start-last_frame);last_frame=start;now=datetime.datetime.now()
                try:
                    if self.running and (not self.want_run or self.run_revision!=self.revision):
                        self.restore()
                    cue_state={}
                    if not self.demo:
                        self.idle=idle.seconds();self.is_locked=idle.locked()
                        cue_state=self.icue.snapshot() if self.icue else {}
                        cue_devices=copy.deepcopy(cue_state.get('devices',[]))
                        for d in cue_devices:d['provider']='icue'
                        self.devices=cue_devices

                    for t in self.active:
                        for r in t['routes']:
                            if r.get('source')!='icue':continue
                            current=next((d for d in self.devices if d['id']==r.get('device')),None)
                            if current:
                                r['device_model']=current['model'];r['device_serial']=current.get('serial','')
                            else:
                                matches=[d for d in self.devices if (r.get('device_serial') and d.get('serial')==r['device_serial']) or (not r.get('device_serial') and r.get('device_model')==d['model'])]
                                if len(matches)==1 and set(r.get('ids',[]))<={p['id'] for p in matches[0]['positions']}:
                                    r['device']=matches[0]['id']

                    required={self.preview_device}|{r['device'] for t in self.config['targets']+self.active for r in t['routes'] if r.get('source')=='icue'}
                    if self.demo:
                        colors={}
                        for d in self.devices:
                            if d['id'] in required:
                                colors[d['id']]={p['id']:tuple(round(x*255) for x in colorsys.hsv_to_rgb((start*.1+p['x']/600+p['y']/600)%1,.85,1)) for p in d['positions']}
                    else:
                        force_scan=self.scan_requested
                        if self.icue:self.icue.configure({x for x in required if x},force_scan,self.config['settings']['fps'])
                        self.scan_requested=False
                        cue_state=self.icue.snapshot() if self.icue else {}
                        cue_devices=copy.deepcopy(cue_state.get('devices',[]))
                        for d in cue_devices:d['provider']='icue'
                        self.devices=cue_devices
                        colors={} if cue_state.get('stalled') else copy.deepcopy(cue_state.get('colors',{}))
                        if cue_state.get('stalled') and not self.icue_notice:
                            self.log('iCUE ne répond plus ; la dernière image WLED reste active pendant la récupération.')
                            self.icue_notice=True
                        elif not cue_state.get('stalled') and self.icue_notice:
                            self.log('iCUE : communication rétablie.')
                            self.icue_notice=False

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
                            ip=t['ip'];errors=[]
                            for i,r in enumerate(t['routes']):
                                if r.get('source')=='icue' and (not r.get('ids') or any(x not in colors.get(r.get('device'),{}) for x in r.get('ids',[]))):
                                    errors.append('Association '+str(i+1)+' : appareil ou LED iCUE indisponibles')
                            if not self.demo and on_screen_missing(t,self.capture):errors.append('Écran indisponible : vérifiez la sélection dans la zone')
                            if not t['routes']:errors.append('Ajoutez une association')

                            due=due_alarm(t,now,self.alarm_seen)
                            if due is not None:
                                try:
                                    alarm_file=self.path.with_name('alarms-fired.json');tmp=alarm_file.with_suffix('.tmp');tmp.write_text(json.dumps(self.alarm_seen));os.replace(tmp,alarm_file)
                                except OSError as e:self.log('Mémorisation du réveil : '+str(e))
                                if window(t['schedule'],now):self.alarms[ip]=(start+due['duration'],copy.deepcopy(due))
                            end,alarm=self.alarms.get(ip,(0,None));alarm_on=start<end
                            on,label=gate(t,self.run_settings,now,self.idle,self.is_locked,self.keep_awake,alarm_on)
                            frame=render(t,colors,start,self.capture.screen,self.capture.level,self.history,dt)
                            if alarm_on and on:
                                frame=effect(alarm['effect'],t['count'],start-(end-alarm['duration']))
                                frame=[tuple(round(c*t['brightness']/100) for c in x) for x in frame];label='Réveil : '+alarm['name']
                            if start<self.tests.get(ip,0) and on:
                                frame=[(100,100,100) if int(start*4)%2 else (0,0,0)]*t['count'];label='Identification'

                            fade=self.fades.setdefault(ip,Fade())
                            if label=='Hors horaires':fade.value=0
                            gain=fade.step(on,dt,self.run_settings['fade_in'],self.run_settings['fade_out'])
                            if on:self.frames[ip]=frame
                            elif gain>0:frame=self.frames.get(ip,frame)
                            frame=[tuple(round(v*gain) for v in pixel) for pixel in frame]

                            if not self.demo:
                                # Wake/refresh is tiny and infrequent. No HTTP health gate:
                                # if Wi-Fi comes back, the next UDP frame resumes immediately.
                                if on and start-self.last_wake.get(ip,0)>=10:
                                    udp_json(self.udp,ip,t['port'],{'on':True,'tt':0},1)
                                    self.last_wake[ip]=start
                                raw=bytes(v for pixel in frame for v in pixel)
                                changed=raw!=self.sent_cache.get(ip)
                                heartbeat=start-self.last_send.get(ip,0)>=.9
                                if changed or heartbeat:
                                    try:
                                        for packet in packets(frame,3):self.udp.sendto(packet,(ip,t['port']))
                                        self.sent_cache[ip]=raw;self.last_send[ip]=start
                                    except OSError as e:
                                        self.log(t['name']+' : UDP '+str(e))

                            states[ip]=('; '.join(errors) if errors and on and not alarm_on else label)
                            if ip==self.plan_ip:self.plan_frame=frame
                            frames[ip]=frame[::max(1,len(frame)//200)]

                    self.target_status=states;self.previews=frames
                    base_status=('Synchronisation active' if self.active else 'Activez au moins un éclairage') if self.running else 'Prêt · choisissez vos associations'
                    uses_icue=any(r.get('source')=='icue' for t in self.active for r in t.get('routes',[]))
                    if uses_icue and cue_state.get('stalled'):self.status=base_status+' · iCUE redémarre'
                    else:self.status=base_status
                    last_error=''
                except Exception as exc:
                    if str(exc)!=last_error:self.log(str(exc));last_error=str(exc)
                    self.status=str(exc)
                fps=self.config['settings']['fps'] if self.running else 8
                self.done.wait(max(0,1/fps-(time.monotonic()-start)))
        finally:
            self.restore();self.capture.close();self.udp.close()
            if self.icue:self.icue.close()

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
