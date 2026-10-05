"""Raptor Link — interface locale, moteur iCUE et icone de notification Windows."""
import functools
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

ROOT=Path(__file__).resolve().parent
DEMO='--demo' in sys.argv
DATA=(Path(os.environ.get('LOCALAPPDATA',str(ROOT)))/'AuroraWLED') if not DEMO else ROOT/'demo-data'
DATA.mkdir(parents=True,exist_ok=True)
# Keep the Aurora data location for a seamless upgrade and shared singleton.
# pythonw n'a pas de console : conserver les erreurs dans un journal local.
if sys.stdout is None:
    sys.stdout=open(DATA/'application.log','a',encoding='utf-8',buffering=1)
    sys.stderr=sys.stdout
from screens import monitors,enable_dpi_awareness,screen_key
enable_dpi_awareness()
from capture import audio_devices
from audio_output import outputs
from engine import Engine, DEFAULT, http, discover, validate
from support import APP_VERSION, Engagement, diagnostics, find_update, issue_url

INSTANCE=DATA/'instance.json'
def request_existing():
    try:
        j=json.loads(INSTANCE.read_text())
        if sys.platform=='win32':
            import ctypes
            ctypes.windll.user32.AllowSetForegroundWindow(int(j['pid']))
        req=urllib.request.Request('http://127.0.0.1:'+str(j['port'])+'/api/open',data=b'{}',headers={'X-Aurora-Token':j['token'],'Content-Type':'application/json'})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=1) as r:
            return r.status==200
    except Exception:return False

def startup(enabled):
    if sys.platform!='win32':return
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
        if enabled:
            launcher=ROOT/'RaptorLink.exe'
            cmd='"'+str(launcher)+'" --background' if launcher.exists() else '"'+str(ROOT/'runtime/pythonw.exe')+'" "'+str(ROOT/'app.py')+'" --background'
            winreg.SetValueEx(key,'AuroraWLED',0,winreg.REG_SZ,cmd)
        else:
            try:winreg.DeleteValue(key,'AuroraWLED')
            except FileNotFoundError:pass

class App:
    def __init__(self):
        self.token=secrets.token_urlsafe(32);self.done=threading.Event();self.tray=None
        self.engine=Engine(DATA/'config.json',DEMO)
        self.engagement=Engagement(DATA/'engagement.json')
        self.update_info={'checked':False,'available':False,'error':''}
        startup(self.engine.config['settings']['startup'])
        self.server=ThreadingHTTPServer(('127.0.0.1',0),self.handler())
        self.url='http://127.0.0.1:'+str(self.server.server_port)+'/#'+self.token
        from window import WindowController,focus_window
        self.window=WindowController(lambda:focus_window('Raptor Link · '+str(self.server.server_port)) if sys.platform=='win32' and not DEMO else False,self.launch_window)
        INSTANCE.write_text(json.dumps({'port':self.server.server_port,'token':self.token,'pid':os.getpid()}))
        if self.engine.config['settings'].get('check_updates',True) and not DEMO:
            threading.Thread(target=self.check_for_updates,daemon=True,name='RaptorLink-update-check').start()
    def check_for_updates(self):
        self.update_info=find_update(APP_VERSION)

    def support_state(self):
        return {
            'version':APP_VERSION,
            'update':copy.deepcopy(self.update_info),
            'engagement':self.engagement.snapshot(),
        }

    def open_feedback(self,data):
        kind=str(data.get('kind','comment'))[:30]
        subject=str(data.get('subject',''))[:180]
        message=str(data.get('message',''))[:12000]
        ecosystem=str(data.get('ecosystem',''))[:200]
        rating=data.get('rating')
        if rating is not None:
            rating=int(rating)
            if not 1<=rating<=5:raise ValueError('La note doit être comprise entre 1 et 5.')
        env=diagnostics(self.engine) if bool(data.get('include_diagnostics')) else None
        url=issue_url(kind,subject,message,rating,env,ecosystem)
        webbrowser.open(url)
        return {'ok':True,'url':url}

    def open(self):
        self.window.open()
    def launch_window(self):
        if not DEMO and sys.platform=='win32':
            paths=[Path(os.environ.get('PROGRAMFILES(X86)','C:/Program Files (x86)'))/'Microsoft/Edge/Application/msedge.exe',
                   Path(os.environ.get('PROGRAMFILES','C:/Program Files'))/'Microsoft/Edge/Application/msedge.exe',
                   Path(os.environ.get('PROGRAMFILES','C:/Program Files'))/'Google/Chrome/Application/chrome.exe',
                   Path(os.environ.get('LOCALAPPDATA',''))/'Google/Chrome/Application/chrome.exe']
            for exe in paths:
                if exe.exists():
                    subprocess.Popen([str(exe),'--app='+self.url,'--window-size=1360,900','--user-data-dir='+str(DATA/'window-profile'),'--no-first-run'],creationflags=0x08000000)
                    return
            self.engine.log('Interface : Microsoft Edge ou Google Chrome est nécessaire pour la fenêtre unique.')
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,'Installez Microsoft Edge ou Google Chrome pour ouvrir la fenêtre Raptor Link.','Raptor Link',16)
            return
        webbrowser.open(self.url)
    def handler(self):
        app=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def reply(self,code,data,ctype='application/json; charset=utf-8'):
                body=json.dumps(data,ensure_ascii=False).encode() if isinstance(data,(dict,list)) else data
                self.send_response(code);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(body)))
                self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
                self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
                self.end_headers();self.wfile.write(body)
            def authorized(self):
                return secrets.compare_digest(self.headers.get('X-Aurora-Token',''),app.token)
            def do_GET(self):
                if self.path.startswith('/api/'):
                    if not self.authorized():return self.reply(403,{'error':'Accès refusé'})
                    if self.path=='/api/screens':
                        try:return self.reply(200,{'screens':monitors(DEMO)})
                        except Exception as e:return self.reply(400,{'error':str(e)})
                    if self.path=='/api/state':
                        state=app.engine.snapshot();state['app']=app.support_state();return self.reply(200,state)
                    if self.path=='/api/audio-devices':
                        try:out=outputs();error=''
                        except Exception as e:out=[];error='Sorties audio : '+str(e)
                        return self.reply(200,{'inputs':audio_devices(),'outputs':out,'error':error})
                    if self.path=='/api/config':
                        with app.engine.lock:cfg=copy.deepcopy(app.engine.config)
                        return self.reply(200,cfg)
                    return self.reply(404,{'error':'Introuvable'})
                mapping={'/':('index.html','text/html; charset=utf-8'),'/style.css':('style.css','text/css'),'/ui.js':('ui.js','text/javascript; charset=utf-8')}
                mapping.update({'/extras.js':('extras.js','text/javascript; charset=utf-8'),'/brand.css':('brand.css','text/css'),'/logo.png':('logo.png','image/png'),'/logo.svg':('logo.svg','image/svg+xml')})
                path=self.path.split('?')[0]
                if path=='/icon.png':return self.reply(200,(ROOT/'icon.png').read_bytes(),'image/png')
                if path not in mapping:return self.reply(404,{'error':'Introuvable'})
                file,mime=mapping[path];self.reply(200,(ROOT/'web'/file).read_bytes(),mime)
            def do_POST(self):
                if not self.authorized():return self.reply(403,{'error':'Accès refusé'})
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<=size<=1000000:raise ValueError('Requête trop volumineuse')
                    data=json.loads(self.rfile.read(size) or b'{}')
                    result={'ok':True}
                    if self.path=='/api/open': threading.Thread(target=app.open,daemon=True).start()
                    elif self.path=='/api/config':
                        previous=app.engine.config['settings']['startup']
                        app.engine.save(data)
                        if data['settings']['startup']!=previous: startup(data['settings']['startup'])
                    elif self.path=='/api/test':app.engine.identify(data['ip'])
                    elif self.path=='/api/profile':app.engine.select_profile(data['index'])
                    elif self.path=='/api/tutorial':
                        with app.engine.lock:
                            app.engine.config['settings']['tutorial']=data['value'] if data['value'] in ['ask','never','done'] else 'ask'
                            temp=app.engine.path.with_suffix('.tmp');temp.write_text(json.dumps(app.engine.config,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(temp,app.engine.path)
                    elif self.path=='/api/validate':result=validate(data)
                    elif self.path=='/api/start':app.engine.want_run=True
                    elif self.path=='/api/stop':app.engine.stop()
                    elif self.path=='/api/awake':app.engine.keep_awake=bool(data['enabled'])
                    elif self.path=='/api/screen-preview':
                        image=app.engine.capture.screen.get(screen_key({'screen_ids':data.get('ids',[]),'screen_layout':data.get('layout',{})})) if hasattr(app.engine,'capture') and isinstance(data.get('ids'),list) and all(isinstance(v,str) for v in data['ids']) else None
                        if app.engine.running and not app.engine.is_locked and app.engine.capture.screen_enabled and image is not None:
                            import io,base64
                            buffer=io.BytesIO();image.save(buffer,format='JPEG',quality=80)
                            result={'image':'data:image/jpeg;base64,'+base64.b64encode(buffer.getvalue()).decode()}
                        else:result={'image':None,'reason':'Image indisponible : lancez une zone écran synchronisée, avec une session déverrouillée.'}
                    elif self.path=='/api/window-ready':app.window.ready()
                    elif self.path=='/api/plan':app.engine.plan_frame=[];app.engine.plan_ip=str(data.get('ip',''))
                    elif self.path=='/api/preview':app.engine.preview_device=str(data.get('device',''))
                    elif self.path=='/api/refresh':app.engine.scan_requested=True
                    elif self.path=='/api/msi-status':result=app.engine.msi_status()
                    elif self.path=='/api/msi-install':result=app.engine.install_msi_sdk()
                    elif self.path=='/api/probe':
                        if DEMO:result={'name':'WLED Démonstration','count':160,'version':'demo'}
                        else:
                            j=http(data['ip'],'/json/info');result={'name':j.get('name','WLED'),'count':j['leds']['count'],'version':j.get('ver','')}
                    elif self.path=='/api/discover':result=discover() if not DEMO else [{'name':'Bureau démo','ip':'192.168.1.175','count':257},{'name':'Logo démo','ip':'192.168.1.80','count':160}]
                    elif self.path=='/api/starter':
                        result=json.loads((ROOT/'Configuration-Vivien.json').read_text())
                        keyboard=next((d for d in app.engine.devices if 'K95' in d['model']),None)
                        if keyboard:
                            from cue_base import sources,Position
                            groups=sources([Position(p['id'],p['x'],p['y']) for p in keyboard['positions']])
                            for t in result['targets']:
                                r=t['routes'][0];r['device']=keyboard['id'];r['ids']=groups[r.pop('starter_source')]
                    elif self.path=='/api/rating':
                        action=str(data.get('action','submit'))
                        if action=='submit':
                            stars=app.engagement.submit_rating(data.get('stars'))
                            result={'ok':True,'stars':stars}
                        elif action=='later':
                            app.engagement.snooze_rating(7);result={'ok':True}
                        elif action=='never':
                            app.engagement.never_rating();result={'ok':True}
                        else:raise ValueError('Action de notation inconnue.')
                    elif self.path=='/api/feedback':result=app.open_feedback(data)
                    elif self.path=='/api/check-update':
                        if app.engine.config['settings'].get('check_updates',True):
                            app.update_info=find_update(APP_VERSION)
                        else:app.update_info={'checked':True,'available':False,'error':''}
                        result=copy.deepcopy(app.update_info)
                    elif self.path=='/api/open-update':
                        url=str(app.update_info.get('url',''))
                        if not url.startswith('https://github.com/Inter-Raptor/Raptor-Link/releases/'):raise ValueError('Aucune mise à jour disponible.')
                        webbrowser.open(url);result={'ok':True}
                    elif self.path=='/api/quit':app.done.set()
                    else:return self.reply(404,{'error':'Introuvable'})
                    self.reply(200,result)
                except Exception as e:self.reply(400,{'error':str(e)})
        return Handler
    def run(self):
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        if not DEMO:
            try:
                from PIL import Image
                import pystray
                self.tray=pystray.Icon('AuroraWLED',Image.open(ROOT/'icon.png'),'Raptor Link',pystray.Menu(
                    pystray.MenuItem('Ouvrir Raptor Link',lambda:self.open(),default=True),
                    pystray.MenuItem('Profils',pystray.Menu(lambda: tuple(pystray.MenuItem(p['name'],(lambda index: lambda:self.engine.select_profile(index))(i)) for i,p in enumerate(self.engine.config.get('profiles',[]))))),
                    pystray.MenuItem('Démarrer',lambda:setattr(self.engine,'want_run',True)),
                    pystray.MenuItem('Arrêter la synchronisation',lambda:self.engine.stop()),
                    pystray.MenuItem('Garder allumé (inactivité)',lambda:setattr(self.engine,'keep_awake',not self.engine.keep_awake),checked=lambda item:self.engine.keep_awake),
                    pystray.MenuItem('Quitter',lambda:self.done.set())))
                threading.Thread(target=self.tray.run,daemon=True).start()
            except Exception as e:self.engine.log('Icône de notification indisponible : '+str(e))
        if '--background' not in sys.argv and '--headless' not in sys.argv:self.open()
        print('Raptor interface:',self.url,flush=True)
        try:
            tray_revision=-1
            while not self.done.wait(.5):
                self.engagement.tick()
                if self.tray and tray_revision!=self.engine.revision:
                    self.tray.update_menu();tray_revision=self.engine.revision
        except KeyboardInterrupt:pass
        finally:
            self.engagement.close();self.engine.shutdown();self.server.shutdown()
            if self.tray:self.tray.stop()
            INSTANCE.unlink(missing_ok=True)

if __name__=='__main__':
    try:
        mutex=None
        if sys.platform=='win32' and not DEMO:
            import ctypes as C
            k=C.WinDLL('kernel32',use_last_error=True)
            k.CreateMutexW.argtypes=[C.c_void_p,C.c_int,C.c_wchar_p]
            k.CreateMutexW.restype=C.c_void_p
            mutex=k.CreateMutexW(None,False,'Local\\AuroraWLED-0.2')
            if not mutex:raise C.WinError(C.get_last_error())
            if C.get_last_error()==183:
                for _ in range(20):
                    if request_existing():break
                    time.sleep(.2)
                sys.exit(0)
        if not request_existing():App().run()
    except Exception:
        (DATA/'erreur.txt').write_text(traceback.format_exc(),encoding='utf-8')
        if sys.platform=='win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,'Raptor Link a rencontré une erreur et va tenter de redémarrer. Détails : '+str(DATA/'erreur.txt'),'Raptor Link',16)
            raise SystemExit(1)
        else:raise
