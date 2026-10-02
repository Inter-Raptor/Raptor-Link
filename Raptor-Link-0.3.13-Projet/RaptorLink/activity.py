"""Presence detection: timestamps and boolean activity only; no key contents retained."""
import ctypes as C
import time

class ActivityClock:
    def __init__(self,now):self.last=now;self.tick=None;self.cursor=None
    def update(self,now,tick,cursor,pressed,reported_idle):
        changed=(self.tick is not None and tick!=self.tick) or (self.cursor is not None and cursor is not None and cursor!=self.cursor) or pressed
        if changed:self.last=now
        if tick is not None:self.tick=tick
        if cursor is not None:self.cursor=cursor
        elapsed=max(0,now-self.last)
        return min(elapsed,reported_idle) if reported_idle is not None else elapsed

class Presence:
    class Info(C.Structure):_fields_=[('size',C.c_uint32),('tick',C.c_uint32)]
    class Point(C.Structure):_fields_=[('x',C.c_int32),('y',C.c_int32)]
    def __init__(self):
        self.user=C.WinDLL('user32',use_last_error=True);self.kernel=C.WinDLL('kernel32',use_last_error=True)
        self.user.GetLastInputInfo.argtypes=[C.POINTER(self.Info)];self.user.GetLastInputInfo.restype=C.c_int
        self.user.GetCursorPos.argtypes=[C.POINTER(self.Point)];self.user.GetCursorPos.restype=C.c_int
        self.user.GetAsyncKeyState.argtypes=[C.c_int];self.user.GetAsyncKeyState.restype=C.c_short
        self.kernel.GetTickCount.argtypes=[];self.kernel.GetTickCount.restype=C.c_uint32
        self.clock=ActivityClock(time.monotonic())
    def seconds(self):
        info=self.Info(C.sizeof(self.Info),0);p=self.Point()
        good=bool(self.user.GetLastInputInfo(C.byref(info)))
        cursor=(p.x,p.y) if self.user.GetCursorPos(C.byref(p)) else None
        pressed=any(self.user.GetAsyncKeyState(k)&0x8000 for k in range(1,255))
        elapsed=((self.kernel.GetTickCount()-info.tick)&0xffffffff)/1000 if good else None
        # Some injected events report a future tick; never interpret it as 49 days idle.
        if elapsed is not None and elapsed>7*86400:elapsed=None
        return self.clock.update(time.monotonic(),info.tick if good else None,cursor,pressed,elapsed)
