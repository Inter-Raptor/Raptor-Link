"""Raptor Link: scheduling, effects and spatial sampling (no network I/O)."""
import colorsys, datetime, math, copy
from screens import screen_key

def number(v,lo,hi,label):
    v=float(v)
    if not math.isfinite(v) or not lo<=v<=hi: raise ValueError(label+' hors limites')
    return v

def clock(s):
    try:
        h,m=map(int,str(s).split(':'))
        if not 0<=h<24 or not 0<=m<60: raise ValueError()
        return h*60+m
    except Exception: raise ValueError('Heure attendue : HH:MM')

def extras(cfg,raw):
    s=cfg['settings']
    for key,default,hi in [('fade_in',3,60),('fade_out',2,60)]: s[key]=number(s.get(key,default),0,hi,key)
    s['audio_device']=int(number(s.get('audio_device',-1),-1,256,'Entrée audio'))
    s['audio_mode']=s.get('audio_mode','input' if s['audio_device']>=0 else 'output')
    if s['audio_mode'] not in ['input','output']:raise ValueError('Type de capture audio invalide')
    s['audio_output']=str(s.get('audio_output','default'))[:512]
    s['tutorial']=s.get('tutorial','ask') if s.get('tutorial','ask') in ['ask','never','done'] else 'ask'
    s['logo_animation']=bool(s.get('logo_animation',True))
    for t in cfg['targets']:
        sch=t.setdefault('schedule',{'enabled':False,'start':'08:00','end':'23:00','mode':'active','days':list(range(7))})
        clock(sch.get('start'));clock(sch.get('end'))
        if sch.get('mode') not in ['active','always']: raise ValueError('Mode horaire invalide')
        sch['days']=list(dict.fromkeys(int(d) for d in sch.get('days',range(7))))
        if any(d<0 or d>6 for d in sch['days']):raise ValueError('Jour invalide')
        if 'alarms' not in t:
            old=t.pop('alarm',None)
            t['alarms']=[dict(old,id='legacy',name='Réveil 1')] if old else []
        t.pop('alarm',None)
        if not isinstance(t['alarms'],list) or len(t['alarms'])>32:raise ValueError('32 réveils maximum par éclairage')
        seen=set()
        for i,alarm in enumerate(t['alarms']):
            alarm['id']=str(alarm.get('id','alarm-'+str(i)))[:80]
            if not alarm['id'] or alarm['id'] in seen:raise ValueError('Identifiant de réveil invalide ou répété')
            seen.add(alarm['id']);alarm['name']=str(alarm.get('name','Réveil '+str(i+1)))[:60]
            alarm['enabled']=bool(alarm.get('enabled',False))
            clock(alarm.get('time'));alarm['duration']=number(alarm.get('duration',30),1,3600,'Durée du réveil')
            alarm['days']=list(dict.fromkeys(int(d) for d in alarm.get('days',range(7))))
            if any(d<0 or d>6 for d in alarm['days']):raise ValueError('Jour invalide')
            if alarm.get('effect') not in ['rainbow','breathe','chase','solid']:raise ValueError('Effet réveil invalide')
        for r in t['routes']:
            r['source']=r.get('source','icue')
            if r['source'] not in ['icue','msi','openrgb','rainbow','breathe','chase','solid','screen','audio']:raise ValueError('Source inconnue')
            r['rgb_ecosystem']=str(r.get('rgb_ecosystem','openrgb'))[:32]
            if r['rgb_ecosystem'] not in ['openrgb','msi','gigabyte','asus','razer','logitech','steelseries']:r['rgb_ecosystem']='openrgb'
            ids=r.get('screen_ids',['primary'])
            if not isinstance(ids,list) or not 1<=len(ids)<=32 or any(not isinstance(v,str) or not v or len(v)>128 for v in ids):raise ValueError('Sélectionnez au moins un écran pour la zone')
            ids=list(dict.fromkeys(ids))
            if any(v in ['all','primary'] for v in ids) and len(ids)!=1:raise ValueError('Sélection écran incohérente')
            r['screen_ids']=ids
            layout=r.get('screen_layout',{})
            if not isinstance(layout,dict) or len(layout)>32:raise ValueError('Disposition des écrans invalide')
            for key,pos in layout.items():
                if not isinstance(key,str) or len(key)>128 or not isinstance(pos,list) or len(pos)!=2:raise ValueError('Position écran invalide')
                layout[key]=[int(number(v,-100000,100000,'Position écran')) for v in pos]
            r['screen_layout']=layout
            r['brightness']=number(r.get('brightness',100),0,100,'Luminosité de zone')
            r['offset']=int(number(r.get('offset',0),-10000,10000,'Décalage'))
            r['smooth']=number(r.get('smooth',0),0,60,'Fondu entre couleurs')
            r['speed']=number(r.get('speed',1),.05,10,'Vitesse')
            r['color']=r.get('color',[255,100,20])
            if not isinstance(r['color'],list) or len(r['color'])!=3:raise ValueError('Couleur invalide')
            r['color']=[int(number(c,0,255,'Couleur')) for c in r['color']]
            pts=r.get('points',[[.05,.5],[.95,.5]])
            if not isinstance(pts,list) or not 2<=len(pts)<=64:raise ValueError('Le tracé demande 2 à 64 points')
            r['points']=[[number(p[0],0,1,'Position'),number(p[1],0,1,'Position')] for p in pts]
            counts=r.get('path_counts')
            if counts is not None:
                if not isinstance(counts,list) or len(counts)!=len(pts)-1:raise ValueError('Le plan demande un nombre de LED par segment')
                if any(not isinstance(c,int) or isinstance(c,bool) or c<0 for c in counts):raise ValueError('Nombre de LED par segment : entier positif ou nul')
                if sum(counts)!=r['end']-r['start']+1:raise ValueError(t['name']+' : le total des LED du plan doit correspondre à la plage de la zone')
            r['path_counts']=counts

    cfg['profiles']=copy.deepcopy(raw.get('profiles',[]))
    if not isinstance(cfg['profiles'],list) or len(cfg['profiles'])>20:raise ValueError('20 profils maximum')
    for p in cfg['profiles']:
        p['name']=str(p['name'])[:50]
        if not isinstance(p.get('targets'),list):raise ValueError('Profil invalide')
    return cfg

def window(sch,now):
    if not sch.get('enabled'):return True
    a,b=clock(sch['start']),clock(sch['end']);m=now.hour*60+now.minute
    if a==b:return now.weekday() in sch['days']
    if a<b:return now.weekday() in sch['days'] and a<=m<b
    return (now.weekday() in sch['days'] and m>=a) or ((now.weekday()-1)%7 in sch['days'] and m<b)

def gate(t,s,now,idle,locked,awake,alarm=False):
    sch=t['schedule']
    if not window(sch,now):return False,'Hors horaires'
    if sch.get('enabled') and sch['mode']=='always':return True,'Plage continue'
    if locked and s['lock_off']:return False,'Verrouillé'
    if alarm:return True,'Réveil'
    delay=t.get('idle_seconds') if t.get('idle_seconds') is not None else s['idle_seconds']
    if delay and idle>=delay and not awake:return False,'Inactivité'
    return True,'Synchronisé'

def effect(kind,n,seconds,color=(255,100,20),speed=1,level=0):
    phase=seconds*speed
    if kind=='solid':return [tuple(color)]*n
    if kind=='breathe':return [tuple(round(c*(.15+.85*(.5+.5*math.sin(phase*2)))) for c in color)]*n
    if kind=='audio':return [tuple(round(c*min(1,level*3)) for c in colorsys.hsv_to_rgb((j/max(1,n)+phase*.1)%1,1,255)) if j/n<min(1,level*3) else (0,0,0) for j in range(n)]
    if kind=='chase':return [tuple(round(c*max(0,1-((j/n-phase*.2)%1)*8)) for c in color) for j in range(n)]
    return [tuple(round(c*255) for c in colorsys.hsv_to_rgb((j/max(1,n)+phase*.12)%1,.95,1)) for j in range(n)]

def sample_path(points,n,counts=None):
    if counts is not None:
        result=[]
        for a,b,count in zip(points,points[1:],counts):
            for i in range(count):
                f=(i+.5)/count
                result.append((a[0]+(b[0]-a[0])*f,a[1]+(b[1]-a[1])*f))
        return result

    distances=[math.dist(a,b) for a,b in zip(points,points[1:])];total=sum(distances)
    result=[]
    for i in range(n):
        d=total*i/max(1,n-1)
        for k,length in enumerate(distances):
            if d<=length or k==len(distances)-1:
                f=d/length if length else 0;a,b=points[k:k+2];result.append((a[0]+(b[0]-a[0])*f,a[1]+(b[1]-a[1])*f));break
            d-=length
    return result

def render(t,colors,seconds,screen=None,audio=0,history=None,dt=.04):
    out=[(0,0,0)]*t['count']
    for ri,r in enumerate(t['routes']):
        n=r['end']-r['start']+1;kind=r.get('source','icue')
        if kind in ('icue','msi','openrgb'):
            src=colors.get(r['device'],{});ids=r['ids']; ordered=[src.get(i,(0,0,0)) for i in ids]
            arr=[ordered[j%len(ordered) if r['mapping']=='repeat' else min(len(ordered)-1,j*len(ordered)//n)] for j in range(n)] if ordered else [(0,0,0)]*n
            if r.get('spatial') and ordered:
                axis=1 if r.get('axis')=='y' else 0
                arr=[ordered[min(len(ordered)-1,int(p[axis]*len(ordered)))] for p in sample_path(r['points'],n,r.get('path_counts'))]
        elif kind=='screen':
            image=screen.get(screen_key(r)) if isinstance(screen,dict) else screen
            arr=[image.getpixel((min(image.width-1,int(x*image.width)),min(image.height-1,int(y*image.height))))[:3] for x,y in sample_path(r['points'],n,r.get('path_counts'))] if image else [(0,0,0)]*n
        else:arr=effect(kind,n,seconds,r['color'],r['speed'],audio)
        if r.get('reverse'):arr.reverse()
        off=r.get('offset',0)%n
        if off:arr=arr[-off:]+arr[:-off]
        gain=r.get('brightness',100)*t['brightness']/10000
        arr=[tuple(round(v*gain) for v in c) for c in arr]
        key=(t['ip'],ri)
        if history is not None:
            transition=history.setdefault(key,ColorTransition())
            arr=transition.step(arr,dt,r.get('smooth',0))
        out[r['start']-1:r['end']]=[tuple(round(v) for v in c) for c in arr]
    return out

class Fade:
    def __init__(self):self.value=0
    def step(self,on,dt,up,down):
        duration=up if on else down
        if duration<=0:self.value=float(on)
        else:self.value=max(0,min(1,self.value+(1 if on else -1)*dt/duration))
        return self.value


class ColorTransition:
    """Linear transition per LED; a new target starts from the displayed color."""
    def __init__(self):
        self.current=[];self.targets=[];self.origins=[];self.elapsed=[]
    def step(self,colors,dt,duration):
        colors=[tuple(c) for c in colors]
        if duration<=0 or len(colors)!=len(self.current):
            self.current=colors[:];self.targets=colors[:];self.origins=colors[:];self.elapsed=[duration]*len(colors)
            return self.current[:]
        for i,target in enumerate(colors):
            if target!=self.targets[i]:
                self.origins[i]=self.current[i];self.targets[i]=target;self.elapsed[i]=0
            self.elapsed[i]+=max(0,dt)
            amount=min(1,self.elapsed[i]/duration)
            self.current[i]=tuple(a+(b-a)*amount for a,b in zip(self.origins[i],target))
        return self.current[:]


def due_alarm(t,now,seen):
    """Latest due entry wins; mark every due entry once for this date/minute."""
    selected=None
    for alarm in t['alarms']:
        key=t['ip'] if alarm['id']=='legacy' else t['ip']+'|'+alarm['id']
        stamp=now.date().isoformat()+' '+alarm['time']
        if alarm['enabled'] and now.weekday() in alarm['days'] and now.hour*60+now.minute==clock(alarm['time']) and seen.get(key)!=stamp:
            seen[key]=stamp;selected=alarm
    return selected
