"""Render text log files as a terminal-style PNG for the README. Usage: terminal_capture.py OUT.png FILE..."""
import re
import sys

from PIL import Image, ImageDraw, ImageFont

out, files = sys.argv[1], sys.argv[2:]
lines = []
for path in files:
    if path.startswith("$ "):
        lines.append(path)
        continue
    for line in open(path, encoding="utf-8").read().splitlines():
        line = re.sub(r"^\d{4}-\d\d-\d\d (\d\d:\d\d:\d\d),\d+ ", r"\1 ", line)
        lines.append(line[:140])
    lines.append("")
try:
    font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 18)
except OSError:
    font = ImageFont.load_default()
width, line_h, pad = 1560, 26, 28
img = Image.new("RGB", (width, pad * 2 + 30 + line_h * len(lines)), (24, 26, 33))
d = ImageDraw.Draw(img)
for i, colour in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
    d.ellipse([pad + i * 24, 14, pad + i * 24 + 14, 28], fill=colour)
y = pad + 30
for line in lines:
    colour = (130, 200, 255) if line.startswith("$ ") else (230, 230, 230)
    if "ERROR" in line:
        colour = (255, 120, 120)
    elif line.startswith(("created", "unchanged", "updated", "All checks passed")):
        colour = (140, 230, 140)
    d.text((pad, y), line, font=font, fill=colour)
    y += line_h
img.save(out)
