"""Manage the single Raptor Link dashboard window on Windows."""
import ctypes as C
from ctypes import wintypes as W
import threading
import time


WM_CLOSE=0x0010


def _matching_windows(title):
    """Return visible top-level windows belonging to this Raptor Link instance.

    Chromium may append the browser name using different separators.  The
    instance title contains the local server port, so a substring match is both
    robust and specific enough to avoid touching unrelated browser windows.
    """
    user=C.WinDLL('user32',use_last_error=True)
    callback=C.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
    user.EnumWindows.argtypes=[callback,W.LPARAM]
    user.GetWindowTextLengthW.argtypes=[W.HWND];user.GetWindowTextLengthW.restype=C.c_int
    user.GetWindowTextW.argtypes=[W.HWND,W.LPWSTR,C.c_int]
    user.IsWindowVisible.argtypes=[W.HWND];user.IsWindowVisible.restype=W.BOOL
    found=[]
    @callback
    def visit(hwnd,_):
        length=user.GetWindowTextLengthW(hwnd)
        if length and user.IsWindowVisible(hwnd):
            text=C.create_unicode_buffer(length+1);user.GetWindowTextW(hwnd,text,length+1)
            if title and title in text.value:
                found.append(hwnd)
        return True
    user.EnumWindows(visit,0)
    return user,found


def focus_window(title):
    user,found=_matching_windows(title)
    if not found:return False
    user.IsIconic.argtypes=[W.HWND];user.IsIconic.restype=W.BOOL
    user.ShowWindow.argtypes=[W.HWND,C.c_int]
    user.SetForegroundWindow.argtypes=[W.HWND];user.SetForegroundWindow.restype=W.BOOL
    user.PostMessageW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM];user.PostMessageW.restype=W.BOOL
    hwnd=found[0]
    if user.IsIconic(hwnd):user.ShowWindow(hwnd,9)
    user.SetForegroundWindow(hwnd)
    # Old releases could leave several dashboard windows open.  As soon as the
    # user reopens Raptor Link, keep one and close only the duplicate dashboards.
    for duplicate in found[1:]:
        try:user.PostMessageW(duplicate,WM_CLOSE,0,0)
        except Exception:pass
    return True


def close_windows(title):
    """Close every dashboard window for this instance, not the browser itself."""
    user,found=_matching_windows(title)
    if not found:return False
    user.PostMessageW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM];user.PostMessageW.restype=W.BOOL
    closed=False
    for hwnd in found:
        try:closed=bool(user.PostMessageW(hwnd,WM_CLOSE,0,0)) or closed
        except Exception:pass
    return closed


class WindowController:
    def __init__(self,focus,launch,close=None,clock=time.monotonic):
        self.focus=focus;self.launch=launch;self.close_window=close;self.clock=clock
        self.pending_until=0;self.lock=threading.Lock()
    def ready(self):
        with self.lock:self.pending_until=0
    def open(self):
        with self.lock:
            if self.focus():
                self.pending_until=0;return
            if self.clock()<self.pending_until:return
            self.launch()
            # Suppress repeated launches while Chromium creates the app window.
            self.pending_until=self.clock()+30
    def close(self):
        with self.lock:self.pending_until=0
        if self.close_window is not None:
            try:return bool(self.close_window())
            except Exception:return False
        return False
