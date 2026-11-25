"""Tableau de bord : état du Wi‑Fi, connexion, audit courant, accès rapides."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QVBoxLayout, QWidget)

from ..events import LEVEL_LABEL
from . import theme
from .common import StatCard, StatusDot, muted, page_title, panel, rssi_color
from .scan_page import LocationBanner


class Dashboard(QWidget):
    navigate = Signal(int)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        root.addWidget(page_title("Tableau de bord"))
        self.banner = LocationBanner()
        root.addWidget(self.banner)

        p, l = panel()
        row = QHBoxLayout()
        self.dot = StatusDot(16)
        row.addWidget(self.dot)
        self.wifi = QLabel("…")
        self.wifi.setObjectName("SectionTitle")
        row.addWidget(self.wifi, 1)
        row.addWidget(QLabel("Carte Wi‑Fi :"))
        self.iface = QComboBox()
        self.iface.setMinimumWidth(320)
        self.iface.activated.connect(self._iface_selected)
        row.addWidget(self.iface)
        l.addLayout(row)
        self.wifi_sub = muted("")
        l.addWidget(self.wifi_sub)
        root.addWidget(p)

        cards = QHBoxLayout()
        self.c_ssid = StatCard("Connecté à")
        self.c_sig = StatCard("Signal")
        self.c_rate = StatCard("Débit radio")
        self.c_aps = StatCard("Réseaux visibles")
        for c in (self.c_ssid, self.c_sig, self.c_rate, self.c_aps):
            cards.addWidget(c)
        root.addLayout(cards)

        p2, l2 = panel()
        l2.addWidget(QLabel("<b>Audit courant</b>"))
        self.ctx_lbl = QLabel("")
        self.ctx_lbl.setStyleSheet("font-size: 13pt;")
        l2.addWidget(self.ctx_lbl)
        self.ctx_sub = muted("")
        l2.addWidget(self.ctx_sub)
        grid = QGridLayout()
        actions = [("Clients && sites", 1), ("Scanner les réseaux", 2), ("Analyser les canaux", 3),
                   ("Relevé sur plan", 4), ("Supervision en direct", 5), ("Tests actifs", 6), ("Rapport", 7)]
        for i, (txt, page) in enumerate(actions):
            b = QPushButton(txt)
            if i == 0:
                b.setObjectName("Primary")
            b.clicked.connect(lambda _=False, p=page: self.navigate.emit(p))
            grid.addWidget(b, i // 4, i % 4)
        l2.addLayout(grid)
        root.addWidget(p2)

        root.addWidget(QLabel("<b>Derniers événements</b>"))
        self.events = QListWidget()
        root.addWidget(self.events, 1)

        ctx.service.availability.connect(lambda *_: self.refresh())
        ctx.service.connection_changed.connect(lambda *_: self.refresh())
        ctx.service.scan_failed.connect(lambda msg, loc, _t: self.banner.show_msg(msg, loc))
        ctx.service.scan_done.connect(lambda *_: self.banner.hide())
        ctx.context_changed.connect(self.refresh)
        ctx.aps_changed.connect(self.refresh)
        ctx.event.connect(self.add_event)
        self.fill_ifaces()
        self.refresh()

    def fill_ifaces(self):
        self.iface.clear()
        for i in self.ctx.service.interfaces():
            self.iface.addItem(f"{i.description} ({i.state})", i.guid)
        if self.ctx.service.iface:
            self.iface.setCurrentIndex(max(0, self.iface.findData(self.ctx.service.iface.guid)))
        if not self.iface.count():
            self.iface.addItem("Aucune carte Wi‑Fi", None)

    def _iface_selected(self):
        g = self.iface.currentData()
        if g:
            self.ctx.service.select_interface(g)

    def refresh(self):
        p = theme.palette()
        th = self.ctx.settings.section("thresholds")
        svc = self.ctx.service
        self.dot.set_color(p.ok if svc.available else p.crit)
        self.wifi.setText("Wi‑Fi opérationnel" if svc.available else "Wi‑Fi indisponible")
        self.wifi_sub.setText(svc.message + ("" if svc.available else
                              " — branchez une carte Wi‑Fi (USB ou intégrée) et vérifiez que le service "
                              "« Configuration automatique WLAN » est démarré."))
        c = svc.connection
        if c:
            self.c_ssid.set(c.ssid or "—", c.bssid)
            self.c_sig.set(f"{c.rssi} dBm", f"canal {c.channel or '?'} · {c.phy}", rssi_color(c.rssi, th))
            self.c_rate.set(f"{c.rx_mbps:.0f} Mb/s", f"émission {c.tx_mbps:.0f} Mb/s")
        else:
            self.c_ssid.set("Non connecté")
            self.c_sig.set("—")
            self.c_rate.set("—")
        own = self.ctx.own_ssids
        n_own = sum(1 for b in self.ctx.aps.values() if b.ssid in own)
        self.c_aps.set(str(len(self.ctx.aps)), f"dont {n_own} du client" if own else "SSID du client non déclarés")
        self.ctx_lbl.setText(self.ctx.label())
        if self.ctx.audit_id:
            a = self.ctx.db.audit(self.ctx.audit_id)
            n_pts = self.ctx.db.q("SELECT COUNT(*) n FROM scans WHERE audit_id=? AND kind='point'",
                                  (self.ctx.audit_id,))[0]["n"]
            n_tests = len(self.ctx.db.tests(self.ctx.audit_id))
            self.ctx_sub.setText(f"{'Supervision' if a['kind'] == 'supervision' else 'Audit'} démarré le "
                                 f"{time.strftime('%d/%m/%Y %H:%M', time.localtime(a['started']))} · "
                                 f"{self.ctx.db.scan_count(self.ctx.audit_id)} scans · {n_pts} points de relevé · "
                                 f"{n_tests} tests")
        else:
            self.ctx_sub.setText("Commencez par créer un client, un site et un audit dans « Clients & sites ». "
                                 "Sans audit, les scans s'affichent mais ne sont pas enregistrés.")

    def add_event(self, ev):
        col = theme.level_color({"error": 3, "warning": 2, "success": 0}.get(ev.level, -1))
        it = QListWidgetItem(f"{time.strftime('%H:%M:%S', time.localtime(ev.ts))}  "
                             f"{LEVEL_LABEL.get(ev.level, ev.level)} — {ev.message}")
        it.setForeground(QColor(col))
        self.events.insertItem(0, it)
        while self.events.count() > 200:
            self.events.takeItem(self.events.count() - 1)
