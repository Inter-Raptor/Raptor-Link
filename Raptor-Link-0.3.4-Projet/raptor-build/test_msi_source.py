import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path('RaptorLink').resolve()))
from msi_source import DLL_NAME, find_msi_sdk, install_official_sdk
from features import extras

class _Response:
    def __init__(self,data):self.data=data
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,n=-1):return self.data[:n] if n>=0 else self.data

def fake_sdk_zip():
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('SDK/x64/'+DLL_NAME,b'MZ'+b'\0'*5000)
    return buffer.getvalue()

class MsiSourceTests(unittest.TestCase):
    def test_find_preferred_sdk(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'vendor'/'msi'/DLL_NAME
            target.parent.mkdir(parents=True)
            target.write_bytes(b'MZ')
            self.assertEqual(find_msi_sdk(Path(tmp)),target)

    def test_install_extracts_only_official_x64_dll(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('msi_source.urllib.request.urlopen',return_value=_Response(fake_sdk_zip())):
                target=install_official_sdk(Path(tmp))
            self.assertTrue(target.exists())
            self.assertEqual(target.name,DLL_NAME)
            self.assertEqual(target.read_bytes()[:2],b'MZ')

    def test_msi_route_is_valid_extra_source(self):
        raw={
            'version':1,
            'settings':{},
            'targets':[{
                'name':'Test','ip':'192.168.1.20','count':10,'port':21324,'enabled':True,
                'brightness':100,'idle_seconds':None,'on_stop':'off','preset':1,
                'routes':[{'source':'msi','device':'msi:test','ids':[0,1],'start':1,'end':10,'mapping':'stretch','reverse':False}]
            }]
        }
        # extras only validates/normalizes source-specific fields after base validation.
        cfg={'version':1,'settings':{},'targets':raw['targets'],'profiles':[]}
        out=extras(cfg,cfg)
        self.assertEqual(out['targets'][0]['routes'][0]['source'],'msi')

if __name__=='__main__':unittest.main()
