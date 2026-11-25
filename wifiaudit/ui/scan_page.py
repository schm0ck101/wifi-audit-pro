"""Page « Scan » : inventaire des points d'accès, détail, historique du signal,
gestion des bornes autorisées."""
from __future__ import annotations

import time
from collections import defaultdict, deque

import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QSplitter,
                               QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget)

from ..wlan.model import BANDS, Bss
from ..wlan.oui import vendor
from . import theme
from .common import NumItem, muted, page_title, rssi_color

COLS = ["SSID", "BSSID", "Fabricant", "Signal", "Canal", "Bande", "Largeur", "Norme", "Sécurité",
        "Débit max", "Clients", "Occupation", "k/v/r", "Statut", "Vu"]


class LocationBanner(QFrame):
    """Bandeau affiché quand Windows refuse l'accès (autorisation Localisation)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Panel")
        lay = QHBoxLayout(self)
        self.msg = QLabel()
        self.msg.setWordWrap(True)
        lay.addWidget(self.msg, 1)
        self.btn = QPushButton("Ouvrir les paramètres de localisation")
        self.btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("ms-settings:privacy-location")))
        lay.addWidget(self.btn)
        self.hide()

    def show_msg(self, text: str, location: bool):
        p = theme.palette()
        self.msg.setText(f"<b style='color:{p.crit}'>Wi‑Fi indisponible</b> — {text}"
                         + ("<br>Windows 11 exige l'autorisation <b>Localisation</b> pour lister les réseaux : "
                            "activez « Autoriser les applications de bureau à accéder à votre position »."
                            if location else ""))
        self.btn.setVisible(location)
        self.show()


class ScanPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.history: dict[str, deque] = defaultdict(lambda: deque(maxlen=720))
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        head = QHBoxLayout()
        head.addWidget(page_title("Scan des réseaux"))
        head.addStretch(1)
        self.ctx_lbl = muted("")
        head.addWidget(self.ctx_lbl)
        root.addLayout(head)
        self.banner = LocationBanner()
        root.addWidget(self.banner)

        bar = QHBoxLayout()
        self.scan_btn = QPushButton("Scanner maintenant")
        self.scan_btn.setObjectName("Primary")
        self.scan_btn.clicked.connect(self.scan_now)
        self.cont = QComboBox()
        self.cont.addItem("Scan manuel", 0)
        for s in (5, 10, 15, 30, 60):
            self.cont.addItem(f"Scan continu : {s} s", s)
        self.cont.currentIndexChanged.connect(lambda: ctx.service.set_continuous(self.cont.currentData()))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filtrer (SSID, BSSID, fabricant…)")
        self.search.textChanged.connect(self.refresh)
        self.band = QComboBox()
        self.band.addItem("Toutes les bandes", None)
        for b in BANDS:
            self.band.addItem(b, b)
        self.band.currentIndexChanged.connect(self.refresh)
        self.own_only = QCheckBox("Réseaux du client seulement")
        self.own_only.toggled.connect(self.refresh)
        self.status = muted("")
        for w in (self.scan_btn, self.cont, self.search, self.band, self.own_only):
            bar.addWidget(w)
        bar.addWidget(self.status, 1)
        root.addLayout(bar)

        split = QSplitter(Qt.Vertical)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(3, Qt.DescendingOrder)
        self.table.horizontalHeader().setStretchLastSection(True)
        for c, w in enumerate((170, 130, 150, 72, 55, 65, 70, 145, 165, 80, 60, 85, 50, 80, 50)):
            self.table.setColumnWidth(c, w)
        self.table.itemSelectionChanged.connect(self._selected)
        split.addWidget(self.table)

        bottom = QWidget()
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        self.detail = QTextBrowser()
        self.detail.setMinimumWidth(380)
        bl.addWidget(self.detail, 2)
        right = QVBoxLayout()
        self.chart = pg.PlotWidget()
        self.chart.setMenuEnabled(False)
        self.chart.getPlotItem().setLabel("left", "dBm")
        self.chart.getPlotItem().setAxisItems({"bottom": pg.DateAxisItem()})
        self.chart.setYRange(-95, -25)
        self.curve = self.chart.plot(connect="finite")
        right.addWidget(self.chart, 1)
        ab = QHBoxLayout()
        self.auth_btn = QPushButton("Marquer comme autorisée(s)")
        self.auth_btn.clicked.connect(lambda: self._authorize(True))
        self.unauth_btn = QPushButton("Retirer l'autorisation")
        self.unauth_btn.clicked.connect(lambda: self._authorize(False))
        self.auth_all = QPushButton("Autoriser toutes les bornes du client")
        self.auth_all.clicked.connect(self._authorize_all)
        self.diag_btn = QPushButton("Exporter un diagnostic…")
        self.diag_btn.setToolTip("Capture brute des données Wi‑Fi de Windows (fichier JSON local)")
        self.diag_btn.clicked.connect(self.export_diag)
        for b in (self.auth_btn, self.unauth_btn, self.auth_all):
            ab.addWidget(b)
        ab.addStretch(1)
        ab.addWidget(self.diag_btn)
        right.addLayout(ab)
        bl.addLayout(right, 3)
        split.addWidget(bottom)
        split.setSizes([480, 260])
        root.addWidget(split, 1)

        ctx.aps_changed.connect(self.refresh)
        ctx.context_changed.connect(self._ctx)
        ctx.service.scan_done.connect(self._scan_done)
        ctx.service.scan_failed.connect(self._scan_failed)
        self._age_timer = QTimer(self)
        self._age_timer.timeout.connect(self._update_status)
        self._age_timer.start(1000)
        self._ctx()

    # --------------------------------------------------------- événements
    def _ctx(self):
        self.ctx_lbl.setText(self.ctx.label() + ("" if self.ctx.audit_id else " — scans non enregistrés"))
        self.refresh()

    def scan_now(self):
        self.ctx.service.request_scan("scan")
        self.status.setText("Scan en cours…")

    def _scan_done(self, res):
        self.banner.hide()
        for b in res.bss:
            self.history[b.bssid].append((res.ts, b.rssi))
        info = res.info
        self.status.setText(f"{len(res.bss)} points d'accès · scan {info.get('duration', '?')} s")
        self._selected()

    def _scan_failed(self, msg, location, _tag):
        self.banner.show_msg(msg, location)
        self.status.setText("Échec du scan")

    def _update_status(self):
        if self.ctx.service.busy:
            self.status.setText("Scan en cours…")

    # -------------------------------------------------------------- table
    def _status(self, b: Bss, auth: set, own: set) -> tuple[str, str | None]:
        p = theme.palette()
        if b.bssid.lower() in auth:
            return "Autorisée", p.ok
        if b.ssid and b.ssid in own:
            return ("Suspecte" if auth else "Client"), (p.crit if auth else p.accent)
        return "Voisin", None

    def refresh(self):
        th = self.ctx.settings.section("thresholds")
        own, auth = self.ctx.own_ssids, self.ctx.authorized
        txt = self.search.text().strip().lower()
        band = self.band.currentData()
        now = time.time()
        rows = []
        for b in self.ctx.aps.values():
            if band and b.band != band:
                continue
            if self.own_only.isChecked() and b.ssid not in own:
                continue
            ven = vendor(b.bssid, b.ies.vendor_ouis)
            if txt and txt not in f"{b.ssid} {b.bssid} {ven} {b.ies.ap_name or ''}".lower():
                continue
            rows.append((b, ven))
        sel = {self.table.item(i.row(), 1).text() for i in self.table.selectionModel().selectedRows()} \
            if self.table.selectionModel() else set()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        p = theme.palette()
        for r, (b, ven) in enumerate(rows):
            st, stc = self._status(b, auth, own)
            age = now - getattr(b, "last_seen", b.seen) if getattr(b, "last_seen", None) else now - b.seen
            vals = [
                QTableWidgetItem(b.ssid or "(masqué)"),
                QTableWidgetItem(b.bssid),
                QTableWidgetItem(ven),
                NumItem(f"{b.rssi} dBm", b.rssi),
                NumItem(str(b.channel or "?"), b.channel or 0),
                QTableWidgetItem(b.band or "?"),
                NumItem(f"{b.width} MHz", b.width),
                QTableWidgetItem(b.standard),
                QTableWidgetItem(b.security),
                NumItem(f"{b.max_rate:.0f} Mb/s", b.max_rate),
                NumItem("—" if b.ies.station_count is None else str(b.ies.station_count),
                        b.ies.station_count if b.ies.station_count is not None else -1),
                NumItem("—" if b.ies.channel_utilization is None else f"{b.ies.channel_utilization:.0f} %",
                        b.ies.channel_utilization if b.ies.channel_utilization is not None else -1),
                QTableWidgetItem(b.roaming_flags),
                QTableWidgetItem(st),
                NumItem(f"{age:.0f} s" if age < 120 else f"{age / 60:.0f} min", age),
            ]
            vals[3].setForeground(QColor(rssi_color(b.rssi, th)))
            if b.insecure:
                vals[8].setForeground(QColor(p.crit))
            if stc:
                vals[13].setForeground(QColor(stc))
            if b.ssid in own:
                f = vals[0].font()
                f.setBold(True)
                vals[0].setFont(f)
            if b.hidden:
                vals[0].setForeground(QColor(p.muted))
            for c, it in enumerate(vals):
                self.table.setItem(r, c, it)
        self.table.setSortingEnabled(True)
        if sel:
            for r in range(self.table.rowCount()):
                if self.table.item(r, 1).text() in sel:
                    self.table.selectRow(r)
        self.auth_btn.setEnabled(bool(self.ctx.site_id))
        self.unauth_btn.setEnabled(bool(self.ctx.site_id))
        self.auth_all.setEnabled(bool(self.ctx.site_id and own))

    def _selected_bss(self) -> list[Bss]:
        out = []
        for i in self.table.selectionModel().selectedRows():
            b = self.ctx.aps.get(self.table.item(i.row(), 1).text())
            if b:
                out.append(b)
        return out

    def _selected(self):
        sel = self._selected_bss()
        if not sel:
            self.detail.setHtml("<p style='color:gray'>Sélectionnez un point d'accès pour voir son détail.</p>")
            self.curve.setData([], [])
            return
        b = sel[0]
        self.detail.setHtml(detail_html(b, self.ctx.settings.section("thresholds")))
        hist = list(self.history.get(b.bssid, []))
        if not hist and self.ctx.audit_id:
            hist = self.ctx.db.rssi_history(self.ctx.audit_id, b.bssid, time.time() - 3600)
        if hist:
            xs, ys = zip(*hist)
            self.curve.setData(list(xs), list(ys), pen=pg.mkPen(theme.palette().accent, width=2),
                               symbol="o", symbolSize=4)
        else:
            self.curve.setData([], [])

    def apply_theme(self):
        p = theme.palette()
        self.chart.setBackground(p.chart_bg)
        self.refresh()

    # -------------------------------------------------------- autorisations
    def _authorize(self, on: bool):
        if not self.ctx.site_id:
            return
        for b in self._selected_bss():
            self.ctx.db.set_authorized(self.ctx.site_id, b.bssid, b.ssid, on)
        self.refresh()

    def _authorize_all(self):
        own = self.ctx.own_ssids
        aps = [b for b in self.ctx.aps.values() if b.ssid in own]
        if not aps:
            return
        if QMessageBox.question(self, "Bornes autorisées",
                                f"Déclarer les {len(aps)} BSSID diffusant les SSID du client comme autorisés ?\n"
                                "À faire uniquement après avoir vérifié qu'il s'agit bien de ses bornes.") \
                != QMessageBox.Yes:
            return
        for b in aps:
            self.ctx.db.set_authorized(self.ctx.site_id, b.bssid, b.ssid, True)
        self.refresh()

    def export_diag(self):
        path, _ = QFileDialog.getSaveFileName(self, "Exporter un diagnostic Wi‑Fi",
                                              f"diagnostic_wifi_{time.strftime('%Y%m%d_%H%M')}.json",
                                              "JSON (*.json)")
        if not path:
            return
        from ..core.diagnostic import export
        data = export(path)
        n = sum(len(s.get("bss", [])) for s in data.get("scans", []))
        errs = "\n".join(data.get("errors", []) + [s.get("error", "") for s in data.get("scans", []) if s.get("error")])
        QMessageBox.information(self, "Diagnostic", f"Fichier créé :\n{path}\n\n{n} points d'accès capturés."
                                + (f"\n\nErreurs :\n{errs}" if errs else ""))


def detail_html(b: Bss, th: dict) -> str:
    p = b.ies
    col = rssi_color(b.rssi, th)
    rows = [
        ("SSID", b.ssid or "<i>masqué</i>"), ("BSSID", b.bssid), ("Fabricant", vendor(b.bssid, p.vendor_ouis)),
        ("Nom de borne annoncé", p.ap_name or "—"),
        ("Signal", f"<b style='color:{col}'>{b.rssi} dBm</b> (qualité Windows {b.link_quality} %)"),
        ("Fréquence", f"{b.freq_mhz:.0f} MHz — {b.band}, canal {b.channel}"
         + (" (DFS)" if b.is_dfs else "")),
        ("Largeur", f"{b.width} MHz (canal central {b.center_channel})"),
        ("Norme", f"{b.standard}, {b.streams} flux spatiaux"),
        ("Débit PHY max théorique", f"{b.max_rate:.0f} Mb/s"),
        ("Sécurité", b.security + (f" — chiffrement {', '.join(b.ciphers)}" if b.ciphers else "")),
        ("Authentification", ", ".join(p.rsn_akm + p.wpa1_akm) or "—"),
        ("PMF (802.11w)", "obligatoire" if p.mfp_required else ("possible" if p.mfp_capable else "non")),
        ("WPS", "<b style='color:#ff4d4f'>activé</b>" if p.wps else "non"),
        ("Itinérance", f"802.11k {'oui' if p.dot11k else 'non'} · 802.11v {'oui' if p.dot11v else 'non'}"
         f" · 802.11r {'oui' if p.dot11r else 'non'}"),
        ("Charge (BSS Load)", "non annoncée" if p.station_count is None else
         f"{p.station_count} clients, canal occupé à {p.channel_utilization:.0f} %"),
        ("Pays", p.country or "—"),
        ("Débits de base", ", ".join(f"{r:g}" for r in sorted(set(p.basic_rates))) + " Mb/s" if p.basic_rates else "—"),
        ("Intervalle de balise", f"{b.beacon_period} TU"),
    ]
    if p.he_bss_color is not None:
        rows.append(("Couleur BSS (Wi‑Fi 6)", str(p.he_bss_color)))
    body = "".join(f"<tr><td style='color:gray;padding-right:12px'>{k}</td><td>{v}</td></tr>" for k, v in rows)
    return f"<table cellspacing=2>{body}</table>"
