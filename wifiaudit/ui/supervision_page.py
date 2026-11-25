"""Page « Supervision » (mode régie) : scan continu, état de chaque borne,
alertes, connexion du poste, ping continu et itinérances."""
from __future__ import annotations

import threading
import time

import pyqtgraph as pg
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QSplitter, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ..core.pinger import Pinger, gateway_ip
from ..core.supervision import ST_MISSING, ST_NEIGHBOR, ST_OK, ST_ROGUE, ST_UNKNOWN, Supervisor
from ..events import LEVEL_LABEL
from ..wlan.oui import vendor
from . import theme
from .common import NumItem, StatCard, muted, page_title, rssi_color

LEVEL_NUM = {"error": 3, "warning": 2, "success": 0, "info": -1}


class _Relay(QObject):
    gateway = Signal(object)


class SupervisionPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.sup: Supervisor | None = None
        self.pinger: Pinger | None = None
        self.conn_hist: list[tuple[float, int]] = []
        self.started = 0.0
        self.relay = _Relay()
        self.relay.gateway.connect(self._start_pinger)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        head = QHBoxLayout()
        head.addWidget(page_title("Supervision en direct"))
        head.addStretch(1)
        self.ctx_lbl = muted("")
        head.addWidget(self.ctx_lbl)
        root.addLayout(head)

        bar = QHBoxLayout()
        self.start_btn = QPushButton("Démarrer la supervision")
        self.start_btn.setObjectName("Primary")
        self.start_btn.clicked.connect(self.toggle)
        self.interval = QComboBox()
        for s in (5, 10, 15, 30, 60):
            self.interval.addItem(f"Scan toutes les {s} s", s)
        self.interval.setCurrentIndex(max(0, self.interval.findData(int(ctx.settings.get("supervision.interval", 10)))))
        self.targets = QLineEdit(ctx.settings.get("supervision.ping_targets", "") or "1.1.1.1")
        self.targets.setPlaceholderText("Cibles de ping en plus de la passerelle (séparées par des virgules)")
        self.state = muted("Arrêtée")
        bar.addWidget(self.start_btn)
        bar.addWidget(self.interval)
        bar.addWidget(QLabel("Ping :"))
        bar.addWidget(self.targets, 1)
        bar.addWidget(self.state)
        root.addLayout(bar)

        cards = QHBoxLayout()
        self.c_conn = StatCard("Connexion du poste")
        self.c_sig = StatCard("Signal")
        self.c_rate = StatCard("Débit radio")
        self.c_gw = StatCard("Ping passerelle")
        self.c_net = StatCard("Ping Internet")
        self.c_aps = StatCard("Bornes autorisées")
        self.c_alert = StatCard("Suspectes / disparues")
        for c in (self.c_conn, self.c_sig, self.c_rate, self.c_gw, self.c_net, self.c_aps, self.c_alert):
            cards.addWidget(c)
        root.addLayout(cards)

        split = QSplitter(Qt.Vertical)
        self.table = QTableWidget(0, 11)
        self.table.setHorizontalHeaderLabels(["Statut", "SSID", "BSSID", "Fabricant", "Canal", "Bande", "Signal",
                                              "Min / Max", "Clients", "Occupation", "Vu il y a"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setColumnWidth(2, 140)
        self.table.setColumnWidth(3, 150)
        split.addWidget(self.table)

        low = QSplitter(Qt.Horizontal)
        self.tabs = QTabWidget()
        self.ping_plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.ping_plot.setLabel("left", "ms")
        self.ping_plot.addLegend()
        self.sig_plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.sig_plot.setLabel("left", "dBm")
        for w in (self.ping_plot, self.sig_plot):
            w.setMenuEnabled(False)
        self.tabs.addTab(self.ping_plot, "Latence")
        self.tabs.addTab(self.sig_plot, "Signal de la connexion")
        low.addWidget(self.tabs)
        ev = QWidget()
        el = QVBoxLayout(ev)
        el.setContentsMargins(0, 0, 0, 0)
        el.addWidget(QLabel("<b>Alertes et événements</b>"))
        self.events = QListWidget()
        el.addWidget(self.events)
        low.addWidget(ev)
        low.setSizes([700, 500])
        split.addWidget(low)
        split.setSizes([380, 300])
        root.addWidget(split, 1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        ctx.service.scan_done.connect(self._scan_done)
        ctx.service.connection_changed.connect(self._conn)
        ctx.event.connect(self._event)
        ctx.context_changed.connect(lambda: self.ctx_lbl.setText(ctx.label()))
        self.ctx_lbl.setText(ctx.label())
        self.apply_theme()

    def apply_theme(self):
        p = theme.palette()
        for w in (self.ping_plot, self.sig_plot):
            w.setBackground(p.chart_bg)
            w.showGrid(x=True, y=True, alpha=0.25)

    # ------------------------------------------------------------ marche
    @property
    def running(self) -> bool:
        return self.sup is not None

    def toggle(self):
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self):
        if not self.ctx.require_audit(self):
            return
        th = self.ctx.settings.section("thresholds")
        sc = self.ctx.settings.section("supervision")
        auth = self.ctx.authorized
        self.sup = Supervisor(self.ctx.own_ssids, auth, th, int(sc.get("missing_scans", 3)),
                              emit=self.ctx.emit_event, audit_id=self.ctx.audit_id)
        self.conn_hist = []
        self.started = time.time()
        self.ctx.settings.set("supervision.ping_targets", self.targets.text())
        self.ctx.service.set_connection_interval(1.0)
        self.ctx.service.set_continuous(self.interval.currentData())
        self.ctx.service.request_scan("auto")
        self.start_btn.setText("Arrêter la supervision")
        self.state.setText("Démarrage…")
        msg = "Supervision démarrée"
        if not auth:
            msg += " — aucune borne autorisée déclarée : la détection de bornes suspectes est inactive " \
                   "(page Scan › « Autoriser toutes les bornes du client »)"
        from ..events import INFO, Event
        self.ctx.emit_event(Event(INFO, "system", msg))
        threading.Thread(target=lambda: self.relay.gateway.emit(gateway_ip()), daemon=True).start()
        self.timer.start(1000)

    def _start_pinger(self, gw):
        if not self.running:
            return
        targets = ([gw] if gw else []) + [t.strip() for t in self.targets.text().split(",") if t.strip()]
        self.gw = gw
        self.net_target = next((t for t in targets if t != gw), None)
        if targets:
            self.pinger = Pinger(targets, float(self.ctx.settings.get("supervision.ping_interval", 1.0))).start()
        self._outage = {t: False for t in targets}

    def stop(self):
        self.ctx.service.set_continuous(0)
        self.ctx.service.set_connection_interval(2.0)
        if self.pinger:
            self.pinger.stop()
        self.pinger = None
        self.sup = None
        self.timer.stop()
        self.start_btn.setText("Démarrer la supervision")
        self.state.setText("Arrêtée")
        from ..events import INFO, Event
        self.ctx.emit_event(Event(INFO, "system", "Supervision arrêtée"))

    # --------------------------------------------------------- données
    def _scan_done(self, res):
        if self.running and res.tag == "auto":
            self.sup.update(res.bss, res.ts)
            self.state.setText(f"Dernier scan {time.strftime('%H:%M:%S', time.localtime(res.ts))} · "
                               f"{len(res.bss)} réseaux · {self.sup.scans} scans")
            self.refresh_table()

    def _conn(self, c):
        if not self.running:
            return
        if c and c.rssi is not None:
            self.conn_hist.append((time.time(), c.rssi))
            self.conn_hist = self.conn_hist[-3600:]

        def roam_cb(frm, to, rssi_after):
            ts = time.time()
            rb = self.conn_hist[-2][1] if len(self.conn_hist) > 1 else None

            def finish():
                gap = self.pinger.gap_around(self.gw, ts) if (self.pinger and getattr(self, "gw", None)) else None
                if self.ctx.audit_id:
                    self.ctx.db.add_roam(self.ctx.audit_id, ts, frm, to, gap, rb, rssi_after)
                if gap is not None:
                    from ..events import INFO, Event
                    self.ctx.emit_event(Event(INFO, "roam", f"Coupure mesurée pendant l'itinérance : {gap:.0f} ms"))
            QTimer.singleShot(5000, finish)

        self.sup.update_connection(c, int(self.ctx.settings.get("supervision.alert_rssi", -75)),
                                   audit_cb=roam_cb)

    def _event(self, ev):
        col = theme.level_color(LEVEL_NUM.get(ev.level, -1))
        it = QListWidgetItem(f"{time.strftime('%H:%M:%S', time.localtime(ev.ts))}  "
                             f"{LEVEL_LABEL.get(ev.level, ev.level)} — {ev.message}")
        it.setForeground(QColor(col))
        self.events.insertItem(0, it)
        while self.events.count() > 500:
            self.events.takeItem(self.events.count() - 1)

    # ---------------------------------------------------------- affichage
    def refresh_table(self):
        if not self.sup:
            return
        th = self.ctx.settings.section("thresholds")
        p = theme.palette()
        tracks = list(self.sup.tracks.values())
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(tracks))
        now = time.time()
        colors = {ST_OK: p.ok, ST_ROGUE: p.crit, ST_MISSING: p.crit, ST_UNKNOWN: p.warn, ST_NEIGHBOR: p.muted}
        order = {ST_ROGUE: 0, ST_MISSING: 1, ST_UNKNOWN: 2, ST_OK: 3, ST_NEIGHBOR: 4}
        for r, t in enumerate(tracks):
            b = t.bss
            vals = [v for _ts, v in t.history if v is not None]
            items = [NumItem(t.status, order[t.status]), QTableWidgetItem(b.ssid or "(masqué)"),
                     QTableWidgetItem(b.bssid), QTableWidgetItem(vendor(b.bssid, b.ies.vendor_ouis)),
                     NumItem(str(b.channel), b.channel or 0), QTableWidgetItem(b.band or ""),
                     NumItem(f"{b.rssi} dBm" if t.missing == 0 else "—", b.rssi if t.missing == 0 else -200),
                     QTableWidgetItem(f"{min(vals)} / {max(vals)}" if vals else "—"),
                     NumItem("—" if b.ies.station_count is None else str(b.ies.station_count),
                             b.ies.station_count or -1),
                     NumItem("—" if b.ies.channel_utilization is None else f"{b.ies.channel_utilization:.0f} %",
                             b.ies.channel_utilization or -1),
                     NumItem(f"{now - t.last_seen:.0f} s", now - t.last_seen)]
            items[0].setTextAlignment(int(Qt.AlignLeft | Qt.AlignVCenter))
            items[0].setForeground(QColor(colors[t.status]))
            items[6].setForeground(QColor(rssi_color(b.rssi, th)))
            for c, it in enumerate(items):
                self.table.setItem(r, c, it)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.AscendingOrder)

    def refresh(self):
        if not self.running:
            return
        p = theme.palette()
        th = self.ctx.settings.section("thresholds")
        c = self.ctx.service.connection
        if c:
            self.c_conn.set(c.ssid or "—", c.bssid)
            self.c_sig.set(f"{c.rssi} dBm", f"qualité {c.signal_quality} % · canal {c.channel or '?'}",
                           rssi_color(c.rssi, th))
            self.c_rate.set(f"{c.rx_mbps:.0f} Mb/s", f"émission {c.tx_mbps:.0f} Mb/s · {c.phy}")
        else:
            self.c_conn.set("Déconnecté", "")
            self.c_sig.set("—")
            self.c_rate.set("—")
        counts = self.sup.counts()
        self.c_aps.set(str(counts[ST_OK]), f"{counts[ST_NEIGHBOR]} réseaux voisins")
        bad = counts[ST_ROGUE] + counts[ST_MISSING]
        self.c_alert.set(f"{counts[ST_ROGUE]} / {counts[ST_MISSING]}", f"{counts[ST_UNKNOWN]} inconnue(s)",
                         p.crit if bad else p.ok)
        self.ping_plot.clear()
        if self.pinger:
            cols = [p.accent, p.warn, p.ok, p.bad]
            for i, t in enumerate(self.pinger.targets):
                s = self.pinger.series(t, time.time() - 900)
                if not s:
                    continue
                xs = [a for a, _ in s]
                ys = [float("nan") if v is None else v for _, v in s]
                self.ping_plot.plot(xs, ys, pen=pg.mkPen(cols[i % 4], width=1.5), name=t, connect="finite")
                lost = [a for a, v in s if v is None]
                if lost:
                    self.ping_plot.plot(lost, [0] * len(lost), pen=None, symbol="t", symbolBrush=p.crit, symbolSize=7)
                ok = [v for _, v in s[-60:] if v is not None]
                loss = 100 * sum(1 for _, v in s[-60:] if v is None) / max(len(s[-60:]), 1)
                card = self.c_gw if t == getattr(self, "gw", None) else self.c_net
                card.set(f"{sum(ok) / len(ok):.1f} ms" if ok else "perdu",
                         f"{t} · pertes {loss:.0f} % (1 min)",
                         p.crit if loss >= 10 or not ok else (p.warn if loss > 0 else None))
                n = self.pinger.consecutive_losses(t)
                from ..events import ERROR, SUCCESS, Event
                if n >= 3 and not self._outage.get(t):
                    self._outage[t] = True
                    self.ctx.emit_event(Event(ERROR, "ping", f"Plus de réponse de {t} ({n} pings perdus)"))
                elif n == 0 and self._outage.get(t):
                    self._outage[t] = False
                    self.ctx.emit_event(Event(SUCCESS, "ping", f"{t} répond de nouveau"))
        self.sig_plot.clear()
        if self.conn_hist:
            xs, ys = zip(*self.conn_hist)
            self.sig_plot.plot(list(xs), list(ys), pen=pg.mkPen(p.accent, width=2))
            self.sig_plot.addLine(y=float(self.ctx.settings.get("supervision.alert_rssi", -75)),
                                  pen=pg.mkPen(p.warn, style=Qt.DashLine))
