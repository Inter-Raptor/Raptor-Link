import sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path('RaptorLink').resolve()))
from engine import probe_target,packets
from features import Fade,gate,extras

class Connection(unittest.TestCase):
 def test_regular_checks_never_switch_off(self):
  t={'ip':'192.168.1.175','count':257};calls=[]
  def fake(ip,path,payload=None):
   calls.append((path,payload))
   return {'leds':{'count':257}} if path=='/json/info' else {'on':True,'bri':128}
  with patch('engine.http',fake):
   self.assertEqual(probe_target(t,True)['bri'],128)
   for _ in range(20):self.assertIsNone(probe_target(t,False))
  self.assertTrue(all(payload is None for _,payload in calls))
  self.assertEqual([path for path,_ in calls[:2]],['/json/info','/json/state'])

 def test_realtime_packet_keeps_wled_alive_for_short_gaps(self):
  packet=packets([(1,2,3)])[0]
  self.assertEqual(packet[0],2)
  self.assertEqual(packet[1],5)

 def test_fade_recovers_after_idle(self):
  f=Fade()
  for _ in range(100):f.step(True,.04,3,2)
  self.assertEqual(f.value,1)
  for _ in range(60):f.step(False,.04,3,2)
  self.assertEqual(f.value,0)
  for _ in range(100):f.step(True,.04,3,2)
  self.assertEqual(f.value,1)

if __name__=='__main__':unittest.main()
