import sys,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path('RaptorLink').resolve()))
from engine import IcueWorker

HANG_CODE = r'''
import sys,json,time
for line in sys.stdin:
    json.loads(line)
    time.sleep(60)
'''

GOOD_CODE = r'''
import sys,json
for line in sys.stdin:
    req=json.loads(line)
    print(json.dumps({'ok':True,'devices':[{'id':'kbd','model':'Fake','serial':'1','type':1,'positions':[{'id':7,'x':0,'y':0,'group':0}]}],'colors':{'kbd':{'7':[1,2,3]}},'error':''}),flush=True)
'''

class IcueWatchdog(unittest.TestCase):
    def test_protocol_restores_integer_led_ids(self):
        w=IcueWorker(lambda msg:None,[sys.executable,'-u','-c',GOOD_CODE])
        try:
            w.configure({'kbd'},True,25)
            deadline=time.monotonic()+2
            state={}
            while time.monotonic()<deadline:
                state=w.snapshot()
                if state['colors']:break
                time.sleep(.02)
            self.assertEqual(state['colors']['kbd'][7],(1,2,3))
        finally:w.close()
    def test_hung_child_is_killed_without_blocking_engine(self):
        w=IcueWorker(lambda msg:None,[sys.executable,'-u','-c',HANG_CODE])
        try:
            w.configure({'kbd'},True,25)
            deadline=time.monotonic()+5.5
            state={}
            while time.monotonic()<deadline:
                state=w.snapshot()
                if state['stalled']:break
                time.sleep(.05)
            self.assertTrue(state['stalled'])
            start=time.monotonic();w.snapshot();self.assertLess(time.monotonic()-start,.2)
            deadline=time.monotonic()+2
            old=state['restarts']
            while time.monotonic()<deadline and w.snapshot()['restarts']<=old:time.sleep(.05)
            self.assertGreater(w.snapshot()['restarts'],old)
            start=time.monotonic();w.close();self.assertLess(time.monotonic()-start,1.2)
        finally:w.close()

if __name__=='__main__':unittest.main()
