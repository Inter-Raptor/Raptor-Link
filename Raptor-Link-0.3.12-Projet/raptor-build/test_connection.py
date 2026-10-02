import sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path('RaptorLink').resolve()))
from engine import probe_target
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
  self.assertEqual(sum(p is not None for _,p in calls),1)
  self.assertTrue(all(p is None for _,p in calls[3:]))
 def test_fade_recovers_after_idle(self):
  f=Fade()
  for _ in range(100):f.step(True,.04,3,2)
  self.assertEqual(f.value,1)
  for _ in range(60):f.step(False,.04,3,2)
  self.assertEqual(f.value,0)
  for _ in range(100):f.step(True,.04,3,2)
  self.assertEqual(f.value,1)
if __name__=='__main__':unittest.main()
