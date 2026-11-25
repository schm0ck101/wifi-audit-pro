"""Page « Relevé » : mesures sur plan (un clic = un scan à l'endroit où l'on
se trouve, avec ping/débit en option), cartes de couverture, positions des
bornes."""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGraphicsEllipseItem,
                               QGraphicsItem, QGraphicsPixmapItem, QGraphicsSimpleTextItem, QHBoxLayout,
                               QLabel, QMessageBox, QPushButton, QSlider, QVBoxLayout, QWidget)

from ..audit import heatmap as hm
from ..core.pinger import gateway_ip, ping_series
from ..events import INFO, Event
from ..wlan.model import BANDS, radio_key
from . import theme
from .common import muted, page_title, panel
from .heatmap_render import colorize, composite, legend_image
from .plan_view import PlanView, png_to_image


def build_apinfo(aps) -> dict[str, hm.ApInfo]:
    return {b.bssid: hm.ApInfo(b.bssid, b.ssid, b.channel, b.band, radio_key(b.bssid), b.max_rate)
            for b in aps}


def radio_labels(aps) -> dict[str, str]:
    """Libellé lisible par radio : SSID principal + fin d'adresse MAC."""
    out: dict[str, str] = {}
    for b in sorted(aps, key=lambda x: -x.rssi):
        k = radio_key(b.bssid)
        if k not in out:
            out[k] = f"{b.ssid or 'masqué'} ·{k[-5:]} ({b.band}, ch {b.channel})"
    return out


class _Relay(QObject):
    tests_done = Signal(int, dict)


class ApMarker(QGraphicsEllipseItem):
    def __init__(self, radio, label, x, y, on_moved):
        super().__init__(-11, -11, 22, 22)
        self.radio = radio
        self.on_moved = on_moved
        self.setPos(x, y)
        self.setBrush(QBrush(QColor("#1f6fd1")))
        pen = QPen(QColor("#ffffff"), 2)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setZValue(30)
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        self.setToolTip(label)
        t = QGraphicsSimpleTextItem(label, self)
        t.setBrush(QColor("#0b2545"))
        f = QFont("Segoe UI", 9)
        f.setBold(True)
        t.setFont(f)
        t.setPos(14, -9)
        self.text = t

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        if self.flags() & QGraphicsItem.ItemIsMovable:
            self.on_moved(self.radio, self.pos().x(), self.pos().y())


class SurveyPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.floor = None
        self.plan_img = None
        self.points: list[dict] = []
        self.pending = None                 # (x, y, marqueur)
        self.overlay: QGraphicsPixmapItem | None = None
        self.items = []
        self.relay = _Relay()
        self.relay.tests_done.connect(self._tests_done)
        self._gw = None

        root = QHBoxLayout(self)
        root.setContentsMargins(24, 20, 16, 16)
        left = QVBoxLayout()
        head = QHBoxLayout()
        head.addWidget(page_title("Relevé sur plan"))
        head.addStretch(1)
        self.ctx_lbl = muted("")
        head.addWidget(self.ctx_lbl)
        left.addLayout(head)
        self.hint = muted("")
        left.addWidget(self.hint)
        self.view = PlanView()
        self.view.clicked.connect(self._clicked)
        left.addWidget(self.view, 1)
        root.addLayout(left, 1)

        side = QWidget()
        side.setFixedWidth(340)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(8, 0, 0, 0)
        p1, l1 = panel()
        form = QFormLayout()
        self.floor_cb = QComboBox()
        self.floor_cb.currentIndexChanged.connect(self._floor_changed)
        self.mode = QComboBox()
        self.mode.addItem("Relevé : clic = mesure", "survey")
        self.mode.addItem("Positionner les bornes", "aps")
        self.mode.addItem("Consultation / suppression", "view")
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.kind = QComboBox()
        for k, label in hm.KINDS.items():
            self.kind.addItem(label, k)
        self.kind.currentIndexChanged.connect(self.render)
        self.net = QComboBox()
        self.net.currentIndexChanged.connect(self.render)
        self.band = QComboBox()
        self.band.addItem("Toutes les bandes", None)
        for b in BANDS:
            self.band.addItem(b, b)
        self.band.currentIndexChanged.connect(self.render)
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(10, 95)
        self.opacity.setValue(int(ctx.settings.get("heatmap.opacity", 0.6) * 100))
        self.opacity.valueChanged.connect(self.render)
        form.addRow("Étage", self.floor_cb)
        form.addRow("Mode", self.mode)
        form.addRow("Carte", self.kind)
        form.addRow("Réseaux", self.net)
        form.addRow("Bande", self.band)
        form.addRow("Opacité", self.opacity)
        l1.addLayout(form)
        self.ping_cb = QCheckBox("Ping de la passerelle à chaque point")
        self.ping_cb.setChecked(ctx.settings.get("heatmap.ping_at_point", True))
        self.iperf_cb = QCheckBox("Test de débit iperf3 à chaque point")
        self.iperf_cb.setChecked(ctx.settings.get("heatmap.iperf_at_point", False))
        l1.addWidget(self.ping_cb)
        l1.addWidget(self.iperf_cb)
        sl.addWidget(p1)

        p2, l2 = panel()
        self.legend = QLabel()
        l2.addWidget(self.legend)
        self.stats = QLabel("")
        self.stats.setWordWrap(True)
        l2.addWidget(self.stats)
        sl.addWidget(p2)

        p3, l3 = panel()
        self.last = QLabel("Aucune mesure")
        self.last.setWordWrap(True)
        l3.addWidget(self.last)
        sl.addWidget(p3)

        for txt, fn in (("Annuler le dernier point", self.undo), ("Estimer la position des bornes", self.estimate),
                        ("Ajuster la vue", self.view.fit), ("Exporter l'image…", self.export_png)):
            b = QPushButton(txt)
            b.clicked.connect(fn)
            sl.addWidget(b)
        sl.addStretch(1)
        root.addWidget(side)

        self._blink = QTimer(self)
        self._blink.timeout.connect(self._blink_pending)
        ctx.context_changed.connect(self.reload)
        ctx.service.scan_done.connect(self._scan_done)
        ctx.service.scan_failed.connect(self._scan_failed)
        self.reload()

    # ---------------------------------------------------------- contexte
    def reload(self):
        self.ctx_lbl.setText(self.ctx.label())
        self.floor_cb.blockSignals(True)
        self.floor_cb.clear()
        if self.ctx.site_id:
            for f in self.ctx.db.floors(self.ctx.site_id):
                self.floor_cb.addItem(f["name"], f["id"])
        self.floor_cb.blockSignals(False)
        self.net.clear()            # nouveau contexte : « Réseaux du client » par défaut
        self._fill_networks()
        self._floor_changed()

    def _fill_networks(self):
        cur = self.net.currentData()
        self.net.blockSignals(True)
        self.net.clear()
        self.net.addItem("Réseaux du client", "own")
        self.net.addItem("Tous les réseaux", "all")
        ssids = sorted({b.ssid for b in self.ctx.aps.values() if b.ssid},
                       key=lambda s: (s not in self.ctx.own_ssids, s.lower()))
        for s in ssids:
            self.net.addItem(f"SSID : {s}", ("ssid", s))
        i = self.net.findData(cur)
        self.net.setCurrentIndex(i if i >= 0 else (0 if self.ctx.own_ssids else 1))
        self.net.blockSignals(False)

    def _floor_changed(self):
        fid = self.floor_cb.currentData()
        self.floor = None
        self.plan_img = None
        self.overlay = None
        self.items = []
        if not fid:
            self.view.set_image(None)
            self.hint.setText("Importez un plan d'étage dans « Clients & sites » pour réaliser un relevé."
                              if self.ctx.site_id else "Sélectionnez un audit dans « Clients & sites ».")
            self.render()
            return
        self.floor = next(f for f in self.ctx.db.floors(self.ctx.site_id) if f["id"] == fid)
        self.plan_img = png_to_image(self.ctx.db.floor_image(fid))
        self.view.set_image(self.plan_img)
        self._mode_changed()
        self.load_points()

    def _mode_changed(self):
        m = self.mode.currentData()
        txt = {"survey": "Placez‑vous à un endroit, puis cliquez sur votre position sur le plan : un scan "
                         "est réalisé (≈ 4 s, restez immobile). Molette = zoom, clic droit = déplacer.",
               "aps": "Faites glisser les icônes des bornes à leur emplacement réel.",
               "view": "Cliquez sur un point de mesure pour le supprimer."}[m]
        if self.floor and not self.floor["scale"]:
            txt += "  ⚠ Plan non étalonné : rayon d'interpolation approximatif."
        self.hint.setText(txt)
        for it in self.items:
            if isinstance(it, ApMarker):
                it.setFlag(QGraphicsItem.ItemIsMovable, m == "aps")

    def load_points(self):
        self.points = (self.ctx.db.survey_points(self.ctx.audit_id, self.floor["id"])
                       if self.ctx.audit_id and self.floor else [])
        self.render()

    # ------------------------------------------------------------ mesures
    def _clicked(self, x, y):
        m = self.mode.currentData()
        if m == "view":
            self._delete_near(x, y)
            return
        if m != "survey":
            return
        if not self.ctx.require_audit(self):
            return
        if self.pending:
            self.last.setText("Mesure en cours, patientez…")
            return
        if not self.ctx.service.available:
            QMessageBox.warning(self, "Wi‑Fi", self.ctx.service.message or "Wi‑Fi indisponible.")
            return
        mk = self.view.scene().addEllipse(-9, -9, 18, 18, QPen(QColor("#ffffff"), 2), QBrush(QColor("#ff8c1a")))
        mk.setPos(x, y)
        mk.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        mk.setZValue(40)
        self.pending = (x, y, mk, time.time())
        self._blink.start(300)
        self.last.setText("Scan en cours… restez immobile.")
        self.ctx.service.request_scan(("point", self.floor["id"], x, y))

    def _blink_pending(self):
        if self.pending:
            mk = self.pending[2]
            mk.setVisible(not mk.isVisible())
            self.last.setText(f"Scan en cours… {time.time() - self.pending[3]:.0f} s")

    def _scan_done(self, res):
        if not (isinstance(res.tag, tuple) and res.tag[0] == "point"):
            return
        _k, fid, x, y = res.tag
        self._clear_pending()
        if not self.ctx.audit_id:
            return
        sid = self.ctx.db.save_scan(self.ctx.audit_id, res.bss, kind="point", floor_id=fid, x=x, y=y, ts=res.ts)
        self.ctx.ingest(res.bss)
        self._fill_networks()
        own = [b for b in res.bss if b.ssid in self.ctx.own_ssids]
        best = max(own or res.bss, key=lambda b: b.rssi, default=None)
        self.last.setText(f"Point {len(self.points) + 1} : {len(res.bss)} réseaux"
                          + (f", meilleur {'du client' if own else ''} : {best.ssid} {best.rssi} dBm" if best else ""))
        if self.ping_cb.isChecked() or self.iperf_cb.isChecked():
            self.last.setText(self.last.text() + "\nTests en cours…")
            threading.Thread(target=self._run_tests, args=(sid,), daemon=True).start()
        self.load_points()

    def _scan_failed(self, msg, location, tag):
        if isinstance(tag, tuple) and tag[0] == "point":
            self._clear_pending()
            self.last.setText(f"Échec : {msg}")
            if location:
                QMessageBox.warning(self, "Localisation", msg)

    def _clear_pending(self):
        self._blink.stop()
        if self.pending:
            try:
                self.view.scene().removeItem(self.pending[2])
            except RuntimeError:
                pass
        self.pending = None

    def _run_tests(self, scan_id):
        out = {}
        try:
            if self.ping_cb.isChecked():
                target = self.ctx.settings.get("heatmap.ping_target", "") or self._gw or gateway_ip()
                self._gw = self._gw or target
                if target:
                    r = ping_series(target, count=8, interval=0.15)
                    self.ctx.db.add_test(self.ctx.audit_id, "ping", r, scan_id)
                    out["ping"] = r
            if self.iperf_cb.isChecked():
                cfg = self.ctx.settings.section("iperf")
                if cfg.get("server"):
                    from ..net.iperf3 import IperfError, run
                    try:
                        r = run(cfg["server"], int(cfg.get("port", 5201)), seconds=5, reverse=True,
                                streams=int(cfg.get("streams", 1)))
                        d = r.to_dict()
                        self.ctx.db.add_test(self.ctx.audit_id, "iperf", d, scan_id)
                        out["iperf"] = d
                    except IperfError as exc:
                        out["iperf_error"] = str(exc)
                else:
                    out["iperf_error"] = "serveur iperf3 non configuré (Paramètres)"
        except Exception as exc:
            out["error"] = str(exc)
        self.relay.tests_done.emit(scan_id, out)

    def _tests_done(self, _sid, out):
        parts = []
        if "ping" in out:
            r = out["ping"]
            parts.append(f"ping {r['target']} : " + (f"{r['avg']:.1f} ms, {r['loss']:.0f} % pertes"
                                                    if r["avg"] is not None else "aucune réponse"))
        if "iperf" in out:
            parts.append(f"débit : {out['iperf']['mbps']:.0f} Mb/s")
        if "iperf_error" in out:
            parts.append(f"iperf3 : {out['iperf_error']}")
        self.last.setText(self.last.text().replace("\nTests en cours…", "") + "\n" + " · ".join(parts))
        self.load_points()

    def undo(self):
        if self.points and QMessageBox.question(self, "Relevé", "Supprimer le dernier point de mesure ?") \
                == QMessageBox.Yes:
            self.ctx.db.delete_scan(self.points[-1]["id"])
            self.load_points()

    def _delete_near(self, x, y):
        if not self.points:
            return
        tol = 15 / max(self.view.transform().m11(), 0.01)
        near = min(self.points, key=lambda p: (p["x"] - x) ** 2 + (p["y"] - y) ** 2)
        if (near["x"] - x) ** 2 + (near["y"] - y) ** 2 <= tol ** 2:
            if QMessageBox.question(self, "Relevé", "Supprimer ce point de mesure ?") == QMessageBox.Yes:
                self.ctx.db.delete_scan(near["id"])
                self.load_points()

    # ------------------------------------------------------------- bornes
    def _aps(self):
        return self.ctx.db.audit_aps(self.ctx.audit_id) if self.ctx.audit_id else list(self.ctx.aps.values())

    def estimate(self):
        if not (self.floor and self.points and self.ctx.site_id):
            return
        aps = self._aps()
        own = self.ctx.own_ssids
        info = build_apinfo([b for b in aps if not own or b.ssid in own])
        pts = [hm.SurveyPoint(p["x"], p["y"], p["readings"]) for p in self.points]
        pos = hm.estimate_ap_positions(pts, info)
        existing = self.ctx.db.ap_positions(self.ctx.site_id)
        labels = radio_labels(aps)
        n = 0
        for radio, (x, y) in pos.items():
            if radio in existing and existing[radio]["manual"]:
                continue
            self.ctx.db.set_ap_position(self.ctx.site_id, radio, self.floor["id"], x, y,
                                        labels.get(radio, radio), manual=False)
            n += 1
        self.last.setText(f"{n} position(s) de borne estimée(s). Ajustez‑les en mode « Positionner les bornes ».")
        self.render()

    def _ap_moved(self, radio, x, y):
        pos = self.ctx.db.ap_positions(self.ctx.site_id).get(radio, {})
        self.ctx.db.set_ap_position(self.ctx.site_id, radio, self.floor["id"], x, y, pos.get("label", radio), True)

    # ------------------------------------------------------------- rendu
    def _selection(self):
        sel = self.net.currentData()
        aps = self._aps()
        if sel == "own":
            own = self.ctx.own_ssids
            return {b.bssid for b in aps if b.ssid in own} if own else None, aps
        if sel == "all" or sel is None:
            return None, aps
        return {b.bssid for b in aps if b.ssid == sel[1]}, aps

    def compute(self):
        if not (self.floor and self.points):
            return None
        selected, aps = self._selection()
        info = build_apinfo(aps)
        pts = []
        for p in self.points:
            sp = hm.SurveyPoint(p["x"], p["y"], p["readings"], scan_id=p["id"])
            for kind, r in p.get("tests", []):
                if kind == "ping":
                    sp.ping = r
                elif kind == "iperf":
                    sp.throughput = {"down": r.get("mbps")}
            pts.append(sp)
        th = self.ctx.settings.section("thresholds")
        hc = self.ctx.settings.section("heatmap")
        return hm.compute(pts, self.kind.currentData(), info, selected, self.floor["width"], self.floor["height"],
                          self.floor["scale"], float(hc.get("radius_m", 8)), float(th["noise_floor"]),
                          float(th["rssi_min"]), self.band.currentData(), float(hc.get("power", 2))), info

    def render(self):
        th = self.ctx.settings.section("thresholds")
        scene = self.view.scene()
        for it in self.items:
            try:
                scene.removeItem(it)
            except RuntimeError:
                pass
        self.items = []
        kind = self.kind.currentData()
        if not self.floor:
            self.legend.clear()
            self.stats.setText("")
            return
        out = self.compute()
        res = out[0] if out else None
        labels = radio_labels(self._aps())
        if res is not None:
            img = colorize(res, th, self.opacity.value() / 100)
            it = scene.addPixmap(QPixmap.fromImage(img))
            it.setTransformationMode(Qt.SmoothTransformation)
            it.setScale(res.cell_px)
            it.setZValue(0)
            self.items.append(it)
            cats = [labels.get(c, c) for c in res.categories]
            self.legend.setPixmap(QPixmap.fromImage(legend_image(kind, th, cats, 310,
                                                                 64 if kind != "best_ap" else 22 + 18 * ((len(cats) + 1) // 2),
                                                                 theme.palette().text)))
            g = res.grid
            if kind == "signal":
                self.stats.setText(
                    f"<b>{len(self.points)}</b> points · surface couverte ≥ {th['rssi_target']} dBm : "
                    f"<b>{100 - hm.area_fraction(g, lambda v: v < th['rssi_target']):.0f} %</b> · ≥ {th['rssi_min']} dBm : "
                    f"<b>{100 - hm.area_fraction(g, lambda v: v < th['rssi_min']):.0f} %</b>")
            else:
                import numpy as np
                v = g[~np.isnan(g)]
                self.stats.setText(f"<b>{len(self.points)}</b> points · min {v.min():.0f} · moy {v.mean():.0f} · "
                                   f"max {v.max():.0f}" if v.size else f"{len(self.points)} points · pas de données")
        else:
            self.legend.clear()
            self.stats.setText("Aucun point de mesure sur cet étage." if self.floor else "")
        # points
        pen = QPen(QColor("#202020"), 1.5)
        pen.setCosmetic(True)
        for p in self.points:
            e = scene.addEllipse(-5, -5, 10, 10, pen, QBrush(QColor("#ffffff")))
            e.setPos(p["x"], p["y"])
            e.setFlag(QGraphicsItem.ItemIgnoresTransformations)
            e.setZValue(20)
            best = max(p["readings"].values(), default=None)
            e.setToolTip(f"{time.strftime('%H:%M:%S', time.localtime(p['ts']))} — {len(p['readings'])} réseaux"
                         + (f", meilleur {best} dBm" if best is not None else ""))
            self.items.append(e)
        # bornes
        if self.ctx.site_id:
            for radio, pos in self.ctx.db.ap_positions(self.ctx.site_id, self.floor["id"]).items():
                mk = ApMarker(radio, pos["label"] or labels.get(radio, radio), pos["x"], pos["y"], self._ap_moved)
                mk.setFlag(QGraphicsItem.ItemIsMovable, self.mode.currentData() == "aps")
                scene.addItem(mk)
                self.items.append(mk)

    def export_png(self):
        if not self.plan_img:
            return
        out = self.compute()
        path, _ = QFileDialog.getSaveFileName(self, "Exporter la carte", f"carte_{self.floor['name']}.png",
                                              "Images PNG (*.png)")
        if not path:
            return
        th = self.ctx.settings.section("thresholds")
        labels = radio_labels(self._aps())
        aps = [(p["x"], p["y"], p["label"] or labels.get(r, r))
               for r, p in self.ctx.db.ap_positions(self.ctx.site_id, self.floor["id"]).items()]
        img = composite(self.plan_img, out[0] if out else None, th, self.opacity.value() / 100,
                        [(p["x"], p["y"]) for p in self.points], aps)
        img.save(path)
        QMessageBox.information(self, "Export", f"Carte enregistrée :\n{path}")

    def showEvent(self, e):
        super().showEvent(e)
        self._fill_networks()
        QTimer.singleShot(0, self.view.fit)
