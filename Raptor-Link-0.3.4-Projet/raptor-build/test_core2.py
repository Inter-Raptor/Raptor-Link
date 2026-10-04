import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str((ROOT/'RaptorLink').resolve()))

from diagnostics import Diagnostics
from engine import validate
from wled_worker import WledWorker

def target():
    return {'name':'Logo','ip':'192.168.1.80','count':12,'port':21324}

class Core2(unittest.TestCase):
    def test_wled_preset_must_be_single_full_strip_source(self):
        cfg={'targets':[dict(target(),enabled=True,brightness=100,idle_seconds=None,on_stop='off',preset=1,routes=[
            {'source':'wled_preset','wled_preset':7,'device':'','ids':[],'start':1,'end':12,'mapping':'stretch','reverse':False}
        ])]}
        out=validate(cfg)
        self.assertEqual(out['targets'][0]['routes'][0]['wled_preset'],7)
        bad={'targets':[dict(cfg['targets'][0],routes=cfg['targets'][0]['routes']+[
            {'source':'solid','device':'','ids':[],'start':1,'end':1,'mapping':'stretch','reverse':False}
        ])]}
        with self.assertRaises(ValueError):validate(bad)

    def test_worker_stream_does_not_poll_http(self):
        calls=[];sent=[]
        with patch.object(WledWorker,'_http',lambda self,payload,timeout=2:calls.append(payload) or {'success':True}), \
             patch.object(WledWorker,'_send_frame',lambda self,frame:sent.append(frame) or True):
            w=WledWorker(target(),lambda *a,**k:None,None)
            try:
                w.RELEASE_SETTLE_SECONDS=.03
                w.stream([(1,2,3)]*12,25,'test')
                time.sleep(.14)
                self.assertGreater(len(sent),1)
                self.assertEqual(calls,[])
            finally:w.close()

    def test_worker_releases_stream_before_preset_and_off(self):
        calls=[];sent=[]
        with patch.object(WledWorker,'_http',lambda self,payload,timeout=2:calls.append(payload.copy()) or {'success':True}), \
             patch.object(WledWorker,'_send_frame',lambda self,frame:sent.append(frame) or setattr(self,'last_udp',time.monotonic()) or True):
            w=WledWorker(target(),lambda *a,**k:None,None)
            try:
                w.RELEASE_SETTLE_SECONDS=.03
                w.stream([(10,20,30)]*12,25,'stream')
                time.sleep(.08)
                w.preset(12,'autonome',1)
                time.sleep(.15)
                self.assertTrue(any(p.get('ps')==12 and p.get('live') is False for p in calls))
                w.off('idle',2)
                time.sleep(.12)
                self.assertTrue(any(p.get('on') is False and p.get('live') is False for p in calls))
            finally:w.close()

    def test_http_failure_uses_udp_json_fallback_once(self):
        http_calls=[];udp_payloads=[];releases=[]
        def broken_http(self,payload,timeout=2):
            http_calls.append(payload.copy())
            self.last_http_error='timed out'
            raise TimeoutError('timed out')
        with patch.object(WledWorker,'_http',broken_http), \
             patch.object(WledWorker,'_release_realtime',lambda self:releases.append(True) or True), \
             patch.object(WledWorker,'_udp_state',lambda self,payload:udp_payloads.append(payload.copy()) or setattr(self,'control_path','udp-json-fallback') or True):
            w=WledWorker(target(),lambda *a,**k:None,None)
            try:
                w.RELEASE_SETTLE_SECONDS=.02
                w.preset(9,'autonome',0)
                time.sleep(.16)
                snap=w.snapshot()
                self.assertEqual(len(http_calls),1)
                self.assertEqual(len(udp_payloads),1)
                self.assertTrue(releases)
                self.assertEqual(udp_payloads[0]['ps'],9)
                self.assertEqual(snap['state'],'AUTONOMOUS_UDP')
            finally:w.close()

    def test_diagnostics_persistent_copyable_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Diagnostics(Path(tmp),'detailed',7,10)
            try:
                d.event('normal','ENGINE','démarrage')
                d.event('detailed','PRESENCE','Logo : Inactivité',idle=300)
                time.sleep(.15)
                report=d.report(30,{'version':'0.4.0'})
                self.assertIn('RAPTOR LINK',report)
                self.assertIn('Logo : Inactivité',report)
                self.assertIn('version: 0.4.0',report)
            finally:d.close()

    def test_diagnostics_off_skips_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Diagnostics(Path(tmp),'off',7,10)
            try:
                d.event('normal','ENGINE','should not exist')
                time.sleep(.1)
                self.assertEqual(d.recent_lines(30),[])
            finally:d.close()

if __name__=='__main__':
    unittest.main()
