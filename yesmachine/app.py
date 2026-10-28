"""Loopback-only desktop control panel and lifecycle coordinator."""

import argparse
import hmac
import ipaddress
import json
import mimetypes
import os
import secrets
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__
from .engine import Engine
from .compatibility import Compatibility, client_profile
from .mobile import MobileShare
from .storage import Store, private_write
from .theme import DEFAULTS, load_theme, save_theme
from .transport import Transport
from .upnp import discover

ROOT = Path(__file__).resolve().parent.parent
WEB = Path(__file__).resolve().parent / "web"


def lan_address():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


class Application:
    def __init__(self, root=ROOT, state=None):
        self.root = root
        self.store = Store(state or root / ".state")
        self.engine = Engine(root, self.store.directory, self.store.settings["native_port"])
        self.transport = Transport(self.store.directory, self.store.settings["access_key"], self.store.event)
        self.compatibility = Compatibility(self.store.directory, self.store.event)
        self.mobile = MobileShare(self.store.directory, self.compatibility, self.store.event)
        self.vnc = None
        self.client_mappings = {}
        self.client_mapping_record = self.store.directory / "client-mappings.json"
        self.marker = "YesMachine-" + self.store.settings["install_id"]
        self.gateway = None
        self.mapping = None
        self.job = {"running": False, "action": "", "error": "", "message": ""}
        self.lock = threading.RLock()
        self.network_lock = threading.RLock()
        self.stopping = threading.Event()
        self.http = None
        self.last_renewal = 0
        self.mapping_record = self.store.directory / "router-mapping.json"
        self.network_error = ("A router mapping from a previous run may remain. Use Clean up an old mapping, or publish again to verify it."
                              if self.mapping_record.exists() else "")
        self.maintenance = threading.Thread(target=self.maintain, daemon=True)
        self.maintenance.start()

    def state(self):
        with self.lock:
            return {"version": __version__, "hostname": socket.gethostname(),
                    "lan_ip": lan_address(), "port": self.store.settings["port"],
                    "fingerprint": self.transport.fingerprint,
                    "host": bool(self.transport.server and self.engine.process
                                 and self.engine.process.poll() is None),
                    "viewer_transports": self.transport.connections,
                    "engine": self.engine.status(), "mapping": self.mapping,
                    "network_error": self.network_error, "job": dict(self.job),
                    "devices": self.store.devices(), "activity": self.store.activity(),
                    "compatibility": self.compatibility.status(), "client_mappings": self.client_mappings,
                    "mobile": self.mobile.status(), "theme": load_theme(self.store.directory),
                    "theme_defaults": DEFAULTS,"capabilities":{"phone_management":True},
                    "vnc":{"active":bool(self.vnc and self.vnc.server),"port":self.vnc.port if self.vnc else 5900,
                           "clients":len(self.vnc.clients) if self.vnc else 0}}

    def submit(self, action, data):
        allowed = {"host-start", "host-stop", "upnp-enable", "upnp-disable", "router-check",
                   "engine-open", "desktop", "files", "terminal", "tunnel", "wake", "quit", "key-rotate",
                   "rdp-start", "rdp-stop", "client-upnp-enable", "client-upnp-disable", "mobile-start", "mobile-stop",
                   "mobile-cycle", "mobile-revoke", "mobile-session-revoke", "vnc-start", "vnc-stop"}
        if action not in allowed:
            raise ValueError("Unknown action.")
        with self.lock:
            if self.stopping.is_set():
                raise ValueError("YesMachine is shutting down. Reopen it before starting another action.")
            if self.job["running"]:
                raise ValueError("Another action is still running. Wait for it to finish, then retry.")
            self.job = {"running": True, "action": action, "error": "", "message": "Working…"}
        threading.Thread(target=self.perform, args=(action, data), daemon=True).start()

    def enable_mapping(self):
        with self.network_lock:
            return self._enable_mapping()

    def _enable_mapping(self):
        if not self.transport.server or not self.engine.process or self.engine.process.poll() is not None:
            raise ValueError("Start hosting before publishing the connection through your router.")
        gateway = self.gateway or discover()
        external = gateway.external_ip()
        if not ipaddress.ip_address(external).is_global:
            raise ValueError(f"The router reports {external}, which is not a public Internet address. UPnP cannot bypass a second router or carrier NAT. LAN access still works.")
        port = self.store.settings["port"]
        lease = gateway.add(port, self.marker)
        self.gateway = gateway
        with self.lock:
            self.mapping = {"address": external, "port": port, "lease": lease,
                            "router": gateway.name, "local_ip": gateway.local_ip}
            self.network_error = ""
            self.last_renewal = time.monotonic()
        private_write(self.mapping_record, json.dumps(self.mapping))
        self.store.event(f"Published encrypted TCP {port} through {gateway.name}")

    def remove_mapping(self):
        with self.network_lock:
            return self._remove_mapping()

    def _remove_mapping(self):
        # Also checks for our stable marker after a prior crash/restart.
        gateway = self.gateway
        if gateway is None:
            gateway = discover()
        gateway.remove(self.store.settings["port"], self.marker)
        self.mapping_record.unlink(missing_ok=True)
        with self.lock:
            self.mapping = None
            self.network_error = ""
        self.store.event("Removed YesMachine's router mapping")

    def perform(self, action, data):
        message = ""
        try:
            if action == "host-start":
                self.engine.start()
                try:
                    if self.stopping.is_set():
                        raise ValueError("Hosting was cancelled because YesMachine is shutting down.")
                    self.transport.start_host(self.store.settings["port"], self.store.settings["native_port"])
                except BaseException:
                    self.engine.stop()
                    raise
                message = "Hosting started. Approve incoming viewers in the native desktop window."
            elif action == "host-stop":
                with self.network_lock:
                    if self.mapping:
                        self.remove_mapping()
                    self.transport.stop_host()
                    self.engine.stop()
                message = "Hosting stopped."
            elif action == "upnp-enable":
                self.enable_mapping()
                message = "Router mapping verified. Share the Internet connection profile with your other device."
            elif action == "upnp-disable":
                self.remove_mapping()
                message = "Router mapping removed. LAN hosting is unchanged."
            elif action == "router-check":
                self.gateway = discover()
                address = self.gateway.external_ip()
                message = f"Found {self.gateway.name}. Router address: {address}. No port mapping was changed."
            elif action == "engine-open":
                self.engine.open()
                message = "Native settings opened."
            elif action == "rdp-start":
                self.compatibility.start_rdp(str(data.get("username", "")), str(data.get("password", "")), int(data.get("port",3389)))
                message = "Standard RDP is ready. Export an .rdp profile for your preferred RDP app."
            elif action == "rdp-stop":
                self.stop_mobile()
                if "rdp" in self.client_mappings:
                    self.client_mapping("rdp", False)
                self.compatibility.stop_rdp()
                message = "Managed RDP stopped and previous desktop-sharing settings restored."
            elif action in ("client-upnp-enable", "client-upnp-disable"):
                self.client_mapping(str(data.get("protocol", "")), action.endswith("enable"))
                message = "Native-client router mapping verified." if action.endswith("enable") else "Native-client router mapping removed."
            elif action == "mobile-start":
                self.mobile.start(int(data.get("duration",3600)))
                message = "Phone share is ready. Open its QR code to connect; anyone with that key can access the desktop until expiry or revocation."
            elif action == "mobile-stop":
                self.stop_mobile()
                message = "Phone share revoked. Its access key and browser sessions no longer work."
            elif action in ("mobile-cycle","mobile-revoke"):
                self.mobile.revoke(str(data.get("id","")),cycle=action=="mobile-cycle")
                message = "Access key cycled; previous sessions for that link were revoked." if action=="mobile-cycle" else "Access link and its browser sessions revoked."
            elif action == "mobile-session-revoke":
                self.mobile.revoke_session(str(data.get("id","")))
                message = "Browser session revoked. Revoke its access link as well to prevent signing in again."
            elif action == "vnc-start":
                from .vnc import VncServer
                if self.vnc and self.vnc.server:
                    raise ValueError("VNC is already running. Stop it before changing its password.")
                if not self.mobile.runner:
                    bootstrap=self.mobile.start(duration=0,local_only=True)
                    self.mobile.grants.pop(bootstrap,None)
                    self.mobile.latest_grant=None
                    self.mobile.update_expiry()
                self.vnc=VncServer(self.store.directory,self.mobile,self.store.event)
                self.mobile._run(self.vnc.start(str(data.get("password","")),int(data.get("port",5900))),60)
                message="VNC/RFB is ready on the local network. VeNCrypt-capable clients can use TLS; classic clients use VNC authentication."
            elif action == "vnc-stop":
                if self.vnc and self.mobile.loop:self.mobile._run(self.vnc.stop(),15)
                self.vnc=None
                message="VNC stopped. Phone shares and native RDP are unchanged."
            elif action == "key-rotate":
                running = bool(self.transport.server)
                self.transport.stop_host()
                self.store.settings["access_key"] = secrets.token_urlsafe(32)
                private_write(self.store.path, json.dumps(self.store.settings, indent=2))
                self.transport.access_key = self.store.settings["access_key"]
                if running:
                    self.transport.start_host(self.store.settings["port"], self.store.settings["native_port"])
                message = "Access key replaced. Existing viewers were disconnected; export new profiles for your other devices."
            elif action in {"desktop", "files", "terminal", "tunnel"}:
                device = self.store.device(data.get("id", ""))
                if device["protocol"] != "yesmachine":
                    if action != "desktop":
                        raise ValueError("Open this native client first; its file and session tools are controlled there.")
                    self.compatibility.launch(device)
                    message = f"Opened the native {device['protocol']} client for {device['name']}. Sign in using that server's credentials."
                else:
                    port = self.transport.viewer(device)
                    self.engine.open(action, port)
                    message = f"Opened {action} for {device['name']}. Complete the host's approval in the native window."
            elif action == "wake":
                device = self.store.device(data.get("id", ""))
                if not device["mac"]:
                    raise ValueError("Add this computer's MAC address before using Wake-on-LAN.")
                packet = b"\xff" * 6 + bytes.fromhex(device["mac"].replace(":", "")) * 16
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    sock.sendto(packet, ("255.255.255.255", 9))
                message = f"Sent a Wake-on-LAN packet for {device['name']}."
            elif action == "quit":
                self.stop_mobile()
                for protocol in list(self.client_mappings):
                    self.client_mapping(protocol, False)
                if self.compatibility.record:
                    self.compatibility.stop_rdp()
                if self.mapping:
                    self.remove_mapping()
                self.store.event("YesMachine stopped")
                self.stopping.set()
                self.transport.close()
                self.engine.stop()
                threading.Thread(target=self.http.shutdown, daemon=True).start()
                return
            self.store.event(message)
            with self.lock:
                self.job = dict(running=False, action=action, error="", message=message)
        except Exception as error:
            message = str(error) or type(error).__name__
            self.store.event(message, "error")
            with self.lock:
                self.job = dict(running=False, action=action, error=message, message="")

    def maintain(self):
        while not self.stopping.wait(10):
            if self.mobile.runner:
                self.mobile.prune()
            with self.network_lock:
                for protocol, mapping in list(self.client_mappings.items()):
                    try:
                        status = self.compatibility.status()[protocol]
                        if not status["listening"]:
                            self.client_mapping(protocol, False)
                        elif time.monotonic() - mapping["renewed"] > 1200:
                            self.client_mapping(protocol, True)
                    except Exception as error:
                        self.network_error = str(error)
                if not self.mapping:
                    continue
                try:
                    if not self.engine.process or self.engine.process.poll() is not None:
                        self.remove_mapping()
                        self.transport.stop_host()
                        self.network_error = "The desktop engine stopped; its router mapping was removed."
                    elif time.monotonic() - self.last_renewal > 1200:
                        self.enable_mapping()
                except Exception as error:
                    self.network_error = f"Router maintenance failed: {error}. Retry removal or mapping from Network."

    def profile(self, internet=False):
        if internet and not self.mapping:
            raise ValueError("Enable UPnP before exporting an Internet profile.")
        return {"format": "yesmachine/1", "name": socket.gethostname(),
                "host": self.mapping["address"] if internet else lan_address(),
                "port": self.store.settings["port"], "fingerprint": self.transport.fingerprint,
                "token": self.store.settings["access_key"], "mac": ""}

    def client_mapping(self, protocol, enable):
        if protocol not in ("rdp", "nomachine"):
            raise ValueError("Choose the RDP or NoMachine protocol.")
        with self.network_lock:
            gateway = self.gateway or discover()
            self.gateway = gateway
            status = self.compatibility.status()[protocol]
            external_port = {"rdp":21181,"nomachine":21182}[protocol]
            marker = self.marker + "-" + protocol
            if enable:
                if not status["listening"]:
                    raise ValueError("Start that protocol's native server before publishing its port.")
                address = gateway.external_ip()
                if not ipaddress.ip_address(address).is_global:
                    raise ValueError("The router does not have a public Internet address. Use the Cloudflare phone share or LAN access.")
                lease = gateway.add(external_port, marker, internal_port=status["port"])
                self.client_mappings[protocol] = {"address":address,"port":external_port,"internal_port":status["port"],
                                                  "lease":lease,"renewed":time.monotonic()}
            else:
                previous = self.client_mappings.get(protocol)
                if previous is None and self.client_mapping_record.exists():
                    previous = json.loads(self.client_mapping_record.read_text()).get(protocol)
                gateway.remove(external_port, marker, internal_port=(previous or {}).get("internal_port",status["port"]))
                self.client_mappings.pop(protocol,None)
            private_write(self.client_mapping_record,json.dumps(self.client_mappings))

    def native_profile(self, protocol, internet=False, identity=None):
        if identity:
            device = self.store.device(identity)
            content, suffix = client_profile(device["protocol"],device["host"],device["port"],device["username"],device["name"])
            return {"content":content,"extension":suffix,"name":device["name"]}
        status = self.compatibility.status().get(protocol)
        if status is None:
            raise ValueError("Choose RDP or NoMachine.")
        mapping = self.client_mappings.get(protocol)
        if internet and not mapping:
            raise ValueError("Publish this native-client port before exporting its Internet profile.")
        content, suffix = client_profile(protocol,mapping["address"] if internet else lan_address(),
                                        mapping["port"] if internet else status["port"],status.get("username",""))
        return {"content":content,"extension":suffix,"name":socket.gethostname()}

    def stop_mobile(self):
        if self.vnc and self.mobile.loop:
            self.mobile._run(self.vnc.stop(),15)
        self.vnc=None
        self.mobile.stop()

    def close(self):
        if self.stopping.is_set():
            return
        self.stopping.set()
        self.stop_mobile()
        with self.network_lock:
            for protocol in list(self.client_mappings):
                try:
                    self.client_mapping(protocol,False)
                except Exception as error:
                    self.store.event(f"Native mapping cleanup failed: {error}","error")
            if self.mapping:
                try:
                    self.remove_mapping()
                except Exception as error:
                    self.store.event(f"Router cleanup failed: {error}", "error")
        self.transport.close()
        self.engine.stop()
        if self.compatibility.record:
            try:
                self.compatibility.stop_rdp()
            except Exception as error:
                self.store.event(f"RDP cleanup failed: {error}","error")


class Handler(BaseHTTPRequestHandler):
    server_version = "YesMachine/0.1"

    def log_message(self, *_):
        pass  # Never log query strings, pairing profiles or access keys.

    def reply(self, data, status=200, kind="application/json", cookie=False):
        body = json.dumps(data).encode() if kind == "application/json" else data
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-YesMachine-App", "1")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if cookie:
            self.send_header("Set-Cookie", f"yesmachine={self.server.secret}; HttpOnly; SameSite=Strict; Path=/")
        self.end_headers()
        self.wfile.write(body)

    def guard(self, api=False):
        expected = f"127.0.0.1:{self.server.server_port}"
        if self.headers.get("Host") != expected:
            self.reply({"error": "Only the local YesMachine address is accepted."}, 403)
            return False
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{expected}":
            self.reply({"error": "Cross-origin requests are not accepted."}, 403)
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.reply({"error": "Open YesMachine from its desktop launcher."}, 403)
            return False
        if api:
            try:
                cookies = SimpleCookie(self.headers.get("Cookie", ""))
                value = cookies["yesmachine"].value if "yesmachine" in cookies else ""
            except Exception:
                value = ""
            if not hmac.compare_digest(value, self.server.secret) or self.headers.get("X-YesMachine") != "1":
                self.reply({"error": "Reload the local control panel to restore its session."}, 403)
                return False
        return True

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if not self.guard(path.startswith("/api/")):
            return
        if path == "/api/state":
            self.reply(self.server.app.state())
        elif path == "/api/profile":
            try:
                self.reply(self.server.app.profile(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("internet") == ["1"]))
            except ValueError as error:
                self.reply({"error": str(error)}, 400)
        elif path == "/api/client-profile":
            try:
                query=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                self.reply(self.server.app.native_profile(query.get("protocol",[""])[0],query.get("internet")==["1"],query.get("id",[None])[0]))
            except ValueError as error:
                self.reply({"error":str(error)},400)
        elif path == "/api/mobile/share":
            try:
                identity=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("id",[None])[0]
                self.reply({**self.server.app.mobile.share(identity),"qr":self.server.app.mobile.qr_svg(identity)})
            except ValueError as error:
                self.reply({"error":str(error)},400)
        elif path in ("/", "/app.js", "/styles.css", "/tokens.css", "/icon.svg"):
            target = WEB / ("index.html" if path == "/" else path[1:])
            kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self.reply(target.read_bytes(), kind=kind, cookie=path == "/")
        else:
            self.reply({"error": "Not found."}, 404)

    def do_POST(self):
        if not self.guard(True):
            return
        try:
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("This action requires JSON.")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 131072:
                raise ValueError("The request is empty or too large.")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object.")
            path = urllib.parse.urlsplit(self.path).path
            if path == "/api/devices/save":
                identity = self.server.app.store.save_device(data)
                self.server.app.store.event("Saved an address-book computer")
                self.reply({"id": identity})
            elif path == "/api/devices/remove":
                self.server.app.store.remove_device(data.get("id", ""))
                self.reply({"ok": True})
            elif path == "/api/devices/import":
                profile = data.get("profile")
                if not isinstance(profile, dict) or profile.get("format") != "yesmachine/1":
                    raise ValueError("Choose a YesMachine connection profile exported by the other computer.")
                if not profile.get("token") or not profile.get("fingerprint"):
                    raise ValueError("The profile is missing its access key or fingerprint.")
                self.reply({"id": self.server.app.store.save_device({key:value for key,value in profile.items() if key != "id"})})
                self.server.app.store.event("Imported a private connection profile")
            elif path == "/api/action":
                self.server.app.submit(data.get("action", ""), data)
                self.reply({"accepted": True}, 202)
            elif path == "/api/theme":
                self.reply({"theme":save_theme(self.server.app.store.directory,data.get("colors"))})
            else:
                self.reply({"error": "Not found."}, 404)
        except (ValueError, TypeError, KeyError) as error:
            self.reply({"error": str(error)}, 400)
        except Exception:
            self.reply({"error": "The local action failed. Check the activity panel and retry."}, 500)


def open_window(url, state):
    import shutil
    executable = next((shutil.which(name) for name in ("google-chrome", "chromium", "msedge") if shutil.which(name)), None)
    if executable:
        with (state / "window.log").open("ab") as logfile:
            subprocess.Popen([executable, f"--app={url}", f"--user-data-dir={state / 'window-profile'}",
                              "--no-first-run", "--disable-default-apps"],
                             stdout=logfile, stderr=logfile, start_new_session=os.name != "nt")
    else:
        webbrowser.open(url)


def main():
    parser = argparse.ArgumentParser(description="YesMachine desktop control center")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=21179)
    parser.add_argument("--state-dir", type=Path)
    args = parser.parse_args()
    try:
        http = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError:
        url = f"http://127.0.0.1:{args.port}"
        try:
            with urllib.request.urlopen(url, timeout=2) as existing:
                if existing.headers.get("X-YesMachine-App") != "1":
                    raise ValueError("Another application owns the control port.")
        except Exception:
            raise SystemExit(f"Control port {args.port} is occupied by another application. Start with --port and an unused local port.") from None
        if not args.no_browser:
            open_window(url, args.state_dir or ROOT / ".state")
        return
    app = Application(state=args.state_dir)
    http.app, http.secret = app, secrets.token_urlsafe(32)
    http.daemon_threads = True
    app.http = http
    url = f"http://127.0.0.1:{http.server_port}"
    print(f"YesMachine {__version__} — {url}", flush=True)
    app.store.event("YesMachine control panel started; hosting is off")
    if not args.no_browser:
        open_window(url, app.store.directory)
    def shutdown(*_):
        threading.Thread(target=http.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        http.serve_forever(poll_interval=.2)
    finally:
        app.close()
        http.server_close()


if __name__ == "__main__":
    main()
