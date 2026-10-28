"""Keep documented semantic palette in sync with its single runtime owner."""
import re
from pathlib import Path

root = Path(__file__).resolve().parent.parent
design = (root / "DESIGN.md").read_text()
css = (root / "yesmachine/web/tokens.css").read_text()
palette = design.split("colors:\n", 1)[1].split("typography:", 1)[0]
for name, color in re.findall(r'  ([\w-]+): "(#[a-fA-F0-9]{6})"', palette):
    assert re.search(r"--" + name + r"\s*:\s*" + color + r"\s*;", css), f"Token drift: {name}"
print("Design palette matches runtime CSS.")
