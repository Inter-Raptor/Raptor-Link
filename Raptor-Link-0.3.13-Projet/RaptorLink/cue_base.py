"""Pont iCUE SDK 4 -> WLED DRGB. Python 3.13 x64, sans modules externes."""
import ctypes as C
import html
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent
class Version(C.Structure):
    _fields_ = [('major', C.c_int), ('minor', C.c_int), ('patch', C.c_int)]
class Details(C.Structure):
    _fields_ = [('client', Version), ('server', Version), ('host', Version)]
class Session(C.Structure):
    _fields_ = [('state', C.c_int), ('details', Details)]
class Device(C.Structure):
    _fields_ = [('type', C.c_int), ('id', C.c_char*128), ('serial', C.c_char*128),
                ('model', C.c_char*128), ('ledCount', C.c_int), ('channelCount', C.c_int)]
class Position(C.Structure):
    _fields_ = [('id', C.c_uint32), ('x', C.c_double), ('y', C.c_double)]
class Color(C.Structure):
    _fields_ = [('id', C.c_uint32), ('r', C.c_ubyte), ('g', C.c_ubyte),
                ('b', C.c_ubyte), ('a', C.c_ubyte)]
class Filter(C.Structure):
    _fields_ = [('mask', C.c_int)]
CALLBACK = C.CFUNCTYPE(None, C.c_void_p, C.POINTER(Session))
ERRORS = {1: 'iCUE non connecte ou SDK desactive', 2: 'Acces reserve par un autre logiciel',
          3: 'Versions SDK/iCUE incompatibles', 4: 'Argument SDK invalide',
          5: 'Operation SDK indisponible', 6: 'Clavier deconnecte', 7: 'Acces SDK non autorise'}
def check(code):
    if code:
        raise RuntimeError('iCUE: %s (code %s)' % (ERRORS.get(code, 'Erreur'), code))

class Cue:
    def __init__(self):
        self.connected = threading.Event()
        self.state = 0
        self.dll_dir = os.add_dll_directory(str(ROOT))
        try:
            self.dll = C.CDLL(str(ROOT/'iCUESDK.x64_2019.dll'))
        except OSError as exc:
            raise RuntimeError('Chargement SDK impossible. Voir LISEZ-MOI.txt (runtime Microsoft VC++ x64). ' + str(exc))
        self.callback = CALLBACK(self.on_session)
        signatures = {
            'CorsairConnect': [CALLBACK, C.c_void_p],
            'CorsairDisconnect': [],
            'CorsairGetDevices': [C.POINTER(Filter), C.c_int, C.POINTER(Device), C.POINTER(C.c_int)],
            'CorsairGetLedPositions': [C.c_char_p, C.c_int, C.POINTER(Position), C.POINTER(C.c_int)],
            'CorsairGetLedColors': [C.c_char_p, C.c_int, C.POINTER(Color)]}
        for name, args in signatures.items():
            func = getattr(self.dll, name)
            func.argtypes = args
            func.restype = C.c_int
        check(self.dll.CorsairConnect(self.callback, None))
    def on_session(self, context, event):
        self.state = event.contents.state
        if self.state == 6:
            self.connected.set()
        else:
            self.connected.clear()
    def discover(self, model):
        if not self.connected.wait(20):
            raise RuntimeError('Connexion iCUE non autorisee (etat %s). Ouvrir iCUE > Parametres > SDK et autoriser python.exe pour le controle logiciel.' % self.state)
        devices, n = (Device*64)(), C.c_int()
        check(self.dll.CorsairGetDevices(C.byref(Filter(1)), 64, devices, C.byref(n)))
        candidates = [d for d in devices[:n.value] if model.lower() in d.model.decode(errors='replace').lower()]
        if len(candidates) != 1:
            raise RuntimeError('Clavier %s non identifie sans ambiguite. Claviers visibles: %s' %
                (model, [d.model.decode(errors='replace') for d in devices[:n.value]]))
        self.device = candidates[0]
        buf, n = (Position*512)(), C.c_int()
        check(self.dll.CorsairGetLedPositions(self.device.id, 512, buf, C.byref(n)))
        self.positions = list(buf[:n.value])
        if not self.positions:
            raise RuntimeError('Aucune LED accessible sur le clavier.')
        self.colors = (Color*len(self.positions))()
        for pos, col in zip(self.positions, self.colors):
            col.id = pos.id
        print('Clavier:', self.device.model.decode(errors='replace'), '-', len(self.positions), 'LED accessibles', flush=True)
    def read(self):
        if not self.connected.is_set():
            raise RuntimeError('Connexion iCUE interrompue.')
        check(self.dll.CorsairGetLedColors(self.device.id, len(self.colors), self.colors))
        return {c.id: (c.r, c.g, c.b) for c in self.colors}
    def close(self):
        self.dll.CorsairDisconnect()
        self.dll_dir.close()

def sources(positions):
    # Le groupe 2 correspond au bandeau du clavier (SDK Corsair).
    edge = sorted([p for p in positions if p.id >> 16 == 2], key=lambda p: (p.x, p.id))
    # Positions physiques de la rangee rouge: G2, ², chiffres, retour,
    # Insert/Home/PageUp, NumLock, /, *, -. Les noms US ne changent pas les positions AZERTY.
    row_ids = {65538, *range(14, 28), 87, 88, 89, 105, 106, 107, 108}
    row = sorted([p for p in positions if p.id in row_ids], key=lambda p: (p.x, p.id))
    return {'edge': [p.id for p in edge], 'number_row': [p.id for p in row]}

def packet(rgb, ids, count, reverse=False):
    if not ids or not 1 <= count <= 490:
        raise ValueError('Source vide ou longueur DRGB invalide.')
    # Groupes contigus aussi egaux que possible. Reproduction sans melange des couleurs.
    pixels = [rgb[ids[min(len(ids)-1, i*len(ids)//count)]] for i in range(count)]
    if reverse:
        pixels.reverse()
    return bytes([2, 2]) + bytes(v for pixel in pixels for v in pixel)

def load_config():
    config = json.loads((ROOT/'config.json').read_text(encoding='utf-8-sig'))
    if not 1 <= config['fps'] <= 40 or not 1 <= config['idle_seconds'] <= 86400:
        raise ValueError('FPS ou delai inactivite incorrect.')
    for d in config['devices']:
        socket.inet_aton(d['ip'])
        if not 1 <= d['count'] <= 490 or d['source'] not in ('edge', 'number_row'):
            raise ValueError('Configuration WLED incorrecte.')
    return config

class Idle:
    class Info(C.Structure):
        _fields_ = [('size', C.c_uint32), ('tick', C.c_uint32)]
    def __init__(self):
        self.user = C.WinDLL('user32', use_last_error=True)
        self.kernel = C.WinDLL('kernel32', use_last_error=True)
        self.user.GetLastInputInfo.argtypes = [C.POINTER(self.Info)]
        self.user.GetLastInputInfo.restype = C.c_int
        self.kernel.GetTickCount.argtypes = []
        self.kernel.GetTickCount.restype = C.c_uint32
    def seconds(self):
        info = self.Info(C.sizeof(self.Info), 0)
        if not self.user.GetLastInputInfo(C.byref(info)):
            raise C.WinError(C.get_last_error())
        return ((self.kernel.GetTickCount()-info.tick) & 0xffffffff)/1000.0

# Pas de proxy pour les adresses locales des WLED.
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
def request(ip, path, data=None):
    payload = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request('http://'+ip+path, data=payload, headers={'Content-Type':'application/json'})
    with HTTP.open(req, timeout=3) as response:
        return json.load(response)

def diagnostic(cue):
    groups = sources(cue.positions)
    print('Bandeau:', len(groups['edge']), 'zones ; rangee rouge:', len(groups['number_row']), 'touches')
    print('Pendant 15 secondes, active une vague coloree dans iCUE. Aucun ordre envoye aux WLED.', flush=True)
    first = cue.read()
    previous = first
    changed = set()
    for step in range(75):
        time.sleep(.2)
        current = cue.read()
        changed.update(k for k in current if current[k] != previous[k])
        previous = current
        if step % 5 == 0:
            print('Lecture en cours :', len(changed), 'LED ont change de couleur', flush=True)
    report = {'model': cue.device.model.decode(errors='replace'), 'sources': groups,
        'changed_ids': sorted(changed), 'positions': [
        {'id': p.id, 'group': p.id >> 16, 'x': p.x, 'y': p.y, 'rgb': previous[p.id]} for p in cue.positions]}
    (ROOT/'diagnostic.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    minx, miny = min(p.x for p in cue.positions), min(p.y for p in cue.positions)
    maxx, maxy = max(p.x for p in cue.positions), max(p.y for p in cue.positions)
    circles = []
    for p in cue.positions:
        fill = '#%02x%02x%02x' % previous[p.id]
        stroke = '#38ddff' if p.id in groups['edge'] else '#ff5b5b' if p.id in groups['number_row'] else '#777'
        circles.append('<circle cx="%s" cy="%s" r="4" fill="%s" stroke="%s"><title>LED %s RGB %s</title></circle>' %
                       (p.x-minx+8, p.y-miny+8, fill, stroke, p.id, previous[p.id]))
    sections=[]
    for label, key in [('Bandeau pour le bureau', 'edge'), ('Rangée pour le logo', 'number_row')]:
        ids=groups[key]
        swatches=''.join('<span style="background:rgb%s" title="LED %s"></span>' % (previous[i], i) for i in ids)
        sections.append('<h2>%s : %s zones, %s ont changé</h2><div>%s</div>' % (label,len(ids),len(set(ids)&changed),swatches))
    doc='''<!doctype html><meta charset="utf-8"><title>Diagnostic iCUE WLED</title>
<style>body{background:#171923;color:#eee;font:18px system-ui;max-width:1000px;margin:40px auto;padding:20px}span{display:inline-block;width:30px;height:45px;border:1px solid #999}svg{width:100%;background:#222535;border-radius:15px}h2{font-size:20px}</style>
<h1>Lecture du K95</h1><p>Capture des couleurs en fin de test. Les WLED n'ont pas été commandés.</p>
<p>Contour bleu : bandeau. Contour rouge : rangée choisie.</p>'''
    doc += '<svg viewBox="0 0 %s %s">%s</svg>' % (maxx-minx+16,maxy-miny+16,''.join(circles))
    doc += ''.join(sections)
    doc += '<p>Si les couleurs changent avec les animations iCUE dans les deux zones, lance 2-SYNCHRONISER.cmd. Sinon, envoie une capture de cette page.</p>'
    (ROOT/'diagnostic.html').write_text(doc,encoding='utf-8')
    webbrowser.open((ROOT/'diagnostic.html').as_uri())
    print('Diagnostic termine. Resultats dans diagnostic.html et diagnostic.json.')

def sync(cue, config):
    groups = sources(cue.positions)
    for d in config['devices']:
        if not groups[d['source']]:
            raise RuntimeError('Source '+d['source']+' absente. Lance 1-TEST-ICUE.cmd et envoie le diagnostic.')
        info = request(d['ip'], '/json/info')
        if info.get('leds',{}).get('count') != d['count']:
            raise RuntimeError('Nombre de LED inattendu pour '+d['name']+'. Verifie config.json.')
        print(d['name'], d['ip'], d['count'], 'LED <-', len(groups[d['source']]), 'zones')
    # Verifier la lecture avant de toucher aux WLED.
    cue.read()
    idle = Idle()
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.settimeout(.2)
    blank = {p.id:(0,0,0) for p in cue.positions}
    off = True
    failures = 0
    next_reconnect = 0
    modified = []
    try:
        # Etat sous-jacent eteint : apres perte du flux ou veille, WLED revient
        # a cet etat apres le timeout UDP (2 s). Pas d'ecriture de preset/config.
        for d in config['devices']:
            modified.append(d)
            request(d['ip'], '/json/state', {'on':False,'transition':0})
        time.sleep(.3)
        print('Synchronisation active. Q puis Entree pour arreter. Fermer cette fenetre arrete aussi le programme.',flush=True)
        stop = threading.Event()
        def console():
            try:
                while not stop.is_set():
                    if input().strip().lower() == 'q':
                        stop.set()
            except EOFError:
                pass
        threading.Thread(target=console,daemon=True).start()
        while not stop.is_set():
            start=time.monotonic()
            inactive=idle.seconds() >= config['idle_seconds']
            if inactive != off:
                print('Pause : 5 minutes sans activite.' if inactive else 'Activite detectee : animations actives.',flush=True)
                off=inactive
            rgb=blank
            if not inactive:
                try:
                    rgb=cue.read()
                    if failures:
                        print('Lecture iCUE retablie.',flush=True)
                    failures=0
                except RuntimeError as exc:
                    if failures==0:
                        print(str(exc)+' Les WLED restent noirs en attendant.',flush=True)
                    failures+=1
                    if time.monotonic() >= next_reconnect:
                        next_reconnect=time.monotonic()+5
                        if cue.connected.is_set():
                            try:
                                cue.discover(config['keyboard_model'])
                                groups=sources(cue.positions)
                                blank={p.id:(0,0,0) for p in cue.positions}
                                rgb=blank
                            except RuntimeError:
                                pass
            for d in config['devices']:
                udp.sendto(packet(rgb, groups[d['source']], d['count'], d.get('reverse',False)),(d['ip'],21324))
            interval=.5 if inactive or failures else 1/config['fps']
            stop.wait(max(0,interval-(time.monotonic()-start)))
    finally:
        for d in modified:
            try:
                udp.sendto(bytes([2,0]),(d['ip'],21324))
                request(d['ip'],'/json/state',{'on':False,'transition':0})
            except Exception:
                pass
        udp.close()
        print('Synchronisation arretee ; WLED eteints.')

def main():
    if sys.platform != 'win32' or C.sizeof(C.c_void_p) != 8:
        raise RuntimeError('Ce programme necessite Windows 64 bits.')
    mutex = None
    kernel = C.WinDLL('kernel32', use_last_error=True)
    if '--test' not in sys.argv:
        kernel.CreateMutexW.argtypes = [C.c_void_p, C.c_int, C.c_wchar_p]
        kernel.CreateMutexW.restype = C.c_void_p
        kernel.CloseHandle.argtypes = [C.c_void_p]
        kernel.CloseHandle.restype = C.c_int
        mutex = kernel.CreateMutexW(None, False, 'Local\\ICUE-WLED-Vivien')
        if not mutex:
            raise C.WinError(C.get_last_error())
        if C.get_last_error() == 183:
            kernel.CloseHandle(mutex)
            raise RuntimeError('La synchronisation est deja lancee. Ferme sa fenetre avant de relancer.')
    config=load_config()
    cue=Cue()
    try:
        cue.discover(config['keyboard_model'])
        if '--test' in sys.argv:
            diagnostic(cue)
        else:
            sync(cue,config)
    finally:
        cue.close()
        if mutex:
            kernel.CloseHandle(mutex)

if __name__=='__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Arret demande.')
    except Exception:
        detail=traceback.format_exc()
        print(detail)
        (ROOT/'erreur.txt').write_text(detail,encoding='utf-8')
        print('Copie de cette erreur dans erreur.txt.')
        sys.exit(1)
