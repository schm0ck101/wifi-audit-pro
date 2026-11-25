"""Page « Canaux » : spectre par bande (chaque réseau dessiné sur sa largeur
réelle), occupation de chaque canal et recommandations."""
from __future__ import annotations

import hashlib

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel,
                               QListWidget, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..audit.channels import analyze, recommend
from ..wlan.model import BAND_5, BAND_6, BAND_24, BANDS, Bss
from . import theme
from .common import NumItem, muted, page_title

BASE = {BAND_24: 2407.0, BAND_5: 5000.0, BAND_6: 5950.0}
XRANGE = {BAND_24: (-1, 16), BAND_5: (32, 169), BAND_6: (-3, 237)}


def ssid_color(name: str) -> QColor:
    h = int(hashlib.md5(name.encode()).hexdigest()[:6], 16)
    c = QColor()
    c.setHsv(h % 360, 170, 235)
    return c


def spectrum_items(plot: pg.PlotItem, aps: list[Bss], band: str, own: set[str], min_rssi: int = -95):
    """Dessine chaque réseau comme un trapèze couvrant sa largeur de canal."""
    plot.clear()
    base = BASE[band]
    font = QFont("Segoe UI", 8)
    for b in sorted(aps, key=lambda x: x.rssi):
        if b.band != band or b.rssi < min_rssi:
            continue
        lo, hi = b.span_mhz
        x0, x1 = (lo - base) / 5, (hi - base) / 5
        slope = min(1.0, (x1 - x0) * 0.12)
        xs = [x0, x0 + slope, x1 - slope, x1]
        ys = [-100, b.rssi, b.rssi, -100]
        c = ssid_color(b.ssid or b.bssid)
        mine = b.ssid in own
        fill = QColor(c)
        fill.setAlpha(110 if mine else 45)
        pen = pg.mkPen(c, width=2.6 if mine else 1.2, style=Qt.SolidLine if mine else Qt.DotLine
                       if b.hidden else Qt.SolidLine)
        plot.addItem(pg.PlotCurveItem(xs, ys, pen=pen, fillLevel=-100, brush=fill))
        t = pg.TextItem(b.ssid or "(masqué)", color=c, anchor=(0.5, 1.1))
        t.setFont(font)
        t.setPos((x0 + x1) / 2, b.rssi)
        plot.addItem(t)
    plot.setXRange(*XRANGE[band], padding=0)
    plot.setYRange(-100, -20, padding=0)


class ChannelsPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.addWidget(page_title("Analyse des canaux"))
        bar = QHBoxLayout()
        self.band = QComboBox()
        for b in BANDS:
            self.band.addItem(b, b)
        self.band.currentIndexChanged.connect(self.refresh)
        self.min_rssi = QComboBox()
        for v in (-95, -85, -80, -75, -70):
            self.min_rssi.addItem(f"Signal ≥ {v} dBm", v)
        self.min_rssi.currentIndexChanged.connect(self.refresh)
        self.width_cb = QComboBox()
        for w in (20, 40, 80, 160):
            self.width_cb.addItem(f"Recommandation pour {w} MHz", w)
        self.width_cb.setCurrentIndex(2)
        self.width_cb.currentIndexChanged.connect(self.refresh)
        bar.addWidget(QLabel("Bande"))
        bar.addWidget(self.band)
        bar.addWidget(self.min_rssi)
        bar.addWidget(self.width_cb)
        bar.addStretch(1)
        self.info = muted("")
        bar.addWidget(self.info)
        root.addLayout(bar)

        split = QSplitter(Qt.Vertical)
        self.pw = pg.PlotWidget()
        self.pw.setMenuEnabled(False)
        self.plot = self.pw.getPlotItem()
        self.plot.setLabel("left", "Signal (dBm)")
        self.plot.setLabel("bottom", "Canal")
        self.plot.getViewBox().setMouseEnabled(x=True, y=False)
        split.addWidget(self.pw)

        low = QWidget()
        ll = QHBoxLayout(low)
        ll.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["Canal", "Principal", "Recouvrants", "Du client", "Plus fort", "Occupation",
                                              "Clients", "Saturation"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        ll.addWidget(self.table, 3)
        rv = QVBoxLayout()
        rv.addWidget(QLabel("<b>Meilleurs choix</b>"))
        self.reco = QListWidget()
        rv.addWidget(self.reco, 1)
        rv.addWidget(QLabel("<b>Canaux actuels du client</b>"))
        self.own_list = QListWidget()
        rv.addWidget(self.own_list, 1)
        ll.addLayout(rv, 2)
        split.addWidget(low)
        split.setSizes([420, 300])
        root.addWidget(split, 1)
        ctx.aps_changed.connect(self.refresh)
        self.apply_theme()

    def apply_theme(self):
        p = theme.palette()
        self.pw.setBackground(p.chart_bg)
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.refresh()

    def refresh(self):
        if not self.isVisible():
            return
        band = self.band.currentData()
        aps = [b for b in self.ctx.aps.values() if b.rssi >= self.min_rssi.currentData()]
        own = self.ctx.own_ssids
        spectrum_items(self.plot, aps, band, own, self.min_rssi.currentData())
        ticks = {BAND_24: list(range(1, 15)), BAND_5: list(range(36, 166, 8)),
                 BAND_6: list(range(1, 234, 16))}[band]
        self.plot.getAxis("bottom").setTicks([[(t, str(t)) for t in ticks]])
        stats = analyze(aps, band, own)
        n_band = sum(1 for b in aps if b.band == band)
        self.info.setText(f"{n_band} réseaux sur la bande · {sum(1 for b in aps if b.band == band and b.ssid in own)} du client")
        shown = [s for s in stats if s.overlapping or band == BAND_24 or s.channel in (36, 40, 44, 48, 149, 153, 157, 161)] \
            if band != BAND_6 else [s for s in stats if s.overlapping]
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(shown))
        p = theme.palette()
        for r, s in enumerate(shown):
            sc = s.score
            col = p.ok if sc < 25 else p.warn if sc < 50 else p.bad if sc < 75 else p.crit
            items = [NumItem(f"{s.channel}" + (" DFS" if s.dfs else ""), s.channel), NumItem(str(s.primary), s.primary),
                     NumItem(str(s.overlapping), s.overlapping), NumItem(str(s.own), s.own),
                     NumItem("—" if s.strongest is None else f"{s.strongest} dBm", s.strongest or -200),
                     NumItem("—" if s.utilization is None else f"{s.utilization:.0f} %", s.utilization or -1),
                     NumItem(str(s.stations), s.stations), NumItem(f"{sc:.0f}", sc)]
            items[7].setForeground(QColor(col))
            for c, it in enumerate(items):
                self.table.setItem(r, c, it)
        self.table.setSortingEnabled(True)
        self.reco.clear()
        w = self.width_cb.currentData()
        if band == BAND_24 and w > 20:
            self.reco.addItem("En 2,4 GHz, utilisez toujours 20 MHz :")
            w = 20
        for label, sc in recommend(stats, band, w)[:6]:
            self.reco.addItem(f"{label} — saturation {sc:.0f}/100")
        self.own_list.clear()
        mine = sorted((b for b in self.ctx.aps.values() if b.band == band and b.ssid in own),
                      key=lambda b: (b.channel or 0, -b.rssi))
        for b in mine:
            self.own_list.addItem(f"Canal {b.channel} ({b.width} MHz) — {b.ssid} — {b.bssid} — {b.rssi} dBm")
        if not own:
            self.own_list.addItem("Déclarez les SSID du client dans « Clients & sites ».")

    def showEvent(self, e):
        super().showEvent(e)
        self.refresh()
