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
from features import effect
from wled_worker import WledWorker
from window import WindowController

def target():
    return {'name':'Logo','ip':'192.168.1.80','count':12,'port':21324}

def wait_for(predicate, timeout=1.0):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate():return True
        time.sleep(.01)
    return bool(predicate())

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
                self.assertTrue(wait_for(lambda:len(sent)>1))
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
                self.assertTrue(wait_for(lambda:len(sent)>0))
                w.preset(12,'autonome',1)
                self.assertTrue(wait_for(lambda:any(p.get('ps')==12 and p.get('live') is False for p in calls)))
                w.off('idle',2)
                self.assertTrue(wait_for(lambda:any(p.get('on') is False and p.get('live') is False for p in calls)))
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
                self.assertTrue(wait_for(lambda:len(udp_payloads)==1))
                snap=w.snapshot()
                self.assertEqual(len(http_calls),1)
                self.assertEqual(len(udp_payloads),1)
                self.assertTrue(releases)
                self.assertEqual(udp_payloads[0]['ps'],9)
                self.assertEqual(snap['state'],'AUTONOMOUS_UDP')
            finally:w.close()

    def test_http_circuit_breaker_skips_repeat_calls(self):
        http_calls=[];udp_payloads=[]
        with patch.object(WledWorker,'_http',lambda self,payload,timeout=None:http_calls.append(payload.copy()) or {'success':True}), \
             patch.object(WledWorker,'_udp_state',lambda self,payload:udp_payloads.append(payload.copy()) or setattr(self,'control_path','udp-json-fallback') or True):
            w=WledWorker(target(),lambda *a,**k:None,None)
            try:
                w.RELEASE_SETTLE_SECONDS=.02
                w.next_http_try=time.monotonic()+60
                w.preset(11,'autonome',0)
                self.assertTrue(wait_for(lambda:len(udp_payloads)==1))
                snap=w.snapshot()
                self.assertEqual(http_calls,[])
                self.assertEqual(len(udp_payloads),1)
                self.assertTrue(snap['pending_confirmation'])
                self.assertGreater(snap['http_retry_in'],50)
                self.assertEqual(snap['state'],'AUTONOMOUS_UDP')
            finally:w.close()

    def test_udp_fallback_is_confirmed_when_http_recovers(self):
        probes=[];udp_payloads=[]
        with patch.object(WledWorker,'_http',lambda self,payload,timeout=None:(_ for _ in ()).throw(AssertionError('HTTP POST should not be needed when state already matches'))), \
             patch.object(WledWorker,'_http_state',lambda self,timeout=.8:probes.append(True) or {'on':True,'ps':9}), \
             patch.object(WledWorker,'_udp_state',lambda self,payload:udp_payloads.append(payload.copy()) or setattr(self,'control_path','udp-json-fallback') or True):
            w=WledWorker(target(),lambda *a,**k:None,None)
            try:
                w.RELEASE_SETTLE_SECONDS=.02
                w.next_http_try=time.monotonic()+60
                w.preset(9,'autonome',0)
                self.assertTrue(wait_for(lambda:len(udp_payloads)==1))
                with w.lock:w.next_http_try=time.monotonic()
                self.assertTrue(wait_for(lambda:bool(probes)))
                snap=w.snapshot()
                self.assertEqual(len(udp_payloads),1)
                self.assertTrue(probes)
                self.assertFalse(snap['pending_confirmation'])
                self.assertEqual(snap['control_path'],'http-recovered')
                self.assertEqual(snap['state'],'AUTONOMOUS')
            finally:w.close()

    def test_stream_entry_wakes_wled_with_full_global_brightness(self):
        controls=[];sent=[]
        with patch.object(WledWorker,'_release_realtime',lambda self:True), \
             patch.object(WledWorker,'_udp_state',lambda self,payload:controls.append(payload.copy()) or True), \
             patch.object(WledWorker,'_send_frame',lambda self,frame:sent.append(frame) or True):
            w=WledWorker(target(),lambda *a,**k:None,None)
            try:
                w.stream([(20,30,40)]*12,25,'test')
                self.assertTrue(wait_for(lambda:bool(sent)))
                self.assertTrue(any(p.get('on') is True and p.get('bri')==255 for p in controls))
            finally:w.close()

    def test_resync_after_wled_reboot_restores_realtime_baseline(self):
        controls=[];releases=[]
        with patch.object(WledWorker,'_release_realtime',lambda self:releases.append(True) or True), \
             patch.object(WledWorker,'_udp_state',lambda self,payload:controls.append(payload.copy()) or True):
            w=WledWorker(target(),lambda *a,**k:None,None)
            try:
                with w.lock:w.resync_requested=True
                w._resync_stream()
                snap=w.snapshot()
                self.assertTrue(releases)
                self.assertTrue(any(p.get('on') is True and p.get('bri')==255 for p in controls))
                self.assertFalse(snap['resync_requested'])
                self.assertGreaterEqual(snap['recoveries'],1)
            finally:w.close()

    def test_manual_wled_reboot_uses_reboot_command(self):
        http=[];udp=[];releases=[]
        with patch.object(WledWorker,'_release_realtime',lambda self:releases.append(True) or True), \
             patch.object(WledWorker,'_http',lambda self,payload,timeout=None:http.append(payload.copy()) or {}), \
             patch.object(WledWorker,'_udp_state',lambda self,payload:udp.append(payload.copy()) or True):
            w=WledWorker(target(),lambda *a,**k:None,None)
            try:
                self.assertTrue(w.reboot('test'))
                self.assertTrue(wait_for(lambda:any(p.get('rb') is True for p in http)))
                self.assertTrue(releases)
                self.assertEqual(udp,[])
            finally:w.close()

    def test_rainbow_effect_contains_multiple_primary_dominances(self):
        frame=effect('rainbow',120,0)
        self.assertTrue(any(r>200 and g<100 and b<100 for r,g,b in frame))
        self.assertTrue(any(g>200 and r<100 and b<100 for r,g,b in frame))
        self.assertTrue(any(b>200 and r<100 and g<100 for r,g,b in frame))

    def test_diagnostics_persistent_copyable_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Diagnostics(Path(tmp),'detailed',7,10)
            try:
                d.event('normal','ENGINE','démarrage')
                d.event('detailed','PRESENCE','Logo : Inactivité',idle=300)
                time.sleep(.15)
                report=d.report(30,{'version':'0.4.2'})
                self.assertIn('RAPTOR LINK',report)
                self.assertIn('Logo : Inactivité',report)
                self.assertIn('version: 0.4.2',report)
            finally:d.close()

    def test_diagnostics_off_skips_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Diagnostics(Path(tmp),'off',7,10)
            try:
                d.event('normal','ENGINE','should not exist')
                time.sleep(.1)
                self.assertEqual(d.recent_lines(30),[])
            finally:d.close()

class WindowLifecycle(unittest.TestCase):
    def test_window_controller_blocks_duplicate_launch_while_starting(self):
        launches=[];now=[100.0]
        w=WindowController(lambda:False,lambda:launches.append(True),clock=lambda:now[0])
        w.open();w.open()
        self.assertEqual(len(launches),1)
        now[0]=131.0
        w.open()
        self.assertEqual(len(launches),2)

    def test_window_controller_focuses_existing_window_without_launching(self):
        launches=[]
        w=WindowController(lambda:True,lambda:launches.append(True))
        w.open()
        self.assertEqual(launches,[])

    def test_window_controller_closes_dashboard_on_app_quit(self):
        closes=[]
        w=WindowController(lambda:False,lambda:None,close=lambda:closes.append(True) or True)
        self.assertTrue(w.close())
        self.assertEqual(closes,[True])

if __name__=='__main__':
    unittest.main()
