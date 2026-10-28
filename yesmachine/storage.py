"""Private, atomic settings and bounded activity. No shell commands."""

import json
import os
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


def private_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def validate_device(data: dict) -> dict:
    protocol = str(data.get("protocol", "yesmachine"))
    if protocol not in ("yesmachine", "rdp", "nomachine"):
        raise ValueError("Choose YesMachine, RDP, or NoMachine as the connection protocol.")
    name = str(data.get("name", "")).strip()
    host = str(data.get("host", "")).strip()
    if not name or len(name) > 80:
        raise ValueError("Enter a computer name with 1–80 characters.")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.:-]{0,252}", host):
        raise ValueError("Enter an IP address or hostname, without a URL or spaces.")
    try:
        port = int(data.get("port", {"yesmachine":21180,"rdp":3389,"nomachine":4000}[protocol]))
        if not 1 <= port <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("Enter a port between 1 and 65535.") from None
    fingerprint = str(data.get("fingerprint", "")).replace(":", "").strip().lower()
    token = str(data.get("token", "")).strip()
    if protocol != "yesmachine":
        fingerprint, token = "", ""
    if fingerprint and not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
        raise ValueError("The certificate fingerprint must contain 64 hexadecimal characters.")
    if token and not re.fullmatch(r"[a-zA-Z0-9_-]{32,100}", token):
        raise ValueError("Paste the complete access key from the other computer.")
    mac = str(data.get("mac", "")).strip().upper().replace("-", ":")
    if mac and not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", mac):
        raise ValueError("Use a MAC address such as AA:BB:CC:DD:EE:FF.")
    username = str(data.get("username", "")).strip()
    if len(username)>128 or any(ord(char)<32 for char in username):
        raise ValueError("Enter a username without control characters.")
    return dict(name=name, host=host, port=port, fingerprint=fingerprint, token=token, mac=mac,
                protocol=protocol, username=username)


class Store:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            directory.chmod(0o700)
        except OSError:
            pass
        self.lock = threading.RLock()
        self.path = directory / "settings.json"
        if self.path.exists():
            self.settings = json.loads(self.path.read_text())
        else:
            self.settings = {"install_id": secrets.token_hex(6), "port": 21180,
                             "native_port": 21118, "access_key": secrets.token_urlsafe(32)}
            private_write(self.path, json.dumps(self.settings, indent=2))
        self.db = sqlite3.connect(directory / "yesmachine.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, host TEXT NOT NULL,
                port INTEGER NOT NULL, fingerprint TEXT NOT NULL, token TEXT NOT NULL,
                mac TEXT NOT NULL, updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS activity (
                id INTEGER PRIMARY KEY, at TEXT NOT NULL, message TEXT NOT NULL,
                level TEXT NOT NULL);
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(devices)")}
        for name, default in (("protocol", "yesmachine"), ("username", "")):
            if name not in columns:
                self.db.execute(f"ALTER TABLE devices ADD COLUMN {name} TEXT NOT NULL DEFAULT '{default}'")
        self.db.commit()
        try:
            (directory / "yesmachine.sqlite3").chmod(0o600)
        except OSError:
            pass

    def devices(self, secrets_allowed=False):
        with self.lock:
            rows = [dict(row) for row in self.db.execute("SELECT * FROM devices ORDER BY name COLLATE NOCASE")]
        if not secrets_allowed:
            for row in rows:
                token = row.pop("token")
                row["paired"] = row["protocol"] != "yesmachine" or bool(token and row["fingerprint"])
        return rows

    def device(self, identity):
        with self.lock:
            row = self.db.execute("SELECT * FROM devices WHERE id=?", (identity,)).fetchone()
        if not row:
            raise ValueError("That computer is no longer in the address book. Refresh the list.")
        return dict(row)

    def save_device(self, data):
        identity = str(data.get("id") or secrets.token_hex(8))
        if not re.fullmatch(r"[a-f0-9]{16}", identity):
            raise ValueError("Invalid address-book identifier.")
        if data.get("id") and not data.get("token"):
            data = {**data, "token": self.device(identity)["token"]}
        value = validate_device(data)
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO devices(id,name,host,port,fingerprint,token,mac,updated,protocol,username) VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (identity, value["name"], value["host"], value["port"],
                             value["fingerprint"], value["token"], value["mac"],
                             datetime.now(timezone.utc).isoformat(), value["protocol"], value["username"]))
        return identity

    def remove_device(self, identity):
        name = self.device(identity)["name"]
        with self.lock, self.db:
            self.db.execute("DELETE FROM devices WHERE id=?", (identity,))
        self.event(f"Removed {name} from the address book")

    def event(self, message, level="info"):
        with self.lock, self.db:
            self.db.execute("INSERT INTO activity(at,message,level) VALUES(?,?,?)",
                            (datetime.now(timezone.utc).isoformat(), message, level))
            self.db.execute("DELETE FROM activity WHERE id NOT IN (SELECT id FROM activity ORDER BY id DESC LIMIT 500)")

    def activity(self):
        with self.lock:
            return [dict(row) for row in self.db.execute("SELECT * FROM activity ORDER BY id DESC LIMIT 100")]
