#!/usr/bin/env python3
"""Create a portable source bundle, explicitly excluding identity and secrets."""

import hashlib
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parent.parent
destination = root / "dist"
destination.mkdir(exist_ok=True)
archive = destination / "yesmachine-0.1.0-source.zip"
roots = [root / name for name in ("yesmachine", "scripts", "tests", "docs", "vendor/guacamole-js-source")]
files = []
for directory in roots:
    files.extend(path for path in directory.rglob("*") if path.is_file()
                 and "__pycache__" not in path.parts and path.suffix != ".pyc")
files.extend(root / name for name in ("README.md", "LICENSE", "NOTICE.md", "PRODUCT.md",
             "DESIGN.md", "UX-CONTRACT.md", "pyproject.toml", "premium-ui.json", ".gitignore",
             "run.sh", "YesMachine.command", "YesMachine.cmd", "Setup.command", "Setup.cmd",
             "vendor/manifest.json", "vendor/browser-backend.json", ".impeccable/config.json"))
with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
    for path in sorted(files):
        relative = path.relative_to(root)
        assert ".state" not in relative.parts
        output.write(path, Path("yesmachine") / relative)
with archive.open("rb") as stream:
    digest = hashlib.file_digest(stream, "sha256").hexdigest()
(destination / "SHA256SUMS").write_text(f"{digest}  {archive.name}\n")
print(f"Created {archive.name} ({archive.stat().st_size:,} bytes); no runtime state or credentials included.")
