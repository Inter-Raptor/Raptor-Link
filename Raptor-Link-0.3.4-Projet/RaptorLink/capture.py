"""Local screen / selected Windows recording input. No recording is retained."""
from audio_output import OutputCapture,outputs
from screens import monitors,selected_monitors,compose_screen
import ctypes as C
import math, threading, time, sys, array

class WAVEFORMATEX(C.Structure):
    _pack_=2
    _fields_=[('tag',C.c_ushort),('channels',C.c_ushort),('rate',C.c_uint32),('avg',C.c_uint32),('align',C.c_ushort),('bits',C.c_ushort),('extra',C.c_ushort)]
class WAVEHDR(C.Structure):
    _fields_=[('data',C.c_void_p),('length',C.c_uint32),('recorded',C.c_uint32),('user',C.c_size_t),('flags',C.c_uint32),('loops',C.c_uint32),('next',C.c_void_p),('reserved',C.c_size_t)]
class CAPS(C.Structure):
    _fields_=[('mid',C.c_ushort),('pid',C.c_ushort),('version',C.c_uint32),('name',C.c_wchar*32),('formats',C.c_uint32),('channels',C.c_ushort),('reserved',C.c_ushort)]
def winmm():
    w=C.WinDLL('winmm')
    w.waveInGetDevCapsW.argtypes=[C.c_size_t,C.POINTER(CAPS),C.c_uint]
    w.waveInOpen.argtypes=[C.POINTER(C.c_void_p),C.c_uint,C.POINTER(WAVEFORMATEX),C.c_size_t,C.c_size_t,C.c_uint]
    for n in ['waveInPrepareHeader','waveInUnprepareHeader','waveInAddBuffer']:
        getattr(w,n).argtypes=[C.c_void_p,C.POINTER(WAVEHDR),C.c_uint]
    for n in ['waveInStart','waveInStop','waveInReset','waveInClose']:getattr(w,n).argtypes=[C.c_void_p]
    return w

def audio_devices():
    if sys.platform!='win32':return []
    w=winmm();out=[]
    for i in range(w.waveInGetNumDevs()):
        c=CAPS()
        if not w.waveInGetDevCapsW(i,C.byref(c),C.sizeof(c)):out.append({'id':i,'name':c.name})
    return out

class Capture:
    def __init__(self):
        self.output=OutputCapture();self.audio_mode='input';self.audio_output=None;self.screen={};self.screen_requests=();self.screen_error='';self.level=0;self.screen_enabled=False;self.audio_device=-1;self.error='';self.done=threading.Event()
        self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start()
    def loop(self):
        w=None;handle=C.c_void_p();buffers=[];selected=-1;last_screen=0;retry=0
        def close():
            nonlocal handle,buffers
            if handle.value:
                w.waveInReset(handle)
                for b,h in buffers:w.waveInUnprepareHeader(handle,C.byref(h),C.sizeof(h))
                w.waveInClose(handle)
            handle=C.c_void_p();buffers=[];self.level=0
        while not self.done.wait(.02):
            if self.screen_enabled and time.monotonic()-last_screen>.10:
                try:
                    from PIL import ImageGrab
                    available=monitors();desktop=ImageGrab.grab(all_screens=True).convert('RGB')
                    images={};errors=[]
                    for key in self.screen_requests:
                        ids,positions=key
                        try:images[key]=compose_screen(desktop,available,selected_monitors(ids,available),layout={id:[x,y] for id,x,y in positions})
                        except Exception as e:errors.append(str(e))
                    self.screen=images;self.screen_error=' ; '.join(dict.fromkeys(errors))
                except Exception as e:self.screen={};self.screen_error='Capture écran : '+str(e)
                last_screen=time.monotonic()
            elif not self.screen_enabled:self.screen={};self.screen_error=''
            self.output.device=self.audio_output if self.audio_mode=='output' else None
            desired=self.audio_device if self.audio_mode=='input' else -1
            if self.audio_mode=='output':
                self.level=self.output.level
                self.error=self.output.error
            if sys.platform!='win32':continue
            try:
                if selected!=desired:
                    close();selected=desired;retry=0
                if selected>=0 and not handle.value and time.monotonic()>retry:
                    w=winmm();fmt=WAVEFORMATEX(1,1,44100,88200,2,16,0)
                    err=w.waveInOpen(C.byref(handle),selected,C.byref(fmt),0,0,0)
                    if err:raise RuntimeError('Entrée audio inaccessible (code '+str(err)+')')
                    for _ in range(4):
                        b=C.create_string_buffer(4410);h=WAVEHDR(C.addressof(b),4410,0,0,0,0,None,0)
                        buffers.append((b,h))
                        if w.waveInPrepareHeader(handle,C.byref(h),C.sizeof(h)) or w.waveInAddBuffer(handle,C.byref(h),C.sizeof(h)):raise RuntimeError('Tampon audio refusé')
                    if w.waveInStart(handle):raise RuntimeError('Capture audio refusée')
                for b,h in buffers:
                    if h.flags&1:
                        samples=array.array('h',b.raw[:h.recorded-(h.recorded%2)])
                        self.level=min(1,math.sqrt(sum(v*v for v in samples)/max(1,len(samples)))/32768*4)
                        h.recorded=0
                        if w.waveInAddBuffer(handle,C.byref(h),C.sizeof(h)):raise RuntimeError('Entrée audio déconnectée')
            except Exception as e:
                self.error=str(e);close();retry=time.monotonic()+5
        close();self.output.close()
    def close(self):self.done.set();self.thread.join(3)
