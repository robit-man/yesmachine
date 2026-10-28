#!/usr/bin/env python3
"""Explicit, pinned runtime setup. Never called by normal app startup."""

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description="Download and verify the pinned YesMachine desktop engine")
    parser.add_argument("--runtime-only", action="store_true")
    parser.add_argument("--source", action="store_true", help="also clone the matching engine source and submodule")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "vendor/manifest.json").read_text())
    system = {"Linux": "linux", "Windows": "windows", "Darwin": "macos"}.get(platform.system())
    machine = platform.machine().lower()
    arch = "aarch64" if machine in ("aarch64", "arm64") else "x86_64" if machine in ("amd64", "x86_64") else machine
    target = f"{system}-{arch}"
    if target not in manifest["assets"]:
        raise SystemExit(f"No pinned runtime for {target}. See the upstream release; no download was attempted.")
    asset = manifest["assets"][target]
    package = ROOT / "vendor" / asset["file"]
    if not package.exists():
        url = f"https://github.com/rustdesk/rustdesk/releases/download/{manifest['version']}/{asset['file']}"
        print(f"Downloading the separate RustDesk engine {manifest['version']} ({target}). NoMachine is unchanged.", flush=True)
        partial = package.with_suffix(package.suffix + ".part")
        with urllib.request.urlopen(url, timeout=30) as response, partial.open("wb") as output:
            shutil.copyfileobj(response, output)
        partial.replace(package)
    with package.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != asset["sha256"]:
        raise SystemExit("Runtime checksum does not match the pinned release. Nothing was extracted or executed.")
    print("Verified SHA-256:", digest, flush=True)
    if system == "linux":
        destination = ROOT / "vendor/runtime" / target
        destination.mkdir(parents=True, exist_ok=True)
        subprocess.run(["dpkg-deb", "-x", str(package), str(destination)], check=True)
    elif system == "windows":
        destination = ROOT / "vendor/runtime/windows"
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(package, destination / "rustdesk.exe")
    else:
        destination = ROOT / "vendor/runtime/macos"
        destination.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="yesmachine-mount-") as mount:
            subprocess.run(["hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", mount, str(package)], check=True)
            try:
                app = next(Path(mount).glob("*.app"))
                shutil.copytree(app, destination / "RustDesk.app", dirs_exist_ok=True, symlinks=True)
            finally:
                subprocess.run(["hdiutil", "detach", mount], check=True)
    if args.source and not (ROOT / "vendor/rustdesk-source/.git").exists():
        subprocess.run(["git", "clone", "--depth", "1", "--branch", manifest["version"],
                        "--recurse-submodules", "--shallow-submodules",
                        "https://github.com/rustdesk/rustdesk.git", str(ROOT / "vendor/rustdesk-source")], check=True)
    if not args.runtime_only:
        python = ROOT / (".venv/Scripts/python.exe" if system == "windows" else ".venv/bin/python")
        if not python.exists():
            subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(ROOT / ".venv")], check=True)
        subprocess.run([str(python), "-m", "pip", "install", "--no-build-isolation", "-e", str(ROOT)], check=True)
    print("YesMachine setup is complete. Start its launcher. No router port has been opened.")


if __name__ == "__main__":
    main()
