"""Isolated browser desktop gateway and revocable Cloudflare quick shares."""

import asyncio
import codecs
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path

from aiohttp import web, WSMsgType

from .storage import private_write
from .theme import load_theme

GUACD_IMAGE = "guacamole/guacd@sha256:8974eaa9ba32f713daf311e7cc8cd7e4cdfba1edea39eed75524e78ef4b08f4f"
ASSETS = Path(__file__).resolve().parent / "mobile"


def unexpired(expires):
    return not expires or time.time()<expires


def instruction(*elements):
    return ",".join(f"{len(str(value))}.{value}" for value in elements) + ";"


class InstructionBuffer:
    """Keep protocol instructions intact when TCP splits a graphics stream.

    Tunnel keepalives share the WebSocket with desktop traffic. Inserting one
    inside a partial image instruction corrupts the display stream.
    """
    def __init__(self):
        self.buffer = ""
        self.position = 0
        self.element_end = None

    def feed(self, data):
        self.buffer += data
        complete = []
        while True:
            if self.element_end is None:
                dot = self.buffer.find(".", self.position)
                if dot < 0:
                    if len(self.buffer) - self.position > 9:
                        raise ValueError("Invalid desktop instruction length.")
                    break
                prefix = self.buffer[self.position:dot]
                if not prefix.isascii() or not prefix.isdigit() or len(prefix) > 9:
                    raise ValueError("Invalid desktop instruction length.")
                length = int(prefix)
                if length > 2097152:
                    raise ValueError("Desktop instruction exceeded its limit.")
                self.element_end = dot + 1 + length
            if len(self.buffer) <= self.element_end:
                break
            delimiter = self.buffer[self.element_end]
            if delimiter not in ",;":
                raise ValueError("Invalid desktop instruction delimiter.")
            self.position = self.element_end + 1
            self.element_end = None
            if delimiter == ";":
                complete.append(self.buffer[:self.position])
                self.buffer = self.buffer[self.position:]
                self.position = 0
            if self.position > 4194304:
                raise ValueError("Desktop instruction exceeded its limit.")
        return complete


async def read_instruction(reader):
    values = []
    for _ in range(256):
        prefix = await reader.readuntil(b".")
        if len(prefix) > 9 or not prefix[:-1].isdigit():
            raise ValueError("Invalid desktop gateway instruction.")
        length = int(prefix[:-1])
        if length > 1048576:
            raise ValueError("Desktop gateway handshake exceeded its limit.")
        values.append((await reader.readexactly(length)).decode())
        delimiter = await reader.readexactly(1)
        if delimiter == b";":
            return values
        if delimiter != b",":
            break
    raise ValueError("Invalid desktop gateway handshake.")


def free_port():
    with socket.socket() as stream:
        stream.bind(("127.0.0.1", 0))
        return stream.getsockname()[1]


class MobileShare:
    def __init__(self, directory, compatibility, event=lambda *_: None):
        self.directory, self.compatibility, self.event = Path(directory), compatibility, event
        self.lock = threading.RLock()
        self.loop = None
        self.thread = None
        self.runner = None
        self.tunnel = None
        self.guacd = None
        self.container = None
        self.public_url = ""
        self.local_port = 0
        self.guacd_port = 0
        self.key = ""
        self.expires = 0
        self.sessions = {}
        self.grants = {}
        self.latest_grant = None
        self.ws_sessions = {}
        self.connection_id = None
        self.joined_count = 0
        self.handshake_lock = None
        self.websockets = set()
        self.failures = defaultdict(deque)
        self.error = ""
        self.expected_origin = ""

    def status(self):
        grants=list(self.grants.items())
        viewers=list(self.sessions.items())
        connected_tokens=set(self.ws_sessions.values())
        links=[{"id":identity,"created":grant["created"],"expires":grant["expires"],
                "sessions":sum(value["grant"]==identity for _,value in viewers)}
               for identity,grant in grants if not grant.get("internal") and unexpired(grant["expires"])]
        sessions=[{"id":value["id"],"share_id":value["grant"],"created":value["created"],
                   "last_seen":value["last_seen"],"expires":value["expires"],"device":value["device"],
                   "connected":secret in connected_tokens}
                  for secret,value in viewers if unexpired(value["expires"]) and not value.get("internal")]
        return {"available": bool(shutil.which("cloudflared")) and bool(shutil.which("guacd") or shutil.which("docker")),
                "active": bool(self.tunnel and self.tunnel.poll() is None and links),
                "url": self.public_url, "expires": self.expires, "clients": len(self.websockets),
                "error": self.error, "local_port": self.local_port,"links":links,"sessions":sessions}

    def _run(self, coroutine, timeout=15):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout)

    def start_gateway(self):
        self.guacd_port = free_port()
        native = shutil.which("guacd")
        if native:
            command = [native, "-b", "127.0.0.1", "-l", str(self.guacd_port), "-p", str(self.directory / "guacd.pid"), "-f"]
        else:
            if not shutil.which("docker") or os.name == "nt" or not sys_linux():
                raise ValueError("Install a local guacd browser-desktop backend; the bundled Docker path currently supports Linux.")
            if subprocess.run(["docker", "image", "inspect", GUACD_IMAGE], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=10).returncode:
                raise ValueError("The browser backend is missing. Run scripts/setup-mobile.py to install its pinned image.")
            self.container = "yesmachine-guacd-" + secrets.token_hex(5)
            command = ["docker", "run", "--rm", "--network", "host", "--name", self.container,
                       "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=256",
                       "--entrypoint", "/opt/guacamole/sbin/guacd", GUACD_IMAGE,
                       "-b", "127.0.0.1", "-l", str(self.guacd_port), "-p", "/tmp/guacd.pid", "-f"]
        with (self.directory / "browser-gateway.log").open("ab") as output:
            self.guacd = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                          start_new_session=os.name != "nt")
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if self.guacd.poll() is not None:
                raise ValueError("The browser desktop backend exited. Check .state/browser-gateway.log.")
            try:
                with socket.create_connection(("127.0.0.1", self.guacd_port), timeout=.3):
                    return
            except OSError:
                time.sleep(.2)
        raise ValueError("The browser desktop backend did not become ready.")

    def new_grant(self,duration,internal=False):
        identity=secrets.token_hex(8)
        self.grants[identity]={"key":secrets.token_urlsafe(32),"created":time.time(),
                               "expires":time.time()+duration if duration else 0,"internal":internal}
        if not internal or self.latest_grant is None:
            self.latest_grant=identity
            self.key=self.grants[identity]["key"]
        self.update_expiry()
        return identity

    def update_expiry(self):
        values=[grant["expires"] for grant in self.grants.values()]
        self.expires=0 if not values or 0 in values else max(values)

    def start(self, duration=3600, local_only=False):
        with self.lock:
            if duration not in (0,900,3600,14400):
                raise ValueError("Choose no expiry, 15 minutes, 1 hour or 4 hours.")
            if len(self.grants)>=32:
                raise ValueError("Revoke an existing access link before creating more shares.")
            if not local_only and not shutil.which("cloudflared"):
                raise ValueError("cloudflared is not installed. Install it separately, then generate the share again.")
            started_rdp = self.compatibility.record is None
            already_running=bool(self.runner)
            try:
                if not already_running:
                    if not self.compatibility.record:
                        self.compatibility.start_rdp("yesmachine-phone", secrets.token_urlsafe(24))
                    self.compatibility.rdp_credentials()
                    self.start_gateway()
                    self.loop = asyncio.new_event_loop()
                    self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
                    self.thread.start()
                    self._run(self._start_server())
                if local_only:
                    if not self.tunnel:
                        self.public_url=f"http://127.0.0.1:{self.local_port}"
                        self.expected_origin=self.public_url
                    return self.new_grant(duration,True)
                if self.tunnel and self.tunnel.poll() is None:
                    return self.new_grant(duration)
                config = self.directory / "quick-tunnel.yml"
                private_write(config, "# YesMachine isolated quick-tunnel configuration\n")
                command = [shutil.which("cloudflared"), "tunnel", "--config", str(config), "--no-autoupdate",
                           "--url", f"http://127.0.0.1:{self.local_port}", "--protocol", "http2"]
                self.tunnel = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                               text=True, bufsize=1, stdin=subprocess.DEVNULL,
                                               start_new_session=os.name != "nt")
                ready = threading.Event()
                process = self.tunnel
                def read_output():
                    with (self.directory / "cloudflared.log").open("a") as output:
                        for line in process.stdout:
                            output.write(line)
                            output.flush()
                            match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                            if match and self.tunnel is process:
                                self.public_url = match.group()
                                self.expected_origin = self.public_url
                            if "Registered tunnel connection" in line and self.tunnel is process:
                                ready.set()
                threading.Thread(target=read_output, daemon=True).start()
                deadline = time.monotonic() + 50
                while not ready.wait(.2) or not self.public_url.startswith("https://"):
                    if self.tunnel.poll() is not None or time.monotonic() > deadline:
                        raise ValueError("Cloudflare did not create a public URL. Check Internet access and .state/cloudflared.log.")
                self.error = ""
                self.event("Created a revocable Cloudflare phone share; the access key is not logged")
                return self.new_grant(duration)
            except BaseException:
                if not already_running:
                    self.stop()
                    if started_rdp and self.compatibility.record:
                        self.compatibility.stop_rdp()
                raise

    def authorized(self, request):
        identity = request.cookies.get("yesmachine_phone", "")
        value=self.sessions.get(identity)
        if not value or value["grant"] not in self.grants or not unexpired(value["expires"]):
            return False
        value["last_seen"]=time.time()
        return True

    def check_origin(self, request, require=False):
        local_origin = f"http://127.0.0.1:{self.local_port}"
        allowed = {self.expected_origin, local_origin}
        actual = request.headers.get("Origin")
        if request.host not in {value.split("://", 1)[-1] for value in allowed if value}:
            raise web.HTTPForbidden(text="Invalid viewer host.")
        if (require and not actual) or (actual and actual not in allowed):
            raise web.HTTPForbidden(text="Invalid viewer origin.")

    async def _start_server(self):
        @web.middleware
        async def security(request, handler):
            self.check_origin(request)
            headers = {"Cache-Control":"no-store", "Referrer-Policy":"no-referrer",
                "X-Content-Type-Options":"nosniff", "X-Frame-Options":"DENY",
                "Content-Security-Policy":"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data: blob:; media-src 'self' blob: data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"}
            try:
                response = await handler(request)
            except web.HTTPException as error:
                error.headers.update(headers)
                raise
            response.headers.update(headers)
            return response
        app = web.Application(middlewares=[security], client_max_size=8192)
        self.handshake_lock=asyncio.Lock()
        app.router.add_get("/", self.page)
        app.router.add_get("/{asset:phone.js|phone.css|guacamole.js}", self.asset)
        app.router.add_post("/api/login", self.login)
        app.router.add_get("/api/session", self.session)
        app.router.add_get("/tunnel", self.websocket)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.local_port = site._server.sockets[0].getsockname()[1]

    async def page(self, request):
        return web.Response(text=(ASSETS / "index.html").read_text(), content_type="text/html")

    async def asset(self, request):
        name = request.match_info["asset"]
        body = (ASSETS / name).read_bytes()
        if name == "phone.css":
            colors = load_theme(self.directory)
            body += ("\n:root{" + ";".join(f"--{key}:{value}" for key, value in colors.items()) + "}").encode()
        return web.Response(body=body,
                            content_type="text/css" if name.endswith(".css") else "application/javascript")

    async def login(self, request):
        self.check_origin(request, True)
        if not self.grants:
            return web.json_response({"error":"This share has expired or was revoked. Generate a new QR code on the host."}, status=403)
        peer = request.headers.get("CF-Connecting-IP", request.remote or "unknown")
        if len(self.failures) > 2048:
            self.failures.clear()
        failures = self.failures[peer]
        while failures and failures[0] < time.monotonic()-60:
            failures.popleft()
        if len(failures) >= 5:
            return web.json_response({"error":"Too many incorrect keys. Wait a minute before retrying."}, status=429)
        try:
            data = await request.json()
            key = data.get("key", "")
        except Exception:
            key = ""
        grant_id=next((identity for identity,value in self.grants.items()
                       if isinstance(key,str) and secrets.compare_digest(key,value["key"]) and unexpired(value["expires"])),None)
        if grant_id is None:
            failures.append(time.monotonic())
            return web.json_response({"error":"The access key is invalid. Scan the current QR code on the host."}, status=403)
        identity = secrets.token_urlsafe(32)
        self.sessions = {key:value for key,value in self.sessions.items() if unexpired(value["expires"])}
        if len(self.sessions) >= 64:
            return web.json_response({"error":"The share has too many signed-in viewers. Regenerate it to revoke previous sessions."}, status=429)
        grant=self.grants[grant_id]
        agent=request.headers.get("User-Agent","")
        device="iPhone / iPad" if any(name in agent for name in ("iPhone","iPad")) else "Android" if "Android" in agent else "Browser"
        self.sessions[identity]={"id":secrets.token_hex(8),"grant":grant_id,"created":time.time(),
                                 "last_seen":time.time(),"expires":grant["expires"],"device":device,
                                 "internal":grant.get("internal",False)}
        response = web.json_response({"expires":grant["expires"]})
        response.set_cookie("yesmachine_phone", identity, httponly=True, samesite="Strict",
                            secure=request.host==self.expected_origin.removeprefix("https://"),
                            max_age=max(1,int(grant["expires"]-time.time())) if grant["expires"] else 31536000)
        return response

    async def session(self, request):
        if not self.authorized(request):
            raise web.HTTPUnauthorized(text="Scan the current QR code to sign in.")
        return web.json_response({"expires":self.sessions[request.cookies["yesmachine_phone"]]["expires"], "hostname":socket.gethostname()})

    async def websocket(self, request):
        self.check_origin(request, True)
        if not self.authorized(request):
            raise web.HTTPUnauthorized(text="Scan the current QR code to sign in.")
        if len(self.websockets) >= 4:
            raise web.HTTPTooManyRequests(text="This share already has four viewers.")
        ws = web.WebSocketResponse(protocols=["guacamole"], heartbeat=15, max_msg_size=2097152)
        await ws.prepare(request)
        self.websockets.add(ws)
        self.ws_sessions[ws]=request.cookies["yesmachine_phone"]
        writer = None
        joined = False
        try:
            async with self.handshake_lock:
                reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", self.guacd_port), 5)
                writer.write(instruction("select", self.connection_id or "rdp").encode())
                await writer.drain()
                arguments = await asyncio.wait_for(read_instruction(reader), 10)
                if arguments[0] != "args":
                    raise ValueError("The browser backend did not offer a desktop.")
                width = max(320,min(1920,int(float(request.query.get("width",1280)))))
                height = max(320,min(1920,int(float(request.query.get("height",800)))))
                values = self.compatibility.rdp_credentials()
                settings = {"hostname":"127.0.0.1", "port":str(self.compatibility.record["port"]),
                        "username":values["username"], "password":values["password"],
                        "security":"nla", "ignore-cert":"true", "disable-audio":"false",
                        "resize-method":"display-update", "enable-drive":"false",
                        "client-name":"YesMachine phone", "server-layout":"en-us-qwerty"}
            # The only upstream is the locally managed RDP host whose certificate
            # was verified at startup. No arbitrary network host is accepted here.
                for message in [instruction("size",width,height,96),
                            instruction("audio","audio/L16;rate=44100,channels=2"),
                            instruction("video"), instruction("image","image/png","image/jpeg"),
                            instruction("timezone",request.query.get("timezone","UTC")[:80]),
                            instruction("connect",*[name if name.startswith("VERSION_") else settings.get(name,"") for name in arguments[1:]])]:
                    writer.write(message.encode())
                await writer.drain()
                response = await asyncio.wait_for(read_instruction(reader), 15)
                if response[0] != "ready":
                    raise ValueError("The RDP desktop connection was rejected. Check GNOME desktop-sharing permissions.")
                self.connection_id=response[1]
                self.joined_count+=1
                joined = True
            await ws.send_str(instruction("",str(uuid.uuid4())))
            async def to_browser():
                decoder = codecs.getincrementaldecoder("utf-8")()
                buffer = InstructionBuffer()
                while self.authorized(request):
                    data = await reader.read(65536)
                    if not data:
                        break
                    messages = buffer.feed(decoder.decode(data))
                    if messages:
                        await ws.send_str("".join(messages))
            async def to_desktop():
                async for message in ws:
                    if message.type == WSMsgType.TEXT and self.authorized(request):
                        if message.data.startswith("0.,"):
                            await ws.send_str(message.data)  # Guacamole tunnel ping, not a desktop instruction.
                        else:
                            writer.write(message.data.encode())
                            await writer.drain()
                    else:
                        break
            tasks = [asyncio.create_task(to_browser()),asyncio.create_task(to_desktop())]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
        except Exception:
            if not ws.closed:
                await ws.send_str(instruction("error","Desktop connection failed. Unlock the host and reconnect.","519"))
        finally:
            if writer:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(),1)
                except Exception:
                    writer.transport.abort()
            self.websockets.discard(ws)
            self.ws_sessions.pop(ws,None)
            if joined:self.joined_count=max(0,self.joined_count-1)
            if not self.joined_count:self.connection_id=None
            await ws.close()
        return ws

    def share(self,identity=None):
        if identity is None:
            identity=self.latest_grant
            if identity not in self.grants or self.grants[identity].get("internal"):
                identity=next((key for key,value in reversed(list(self.grants.items()))
                               if not value.get("internal") and unexpired(value["expires"])),None)
        grant=self.grants.get(identity)
        if not self.public_url or not grant or not unexpired(grant["expires"]):
            raise ValueError("Generate an active phone share first.")
        return {"url":self.public_url + "#key=" + grant["key"], "expires":grant["expires"],"id":identity}

    def qr_svg(self,identity=None):
        import qrcode
        from qrcode.image.svg import SvgPathImage
        code=qrcode.make(self.share(identity)["url"], image_factory=SvgPathImage, box_size=8, border=4)
        buffer=io.BytesIO();code.save(buffer)
        return buffer.getvalue().decode()

    def revoke_session(self,identity):
        tokens=[token for token,value in self.sessions.items() if value["id"]==identity]
        for token in tokens:self.sessions.pop(token,None)
        if self.loop:
            async def close():
                await asyncio.gather(*(ws.close(code=1001,message=b"Session revoked") for ws,token in list(self.ws_sessions.items()) if token in tokens),return_exceptions=True)
            self._run(close())

    def revoke(self,identity,cycle=False):
        if identity not in self.grants:raise ValueError("That access link is no longer active.")
        sessions=[value["id"] for value in self.sessions.values() if value["grant"]==identity]
        for value in sessions:self.revoke_session(value)
        if cycle:
            self.grants[identity]["key"]=secrets.token_urlsafe(32)
            self.latest_grant=identity;self.key=self.grants[identity]["key"]
        else:self.grants.pop(identity)
        self.update_expiry()

    def prune(self):
        for identity,grant in list(self.grants.items()):
            if not unexpired(grant["expires"]):self.revoke(identity)
        if self.tunnel and not any(not value.get("internal") for value in self.grants.values()):
            self.stop_tunnel()

    def stop_tunnel(self):
        if self.tunnel:
            self.tunnel.terminate()
            try:self.tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:self.tunnel.kill()
            self.tunnel=None
        self.public_url=f"http://127.0.0.1:{self.local_port}" if self.local_port else ""
        self.expected_origin=self.public_url

    def stop(self):
        with self.lock:
            self.key = ""
            self.expires = 0
            self.sessions.clear()
            self.grants.clear()
            self.ws_sessions.clear()
            self.stop_tunnel()
            if self.loop:
                async def cleanup():
                    for ws in list(self.websockets):
                        await ws.close(code=1001,message=b"Share revoked")
                    if self.runner:
                        await self.runner.cleanup()
                try:
                    self._run(cleanup(),10)
                finally:
                    self.loop.call_soon_threadsafe(self.loop.stop)
                    self.thread.join(timeout=3)
                    self.loop.close()
                    self.loop = self.thread = self.runner = None
            if self.container:
                subprocess.run(["docker","stop","-t","2",self.container],stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,timeout=8,check=False)
                self.container = None
            if self.guacd and self.guacd.poll() is None:
                self.guacd.terminate()
                try:
                    self.guacd.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    self.guacd.kill()
            self.guacd = None
            self.public_url = self.expected_origin = ""
            self.local_port = 0
            self.connection_id=None;self.joined_count=0


def sys_linux():
    import platform
    return platform.system()=="Linux"
