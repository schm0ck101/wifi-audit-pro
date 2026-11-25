"""Fenêtre principale : barre latérale, pages, zone de notification."""
from __future__ import annotations

import logging

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QIcon
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu,
                               QPushButton, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget)

from ..config import APP_NAME, APP_VERSION, AUTHOR, AUTHOR_URL
from . import theme
from .channels_page import ChannelsPage
from .clients_page import ClientsPage
from .common import Notifier, StatusDot
from .dashboard import Dashboard
from .report_page import ReportPage
from .scan_page import ScanPage
from .settings_page import SettingsPage
from .supervision_page import SupervisionPage
from .survey_page import SurveyPage
from .tests_page import TestsPage

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(theme.app_icon_pixmap(256)))
        self.resize(1480, 920)
        self.setMinimumSize(1150, 720)
        central = QWidget()
        central.setObjectName("Central")
        self.setCentralWidget(central)
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(232)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 16, 12, 14)
        sl.setSpacing(3)
        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(theme.app_icon_pixmap(72).scaled(38, 38, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        head.addWidget(logo)
        hb = QVBoxLayout()
        hb.setSpacing(0)
        t = QLabel(APP_NAME)
        t.setObjectName("AppTitle")
        st = QLabel(f"Audit & supervision · v{APP_VERSION}")
        st.setObjectName("AppSubtitle")
        hb.addWidget(t)
        hb.addWidget(st)
        head.addLayout(hb, 1)
        sl.addLayout(head)
        sl.addSpacing(16)

        self.stack = QStackedWidget()
        self.dashboard = Dashboard(ctx)
        self.clients = ClientsPage(ctx)
        self.scan = ScanPage(ctx)
        self.channels = ChannelsPage(ctx)
        self.survey = SurveyPage(ctx)
        self.supervision = SupervisionPage(ctx)
        self.tests = TestsPage(ctx)
        self.report = ReportPage(ctx)
        self.settings_page = SettingsPage(ctx)
        self.pages = [(theme.G_DASHBOARD, "Tableau de bord", self.dashboard),
                      (theme.G_CLIENTS, "Clients && sites", self.clients),
                      (theme.G_WIFI, "Scan", self.scan),
                      (theme.G_CHANNELS, "Canaux", self.channels),
                      (theme.G_MAP, "Relevé sur plan", self.survey),
                      (theme.G_SUPERVISION, "Supervision", self.supervision),
                      (theme.G_TESTS, "Tests", self.tests),
                      (theme.G_REPORT, "Rapport", self.report),
                      (theme.G_SETTINGS, "Paramètres", self.settings_page)]
        self.nav = QButtonGroup(self)
        self.buttons = []
        for i, (glyph, label, page) in enumerate(self.pages):
            b = QPushButton(f"  {label}")
            b.setObjectName("NavButton")
            b.setCheckable(True)
            b.setIconSize(QSize(18, 18))
            b.setProperty("glyph", glyph)
            b.setCursor(Qt.PointingHandCursor)
            self.nav.addButton(b, i)
            self.buttons.append(b)
            sl.addWidget(b)
            self.stack.addWidget(page)
        self.nav.idClicked.connect(self.show_page)
        sl.addStretch(1)

        box = QFrame()
        box.setObjectName("Panel")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(12, 10, 12, 10)
        r = QHBoxLayout()
        self.dot = StatusDot(12)
        r.addWidget(self.dot)
        self.wifi_lbl = QLabel("")
        self.wifi_lbl.setObjectName("SectionTitle")
        r.addWidget(self.wifi_lbl, 1)
        bl.addLayout(r)
        self.ctx_lbl = QLabel("")
        self.ctx_lbl.setObjectName("Muted")
        self.ctx_lbl.setWordWrap(True)
        bl.addWidget(self.ctx_lbl)
        sl.addWidget(box)
        self.theme_btn = QPushButton()
        self.theme_btn.setObjectName("NavButton")
        self.theme_btn.clicked.connect(self.toggle_theme)
        sl.addWidget(self.theme_btn)

        # signature de l'auteur (clic = site)
        brand = QLabel()
        brand.setAlignment(Qt.AlignCenter)
        pm = theme.brand_pixmap(16)
        if pm.isNull():
            brand.setText(f"Créé par {AUTHOR}")
        else:
            brand.setPixmap(pm)
        brand.setToolTip(f"{APP_NAME} — créé par {AUTHOR} · {AUTHOR_URL}")
        brand.setCursor(Qt.PointingHandCursor)
        brand.mousePressEvent = lambda e: QDesktopServices.openUrl(QUrl(AUTHOR_URL))
        brand.setContentsMargins(0, 6, 0, 2)
        sl.addWidget(brand)
        lay.addWidget(side)
        lay.addWidget(self.stack, 1)

        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self.windowIcon(), self)
            self.tray.setToolTip(APP_NAME)
            m = QMenu()
            a = QAction("Afficher", self)
            a.triggered.connect(self.showNormal)
            q = QAction("Quitter", self)
            q.triggered.connect(self.close)
            m.addAction(a)
            m.addSeparator()
            m.addAction(q)
            self.tray.setContextMenu(m)
            self.tray.activated.connect(lambda reason: self.showNormal() if reason == QSystemTrayIcon.Trigger else None)
            self.tray.show()
        self.notifier = Notifier(self, self.tray, ctx.settings)
        ctx.event.connect(self.notifier.notify)
        self.dashboard.navigate.connect(self.show_page)
        self.settings_page.applied.connect(self.apply_settings)
        ctx.context_changed.connect(self.update_status)
        ctx.service.availability.connect(lambda *_: self.update_status())
        self.apply_icons()
        self.show_page(0 if ctx.audit_id else 1)
        self.update_status()

    def show_page(self, i: int):
        self.buttons[i].setChecked(True)
        self.stack.setCurrentIndex(i)

    def update_status(self):
        p = theme.palette()
        ok = self.ctx.service.available
        self.dot.set_color(p.ok if ok else p.crit)
        self.wifi_lbl.setText("Wi‑Fi disponible" if ok else "Wi‑Fi indisponible")
        self.ctx_lbl.setText(self.ctx.label())

    def apply_icons(self):
        p = theme.palette()
        for b in self.buttons:
            b.setIcon(theme.glyph_icon(b.property("glyph"), p.text))
        dark = p.name == "dark"
        self.theme_btn.setIcon(theme.glyph_icon("" if dark else "", p.text))
        self.theme_btn.setText("  Mode clair" if dark else "  Mode sombre")

    def toggle_theme(self):
        new = "light" if theme.palette().name == "dark" else "dark"
        self.ctx.settings.set("ui.theme", new)
        self.ctx.settings.save()
        self.apply_settings()

    def apply_settings(self):
        theme.apply_theme(self.ctx.settings.get("ui.theme", "dark"))
        self.apply_icons()
        for _g, _l, page in self.pages:
            if hasattr(page, "apply_theme"):
                page.apply_theme()
        self.survey.render()
        self.update_status()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.notifier.reposition()

    def closeEvent(self, e):
        try:
            if self.supervision.running:
                self.supervision.stop()
            self.ctx.service.shutdown()
            self.ctx.settings.save()
        except Exception:
            log.exception("Fermeture")
        if self.tray:
            self.tray.hide()
        e.accept()
        QApplication.instance().quit()
