import sys,unittest,datetime,copy
from pathlib import Path
sys.path.insert(0,str(Path('RaptorLink').resolve()))
from features import ColorTransition,due_alarm,gate,sample_path,render
from screens import selected_monitors,compose_screen,screen_key
from PIL import Image
from engine import validate
from window import WindowController
from test_features import target
class Release(unittest.TestCase):
 def test_linear_duration_and_retarget(self):
  f=ColorTransition();f.step([(0,0,200)],.1,1)
  self.assertEqual(f.step([(200,0,0)],.5,1),[(100,0,100)])
  self.assertEqual(f.step([(200,0,0)],.5,1),[(200,0,0)])
  self.assertEqual(f.step([(0,200,0)],.5,1),[(100,100,0)])
  self.assertEqual(f.step([(0,0,200)],.5,1),[(50,50,100)])
  self.assertEqual(f.step([(0,0,200)],.5,1),[(0,0,200)])
  self.assertEqual(f.step([(200,0,0)],.1,0),[(200,0,0)])
 def test_independent_leds(self):
  f=ColorTransition();f.step([(0,0,0)]*2,.1,1)
  for i in range(10):out=f.step([(100,0,0),(0,i*10,0)],.1,1)
  self.assertAlmostEqual(out[0][0],100)
 def test_old_alarm_migrates_without_duplicate(self):
  t=target();t['alarm']={'enabled':True,'time':'09:00','duration':30,'days':[3],'effect':'rainbow'}
  t=validate({'targets':[t]})['targets'][0]
  self.assertNotIn('alarm',t);self.assertEqual(t['alarms'][0]['id'],'legacy')
  now=datetime.datetime(2026,10,1,9);seen={t['ip']:'2026-10-01 09:00'}
  self.assertIsNone(due_alarm(t,now,seen))
 def test_multiple_alarms_once_and_overlap_priority(self):
  t=target();t['alarms']=[dict(id=str(i),name=str(i),enabled=True,time=hour,duration=90,days=[3],effect='rainbow') for i,hour in enumerate(['09:00','09:01','09:01'])]
  t=validate({'targets':[t]})['targets'][0];seen={}
  self.assertEqual(due_alarm(t,datetime.datetime(2026,10,1,9),seen)['id'],'0')
  self.assertIsNone(due_alarm(t,datetime.datetime(2026,10,1,9),seen))
  self.assertEqual(due_alarm(t,datetime.datetime(2026,10,1,9,1),seen)['id'],'2')
  self.assertIsNone(due_alarm(t,datetime.datetime(2026,10,1,9,1),seen))
  self.assertIsNone(due_alarm(t,datetime.datetime(2026,10,2,9),seen))
  self.assertEqual(due_alarm(t,datetime.datetime(2026,10,8,9),seen)['id'],'0')
 def test_alarm_validation(self):
  t=target();t['alarms']=[dict(id='a',enabled=True,time='25:00',effect='solid')]
  with self.assertRaises(ValueError):validate({'targets':[t]})
 def test_window_double_click_focus_close_reopen(self):
  state={'time':100,'visible':False,'launches':0,'focus':0}
  def focus():
   if state['visible']:state['focus']+=1;return True
   return False
  def launch():state['launches']+=1
  w=WindowController(focus,launch,lambda:state['time'])
  w.open();w.open();self.assertEqual(state['launches'],1)
  state['visible']=True;w.open();self.assertEqual(state['focus'],1);self.assertEqual(state['launches'],1)
  state['visible']=False;w.ready();w.open();self.assertEqual(state['launches'],2)
  state['time']+=31;w.open();self.assertEqual(state['launches'],3)
class Geometry(unittest.TestCase):
 def test_counts_and_exact_render(self):
  pts=[[0,0],[.5,0],[1,1]];positions=sample_path(pts,105,[5,100])
  self.assertEqual(len(positions),105);self.assertTrue(all(p[1]==0 for p in positions[:5]));self.assertGreater(positions[5][1],0)
  t=target();t['count']=105;t['routes'][0].update(end=105,source='screen',screen_ids=['a'],points=pts,path_counts=[5,100])
  t=validate({'targets':[t]})['targets'][0]
  img=Image.new('RGB',(100,100),(20,40,60))
  self.assertEqual(render(t,{},0,{screen_key(t['routes'][0]):img}),[(20,40,60)]*105)
  t['routes'][0]['path_counts']=[5,99]
  with self.assertRaises(ValueError):validate({'targets':[t]})
 def test_negative_coordinates_and_gap(self):
  monitors=[dict(id='left',primary=False,rect=[-100,0,0,100]),dict(id='main',primary=True,rect=[0,0,100,100]),dict(id='right',primary=False,rect=[100,0,200,100])]
  img=Image.new('RGB',(300,100));img.paste((255,0,0),(0,0,100,100));img.paste((0,255,0),(100,0,200,100));img.paste((0,0,255),(200,0,300,100))
  out=compose_screen(img,monitors,selected_monitors(['left','right'],monitors),(300,100))
  self.assertEqual(out.getpixel((50,50)),(255,0,0));self.assertEqual(out.getpixel((150,50)),(0,0,0));self.assertEqual(out.getpixel((250,50)),(0,0,255))
  out=compose_screen(img,monitors,selected_monitors(['primary'],monitors))
  self.assertEqual(out.getpixel((0,0)),(0,255,0))
  with self.assertRaises(ValueError):selected_monitors(['missing'],monitors)
 def test_custom_layout_captures_original_screen_pixels(self):
  monitors=[dict(id='a',primary=True,rect=[0,0,100,100]),dict(id='b',primary=False,rect=[100,0,200,100])]
  img=Image.new('RGB',(200,100));img.paste((255,0,0),(0,0,100,100));img.paste((0,0,255),(100,0,200,100))
  out=compose_screen(img,monitors,monitors,(100,200),layout={'a':[0,0],'b':[0,100]})
  self.assertEqual(out.getpixel((50,50)),(255,0,0));self.assertEqual(out.getpixel((50,150)),(0,0,255))
 def test_no_screen_selected_rejected(self):
  t=target();t['routes'][0]['screen_ids']=[]
  with self.assertRaises(ValueError):validate({'targets':[t]})
if __name__=='__main__':unittest.main()
