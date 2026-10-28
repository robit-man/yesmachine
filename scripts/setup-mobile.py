#!/usr/bin/env python3
"""Explicit browser-backend setup; never invoked by the application."""
import argparse
import json
import platform
import shutil
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--check', action='store_true', help='check only; do not download')
args = parser.parse_args()
manifest = json.loads((root / 'vendor/browser-backend.json').read_text())
if platform.system() != 'Linux' or not shutil.which('docker'):
    raise SystemExit('The pinned Docker backend requires Linux and an accessible Docker daemon. Install guacd separately on other hosts.')
if not shutil.which('cloudflared'):
    raise SystemExit('Install cloudflared separately before generating public shares. This script does not update it.')
image = manifest['image']
exists = subprocess.run(['docker','image','inspect',image],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode == 0
if not exists:
    if args.check:
        raise SystemExit('Pinned browser backend is missing. Run this script without --check to download it explicitly.')
    subprocess.run(['docker','pull',image],check=True)
print('Pinned browser backend and cloudflared are available. NoMachine is unchanged.')
