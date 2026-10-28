"""Standard RFB 3.3/3.7/3.8 adapter over the shared browser desktop.

Uses the official Guacamole renderer in an isolated local headless browser.
Supports VNC Authentication, optional VeNCrypt X509Vnc, Raw/Zlib rectangles,
true-color pixel formats, keyboard, pointer, clipboard, and DesktopSize.
"""

import asyncio
import base64
import hmac
import io
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import struct
import subprocess
import time
import zlib
from pathlib import Path

import aiohttp
import numpy as np
from PIL import Image
from cryptography.hazmat.primitives.ciphers import Cipher, modes
from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES

from .compatibility import native_certificate

PIXEL_FORMAT = struct.pack(">BBBBHHHBBB3x",32,24,0,1,255,255,255,16,8,0)


def challenge_response(password, challenge):
    key=bytes(int(f"{byte:08b}"[::-1],2) for byte in password.encode("ascii"))
    encryptor=Cipher(TripleDES(key),modes.ECB()).encryptor()
    return encryptor.update(challenge)+encryptor.finalize()


def encode_pixels(image, pixel_format):
    bits,depth,big,true_color,rmax,gmax,bmax,rshift,gshift,bshift=struct.unpack(">BBBBHHHBBB3x",pixel_format)
    if bits not in (8,16,32) or not true_color or depth>bits or big not in (0,1):
        raise ValueError("Only 8-, 16-, and 32-bit true-color VNC formats are supported.")
    masks=[]
    for maximum,shift in ((rmax,rshift),(gmax,gshift),(bmax,bshift)):
        if maximum<=0 or maximum&(maximum+1) or shift>=bits or (maximum<<shift)>=(1<<bits):
            raise ValueError("Invalid VNC color mask.")
        masks.append(maximum<<shift)
    if any(masks[i]&masks[j] for i in range(3) for j in range(i)):
        raise ValueError("Overlapping VNC color masks.")
    values=np.asarray(image.convert("RGB"),dtype=np.uint32)
    packed=((values[:,:,0]*rmax//255)<<rshift)|((values[:,:,1]*gmax//255)<<gshift)|((values[:,:,2]*bmax//255)<<bshift)
    dtype=(">" if big else "<")+{8:"u1",16:"u2",32:"u4"}[bits]
    return packed.astype(dtype).tobytes()


class BrowserCanvas:
    def __init__(self,directory,mobile):
        self.directory,self.mobile=Path(directory),mobile
        self.process=None;self.session=None;self.ws=None;self.sequence=0
        self.frame=None;self.geometry=None;self.task=None;self.profile=None;self.lock=asyncio.Lock()

    async def call(self,method,params=None):
        async with self.lock:
            self.sequence+=1;identity=self.sequence
            await self.ws.send_json({"id":identity,"method":method,"params":params or {}})
            while True:
                response=await asyncio.wait_for(self.ws.receive_json(),10)
                if response.get("id")==identity:
                    if "error" in response:
                        raise ValueError("The local desktop renderer rejected a command.")
                    return response.get("result",{})

    async def evaluate(self,expression):
        result=await self.call("Runtime.evaluate",{"expression":expression,"returnByValue":True,"awaitPromise":True})
        if result.get("exceptionDetails"):
            raise ValueError("The local desktop renderer could not complete an action.")
        return result.get("result",{}).get("value")

    async def start(self):
        browser=next((shutil.which(name) for name in ("google-chrome","chromium","chromium-browser","msedge") if shutil.which(name)),None)
        if not browser:
            raise ValueError("A Chrome/Chromium browser is required for this VNC adapter.")
        self.profile=self.directory/("vnc-browser-"+secrets.token_hex(5));self.profile.mkdir(mode=0o700)
        with (self.directory/"vnc-browser.log").open("ab") as logfile:
            self.process=subprocess.Popen([browser,"--headless=new","--disable-gpu","--disable-dev-shm-usage",
                "--remote-debugging-address=127.0.0.1","--remote-debugging-port=0",f"--user-data-dir={self.profile}",
                "--window-size=1600,1000","--no-first-run","--disable-default-apps","about:blank"],
                stdin=subprocess.DEVNULL,stdout=logfile,stderr=logfile,start_new_session=os.name!="nt")
        active=self.profile/"DevToolsActivePort"
        deadline=time.monotonic()+15
        while not active.exists():
            if self.process.poll() is not None or time.monotonic()>deadline:
                raise ValueError("The VNC renderer did not start. Check .state/vnc-browser.log.")
            await asyncio.sleep(.1)
        port=int(active.read_text().splitlines()[0])
        self.session=aiohttp.ClientSession()
        async with self.session.get(f"http://127.0.0.1:{port}/json/list") as response:
            tabs=await response.json()
        page=next(tab for tab in tabs if tab["type"]=="page")
        self.ws=await self.session.ws_connect(page["webSocketDebuggerUrl"],max_msg_size=16*1024*1024)
        url=f"http://127.0.0.1:{self.mobile.local_port}"
        await self.call("Page.navigate",{"url":url})
        for _ in range(100):
            if await self.evaluate("document.readyState==='complete' && Boolean(document.querySelector('#access-key'))"):
                break
            await asyncio.sleep(.1)
        # Access key travels only through private loopback IPC, never argv or URL.
        self.grant = self.mobile.new_grant(0, internal=True)
        key = self.mobile.grants[self.grant]["key"]
        expression="fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:"+json.dumps(key)+"})}).then(response=>response.ok)"
        if not await self.evaluate(expression):
            raise ValueError("The private VNC renderer could not authenticate to its desktop gateway.")
        await self.call("Page.navigate",{"url":url})
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            if await self.evaluate("Boolean(window.yesmachineRemote && window.yesmachineRemote.ready())"):
                break
            await asyncio.sleep(.2)
        else:
            raise ValueError("The VNC renderer could not connect to RDP. Check the host's permissions and keyring.")
        await self.capture()
        self.task=asyncio.create_task(self.frames())

    async def capture(self):
        result=await self.evaluate("window.yesmachineRemote.capture()")
        if not result:
            return
        frame=Image.open(io.BytesIO(base64.b64decode(result["data"]))).convert("RGB")
        if frame.width*frame.height>4194304:
            raise ValueError("VNC frame exceeded its size limit.")
        self.geometry,self.frame=result,frame

    async def frames(self):
        while True:
            try:
                await self.capture()
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(.5)
            await asyncio.sleep(.2)

    async def pointer(self,mask,x,y):
        state={"x":round(x*self.geometry['width']/self.frame.width),"y":round(y*self.geometry['height']/self.frame.height),"left":bool(mask&1),"middle":bool(mask&2),"right":bool(mask&4),"up":bool(mask&8),"down":bool(mask&16)}
        await self.evaluate("window.yesmachineRemote.pointer("+json.dumps(state)+")")

    async def key(self,down,key):
        await self.evaluate(f"window.yesmachineRemote.key({int(bool(down))},{int(key)})")

    async def clipboard(self,text):
        await self.evaluate("window.yesmachineRemote.clipboard("+json.dumps(text)+")")

    async def close(self):
        if self.task:
            self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
        if self.ws:await self.ws.close()
        if self.session:await self.session.close()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:await asyncio.to_thread(self.process.wait,5)
            except subprocess.TimeoutExpired:self.process.kill()
        if self.profile:shutil.rmtree(self.profile,ignore_errors=True)
        if getattr(self,"grant",None):
            # The browser is closed; remove only its private, internal grant.
            self.mobile.grants.pop(self.grant,None)
            self.mobile.sessions={key:value for key,value in self.mobile.sessions.items() if value['grant']!=self.grant}
            self.mobile.update_expiry()


class VncServer:
    def __init__(self,directory,mobile,event=lambda *_:None):
        self.directory,self.mobile,self.event=Path(directory),mobile,event
        self.server=None;self.canvas=None;self.password="";self.port=5900;self.clients=set();self.failures={}
        self.context=None

    async def start(self,password,port=5900,bind="0.0.0.0",canvas=None):
        if self.server:raise ValueError("VNC is already running. Stop it before changing its password.")
        if len(password)!=8 or any(ord(char)<32 or ord(char)>126 for char in password):
            raise ValueError("Classic VNC authentication needs exactly 8 ASCII characters. Use a separate VNC password.")
        if not 1024<=int(port)<=65535:raise ValueError("Choose an unused VNC port between 1024 and 65535.")
        self.password=password;self.port=int(port)
        cert,key=native_certificate(self.directory/"vnc-tls")
        self.context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.minimum_version=ssl.TLSVersion.TLSv1_2;self.context.load_cert_chain(cert,key)
        self.canvas=canvas or BrowserCanvas(self.directory,self.mobile)
        try:
            if canvas is None:await self.canvas.start()
            if self.canvas.frame is None:raise ValueError("The VNC desktop has not produced a frame.")
            self.server=await asyncio.start_server(self.client,bind,self.port)
            self.port=self.server.sockets[0].getsockname()[1]
        except BaseException:
            await self.canvas.close();self.canvas=None;self.password="";raise

    async def client(self,reader,writer):
        task=asyncio.current_task();peer=writer.get_extra_info("peername")[0]
        if len(self.clients)>=4:
            writer.close();return
        self.clients.add(task)
        try:
            writer.write(b"RFB 003.008\n");await writer.drain()
            version=await asyncio.wait_for(reader.readexactly(12),5)
            if version not in (b"RFB 003.003\n",b"RFB 003.007\n",b"RFB 003.008\n"):return
            modern=version!=b"RFB 003.003\n"
            if modern:
                writer.write(b"\x02\x13\x02");await writer.drain()
                chosen=(await asyncio.wait_for(reader.readexactly(1),5))[0]
                if chosen==19:
                    writer.write(b"\x00\x02");await writer.drain()
                    if await reader.readexactly(2)!=b"\x00\x02":return
                    writer.write(b"\x00\x01"+struct.pack(">I",261));await writer.drain()
                    if struct.unpack(">I",await reader.readexactly(4))[0]!=261:return
                    writer.write(b"\x01");await writer.drain()
                    await writer.start_tls(self.context,ssl_handshake_timeout=5)
                elif chosen!=2:return
            else:
                writer.write(struct.pack(">I",2));await writer.drain()
            now=time.monotonic();count,stamp=self.failures.get(peer,(0,now))
            if now-stamp>60:count=0;stamp=now
            if count>=5:return
            challenge=secrets.token_bytes(16);writer.write(challenge);await writer.drain()
            response=await asyncio.wait_for(reader.readexactly(16),5)
            if not hmac.compare_digest(response,challenge_response(self.password,challenge)):
                self.failures[peer]=(count+1,stamp)
                writer.write(struct.pack(">I",1))
                if version==b"RFB 003.008\n":
                    reason=b"Incorrect VNC key."
                    writer.write(struct.pack(">I",len(reason))+reason)
                await writer.drain();return
            writer.write(struct.pack(">I",0));await writer.drain()
            await asyncio.wait_for(reader.readexactly(1),5)
            width,height=self.canvas.frame.size;name=b"YesMachine VNC desktop"
            writer.write(struct.pack(">HH",width,height)+PIXEL_FORMAT+struct.pack(">I",len(name))+name);await writer.drain()
            pixel_format=PIXEL_FORMAT;encodings={0};compressor=zlib.compressobj(3);last_frame=0
            while True:
                kind=(await reader.readexactly(1))[0]
                if kind==0:
                    await reader.readexactly(3);pixel_format=await reader.readexactly(16)
                    encode_pixels(Image.new("RGB",(1,1)),pixel_format)
                elif kind==2:
                    await reader.readexactly(1);amount=struct.unpack(">H",await reader.readexactly(2))[0]
                    if amount>256:return
                    encodings=set(struct.unpack(">"+"i"*amount,await reader.readexactly(amount*4)))
                elif kind==3:
                    _incremental,x,y,w,h=struct.unpack(">BHHHH",await reader.readexactly(9))
                    await asyncio.sleep(max(0,.2-(time.monotonic()-last_frame)))
                    frame=self.canvas.frame
                    if frame.size!=(width,height):
                        if -223 not in encodings:return
                        width,height=frame.size
                        writer.write(b"\x00\x00\x00\x01"+struct.pack(">HHHHi",0,0,width,height,-223));await writer.drain();continue
                    w,h=min(w,max(0,width-x)),min(h,max(0,height-y))
                    if not w or not h:
                        writer.write(b"\0\0\0\0");await writer.drain();continue
                    pixels=await asyncio.to_thread(encode_pixels,frame.crop((x,y,x+w,y+h)),pixel_format)
                    encoding=6 if 6 in encodings else 0
                    if encoding==6:
                        packed=compressor.compress(pixels)+compressor.flush(zlib.Z_SYNC_FLUSH);pixels=struct.pack(">I",len(packed))+packed
                    writer.write(b"\x00\x00\x00\x01"+struct.pack(">HHHHi",x,y,w,h,encoding)+pixels)
                    await writer.drain();last_frame=time.monotonic()
                elif kind==4:
                    down,_pad,key=struct.unpack(">BHI",await reader.readexactly(7));await self.canvas.key(down,key)
                elif kind==5:
                    mask,x,y=struct.unpack(">BHH",await reader.readexactly(5));await self.canvas.pointer(mask,x,y)
                elif kind==6:
                    await reader.readexactly(3);length=struct.unpack(">I",await reader.readexactly(4))[0]
                    if length>1048576:return
                    await self.canvas.clipboard((await reader.readexactly(length)).decode("latin-1"))
                else:return
        except (OSError,ValueError,asyncio.IncompleteReadError,asyncio.TimeoutError):
            pass
        finally:
            writer.close()
            try:await asyncio.wait_for(writer.wait_closed(),1)
            except BaseException:writer.transport.abort()
            self.clients.discard(task)

    async def stop(self):
        server,self.server=self.server,None
        if server:server.close()
        tasks=list(self.clients)
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        if server:await server.wait_closed()
        if self.canvas:await self.canvas.close()
        self.canvas=None;self.password=""
