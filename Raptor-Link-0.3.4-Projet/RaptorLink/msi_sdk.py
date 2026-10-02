"""Minimal ctypes wrapper around MSI's official Mystic Light SDK.

The proprietary MSI DLL is NOT redistributed by Raptor Link. It is loaded from
an SDK copy installed/downloaded by the user. Calls run inside a disposable
worker process so a stuck vendor SDK cannot freeze the main Raptor Link engine.
"""
from __future__ import annotations
import ctypes as C
import hashlib
import os
from pathlib import Path

STATUS={
    0:"OK",-1:"Erreur Mystic Light",-2:"Délai Mystic Light dépassé",
    -3:"Fonction Mystic Light non implémentée",-4:"SDK Mystic Light non initialisé",
    -101:"Argument Mystic Light invalide",-102:"Appareil Mystic Light introuvable",
    -103:"Fonction Mystic Light non prise en charge",
}

class MysticLightError(RuntimeError):
    def __init__(self,code,where="Mystic Light"):
        self.code=int(code)
        super().__init__(f"{where} : {STATUS.get(self.code,'erreur '+str(self.code))}")

class _Ole:
    def __init__(self):
        if os.name!="nt":raise OSError("MSI Mystic Light nécessite Windows")
        self.dll=C.OleDLL("oleaut32")
        self.dll.SysAllocString.argtypes=[C.c_wchar_p];self.dll.SysAllocString.restype=C.c_void_p
        self.dll.SysFreeString.argtypes=[C.c_void_p];self.dll.SysFreeString.restype=None
        self.dll.SysStringLen.argtypes=[C.c_void_p];self.dll.SysStringLen.restype=C.c_uint
        self.dll.SafeArrayGetDim.argtypes=[C.c_void_p];self.dll.SafeArrayGetDim.restype=C.c_uint
        self.dll.SafeArrayGetLBound.argtypes=[C.c_void_p,C.c_uint,C.POINTER(C.c_long)];self.dll.SafeArrayGetLBound.restype=C.c_long
        self.dll.SafeArrayGetUBound.argtypes=[C.c_void_p,C.c_uint,C.POINTER(C.c_long)];self.dll.SafeArrayGetUBound.restype=C.c_long
        self.dll.SafeArrayGetElement.argtypes=[C.c_void_p,C.POINTER(C.c_long),C.c_void_p];self.dll.SafeArrayGetElement.restype=C.c_long
        self.dll.SafeArrayDestroy.argtypes=[C.c_void_p];self.dll.SafeArrayDestroy.restype=C.c_long
    def alloc(self,text):
        ptr=self.dll.SysAllocString(str(text))
        if not ptr:raise MemoryError("Impossible d'allouer une chaîne BSTR")
        return ptr
    def text(self,ptr):
        if not ptr:return ""
        return C.wstring_at(ptr,self.dll.SysStringLen(ptr))
    def free(self,ptr):
        if ptr:self.dll.SysFreeString(ptr)
    def strings(self,array,destroy=True):
        if not array:return []
        try:
            if self.dll.SafeArrayGetDim(array)!=1:raise MysticLightError(-101,"Tableau SDK MSI")
            lo=C.c_long();hi=C.c_long()
            if self.dll.SafeArrayGetLBound(array,1,C.byref(lo))<0 or self.dll.SafeArrayGetUBound(array,1,C.byref(hi))<0:
                raise MysticLightError(-1,"Lecture tableau SDK MSI")
            result=[]
            for idx in range(lo.value,hi.value+1):
                i=C.c_long(idx);value=C.c_void_p()
                hr=self.dll.SafeArrayGetElement(array,C.byref(i),C.byref(value))
                if hr<0:raise MysticLightError(hr,"Lecture tableau SDK MSI")
                try:result.append(self.text(value.value))
                finally:self.free(value.value)
            return result
        finally:
            if destroy:self.dll.SafeArrayDestroy(array)

class MysticLightSDK:
    def __init__(self,dll_path):
        self.path=Path(dll_path)
        if not self.path.exists():raise FileNotFoundError(str(self.path))
        self.ole=_Ole()
        # MSI's published header uses ordinary C function pointers (cdecl).
        self.dll=C.CDLL(str(self.path))
        self.last_diag={"dll_path":str(self.path),"dll_loaded":True,"initialize_code":None,"device_info_code":None,"raw_device_types":[],"raw_led_counts":[],"devices":[]}
        self._bind()
        init_code=int(self.dll.MLAPI_Initialize())
        self.last_diag["initialize_code"]=init_code
        self._check(init_code,"Initialisation Mystic Light")
        self.devices=self.scan()

    def _bind(self):
        cv=C.c_void_p;u=C.c_uint;p_u=C.POINTER(u);p_v=C.POINTER(cv)
        f=self.dll
        f.MLAPI_Initialize.argtypes=[];f.MLAPI_Initialize.restype=C.c_int
        f.MLAPI_GetDeviceInfo.argtypes=[p_v,p_v];f.MLAPI_GetDeviceInfo.restype=C.c_int
        f.MLAPI_GetLedInfo.argtypes=[cv,u,p_v,p_v];f.MLAPI_GetLedInfo.restype=C.c_int
        f.MLAPI_GetLedColor.argtypes=[cv,u,p_u,p_u,p_u];f.MLAPI_GetLedColor.restype=C.c_int

    def _check(self,code,where):
        if int(code)!=0:raise MysticLightError(code,where)

    def _device_bstr(self,name):
        return self.ole.alloc(name)

    def scan(self):
        types=C.c_void_p();counts=C.c_void_p()
        code=int(self.dll.MLAPI_GetDeviceInfo(C.byref(types),C.byref(counts)))
        self.last_diag["device_info_code"]=code
        self._check(code,"Détection des appareils MSI")
        names=self.ole.strings(types.value)
        led_counts=self.ole.strings(counts.value)
        self.last_diag["raw_device_types"]=list(names)
        self.last_diag["raw_led_counts"]=list(led_counts)
        out=[]
        for device_index,(name,count_text) in enumerate(zip(names,led_counts)):
            try:count=int(str(count_text).strip())
            except Exception:continue
            if count<0 or count>4096:continue
            bstr=self._device_bstr(name)
            try:
                positions=[]
                for led_index in range(count):
                    led_name=C.c_void_p();styles=C.c_void_p()
                    label=f"LED {led_index+1}"
                    try:
                        code=self.dll.MLAPI_GetLedInfo(bstr,led_index,C.byref(led_name),C.byref(styles))
                        if code==0:
                            label=self.ole.text(led_name.value) or label
                            if styles.value:self.ole.strings(styles.value)
                    finally:
                        self.ole.free(led_name.value)
                    positions.append({"id":led_index,"x":led_index*24.0,"y":device_index*34.0,"group":device_index,"name":label})
            finally:self.ole.free(bstr)
            stable=hashlib.sha1(name.encode("utf-8",errors="replace")).hexdigest()[:16]
            out.append({"id":"msi:"+stable,"provider":"msi","model":name,"name":name,"serial":"","type":0,
                        "positions":positions,"native_name":name})
        self.devices=out
        self.last_diag["devices"]=[{"model":d["model"],"leds":len(d["positions"])} for d in out]
        self.last_diag["device_count"]=len(out)
        return out

    def read_device(self,device):
        name=device.get("native_name") or device.get("model") or ""
        bstr=self._device_bstr(name)
        try:
            colors={}
            for p in device.get("positions",[]):
                r=C.c_uint();g=C.c_uint();b=C.c_uint()
                code=self.dll.MLAPI_GetLedColor(bstr,int(p["id"]),C.byref(r),C.byref(g),C.byref(b))
                if code==0:
                    colors[int(p["id"])]=(max(0,min(255,int(r.value))),max(0,min(255,int(g.value))),max(0,min(255,int(b.value))))
                elif code not in (-103,):
                    raise MysticLightError(code,f"Lecture MSI {name} LED {p['id']}")
            return colors
        finally:self.ole.free(bstr)


    def diagnostic(self):
        return dict(self.last_diag)
