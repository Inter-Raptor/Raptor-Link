"""Bring the existing Windows dashboard forward instead of opening duplicates."""
import ctypes as C
from ctypes import wintypes as W
import threading
import time


def focus_window(title):
    user=C.WinDLL('user32',use_last_error=True)
    callback=C.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
    user.EnumWindows.argtypes=[callback,W.LPARAM]
    user.GetWindowTextLengthW.argtypes=[W.HWND];user.GetWindowTextLengthW.restype=C.c_int
    user.GetWindowTextW.argtypes=[W.HWND,W.LPWSTR,C.c_int]
    user.IsWindowVisible.argtypes=[W.HWND];user.IsWindowVisible.restype=W.BOOL
    user.IsIconic.argtypes=[W.HWND];user.IsIconic.restype=W.BOOL
    user.ShowWindow.argtypes=[W.HWND,C.c_int]
    user.SetForegroundWindow.argtypes=[W.HWND];user.SetForegroundWindow.restype=W.BOOL
    found=[]
    @callback
    def visit(hwnd,_):
        length=user.GetWindowTextLengthW(hwnd)
        if length and user.IsWindowVisible(hwnd):
            text=C.create_unicode_buffer(length+1);user.GetWindowTextW(hwnd,text,length+1)
            if text.value==title or text.value.startswith(title+' - '):
                found.append(hwnd);return False
        return True
    user.EnumWindows(visit,0)
    if not found:return False
    hwnd=found[0]
    if user.IsIconic(hwnd):user.ShowWindow(hwnd,9)
    user.SetForegroundWindow(hwnd)
    return True


class WindowController:
    def __init__(self,focus,launch,clock=time.monotonic):
        self.focus=focus;self.launch=launch;self.clock=clock
        self.pending_until=0;self.lock=threading.Lock()
    def ready(self):
        with self.lock:self.pending_until=0
    def open(self):
        with self.lock:
            if self.focus():
                self.pending_until=0;return
            if self.clock()<self.pending_until:return
            self.launch()
            # Also suppress double clicks while Chromium is starting.
            self.pending_until=self.clock()+30
