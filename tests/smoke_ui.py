"""Test fonctionnel de l'interface complète.

BANC DE TEST UNIQUEMENT : le poste de développement n'a pas de carte Wi‑Fi.
Les scans sont donc construits ici (éléments 802.11 conformes à la norme,
comme dans test_core.py) et injectés dans le service, afin d'exercer toutes
les pages : clients, scan, canaux, relevé sur plan + heatmap, supervision et
alertes, tests, rapport PDF/Excel. L'application elle‑même n'embarque aucune
donnée simulée.

    python tests/smoke_ui.py <dossier_sortie>
"""
import math
import os
import random
import sys
import time
from pathlib import Path

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
os.environ["WIFIAUDIT_DATA_DIR"] = str(OUT / "data")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)

from tests.test_core import eht_op, he_cap, he_op_6ghz, ht_cap, ht_op, ie, rsn, vht_cap, vht_op  # noqa: E402
from wifiaudit.config import Settings  # noqa: E402
from wifiaudit.core.service import ScanResult, WifiService  # noqa: E402
from wifiaudit.events import setup_logging  # noqa: E402
from wifiaudit.wlan.model import Bss  # noqa: E402

setup_logging(True)
app = QApplication(sys.argv[:1])
app.setStyle("Fusion")
from wifiaudit.store.db import Database  # noqa: E402
from wifiaudit.ui import theme  # noqa: E402
from wifiaudit.ui.common import AppContext  # noqa: E402
from wifiaudit.ui.main_window import MainWindow  # noqa: E402
from wifiaudit.ui.plan_view import image_to_png  # noqa: E402

settings = Settings()
theme.apply_theme("dark")
db = Database()
svc = WifiService(settings)
ctx = AppContext(settings, db, svc)
win = MainWindow(ctx)
svc.start()
win.resize(1600, 980)
win.show()
events = []
ctx.event.connect(events.append)


def pump(s):
    t = time.time() + s
    while time.time() < t:
        app.processEvents()
        time.sleep(0.02)


def shot(name):
    pump(0.4)
    win.grab().save(str(OUT / f"{name}.png"))


# --------------------------------------------------------- plan d'étage
W, H = 1400, 800
plan = QImage(W, H, QImage.Format_RGB32)
plan.fill(Qt.white)
p = QPainter(plan)
p.setPen(QPen(QColor("#333"), 6))
p.drawRect(20, 20, W - 40, H - 40)
for x in (420, 900):
    p.drawLine(x, 20, x, 520)
p.drawLine(20, 520, W - 20, 520)
p.setFont(QFont("Segoe UI", 22))
for (x, y, t) in ((120, 260, "Accueil"), (560, 260, "Open space"), (1050, 260, "Salle de réunion"),
                  (600, 680, "Entrepôt")):
    p.drawText(x, y, t)
p.end()

cid = db.save_client({"name": "ACME Industries", "contact": "M. Dupont", "address": "12 rue des Lilas, Lyon"})
sid = db.save_site({"client_id": cid, "name": "Siège Lyon", "address": "12 rue des Lilas",
                    "own_ssids": ["ACME-Corp", "ACME-Invites"]})
fid = db.add_floor(sid, "Rez‑de‑chaussée", image_to_png(plan), W, H)
db.update_floor(fid, scale=40 / W)           # 40 m de large
aid = db.create_audit(sid, "Audit initial", "audit", "Auditeur Test")
ctx.set_context(cid, sid, aid)

# ------------------------------------------------------ points d'accès
APS = [  # (bssid, ssid, freq, ies, position)
    ("ac:8b:a9:10:00:01", "ACME-Corp", 5180, rsn([8, 2], caps=0x80) + ht_cap(2) + ht_op(36, 1, True) + vht_cap(2)
     + vht_op(1, 42) + he_cap(2) + ie(70, b"\0" * 5) + ie(127, b"\0\0\x08") + ie(11, b"\x08\x00\x3c\0\0"), (220, 260)),
    ("ac:8b:a9:10:00:02", "ACME-Corp", 2437, rsn([8, 2], caps=0x80) + ie(3, b"\x06") + ht_cap(2) + ht_op(6), (220, 260)),
    ("ae:8b:a9:10:00:03", "ACME-Invites", 2437, ie(3, b"\x06") + ht_cap(2) + ht_op(6), (220, 260)),
    ("ac:8b:a9:20:00:01", "ACME-Corp", 5180, rsn([8, 2], caps=0x80) + ht_cap(2) + ht_op(36, 1, True) + vht_cap(2)
     + vht_op(1, 42) + he_cap(2) + ie(11, b"\x16\x00\xd0\0\0"), (1150, 260)),
    ("ac:8b:a9:20:00:02", "ACME-Corp", 2422, rsn([2], pairwise=(2, 4)) + ie(3, b"\x03") + ht_cap(2) + ht_op(3, 1, True)
     + ie(221, b"\x00\x50\xf2\x04\x10"), (1150, 260)),
    ("ac:8b:a9:30:00:01", "ACME-Corp", 6135, rsn([8], caps=0xC0) + he_cap(2) + he_op_6ghz(37, 2, 39), (650, 300)),
]
NEIGH = [("f4:ca:e5:00:00:01", "Livebox-1A2B", 2412, -58), ("00:1e:58:00:00:02", "Bbox-99", 2462, -71),
         ("3c:a6:2f:00:00:03", "FreeWifi_secure", 5500, -80), ("02:00:00:00:00:04", "", 5745, -77),
         ("10:13:31:00:00:05", "SFR_7F30", 2427, -66)]


def make_bss(pos, jitter=2.0, rogue=False):
    out = []
    for mac, ssid, freq, blob, (ax, ay) in APS:
        d = math.hypot(pos[0] - ax, pos[1] - ay) * (40 / W)
        rssi = int(-38 - 30 * math.log10(max(d, 1)) - (6 if freq < 3000 else 0) * -1 - (8 if freq > 5900 else 0)
                   + random.uniform(-jitter, jitter))
        if pos[1] > 520 and ay < 520:
            rssi -= 8                                    # mur
        if rssi < -92:
            continue
        out.append(Bss(bssid=mac, ssid=ssid, rssi=rssi, link_quality=70, freq_mhz=freq,
                       capability=0x0011 if "Invites" not in ssid else 0x0001,
                       ie_blob=ie(0, ssid.encode()) + blob))
    for mac, ssid, freq, rssi in NEIGH:
        out.append(Bss(bssid=mac, ssid=ssid, rssi=rssi + random.randint(-3, 3), link_quality=40, freq_mhz=freq,
                       ie_blob=ie(0, ssid.encode()) + rsn([2]) + ht_cap(2) + ht_op(1)))
    if rogue:
        out.append(Bss(bssid="66:55:44:33:22:11", ssid="ACME-Corp", rssi=-55, link_quality=60, freq_mhz=5200,
                       ie_blob=ie(0, b"ACME-Corp") + rsn([2]) + ht_cap(1) + ht_op(40)))
    return out


# --------------------------------------------------------------- scans
for i in range(3):
    svc.scan_done.emit(ScanResult(time.time(), make_bss((600, 300)), "scan", {"duration": 3.9}))
    pump(0.3)
win.show_page(0)
shot("01_tableau_de_bord")
win.show_page(1)
shot("02_clients")
win.show_page(2)
win.scan.table.selectRow(0)
shot("03_scan")
win.show_page(3)
shot("04_canaux_5")
win.channels.band.setCurrentIndex(0)
shot("05_canaux_24")

# --------------------------------------------------------------- relevé
win.show_page(4)
win.survey.ping_cb.setChecked(False)
pump(0.5)
for y in range(80, 760, 110):
    for x in range(80, 1360, 150):
        svc.scan_done.emit(ScanResult(time.time(), make_bss((x, y)), ("point", fid, float(x), float(y))))
        pump(0.05)
pump(0.5)
win.survey.estimate()
shot("06_releve_signal")
for k in ("snr", "best_ap", "count", "rate"):
    win.survey.kind.setCurrentIndex(win.survey.kind.findData(k))
    shot(f"07_releve_{k}")
win.survey.kind.setCurrentIndex(0)

# --------------------------------------------------------- supervision
ctx.db.set_authorized(sid, "ac:8b:a9:10:00:01", "ACME-Corp")
for mac, ssid, *_ in APS:
    ctx.db.set_authorized(sid, mac, ssid)
win.show_page(5)
win.supervision.start()
pump(1)
svc.scan_done.emit(ScanResult(time.time(), make_bss((600, 300)), "auto"))
pump(0.5)
svc.scan_done.emit(ScanResult(time.time(), make_bss((600, 300), rogue=True), "auto"))
pump(0.5)
gone = [b for b in make_bss((600, 300)) if b.bssid != "ac:8b:a9:20:00:01"]
for _ in range(3):
    svc.scan_done.emit(ScanResult(time.time(), gone, "auto"))
    pump(0.3)
pump(3)
shot("08_supervision")
win.supervision.stop()

# ---------------------------------------------------------------- tests
win.show_page(6)
win.tests.ping_target.setText("127.0.0.1")
win.tests.ping_count.setValue(10)
win.tests.run_ping()
pump(4)
shot("09_tests")

# -------------------------------------------------------------- rapport
win.show_page(7)
win.report.analyse()
win.report.table.selectRow(0)
shot("10_rapport")
from wifiaudit.report.build import export_excel, export_pdf, gather  # noqa: E402
data = gather(db, settings, aid, with_images=True)
export_pdf(data, OUT / "rapport.pdf", settings.section("thresholds"))
export_excel(gather(db, settings, aid, with_images=False), OUT / "rapport.xlsx")
print("constats :", [(f.severity, f.title) for f in data.findings])
print("score :", data.score, "| couverture :", [fr.coverage for fr in data.floors])

win.toggle_theme()
win.show_page(4)
shot("11_releve_clair")
win.show_page(2)
shot("12_scan_clair")
win.toggle_theme()
print("événements :", [e.message for e in events][:12])
win.close()
pump(0.5)
print("OK")
