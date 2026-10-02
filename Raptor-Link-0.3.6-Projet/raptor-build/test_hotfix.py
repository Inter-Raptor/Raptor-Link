import sys,unittest,array
from pathlib import Path
sys.path.insert(0,str(Path('RaptorLink').resolve()))
from activity import ActivityClock
from audio_output import pcm_level
class Hotfix(unittest.TestCase):
 def test_mouse_keeps_active_with_frozen_windows_timestamp(self):
  c=ActivityClock(0)
  for second in range(301):
   age=c.update(second,123,(second,42),False,second)
   self.assertEqual(age,0)
  self.assertEqual(c.update(360,123,(300,42),False,360),60)
 def test_key_keeps_active(self):
  c=ActivityClock(0)
  for second in range(301):self.assertEqual(c.update(second,123,(0,0),True,second),0)
 def test_timestamp_restarts_idle(self):
  c=ActivityClock(0);c.update(0,123,(0,0),False,0)
  self.assertEqual(c.update(65,456,(0,0),False,0),0)
  self.assertEqual(c.update(125,456,(0,0),False,60),60)
 def test_audio_levels(self):
  self.assertEqual(pcm_level(bytes(2048)),0)
  self.assertEqual(pcm_level(array.array('h',[16384,-16384]*512).tobytes()),1)
if __name__=='__main__':unittest.main()
