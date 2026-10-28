"""Unmodified RustDesk runtime integration; subprocess arguments are arrays."""

import json
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path

from .storage import private_write
from .compatibility import tcp_open


def listener(port):
    return tcp_open(port)


def update_options(path, options):
    source = path.read_text() if path.exists() else ""
    lines = source.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == "[options]"), None)
    if start is None:
        lines += ["", "[options]"]
        start = len(lines) - 1
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("[")), len(lines))
    section = lines[start + 1:end]
    for key, value in options.items():
        pattern = re.compile(r"^\s*" + re.escape(key) + r"\s*=")
        section = [line for line in section if not pattern.match(line)]
        section.append(f"{key} = {json.dumps(value, ensure_ascii=False)}")
    private_write(path, "\n".join(lines[:start + 1] + section + lines[end:]) + "\n")


class Engine:
    def __init__(self, root, state, port):
        self.root, self.state, self.port = root, state, port
        self.lock = threading.RLock()
        self.process = None
        self.children = []
        self.env = os.environ.copy()
        if platform.system() == "Linux":
            self.env["XDG_CONFIG_HOME"] = str(state / "engine-config")
            self.config = state / "engine-config/rustdesk/RustDesk2.toml"
            arch = "aarch64" if platform.machine() in ("aarch64", "arm64") else "x86_64"
            self.binary = root / f"vendor/runtime/linux-{arch}/usr/share/rustdesk/rustdesk"
        elif platform.system() == "Windows":
            self.config = Path(os.environ["APPDATA"]) / "RustDesk/config/RustDesk2.toml"
            self.binary = root / "vendor/runtime/windows/rustdesk.exe"
        else:
            self.config = Path.home() / "Library/Application Support/com.carriez.RustDesk/RustDesk2.toml"
            self.binary = root / "vendor/runtime/macos/RustDesk.app/Contents/MacOS/RustDesk"
        if not self.binary.is_file():
            found = shutil.which("rustdesk")
            if found:
                self.binary = Path(found)
        self.log = state / "engine.log"

    def installed(self):
        return self.binary.is_file()

    def _spawn(self, arguments):
        if not self.installed():
            raise ValueError("The desktop engine is missing. Run the setup script in this folder first.")
        if not self.config.exists():
            update_options(self.config, {"direct-server": "N", "whitelist": "127.0.0.1/32,::1/128,::ffff:127.0.0.1/128",
                           "custom-rendezvous-server": "127.0.0.1:21116", "relay-server": "127.0.0.1:21117"})
        update_options(self.config.with_name("RustDesk_local.toml"),
                       {"enable-check-update": "N", "allow-auto-update": "N"})
        with self.log.open("ab") as logfile:
            process = subprocess.Popen([str(self.binary), *arguments], env=self.env,
                                       cwd=self.binary.parent, stdin=subprocess.DEVNULL,
                                       stdout=logfile, stderr=logfile,
                                       start_new_session=os.name != "nt")
        return process

    def start(self):
        with self.lock:
            if self.process and self.process.poll() is None and listener(self.port):
                return
            if listener(self.port):
                raise ValueError(f"Port {self.port} is already occupied by another process. Close that host before starting YesMachine.")
            if self.config.exists() and not self.config.with_suffix(".pre-yesmachine.toml").exists():
                private_write(self.config.with_suffix(".pre-yesmachine.toml"), self.config.read_text())
            update_options(self.config, {
                "direct-server": "Y", "direct-access-port": str(self.port),
                "approve-mode": "click", "verification-method": "use-both-passwords",
                "whitelist": "127.0.0.1/32,::1/128,::ffff:127.0.0.1/128", "stop-service": "",
                "custom-rendezvous-server": "127.0.0.1:21116",
                "relay-server": "127.0.0.1:21117", "disable-auto-update": "Y",
            })
            self.process = self._spawn(["--server"])
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise ValueError("The desktop engine exited before hosting started. Open native settings and check OS screen/input permissions. See .state/engine.log.")
                if listener(self.port):
                    return
                time.sleep(.2)
            self.stop()
            raise ValueError("The desktop engine did not open its listener. Check its permissions and .state/engine.log, then retry.")

    def stop(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                if os.name == "nt":
                    self.process.terminate()
                else:
                    os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
            self.process = None

    def open(self, mode="settings", port=None):
        flags = {"desktop": "--connect", "files": "--file-transfer",
                 "terminal": "--terminal", "tunnel": "--port-forward"}
        if mode == "settings":
            arguments = ["--no-server"]
        elif mode in flags and port:
            arguments = [flags[mode], f"127.0.0.1:{port}", "--no-server"]
        else:
            raise ValueError("Unknown desktop action.")
        process = self._spawn(arguments)
        self.children = [child for child in self.children if child.poll() is None]
        self.children.append(process)
        time.sleep(.5)
        if process.poll() not in (None, 0):
            raise ValueError("The native window could not open. See .state/engine.log for the OS error.")
        return process.pid

    def status(self):
        return {"installed": self.installed(), "version": "1.5.0",
                "running": bool(self.process and self.process.poll() is None),
                "listening": listener(self.port), "platform": platform.system(),
                "session": os.environ.get("XDG_SESSION_TYPE", "native"),
                "config": str(self.config)}
