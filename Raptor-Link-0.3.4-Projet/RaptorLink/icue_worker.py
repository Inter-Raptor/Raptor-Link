"""Disposable Corsair SDK bridge used by Raptor Link's watchdog."""
import ctypes as C
import json
import sys
from cue_base import Cue,Device,Filter,Position,Color,check

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
            except RuntimeError:
                continue
        return out
    def read_device(self,d):
        cols=(Color*len(d['positions']))()
        for c,p in zip(cols,d['positions']):c.id=p['id']
        check(self.dll.CorsairGetLedColors(d['id'].encode(),len(cols),cols))
        return {str(c.id):[c.r,c.g,c.b] for c in cols}

def emit(data):
    sys.stdout.write(json.dumps(data,separators=(',',':'))+'\n');sys.stdout.flush()

def main():
    cue=None;devices=[]
    for line in sys.stdin:
        try:
            req=json.loads(line);required=set(req.get('required',[]))
            if cue is None:cue=GenericCue()
            if not cue.connected.is_set():
                emit({'ok':False,'fatal':False,'devices':[],'colors':{},'error':'iCUE non connecté'})
                continue
            if req.get('scan') or not devices:devices=cue.scan()
            colors={}
            for d in devices:
                if d['id'] in required:colors[d['id']]=cue.read_device(d)
            emit({'ok':True,'devices':devices,'colors':colors,'error':''})
        except Exception as e:
            emit({'ok':False,'fatal':True,'devices':[],'colors':{},'error':str(e)})
            if cue is not None:
                try:cue.close()
                except Exception:pass
            cue=None;devices=[]
    if cue is not None:
        try:cue.close()
        except Exception:pass

if __name__=='__main__':main()
