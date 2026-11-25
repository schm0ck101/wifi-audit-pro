"""Génère assets/WiFiAuditPro.ico (multi‑résolutions) et assets/icon.png à
partir de l'icône dessinée dans nettrace/ui/theme.py."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from wifiaudit.ui.theme import app_icon_pixmap  # noqa: E402

app = QGuiApplication(sys.argv)
assets = ROOT / "assets"
assets.mkdir(exist_ok=True)
png = assets / "icon.png"
app_icon_pixmap(512).save(str(png))
img = Image.open(png).convert("RGBA")
img.save(assets / "WiFiAuditPro.ico",
         sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("Icône générée :", assets / "WiFiAuditPro.ico")

