"""Local Windows monitor selection; images stay in memory only.
API references: learn.microsoft.com/windows/win32/api/winuser/nf-winuser-enumdisplaymonitors
pillow.readthedocs.io/en/stable/reference/ImageGrab.html
"""
import ctypes as C
from ctypes import wintypes as W
import sys

def enable_dpi_awareness():
    if sys.platform=='win32':
        try:
            fn=C.WinDLL('user32').SetProcessDpiAwarenessContext
            fn.argtypes=[C.c_void_p];fn.restype=W.BOOL;fn(C.c_void_p(-4))
        except (AttributeError,OSError):pass

def monitors(demo=False):
    if demo:return [dict(id='demo1',name='Écran 1 · principal · 1920 × 1080',primary=True,rect=[0,0,1920,1080]),dict(id='demo2',name='Écran 2 · 1920 × 1080',primary=False,rect=[1920,0,3840,1080])]
    if sys.platform!='win32':return []
    class Info(C.Structure):
        _fields_=[('size',W.DWORD),('rect',W.RECT),('work',W.RECT),('flags',W.DWORD),('device',W.WCHAR*32)]
    user=C.WinDLL('user32',use_last_error=True)
    callback=C.WINFUNCTYPE(W.BOOL,W.HANDLE,W.HDC,C.POINTER(W.RECT),W.LPARAM)
    user.EnumDisplayMonitors.argtypes=[W.HDC,C.POINTER(W.RECT),callback,W.LPARAM];user.EnumDisplayMonitors.restype=W.BOOL
    user.GetMonitorInfoW.argtypes=[W.HANDLE,C.POINTER(Info)];user.GetMonitorInfoW.restype=W.BOOL
    result=[]
    @callback
    def visit(handle,dc,rect,data):
        info=Info();info.size=C.sizeof(info)
        if user.GetMonitorInfoW(handle,C.byref(info)):
            r=info.rect;number=info.device.replace('\\\\.\\DISPLAY','')
            result.append(dict(id=info.device,name='Écran '+number+(' · principal' if info.flags&1 else '')+f' · {r.right-r.left} × {r.bottom-r.top}',primary=bool(info.flags&1),rect=[r.left,r.top,r.right,r.bottom]))
        return True
    if not user.EnumDisplayMonitors(None,None,visit,0):raise C.WinError(C.get_last_error())
    return sorted(result,key=lambda m:m['id'])

def selected_monitors(ids,available):
    if ids==('primary',) or ids==['primary']:selected=[m for m in available if m['primary']]
    elif ids==('all',) or ids==['all']:selected=available
    else:
        selected=[m for m in available if m['id'] in ids]
        if len(selected)!=len(ids):raise ValueError('Un écran sélectionné est déconnecté. Actualisez les écrans de la zone.')
    if not selected:raise ValueError('Aucun écran disponible')
    return selected

def screen_key(route):
    return (tuple(route.get('screen_ids',['primary'])),tuple(sorted((key,int(pos[0]),int(pos[1])) for key,pos in route.get('screen_layout',{}).items())))

def layout_rect(m,layout):
    x,y,r,b=m['rect'];left,top=layout.get(m['id'],[x,y])
    return [left,top,left+r-x,top+b-y]

def compose_screen(desktop,available,selected,size=(320,180),layout=None):
    from PIL import Image
    left=min(m['rect'][0] for m in available);top=min(m['rect'][1] for m in available)
    layout=layout or {};rects=[layout_rect(m,layout) for m in selected]
    x0=min(r[0] for r in rects);y0=min(r[1] for r in rects)
    x1=max(r[2] for r in rects);y1=max(r[3] for r in rects)
    result=Image.new('RGB',size)
    for m,dest in zip(selected,rects):
        x,y,r,b=m['rect'];dx,dy,dr,db=dest;a=round((dx-x0)*size[0]/(x1-x0));c=round((dr-x0)*size[0]/(x1-x0));d=round((dy-y0)*size[1]/(y1-y0));e=round((db-y0)*size[1]/(y1-y0))
        if c>a and e>d:result.paste(desktop.crop((x-left,y-top,r-left,b-top)).resize((c-a,e-d)),(a,d))
    return result
