"""WASAPI speaker/headphone loopback. Samples are measured and immediately discarded."""
import array,math,threading,time,sys

def pcm_level(data):
    samples=array.array('h',data[:len(data)//2*2])
    return min(1,math.sqrt(sum(v*v for v in samples)/max(1,len(samples)))/32768*4)

def outputs():
    if sys.platform!='win32':return []
    import pyaudiowpatch as pa
    with pa.PyAudio() as p:
        return [{'id':d['name'],'name':d['name'].replace(' [Loopback]',''),'kind':'output'} for d in p.get_loopback_device_info_generator()]

class OutputCapture:
    def __init__(self):
        self.device=None;self.level=0;self.error='';self.actual='';self.done=threading.Event();self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start()
    def loop(self):
        p=None;stream=None;selected=None;retry=0;last_sample=0
        def close():
            nonlocal p,stream
            if stream:
                try:stream.close()
                except Exception:pass
            if p:
                try:p.terminate()
                except Exception:pass
            p=None;stream=None;self.level=0;self.actual=''
        def callback(data,frames,timing,status):
            nonlocal last_sample
            self.level=pcm_level(data or b'');last_sample=time.monotonic()
            return (None,0) # paContinue
        while not self.done.wait(.05):
            if sys.platform!='win32':continue
            if selected!=self.device:close();selected=self.device;retry=0;self.error=''
            if selected is None:continue
            try:
                if stream and not stream.is_active():raise RuntimeError('Sortie audio interrompue')
                if stream and time.monotonic()-last_sample>.3:self.level=0
                if stream or time.monotonic()<retry:continue
                import pyaudiowpatch as pa
                p=pa.PyAudio()
                if selected=='default':d=p.get_default_wasapi_loopback()
                else:
                    d=next((d for d in p.get_loopback_device_info_generator() if d['name']==selected),None)
                    if d is None:raise RuntimeError('Sortie audio absente : '+selected)
                stream=p.open(format=pa.paInt16,channels=int(d['maxInputChannels']),rate=int(d['defaultSampleRate']),input=True,input_device_index=int(d['index']),frames_per_buffer=1024,stream_callback=callback)
                self.actual=d['name'];self.error=''
            except Exception as e:
                self.error='Capture sortie audio : '+str(e);close();retry=time.monotonic()+3
        close()
    def close(self):self.done.set();self.thread.join(5)
