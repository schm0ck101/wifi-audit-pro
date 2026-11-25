"""Page « Paramètres »."""
from __future__ import annotations

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
                               QGridLayout, QGroupBox, QHBoxLayout, QLineEdit, QMessageBox, QPushButton,
                               QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from ..config import APP_NAME, APP_VERSION, AUTHOR, AUTHOR_URL, data_dir
from .common import muted, page_title


def _spin(lo, hi, val, suffix="", dec=None, step=1):
    s = QDoubleSpinBox() if dec is not None else QSpinBox()
    if dec is not None:
        s.setDecimals(dec)
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setValue(val)
    if suffix:
        s.setSuffix(suffix)
    return s


class SettingsPage(QWidget):
    applied = Signal()

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.s = ctx.settings
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 16)
        outer.addWidget(page_title("Paramètres"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        grid = QGridLayout(body)
        grid.setSpacing(14)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        g = self.s.get
        self.w = {}

        def group(title, fields, r, c):
            box = QGroupBox(title)
            f = QFormLayout(box)
            for key, label, widget in fields:
                self.w[key] = widget
                f.addRow(label, widget)
            grid.addWidget(box, r, c)

        group("Seuils d'analyse", [
            ("thresholds.rssi_target", "Signal objectif (voix/vidéo)", _spin(-90, -30, g("thresholds.rssi_target"), " dBm")),
            ("thresholds.rssi_min", "Signal minimum (données)", _spin(-95, -40, g("thresholds.rssi_min"), " dBm")),
            ("thresholds.noise_floor", "Bruit de fond supposé", _spin(-110, -70, g("thresholds.noise_floor"), " dBm")),
            ("thresholds.snr_target", "SNR objectif", _spin(5, 50, g("thresholds.snr_target"), " dB")),
            ("thresholds.util_warn", "Occupation canal : alerte", _spin(10, 100, g("thresholds.util_warn"), " %")),
            ("thresholds.util_crit", "Occupation canal : critique", _spin(10, 100, g("thresholds.util_crit"), " %")),
            ("thresholds.max_ssids_per_radio", "SSID max. par radio", _spin(1, 16, g("thresholds.max_ssids_per_radio"))),
            ("thresholds.cochannel_max", "Bornes co‑canal tolérées", _spin(1, 16, g("thresholds.cochannel_max"))),
        ], 0, 0)
        theme_cb = QComboBox()
        theme_cb.addItem("Sombre", "dark")
        theme_cb.addItem("Clair", "light")
        theme_cb.setCurrentIndex(0 if g("ui.theme") == "dark" else 1)
        notif = QCheckBox("Notifications dans la fenêtre")
        notif.setChecked(g("ui.notifications"))
        tray = QCheckBox("Notifications Windows (fenêtre en arrière‑plan)")
        tray.setChecked(g("ui.tray_notifications"))
        group("Relevé, scan et interface", [
            ("heatmap.radius_m", "Rayon d'interpolation", _spin(1, 50, g("heatmap.radius_m"), " m", 1, 0.5)),
            ("heatmap.power", "Exposant IDW", _spin(1, 4, g("heatmap.power"), "", 1, 0.5)),
            ("heatmap.ping_target", "Cible du ping aux points", QLineEdit(g("heatmap.ping_target"))),
            ("scan.max_age", "Âge max. d'un résultat de scan", _spin(5, 120, g("scan.max_age"), " s")),
            ("ui.theme", "Thème", theme_cb),
            ("ui.notifications", "", notif),
            ("ui.tray_notifications", "", tray),
        ], 0, 1)
        self.w["heatmap.ping_target"].setPlaceholderText("vide = passerelle")
        group("Supervision", [
            ("supervision.missing_scans", "Borne disparue après", _spin(1, 20, g("supervision.missing_scans"), " scans")),
            ("supervision.alert_rssi", "Alerte signal faible sous", _spin(-95, -40, g("supervision.alert_rssi"), " dBm")),
            ("supervision.ping_interval", "Intervalle de ping", _spin(0.2, 10, g("supervision.ping_interval"), " s", 1, 0.5)),
        ], 1, 0)
        group("Serveur iperf3 par défaut", [
            ("iperf.server", "Serveur", QLineEdit(g("iperf.server"))),
            ("iperf.port", "Port", _spin(1, 65535, g("iperf.port"))),
            ("iperf.seconds", "Durée", _spin(3, 120, g("iperf.seconds"), " s")),
            ("iperf.streams", "Flux parallèles", _spin(1, 16, g("iperf.streams"))),
        ], 1, 1)
        logo_row = QWidget()
        lr = QHBoxLayout(logo_row)
        lr.setContentsMargins(0, 0, 0, 0)
        logo = QLineEdit(g("report.logo"))
        logo.setPlaceholderText(f"(logo {AUTHOR} par défaut)")
        pick = QPushButton("Choisir…")
        pick.clicked.connect(lambda: self._pick_logo(logo))
        lr.addWidget(logo, 1)
        lr.addWidget(pick)
        self.w["report.logo"] = logo
        box = QGroupBox("Identité sur les rapports")
        f = QFormLayout(box)
        for key, label in (("report.company", "Société"), ("report.auditor", "Auditeur"),
                           ("report.phone", "Téléphone"), ("report.email", "E‑mail")):
            self.w[key] = QLineEdit(g(key))
            f.addRow(label, self.w[key])
        f.addRow("Logo", logo_row)
        grid.addWidget(box, 2, 0, 1, 2)
        grid.addWidget(muted(f"{APP_NAME} {APP_VERSION} — créé par {AUTHOR} ({AUTHOR_URL}) — données : {data_dir()}"), 3, 0, 1, 2)

        bar = QHBoxLayout()
        d = QPushButton("Ouvrir le dossier des données")
        d.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_dir()))))
        bar.addWidget(d)
        bar.addStretch(1)
        save = QPushButton("Enregistrer")
        save.setObjectName("Primary")
        save.clicked.connect(self.save)
        bar.addWidget(save)
        outer.addLayout(bar)

    def _pick_logo(self, edit):
        p, _ = QFileDialog.getOpenFileName(self, "Logo", "", "Images (*.png *.jpg *.jpeg *.bmp *.svg)")
        if p:
            edit.setText(p)

    def save(self):
        if self.w["thresholds.rssi_min"].value() >= self.w["thresholds.rssi_target"].value():
            QMessageBox.warning(self, "Seuils", "Le signal minimum doit être inférieur au signal objectif.")
            return
        for key, w in self.w.items():
            if isinstance(w, (QSpinBox, QDoubleSpinBox)):
                v = w.value()
            elif isinstance(w, QComboBox):
                v = w.currentData()
            elif isinstance(w, QCheckBox):
                v = w.isChecked()
            else:
                v = w.text().strip()
            self.s.set(key, v)
        try:
            self.s.save()
        except OSError as exc:
            QMessageBox.warning(self, "Paramètres", str(exc))
            return
        self.applied.emit()
        QMessageBox.information(self, "Paramètres", "Paramètres enregistrés.")
