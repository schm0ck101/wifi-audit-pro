"""Page « Tests » : ping, débit iperf3, test d'itinérance (coupure mesurée
à chaque changement de borne)."""
from __future__ import annotations

import threading
import time

import pyqtgraph as pg
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFormLayout, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPushButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.pinger import Pinger, gateway_ip, ping_series
from ..events import INFO, Event
from ..net.iperf3 import IperfError, run
from . import theme
from .common import fmt_ts, muted, page_title, panel


class _Relay(QObject):
    text = Signal(str, str)        # zone, texte
    progress = Signal(float, float)
    done = Signal()


class TestsPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.relay = _Relay()
        self.relay.text.connect(self._text)
        self.relay.progress.connect(self._progress)
        self.relay.done.connect(self.reload)
        self.iperf_points: list[tuple[float, float]] = []
        self.roam_pinger: Pinger | None = None
        self.roam_last = None
        self.roams: list[dict] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.addWidget(page_title("Tests actifs"))
        root.addWidget(muted("Mesures depuis la position actuelle du poste. Les résultats sont enregistrés dans "
                             "l'audit courant et repris dans le rapport."))
        grid = QGridLayout()
        grid.setSpacing(12)

        # --- ping
        p1, l1 = panel()
        l1.addWidget(QLabel("<b>Ping</b>"))
        f = QFormLayout()
        self.ping_target = QLineEdit()
        self.ping_target.setPlaceholderText("vide = passerelle")
        self.ping_count = QSpinBox()
        self.ping_count.setRange(5, 1000)
        self.ping_count.setValue(50)
        f.addRow("Cible", self.ping_target)
        f.addRow("Nombre", self.ping_count)
        l1.addLayout(f)
        b = QPushButton("Lancer le ping")
        b.clicked.connect(self.run_ping)
        l1.addWidget(b)
        self.ping_res = QLabel("")
        self.ping_res.setWordWrap(True)
        l1.addWidget(self.ping_res)
        l1.addStretch(1)
        grid.addWidget(p1, 0, 0)

        # --- iperf3
        p2, l2 = panel()
        l2.addWidget(QLabel("<b>Débit (iperf3)</b>"))
        cfg = ctx.settings.section("iperf")
        f = QFormLayout()
        self.server = QLineEdit(cfg.get("server", ""))
        self.server.setPlaceholderText("serveur iperf3 (ex. NAS du client, 192.168.1.10)")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(cfg.get("port", 5201)))
        self.secs = QSpinBox()
        self.secs.setRange(3, 120)
        self.secs.setValue(int(cfg.get("seconds", 10)))
        self.streams = QSpinBox()
        self.streams.setRange(1, 16)
        self.streams.setValue(int(cfg.get("streams", 1)))
        self.direction = QComboBox()
        self.direction.addItem("Descendant puis montant", "both")
        self.direction.addItem("Descendant (serveur → poste)", "down")
        self.direction.addItem("Montant (poste → serveur)", "up")
        for lab, w in (("Serveur", self.server), ("Port", self.port), ("Durée (s)", self.secs),
                       ("Flux parallèles", self.streams), ("Sens", self.direction)):
            f.addRow(lab, w)
        l2.addLayout(f)
        self.iperf_btn = QPushButton("Lancer le test de débit")
        self.iperf_btn.clicked.connect(self.run_iperf)
        l2.addWidget(self.iperf_btn)
        self.iperf_res = QLabel("")
        self.iperf_res.setWordWrap(True)
        l2.addWidget(self.iperf_res)
        self.iperf_plot = pg.PlotWidget()
        self.iperf_plot.setMenuEnabled(False)
        self.iperf_plot.setLabel("left", "Mb/s")
        self.iperf_plot.setLabel("bottom", "s")
        self.iperf_plot.setMinimumHeight(140)
        l2.addWidget(self.iperf_plot)
        grid.addWidget(p2, 0, 1)

        # --- itinérance
        p3, l3 = panel()
        l3.addWidget(QLabel("<b>Test d'itinérance</b>"))
        l3.addWidget(muted("Démarrez puis déplacez‑vous d'une borne à l'autre. Chaque changement de borne est "
                           "enregistré avec la coupure mesurée (ping de la passerelle toutes les 100 ms)."))
        self.roam_btn = QPushButton("Démarrer le test d'itinérance")
        self.roam_btn.clicked.connect(self.toggle_roam)
        l3.addWidget(self.roam_btn)
        self.roam_state = QLabel("")
        l3.addWidget(self.roam_state)
        self.roam_table = QTableWidget(0, 5)
        self.roam_table.setHorizontalHeaderLabels(["Heure", "De", "Vers", "Coupure", "Signal après"])
        self.roam_table.verticalHeader().hide()
        self.roam_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.roam_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        l3.addWidget(self.roam_table, 1)
        grid.addWidget(p3, 0, 2)
        root.addLayout(grid)

        # --- historique
        p4, l4 = panel()
        l4.addWidget(QLabel("<b>Résultats enregistrés dans l'audit</b>"))
        self.hist = QTableWidget(0, 4)
        self.hist.setHorizontalHeaderLabels(["Date", "Test", "Résultat", "Contexte"])
        self.hist.verticalHeader().hide()
        self.hist.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.hist.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        l4.addWidget(self.hist)
        root.addWidget(p4, 1)

        self.roam_timer = QTimer(self)
        self.roam_timer.timeout.connect(self._roam_tick)
        ctx.context_changed.connect(self.reload)
        self.apply_theme()
        self.reload()

    def apply_theme(self):
        self.iperf_plot.setBackground(theme.palette().chart_bg)

    def _text(self, zone, txt):
        {"ping": self.ping_res, "iperf": self.iperf_res, "roam": self.roam_state}[zone].setText(txt)

    def _ctx_text(self):
        c = self.ctx.service.connection
        return f"{c.ssid} {c.bssid} {c.rssi} dBm" if c else "non connecté au Wi‑Fi"

    # -------------------------------------------------------------- ping
    def run_ping(self):
        target = self.ping_target.text().strip()
        n = self.ping_count.value()
        self.ping_res.setText("Ping en cours…")
        ctxt = self._ctx_text()

        def work():
            t = target or gateway_ip()
            if not t:
                self.relay.text.emit("ping", "Passerelle introuvable : indiquez une cible.")
                return
            r = ping_series(t, n, 0.2)
            r["context"] = ctxt
            if self.ctx.audit_id:
                self.ctx.db.add_test(self.ctx.audit_id, "ping", r)
            txt = (f"{r['target']} : {r['received']}/{r['sent']} réponses, pertes {r['loss']} %"
                   + (f"<br>moyenne {r['avg']} ms · min {r['min']} · max {r['max']} · jitter {r['jitter']} ms"
                      if r["avg"] is not None else ""))
            self.relay.text.emit("ping", txt)
            self.relay.done.emit()

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------- iperf
    def run_iperf(self):
        server = self.server.text().strip()
        if not server:
            self.iperf_res.setText("Indiquez un serveur iperf3 (lancé avec « iperf3 -s »).")
            return
        self.ctx.settings.set("iperf", {"server": server, "port": self.port.value(), "seconds": self.secs.value(),
                                        "streams": self.streams.value()})
        dirs = {"both": [True, False], "down": [True], "up": [False]}[self.direction.currentData()]
        self.iperf_btn.setEnabled(False)
        self.iperf_points = []
        self.iperf_res.setText("Test en cours…")
        ctxt = self._ctx_text()
        port, secs, streams = self.port.value(), self.secs.value(), self.streams.value()

        def work():
            lines = []
            for rev in dirs:
                try:
                    r = run(server, port, secs, reverse=rev, streams=streams,
                            progress=lambda m, t: self.relay.progress.emit(m, t))
                    d = r.to_dict()
                    d["context"] = ctxt
                    if self.ctx.audit_id:
                        self.ctx.db.add_test(self.ctx.audit_id, "iperf", d)
                    lines.append(f"{'Descendant' if rev else 'Montant'} : <b>{r.mbps:.0f} Mb/s</b>")
                except IperfError as exc:
                    lines.append(f"{'Descendant' if rev else 'Montant'} : échec — {exc}")
                self.relay.text.emit("iperf", "<br>".join(lines))
            self.relay.done.emit()

        threading.Thread(target=work, daemon=True).start()

    def _progress(self, mbps, t):
        self.iperf_points.append((t, mbps))
        xs, ys = zip(*self.iperf_points)
        self.iperf_plot.clear()
        self.iperf_plot.plot(list(range(len(xs))), list(ys), pen=pg.mkPen(theme.palette().accent, width=2),
                             symbol="o", symbolSize=5)

    # -------------------------------------------------------- itinérance
    def toggle_roam(self):
        if self.roam_pinger:
            self.roam_pinger.stop()
            self.roam_pinger = None
            self.roam_timer.stop()
            self.ctx.service.set_connection_interval(2.0)
            self.roam_btn.setText("Démarrer le test d'itinérance")
            self.roam_state.setText(f"Terminé : {len(self.roams)} itinérance(s).")
            return
        if not self.ctx.require_audit(self):
            return
        gw = gateway_ip()
        if not gw:
            self.roam_state.setText("Passerelle introuvable (poste connecté ?).")
            return
        self.gw = gw
        self.roam_pinger = Pinger([gw], interval=0.1, timeout_ms=400, history=20000).start()
        self.ctx.service.set_connection_interval(0.25)
        c = self.ctx.service.connection
        self.roam_last = c.bssid if c else None
        self.roams = []
        self.roam_table.setRowCount(0)
        self.roam_btn.setText("Arrêter le test")
        self.roam_timer.start(250)

    def _roam_tick(self):
        c = self.ctx.service.connection
        s = self.roam_pinger.series(self.gw, time.time() - 10) if self.roam_pinger else []
        loss = sum(1 for _t, v in s if v is None)
        self.roam_state.setText((f"Connecté à {c.bssid} ({c.rssi} dBm)" if c else "Déconnecté")
                                + f" · ping passerelle : {loss} perte(s) sur 10 s")
        if c and self.roam_last and c.bssid != self.roam_last:
            frm, ts, rssi = self.roam_last, time.time(), c.rssi
            self.ctx.emit_event(Event(INFO, "roam", f"Itinérance {frm} → {c.bssid}"))
            QTimer.singleShot(4000, lambda: self._roam_finish(frm, c.bssid, ts, rssi))
        if c:
            self.roam_last = c.bssid

    def _roam_finish(self, frm, to, ts, rssi):
        if not self.roam_pinger:
            return
        gap = self.roam_pinger.gap_around(self.gw, ts, window=4)
        if self.ctx.audit_id:
            self.ctx.db.add_roam(self.ctx.audit_id, ts, frm, to, gap, None, rssi)
        r = self.roam_table.rowCount()
        self.roam_table.insertRow(r)
        for col, v in enumerate((time.strftime("%H:%M:%S", time.localtime(ts)), frm, to,
                                 "—" if gap is None else f"{gap:.0f} ms", f"{rssi} dBm")):
            self.roam_table.setItem(r, col, QTableWidgetItem(v))
        self.roams.append({"gap": gap})

    # ------------------------------------------------------------ historique
    def reload(self):
        self.iperf_btn.setEnabled(True)
        rows = self.ctx.db.tests(self.ctx.audit_id) if self.ctx.audit_id else []
        self.hist.setRowCount(len(rows))
        for i, t in enumerate(reversed(rows)):
            r = t["result"]
            if t["kind"] == "ping":
                res = (f"{r.get('target')} : moy. {r.get('avg')} ms, pertes {r.get('loss')} %, jitter {r.get('jitter')} ms")
                kind = "Ping" + (" (point de relevé)" if t["scan_id"] else "")
            else:
                res = f"{'Descendant' if r.get('direction') == 'down' else 'Montant'} {r.get('mbps')} Mb/s ({r.get('server')})"
                kind = "Débit iperf3" + (" (point de relevé)" if t["scan_id"] else "")
            for c, v in enumerate((fmt_ts(t["ts"]), kind, res, r.get("context", ""))):
                self.hist.setItem(i, c, QTableWidgetItem(v))
