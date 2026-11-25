"""Construit wifiaudit/data/oui.json.gz (préfixe MAC -> fabricant) à partir du
fichier « manuf » de Wireshark (https://www.wireshark.org/download/automated/data/manuf).

    python tools/build_oui.py [chemin_manuf]
"""
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tools" / "manuf.txt"
out: dict[str, str] = {}
for line in src.read_text(encoding="utf-8", errors="replace").splitlines():
    if not line or line.startswith("#"):
        continue
    parts = line.split("\t")
    if len(parts) < 2:
        continue
    prefix = parts[0].strip()
    name = (parts[2] if len(parts) > 2 and parts[2].strip() else parts[1]).strip()
    if "/" in prefix:                       # MA‑M / MA‑S : préfixe/longueur
        p, bits = prefix.split("/")
        hexp = p.replace(":", "").replace("-", "").upper()[: int(bits) // 4]
    else:
        hexp = prefix.replace(":", "").replace("-", "").upper()
        if len(hexp) != 6:
            continue
    out[hexp] = name[:60]
dst = ROOT / "wifiaudit" / "data" / "oui.json.gz"
dst.parent.mkdir(parents=True, exist_ok=True)
with gzip.open(dst, "wt", encoding="utf-8") as f:
    json.dump(out, f, separators=(",", ":"))
print(f"{len(out)} préfixes -> {dst} ({dst.stat().st_size // 1024} Ko)")
