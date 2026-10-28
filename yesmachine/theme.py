"""Validated, unrestricted RGB theme preferences; no CSS strings accepted."""
import json
import re
from pathlib import Path

from .storage import private_write

DEFAULTS = {"background":"#101419", "surface":"#191f27", "text":"#ffffff", "muted":"#b4c0ce",
            "primary":"#226b9a", "secondary":"#202f3e", "border":"#3a4655", "success":"#8de0b4",
            "warning":"#ffd08b", "danger":"#ffacb8", "link":"#9bd5ff", "on-primary":"#ffffff"}


def load_theme(directory):
    path = Path(directory) / "theme.json"
    return {**DEFAULTS, **json.loads(path.read_text())} if path.exists() else dict(DEFAULTS)


def save_theme(directory, colors):
    if not isinstance(colors, dict) or set(colors) != set(DEFAULTS):
        raise ValueError("Choose a color for every theme role.")
    if any(not isinstance(value, str) or not re.fullmatch(r"#[a-fA-F0-9]{6}", value) for value in colors.values()):
        raise ValueError("Use a six-digit hex color such as #3399ff.")
    result = {name: value.lower() for name, value in colors.items()}
    private_write(Path(directory) / "theme.json", json.dumps(result, indent=2))
    return result
