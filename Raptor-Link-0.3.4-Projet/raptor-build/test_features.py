import sys,unittest,datetime,copy,tempfile,time
from pathlib import Path
sys.path.insert(0,str(Path('RaptorLink').resolve()))
from engine import validate,Engine,DEFAULT
from features import window,gate,Fade,render,sample_path

def target():
 return {'name':'Test','ip':'192.168.1.175','count':10,'routes':[{'device':'','ids':[],'start':1,'end':10,'source':'solid','color':[200,100,0]}]}
class Features(unittest.TestCase):
 def setUp(self):self.c=validate({'targets':[target()]});self.t=self.c['targets'][0];self.s=self.c['settings']
 def test_overnight(self):
  sch={'enabled':True,'start':'22:00','end':'06:00','days':[0],'mode':'active'}
  self.assertTrue(window(sch,datetime.datetime(2026,9,28,23)))
  self.assertTrue(window(sch,datetime.datetime(2026,9,29,5,59)))
  self.assertFalse(window(sch,datetime.datetime(2026,9,29,6)))
  self.assertFalse(window(sch,datetime.datetime(2026,9,29,23)))
 def test_schedule_priority(self):
  self.t['schedule'].update(enabled=True,start='09:00',end='17:00',mode='always')
  self.assertFalse(gate(self.t,self.s,datetime.datetime(2026,9,30,18),0,False,True,True)[0])
  self.assertTrue(gate(self.t,self.s,datetime.datetime(2026,9,30,12),9999,True,False)[0])
  self.t['schedule']['mode']='active'
  self.assertFalse(gate(self.t,self.s,datetime.datetime(2026,9,30,12),9999,False,False)[0])
 def test_fade(self):
  f=Fade();self.assertAlmostEqual(f.step(True,.5,2,1),.25);self.assertAlmostEqual(f.step(True,.5,2,1),.5)
  self.assertAlmostEqual(f.step(False,.25,2,1),.25);self.assertEqual(f.step(False,1,2,1),0);self.assertEqual(f.step(True,.1,0,0),1)
 def test_render_zone_gain_and_reverse(self):
  r=self.t['routes'][0];r.update(source='icue',device='d',ids=[1,2],reverse=True,brightness=50)
  out=render(self.t,{'d':{1:(200,0,0),2:(0,100,0)}},0)
  self.assertEqual(out[0],(0,50,0));self.assertEqual(out[-1],(100,0,0))
 def test_spatial(self):
  r=self.t['routes'][0];r.update(source='icue',device='d',ids=[1,2],spatial=True,points=[[1,0],[0,0]])
  out=render(self.t,{'d':{1:(200,0,0),2:(0,100,0)}},0)
  self.assertEqual(out[0],(0,100,0));self.assertEqual(out[-1],(200,0,0))
 def test_bad_profile_rejected(self):
  with self.assertRaises(ValueError):validate({'profiles':[{'name':'Bad','targets':[dict(target(),count=0)]}]})
 def test_independent_bad_source(self):
  with tempfile.TemporaryDirectory() as d:
   engine=Engine(Path(d)/'config.json',True)
   try:
    t2=target();t2.update(name='Missing',ip='192.168.1.80');t2['routes'][0]['source']='icue'
    engine.save({'settings':{'fade_in':0},'targets':[target(),t2]});engine.want_run=True
    time.sleep(.6)
    state=engine.snapshot();self.assertTrue(state['running']);self.assertEqual(state['targets']['192.168.1.175'],'Synchronisé');self.assertIn('Association 1',state['targets']['192.168.1.80']);self.assertEqual(state['frames']['192.168.1.175'][0],(200,100,0))
   finally:engine.shutdown()
 def test_path(self):self.assertEqual(sample_path([[0,0],[1,0],[1,1]],3),[(0,0),(1,0),(1,1)])
if __name__=='__main__':unittest.main()
