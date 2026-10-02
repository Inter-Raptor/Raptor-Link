"""OpenRGB SDK source for Raptor Link.

Implements the documented OpenRGB network protocol directly (protocol 0-5).
No OpenRGB code or Python binding is bundled. Raptor Link only talks to a
running OpenRGB SDK server, normally on 127.0.0.1:6742.
"""
from __future__ import annotations

import copy
import hashlib
import socket
import struct
import threading
import time

MAGIC=b"ORGB"
HEADER=struct.Struct("<4sIII")
CLIENT_PROTOCOL=5
DEFAULT_HOST="127.0.0.1"
DEFAULT_PORT=6742

REQUEST_CONTROLLER_COUNT=0
REQUEST_CONTROLLER_DATA=1
REQUEST_PROTOCOL_VERSION=40
SET_CLIENT_NAME=50
DEVICE_LIST_UPDATED=100

class ProtocolError(RuntimeError):
    pass

class Reader:
    def __init__(self,data):
        self.data=memoryview(data);self.pos=0
    def take(self,n):
        if n<0 or self.pos+n>len(self.data):raise ProtocolError("Paquet OpenRGB incomplet")
        out=self.data[self.pos:self.pos+n];self.pos+=n;return bytes(out)
    def u16(self):return struct.unpack("<H",self.take(2))[0]
    def u32(self):return struct.unpack("<I",self.take(4))[0]
    def i32(self):return struct.unpack("<i",self.take(4))[0]
    def string(self):
        n=self.u16()
        raw=self.take(n)
        if raw.endswith(b"\0"):raw=raw[:-1]
        return raw.decode("utf-8",errors="replace")
    def color(self):
        value=self.u32()
        return (value&255,(value>>8)&255,(value>>16)&255)

def _skip_mode(r,protocol):
    r.string()
    if protocol<6:r.i32()
    r.u32();r.u32();r.u32()
    if protocol>=3:r.u32();r.u32()
    r.u32();r.u32();r.u32()
    if protocol>=3:r.u32()
    r.u32();r.u32()
    for _ in range(r.u16()):r.color()

def _read_matrix(r,length):
    if not length:return None
    raw=Reader(r.take(length))
    height=raw.u32();width=raw.u32()
    if height>2048 or width>2048 or height*width>1_000_000:
        raise ProtocolError("Matrice OpenRGB invalide")
    cells=[raw.u32() for _ in range(min(height*width,(length-8)//4))]
    return {"height":height,"width":width,"cells":cells}

def parse_controller_data(payload,protocol,device_id=0):
    """Parse a REQUEST_CONTROLLER_DATA response into Raptor Link's device form."""
    r=Reader(payload)
    data_size=r.u32()
    if data_size>len(payload)+4:raise ProtocolError("Taille OpenRGB invalide")
    device_type=r.i32()
    name=r.string()
    vendor=r.string() if protocol>=1 else ""
    description=r.string();version=r.string();serial=r.string();location=r.string()
    num_modes=r.u16();active_mode=r.i32()
    if num_modes>1024:raise ProtocolError("Trop de modes OpenRGB")
    for _ in range(num_modes):_skip_mode(r,protocol)
    zones=[]
    num_zones=r.u16()
    if num_zones>4096:raise ProtocolError("Trop de zones OpenRGB")
    for zone_index in range(num_zones):
        zone_name=r.string();zone_type=r.i32();leds_min=r.u32();leds_max=r.u32();leds_count=r.u32()
        matrix_len=r.u16();matrix=_read_matrix(r,matrix_len)
        segments=[]
        if protocol>=4:
            count=r.u16()
            if count>4096:raise ProtocolError("Trop de segments OpenRGB")
            for _ in range(count):
                segment_name=r.string();segment_type=r.i32();start=r.u32();segment_leds=r.u32()
                segments.append({"name":segment_name,"type":segment_type,"start":start,"count":segment_leds})
        flags=r.u32() if protocol>=5 else 0
        zones.append({"name":zone_name,"type":zone_type,"min":leds_min,"max":leds_max,"count":leds_count,
                      "matrix":matrix,"segments":segments,"flags":flags,"index":zone_index})
    num_leds=r.u16()
    if num_leds>65535:raise ProtocolError("Trop de LED OpenRGB")
    leds=[]
    for index in range(num_leds):
        led_name=r.string()
        led_value=r.u32() if protocol<6 else index
        leds.append({"name":led_name,"value":led_value,"index":index})
    num_colors=r.u16()
    if num_colors>65535:raise ProtocolError("Trop de couleurs OpenRGB")
    colors=[r.color() for _ in range(num_colors)]
    display_names=[]
    flags=0
    if protocol>=5:
        display_count=r.u16()
        if display_count>65535:raise ProtocolError("Trop de noms OpenRGB")
        display_names=[r.string() for _ in range(display_count)]
        flags=r.u32()

    identity="|".join([vendor,name,serial,location])
    stable=hashlib.sha1(identity.encode("utf-8",errors="replace")).hexdigest()[:16]
    stable_id="openrgb:"+stable

    # OpenRGB LEDs are globally indexed. Build useful visual positions from zone
    # matrices where possible, otherwise keep a simple left-to-right layout.
    positions=[{"id":i,"x":i*18.0,"y":0.0,"group":0,"name":leds[i]["name"] if i<len(leds) else f"LED {i+1}"} for i in range(num_leds)]
    cursor=0
    for zi,zone in enumerate(zones):
        count=min(zone["count"],max(0,num_leds-cursor))
        for local in range(count):
            idx=cursor+local
            positions[idx]["group"]=zi
            positions[idx]["x"]=local*18.0
            positions[idx]["y"]=zi*32.0
        matrix=zone.get("matrix")
        if matrix:
            width=max(1,matrix["width"]);height=max(1,matrix["height"])
            for cell,value in enumerate(matrix["cells"]):
                if value==0xFFFFFFFF or value>=num_leds:continue
                positions[value]["x"]=(cell%width)*22.0
                positions[value]["y"]=(cell//width)*22.0+zi*8.0
                positions[value]["group"]=zi
        cursor+=count

    color_map={i:(colors[i] if i<len(colors) else (0,0,0)) for i in range(num_leds)}
    model=(vendor+" "+name).strip() or name or "OpenRGB device"
    return {
        "id":stable_id,
        "openrgb_id":device_id,
        "provider":"openrgb",
        "model":model,
        "vendor":vendor,
        "name":name,
        "serial":serial,
        "location":location,
        "description":description,
        "version":version,
        "type":device_type,
        "active_mode":active_mode,
        "positions":positions,
        "zones":[{"index":z["index"],"name":z["name"],"count":z["count"]} for z in zones],
        "colors":color_map,
        "flags":flags,
    }

class OpenRGBConnection:
    def __init__(self,host=DEFAULT_HOST,port=DEFAULT_PORT,timeout=1.0):
        if host not in ("127.0.0.1","localhost","::1"):
            raise ValueError("Pour cette version, le serveur OpenRGB doit être local.")
        self.host=host;self.port=int(port);self.timeout=float(timeout);self.sock=None;self.protocol=0
    def connect(self):
        self.close()
        self.sock=socket.create_connection((self.host,self.port),timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        self._send(REQUEST_PROTOCOL_VERSION,0,struct.pack("<I",CLIENT_PROTOCOL))
        try:
            _,packet,body=self._recv_until(REQUEST_PROTOCOL_VERSION)
            server=struct.unpack("<I",body[:4])[0] if len(body)>=4 else 0
            self.protocol=min(CLIENT_PROTOCOL,server)
        except socket.timeout:
            self.protocol=0
        self._send(SET_CLIENT_NAME,0,b"Raptor Link\0")
        return self.protocol
    def close(self):
        if self.sock:
            try:self.sock.close()
            except OSError:pass
        self.sock=None
    def _send(self,packet_id,device_id=0,data=b""):
        if not self.sock:raise ConnectionError("OpenRGB non connecté")
        self.sock.sendall(HEADER.pack(MAGIC,int(device_id),int(packet_id),len(data))+data)
    def _recvn(self,n):
        data=bytearray()
        while len(data)<n:
            chunk=self.sock.recv(n-len(data))
            if not chunk:raise ConnectionError("Connexion OpenRGB fermée")
            data.extend(chunk)
        return bytes(data)
    def _recv(self):
        magic,device_id,packet_id,size=HEADER.unpack(self._recvn(HEADER.size))
        if magic!=MAGIC:raise ProtocolError("Réponse OpenRGB invalide")
        if size>32*1024*1024:raise ProtocolError("Paquet OpenRGB trop volumineux")
        return device_id,packet_id,self._recvn(size)
    def _recv_until(self,packet_id,device_id=None):
        end=time.monotonic()+self.timeout
        while True:
            remaining=end-time.monotonic()
            if remaining<=0:raise socket.timeout()
            self.sock.settimeout(remaining)
            dev,pid,body=self._recv()
            if pid==packet_id and (device_id is None or dev==device_id):return dev,pid,body
    def controller_ids(self):
        self._send(REQUEST_CONTROLLER_COUNT)
        _,_,body=self._recv_until(REQUEST_CONTROLLER_COUNT)
        if len(body)<4:raise ProtocolError("Compteur OpenRGB invalide")
        count=struct.unpack("<I",body[:4])[0]
        if count>4096:raise ProtocolError("Trop de contrôleurs OpenRGB")
        if self.protocol>=6:
            if len(body)<4+4*count:raise ProtocolError("Liste OpenRGB incomplète")
            return list(struct.unpack("<"+"I"*count,body[4:4+4*count]))
        return list(range(count))
    def controller(self,device_id):
        data=b"" if self.protocol==0 else struct.pack("<I",self.protocol)
        self._send(REQUEST_CONTROLLER_DATA,device_id,data)
        _,_,body=self._recv_until(REQUEST_CONTROLLER_DATA,device_id)
        return parse_controller_data(body,self.protocol,device_id)

class OpenRGBWorker:
    """Background OpenRGB reader with bounded network timeouts and reconnects."""
    def __init__(self,logger,host=DEFAULT_HOST,port=DEFAULT_PORT,connection_factory=OpenRGBConnection):
        self.log=logger;self.host=host;self.port=port;self.connection_factory=connection_factory
        self.lock=threading.RLock();self.done=threading.Event();self.required=set();self.force_scan=True
        self.interval=1/15;self.devices=[];self.colors={};self.error="";self.connected=False;self.protocol=None
        self.last_ok=0.0;self.last_progress=time.monotonic();self.reconnects=0
        self.thread=threading.Thread(target=self.loop,daemon=True,name="RaptorLink-OpenRGB");self.thread.start()
    def configure(self,required,force_scan=False,fps=15):
        with self.lock:
            self.required={str(x) for x in required if str(x).startswith("openrgb:")}
            self.interval=1/max(1,min(25,int(fps)))
            if force_scan:self.force_scan=True
    def snapshot(self):
        with self.lock:
            return {
                "devices":copy.deepcopy(self.devices),"colors":copy.deepcopy(self.colors),"error":self.error,
                "connected":self.connected,"protocol":self.protocol,"last_ok":self.last_ok,
                "age":max(0,time.monotonic()-self.last_progress),"reconnects":self.reconnects,
            }
    def close(self):
        self.done.set();self.thread.join(timeout=2)
    def _publish(self,devices=None,colors=None,error=None,connected=None,protocol=None):
        with self.lock:
            if devices is not None:self.devices=copy.deepcopy(devices)
            if colors is not None:self.colors=copy.deepcopy(colors)
            if error is not None:self.error=str(error)
            if connected is not None:self.connected=bool(connected)
            if protocol is not None:self.protocol=protocol
            if connected:self.last_ok=time.monotonic()
            self.last_progress=time.monotonic()
    def loop(self):
        conn=None;devices=[];by_stable={};last_scan=0.0;retry_at=0.0
        while not self.done.is_set():
            try:
                now=time.monotonic()
                if conn is None:
                    if now<retry_at:
                        self.done.wait(min(.25,retry_at-now));continue
                    conn=self.connection_factory(self.host,self.port,1.0);protocol=conn.connect();self.reconnects+=1
                    self._publish(error="",connected=True,protocol=protocol);last_scan=0
                with self.lock:
                    required=set(self.required);force=self.force_scan;self.force_scan=False;interval=self.interval
                do_scan=force or now-last_scan>=5 or not devices
                if do_scan:
                    ids=conn.controller_ids();fresh=[]
                    for device_id in ids:
                        d=conn.controller(device_id);fresh.append(d)
                    devices=fresh;by_stable={d["id"]:d["openrgb_id"] for d in devices};last_scan=time.monotonic()
                colors={}
                # Refresh only the controllers actually used by Raptor Link. A
                # controller description contains the current per-LED colors.
                for stable in required:
                    device_id=by_stable.get(stable)
                    if device_id is None:continue
                    d=conn.controller(device_id)
                    colors[stable]=d["colors"]
                    for i,old in enumerate(devices):
                        if old["id"]==stable:
                            devices[i]=d;break
                self._publish(devices=devices,colors=colors,error="",connected=True)
                self.done.wait(interval)
            except Exception as exc:
                self._publish(error=exc,connected=False)
                if conn:
                    try:conn.close()
                    except Exception:pass
                conn=None;devices=[];by_stable={};retry_at=time.monotonic()+2
                self.done.wait(.2)
        if conn:
            try:conn.close()
            except Exception:pass
