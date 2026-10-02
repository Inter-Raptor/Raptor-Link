"""Disposable native MSI Mystic Light SDK bridge."""
import json
import sys
from pathlib import Path
from msi_sdk import MysticLightSDK

def emit(data):
    sys.stdout.write(json.dumps(data,separators=(',',':'))+'\n');sys.stdout.flush()

def main():
    if len(sys.argv)<2:
        emit({'ok':False,'fatal':True,'devices':[],'colors':{},'error':'DLL MSI SDK manquante'});return
    dll=Path(sys.argv[1]);sdk=None;devices=[]
    for line in sys.stdin:
        try:
            req=json.loads(line);required=set(req.get('required',[]))
            if sdk is None:sdk=MysticLightSDK(dll)
            if req.get('scan') or not devices:devices=sdk.scan()
            colors={}
            for d in devices:
                if d['id'] in required:
                    colors[d['id']]={str(k):list(v) for k,v in sdk.read_device(d).items()}
            emit({'ok':True,'devices':devices,'colors':colors,'error':''})
        except Exception as exc:
            emit({'ok':False,'fatal':True,'devices':[],'colors':{},'error':str(exc)})
            sdk=None;devices=[]

if __name__=='__main__':main()
