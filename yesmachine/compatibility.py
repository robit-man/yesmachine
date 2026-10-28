"""Native RDP hosting and interoperable, password-free client profiles.

NX remains the installed NoMachine server's responsibility. This module never
updates or reconfigures NoMachine. GNOME credentials go directly to libsecret.
"""

import getpass
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import ssl
import struct
import subprocess
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from .storage import private_write

SCHEMA = "org.gnome.desktop.remote-desktop.rdp"
KEYS = ("enable", "port", "tls-cert", "tls-key", "view-only", "negotiate-port")


def tcp_open(port):
    """Inspect listeners without connecting: GRD probes can disrupt a session."""
    if platform.system()=="Linux":
        for name in ("/proc/net/tcp","/proc/net/tcp6"):
            try:
                for line in Path(name).read_text().splitlines()[1:]:
                    fields=line.split()
                    if fields[3]=="0A" and int(fields[1].rsplit(":",1)[1],16)==int(port):
                        return True
            except (OSError,ValueError,IndexError):
                continue
        return False
    try:
        import psutil
        return any(connection.status=="LISTEN" and connection.laddr.port==int(port)
                   for connection in psutil.net_connections(kind="tcp"))
    except Exception:
        return False


def read_settings():
    import gi
    from gi.repository import Gio, GLib
    context = GLib.MainContext.default()
    for _ in range(30):
        if not context.pending():
            break
        context.iteration(False)
    settings = Gio.Settings.new(SCHEMA)
    return {key: settings.get_value(key).unpack() for key in KEYS}


def write_settings(values):
    from gi.repository import Gio, GLib
    settings = Gio.Settings.new(SCHEMA)
    for key, value in values.items():
        kind = settings.get_value(key).get_type_string()
        if not settings.set_value(key, GLib.Variant(kind, value)):
            raise ValueError("GNOME rejected a desktop-sharing setting.")
    Gio.Settings.sync()


def credentials(operation, username=None, password=None):
    import gi
    gi.require_version("Secret", "1")
    from gi.repository import Secret, GLib
    schema = Secret.Schema.new("org.gnome.RemoteDesktop.RdpCredentials", Secret.SchemaFlags.NONE,
                               {"credentials": Secret.SchemaAttributeType.STRING})
    try:
        if operation == "get":
            value = Secret.password_lookup_sync(schema, {}, None)
            return GLib.Variant.parse(GLib.VariantType("a{sv}"), value, None, None).unpack() if value else None
        if operation == "clear":
            Secret.password_clear_sync(schema, {}, None)
            return
        value = GLib.Variant("a{sv}", {"username": GLib.Variant("s", username),
                                      "password": GLib.Variant("s", password)})
        if not Secret.password_store_sync(schema, {}, Secret.COLLECTION_DEFAULT,
                                          "GNOME Remote Desktop RDP credentials", value.print_(True), None):
            raise ValueError
    except Exception:
        raise ValueError("GNOME's keyring is unavailable. Unlock the keyring and retry RDP setup.") from None


def native_certificate(directory):
    cert_path, key_path = directory / "rdp.crt", directory / "rdp.key"
    if not cert_path.exists() or not key_path.exists():
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, socket.gethostname())])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=3650))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.SubjectAlternativeName([x509.DNSName(socket.gethostname())]), critical=False)
                .sign(key, hashes.SHA256()))
        private_write(key_path, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                 serialization.NoEncryption()).decode())
        private_write(cert_path, cert.public_bytes(serialization.Encoding.PEM).decode())
    return cert_path, key_path


def receive_exact(stream, length):
    result = b""
    while len(result) < length:
        chunk = stream.recv(length - len(result))
        if not chunk:
            raise OSError("The RDP server closed its negotiation.")
        result += chunk
    return result


def probe_rdp(host, port):
    """Perform X.224/RDP negotiation and TLS, without sending credentials."""
    packet = bytes.fromhex("030000130ee000000000000100080003000000")
    with socket.create_connection((host, port), timeout=3) as stream:
        stream.settimeout(3)
        stream.sendall(packet)
        header = receive_exact(stream, 4)
        length = int.from_bytes(header[2:], "big")
        if header[:2] != b"\x03\x00" or not 12 <= length <= 4096:
            raise ValueError("The selected port did not answer as an RDP server.")
        reply = receive_exact(stream, length - 4)
        kind, _flags, size, selected = struct.unpack("<BBHI", reply[-8:])
        if kind != 2 or size != 8 or selected not in (1, 2, 8):
            raise ValueError("The RDP server did not negotiate TLS/NLA. Legacy unencrypted RDP is not enabled.")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # Public certificate inspection only; no credentials are sent.
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        with context.wrap_socket(stream, server_hostname=host) as secure:
            return {"security": "NLA" if selected in (2, 8) else "TLS",
                    "fingerprint": hashlib.sha256(secure.getpeercert(binary_form=True)).hexdigest()}


def endpoint(host, port):
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def client_profile(protocol, host, port, username="", name="YesMachine"):
    # Never permit line/control injection into native profile formats.
    if any(any(ord(char) < 32 for char in str(value)) for value in (host, username, name)):
        raise ValueError("Client profile values cannot contain control characters.")
    if protocol == "rdp":
        lines = [f"full address:s:{endpoint(host, int(port))}", f"username:s:{username}",
                 "prompt for credentials:i:1", "authentication level:i:1", "enablecredsspsupport:i:1",
                 "redirectclipboard:i:1", "audiomode:i:0", "screen mode id:i:2",
                 "use multimon:i:0", "dynamic resolution:i:1"]
        return "\r\n".join(lines) + "\r\n", ".rdp"
    if protocol != "nomachine":
        raise ValueError("Choose RDP or NoMachine for a native client profile.")
    root = ET.Element("NXClientSettings", {"version": "2.1", "application": "nxclient"})
    for group_name, options in {
        "General": {"Connection name": name, "Connection service": "nx", "Server host": host,
                    "Server port": "22", "NoMachine daemon port": str(int(port)),
                    "Remember username": "true"},
        "Login": {"User": username, "Auth": "nx-password", "Remember password": "false"},
    }.items():
        group = ET.SubElement(root, "group", {"name": group_name})
        for key, value in options.items():
            ET.SubElement(group, "option", {"key": key, "value": value})
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode"), ".nxs"


class Compatibility:
    def __init__(self, directory, event=lambda *_: None):
        self.directory, self.event = Path(directory).resolve(), event
        self.record_path = self.directory / "rdp-managed.json"
        self.record = json.loads(self.record_path.read_text()) if self.record_path.exists() else None
        self.system = platform.system()

    def status(self):
        rdp = {"available": False, "provider": "Native RDP server required", "port": 3389,
               "enabled": False, "listening": False, "managed": bool(self.record), "username": ""}
        if self.system == "Linux" and shutil.which("grdctl"):
            try:
                settings = read_settings()
                rdp.update(available=True, provider="GNOME Remote Desktop", port=int(settings["port"]),
                           enabled=bool(settings["enable"]), listening=tcp_open(int(settings["port"])),
                           username=self.record["username"] if self.record else "")
            except Exception:
                rdp["error"] = "GNOME desktop settings are unavailable in this session."
        elif self.system == "Windows":
            rdp.update(provider="Windows Remote Desktop", available=True, listening=tcp_open(3389))
            rdp["enabled"] = rdp["listening"]
            rdp["error"] = "Enable Remote Desktop in Windows Settings; a supported Windows edition is required."
        else:
            rdp["listening"] = tcp_open(3389)
            rdp["enabled"] = rdp["listening"]
            rdp["error"] = "An installed RDP server is required on this operating system."
        nx = self.nxplayer()
        return {"rdp": rdp, "nomachine": {"available": bool(nx), "listening": tcp_open(4000),
                "port": 4000, "provider": "Existing NoMachine server", "username": getpass.getuser()}}

    def nxplayer(self):
        paths = [Path("/usr/NX/bin/nxplayer"), Path("/Applications/NoMachine.app/Contents/MacOS/nxplayer"),
                 Path("/Applications/NoMachine.app/Contents/Frameworks/bin/nxplayer")]
        if os.name == "nt":
            paths += [Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "NoMachine/bin/nxplayer.exe"]
        return next((path for path in paths if path.is_file()), None)

    def start_rdp(self, username, password, port=3389):
        if self.system != "Linux" or not shutil.which("grdctl"):
            raise ValueError("Enable the native RDP server in this operating system's settings, then export an RDP profile.")
        if not re.fullmatch(r"[^\x00-\x1f]{1,128}", username) or not 12 <= len(password) <= 256 or "\0" in password:
            raise ValueError("Choose an RDP username and a password with 12–256 characters.")
        if self.record:
            current = read_settings()
            if current["enable"] and tcp_open(self.record["port"]):
                if int(port) != self.record["port"]:
                    raise ValueError("Stop managed RDP before changing its port.")
                credentials("set", username, password)
                self.record["username"] = username
                private_write(self.record_path, json.dumps(self.record))
                self.event("Updated managed RDP credentials in the OS keyring")
                return
            raise ValueError("A managed RDP setup needs cleanup first. Use Stop RDP and retry.")
        if not 1024 <= int(port) <= 65535 or int(port) in (4000, 21118, 21179, 21180):
            raise ValueError("Choose an unused RDP port between 1024 and 65535, such as 3389.")
        before = read_settings()
        if before["enable"] or before["tls-cert"] or before["tls-key"] or credentials("get"):
            raise ValueError("GNOME already has an RDP configuration. It was left unchanged; use its settings or export a profile for that server.")
        if tcp_open(int(port)):
            raise ValueError("That RDP port is already in use. Choose another port.")
        active = subprocess.run(["systemctl", "--user", "is-active", "--quiet", "gnome-remote-desktop"], check=False).returncode == 0
        cert, key = native_certificate(self.directory / "rdp-tls")
        self.record = {"before": before, "service_was_active": active, "username": username,
                       "port": int(port), "cert": str(cert)}
        private_write(self.record_path, json.dumps(self.record))
        try:
            credentials("set", username, password)
            write_settings({"port": int(port), "tls-cert": str(cert), "tls-key": str(key),
                            "view-only": False, "negotiate-port": False, "enable": True})
            subprocess.run(["systemctl", "--user", "start", "gnome-remote-desktop"], check=True, capture_output=True, timeout=15)
            expected = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest()
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                try:
                    probe = probe_rdp("127.0.0.1", int(port))
                    if probe["fingerprint"] != expected:
                        raise ValueError("The RDP listener presented another certificate.")
                    self.event(f"Standard RDP is ready on TCP {port} with {probe['security']}")
                    return
                except OSError:
                    time.sleep(.3)
            raise ValueError("GNOME RDP did not become ready. Check desktop-sharing permissions in GNOME Settings.")
        except BaseException:
            self.stop_rdp(force=True)
            raise

    def stop_rdp(self, force=False):
        if not self.record:
            raise ValueError("This RDP server was not started by YesMachine; it was left unchanged.")
        current = read_settings()
        already_restored=current==self.record["before"]
        if not force and not already_restored and (current["tls-cert"] != self.record["cert"] or int(current["port"]) != self.record["port"]):
            raise ValueError("RDP settings were changed outside YesMachine; they were left unchanged.")
        write_settings({"enable": False})
        write_settings(self.record["before"])
        credentials("clear")
        if not self.record["service_was_active"]:
            subprocess.run(["systemctl", "--user", "stop", "--no-block", "gnome-remote-desktop"],
                           check=True,capture_output=True,timeout=3)
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                state=subprocess.run(["systemctl","--user","show","gnome-remote-desktop","-p","ActiveState","--value"],
                                     capture_output=True,text=True,timeout=2).stdout.strip()
                if state in ("inactive","failed"):break
                time.sleep(.2)
            else:
                # Only terminate the service this setup started; pre-existing
                # user services take the separate restore-only path above.
                subprocess.run(["systemctl","--user","kill","--kill-whom=all","--signal=SIGKILL","gnome-remote-desktop"],
                               check=False,capture_output=True,timeout=3)
        self.record_path.unlink(missing_ok=True)
        self.record = None
        self.event("Stopped managed RDP and restored the previous desktop-sharing settings")

    def rdp_credentials(self):
        if not self.record:
            raise ValueError("Start a YesMachine-managed RDP host before opening the browser viewer.")
        value = credentials("get")
        if not value or not value.get("password"):
            raise ValueError("The RDP keyring entry is unavailable. Unlock your keyring and retry.")
        return value

    def launch(self, device):
        protocol = device["protocol"]
        content, suffix = client_profile(protocol, device["host"], device["port"], device.get("username", ""), device["name"])
        path = self.directory / "client-profiles" / (device["id"] + suffix)
        private_write(path, content)
        if protocol == "nomachine":
            player = self.nxplayer()
            if not player:
                raise ValueError("NoMachine Player is not installed here. Export the .nxs profile and open it on a device with Player.")
            command = [str(player), "--session", str(path)]
        elif os.name == "nt":
            command = ["mstsc.exe", str(path)]
        elif self.system == "Darwin":
            command = ["open", str(path)]
        elif shutil.which("remmina"):
            command = ["remmina", "--connect", str(path)]
        else:
            raise ValueError("No RDP client was found. Export the .rdp file and open it with your preferred RDP app.")
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=os.name != "nt")
