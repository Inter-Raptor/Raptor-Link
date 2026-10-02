import struct
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0,str(Path('RaptorLink').resolve()))
from openrgb_source import OpenRGBWorker, parse_controller_data

def s(value):
    raw=value.encode()+b'\0'
    return struct.pack('<H',len(raw))+raw

def controller_payload():
    body=bytearray()
    body+=struct.pack('<i',1)
    body+=s('Mystic Light Board')
    body+=s('MSI')
    body+=s('Synthetic OpenRGB test device')
    body+=s('1.0')
    body+=s('SERIAL-1')
    body+=s('USB')
    body+=struct.pack('<Hi',0,-1)  # modes, active mode
    body+=struct.pack('<H',1)     # zones
    body+=s('Mainboard')
    body+=struct.pack('<iIIIH',1,3,3,3,0)
    body+=struct.pack('<H',0)     # segments (protocol 4+)
    body+=struct.pack('<I',0)     # zone flags (protocol 5)
    body+=struct.pack('<H',3)
    for i in range(3):
        body+=s('LED '+str(i+1))+struct.pack('<I',i)
    body+=struct.pack('<H',3)
    for rgb in [(10,20,30),(40,50,60),(70,80,90)]:
        value=rgb[0]|(rgb[1]<<8)|(rgb[2]<<16)
        body+=struct.pack('<I',value)
    body+=struct.pack('<H',3)
    for i in range(3):body+=s('Display '+str(i+1))
    body+=struct.pack('<I',0)
    return struct.pack('<I',len(body)+4)+body

class FakeConnection:
    def __init__(self,*args,**kwargs):self.closed=False
    def connect(self):return 5
    def close(self):self.closed=True
    def controller_ids(self):return [0]
    def controller(self,device_id):return parse_controller_data(controller_payload(),5,device_id)

class OpenRGBTests(unittest.TestCase):
    def test_protocol_5_controller_parses(self):
        d=parse_controller_data(controller_payload(),5,0)
        self.assertEqual(d['provider'],'openrgb')
        self.assertEqual(d['vendor'],'MSI')
        self.assertIn('Mystic Light',d['model'])
        self.assertEqual(len(d['positions']),3)
        self.assertEqual(d['colors'][1],(40,50,60))

    def test_worker_publishes_real_rgb_shape(self):
        worker=OpenRGBWorker(lambda msg:None,connection_factory=FakeConnection)
        try:
            deadline=time.monotonic()+2
            state={}
            while time.monotonic()<deadline:
                state=worker.snapshot()
                if state['connected'] and state['devices']:break
                time.sleep(.02)
            self.assertTrue(state['connected'])
            device=state['devices'][0]
            worker.configure({device['id']},True,15)
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                state=worker.snapshot()
                if state['colors'].get(device['id']):break
                time.sleep(.02)
            self.assertEqual(state['colors'][device['id']][2],(70,80,90))
        finally:
            worker.close()

if __name__=='__main__':unittest.main()
