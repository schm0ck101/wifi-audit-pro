"""Éléments partagés de l'interface : contexte (client/site/audit courant),
widgets simples, couleurs de signal, notifications."""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
                               QMessageBox, QSystemTrayIcon, QTableWidgetItem, QVBoxLayout, QWidget)

from ..events import CATEGORY_LABEL, ERROR, SUCCESS, WARNING, Event
from ..wlan.model import Bss
from . import theme


# ------------------------------------------------------------------ couleurs
def rssi_level(rssi: float | None, th: dict) -> int:
    """0 bon, 1 correct, 2 faible, 3 très faible."""
    if rssi is None or (isinstance(rssi, float) and math.isnan(rssi)):
        return 3
    if rssi >= th.get("rssi_target", -67):
        return 0
    if rssi >= th.get("rssi_min", -75):
        return 1
    if rssi >= -82:
        return 2
    return 3


def rssi_color(rssi, th) -> str:
    return theme.level_color(rssi_level(rssi, th))


# ------------------------------------------------------------------ widgets
class StatusDot(QLabel):
    def __init__(self, size=12, parent=None):
        super().__init__(parent)
        self._s = size
        self.setFixedSize(size, size)
        self.set_color(theme.palette().muted)

    def set_color(self, c: str):
        self.setStyleSheet(f"background:{c}; border-radius:{self._s // 2}px;")


class StatCard(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("StatCard")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(0)
        t = QLabel(title.upper())
        t.setObjectName("StatTitle")
        self.v = QLabel("—")
        self.v.setObjectName("StatValue")
        self.s = QLabel("")
        self.s.setObjectName("StatSub")
        lay.addWidget(t)
        lay.addWidget(self.v)
        lay.addWidget(self.s)

    def set(self, value: str, sub: str = "", color: str | None = None):
        self.v.setText(value)
        self.s.setText(sub)
        self.v.setStyleSheet(f"color:{color};" if color else "")


class NumItem(QTableWidgetItem):
    """Cellule triée numériquement."""

    def __init__(self, text: str, value):
        super().__init__(text)
        self.value = -1e18 if value is None or (isinstance(value, float) and math.isnan(value)) else value
        self.setTextAlignment(int(Qt.AlignRight | Qt.AlignVCenter))

    def __lt__(self, other):
        return self.value < other.value if isinstance(other, NumItem) else super().__lt__(other)


def page_title(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("PageTitle")
    return lab


def muted(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("Muted")
    lab.setWordWrap(True)
    return lab


def panel() -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName("Panel")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(14, 12, 14, 12)
    return f, lay


def fmt_ts(ts) -> str:
    return time.strftime("%d/%m/%Y %H:%M", time.localtime(ts)) if ts else "—"


# ----------------------------------------------------------------- contexte
class AppContext(QObject):
    """État partagé : base, paramètres, service Wi‑Fi, sélection courante
    (client › site › audit) et points d'accès vus pendant la session."""

    context_changed = Signal()
    aps_changed = Signal()
    event = Signal(object)          # Event

    def __init__(self, settings, db, service):
        super().__init__()
        self.settings = settings
        self.db = db
        self.service = service
        c = settings.section("context")
        self.client_id = c.get("client")
        self.site_id = c.get("site")
        self.audit_id = c.get("audit")
        self._validate()
        self.aps: dict[str, Bss] = {}            # bssid -> dernier Bss vu (session)
        self.last_scan_ts = 0.0
        service.scan_done.connect(self._on_scan)

    def _validate(self):
        if self.client_id and not self.db.client(self.client_id):
            self.client_id = self.site_id = self.audit_id = None
        if self.site_id and not self.db.site(self.site_id):
            self.site_id = self.audit_id = None
        if self.audit_id and not self.db.audit(self.audit_id):
            self.audit_id = None

    def set_context(self, client_id=None, site_id=None, audit_id=None):
        self.client_id, self.site_id, self.audit_id = client_id, site_id, audit_id
        self._validate()
        self.settings.set("context", {"client": self.client_id, "site": self.site_id,
                                      "audit": self.audit_id})
        try:
            self.settings.save()
        except OSError:
            pass
        self.aps = {}
        if self.audit_id:
            for b in self.db.audit_aps(self.audit_id):
                self.aps[b.bssid] = b
        self.context_changed.emit()
        self.aps_changed.emit()

    @property
    def site(self):
        return self.db.site(self.site_id) if self.site_id else None

    @property
    def own_ssids(self) -> set[str]:
        s = self.site
        return set(s["own_ssids"]) if s else set()

    @property
    def authorized(self) -> set[str]:
        return set(self.db.authorized(self.site_id)) if self.site_id else set()

    def label(self) -> str:
        parts = []
        if self.client_id:
            parts.append(self.db.client(self.client_id)["name"])
        if self.site_id:
            parts.append(self.site["name"])
        if self.audit_id:
            parts.append(self.db.audit(self.audit_id)["name"])
        return " › ".join(parts) if parts else "Aucun audit sélectionné"

    def emit_event(self, ev: Event):
        if ev.audit_id is None:
            ev.audit_id = self.audit_id
        if ev.audit_id:
            try:
                self.db.add_event(ev)
            except Exception:
                pass
        self.event.emit(ev)

    def require_audit(self, parent) -> bool:
        if self.audit_id:
            return True
        QMessageBox.information(parent, "Audit requis",
                                "Sélectionnez ou créez d'abord un audit dans « Clients & sites » "
                                "(Client › Site › Audit). Les mesures y seront enregistrées.")
        return False

    def _on_scan(self, res):
        if res.tag not in (None, "auto", "scan"):
            return                      # scans de relevé / supervision : gérés par leur page
        self.last_scan_ts = res.ts
        for b in res.bss:
            self.aps[b.bssid] = b
        if self.audit_id:
            self.db.save_scan(self.audit_id, res.bss, kind="scan", ts=res.ts)
        self.aps_changed.emit()

    def ingest(self, bss: list[Bss]):
        """Ajoute des résultats issus d'un relevé ou de la supervision."""
        for b in bss:
            self.aps[b.bssid] = b
        self.aps_changed.emit()


# ------------------------------------------------------------ notifications
class Toast(QFrame):
    def __init__(self, ev: Event, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setFixedWidth(380)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 12, 0)
        bar = QFrame()
        bar.setFixedWidth(5)
        col = theme.level_color({ERROR: 3, WARNING: 2, SUCCESS: 0}.get(ev.level, -1))
        bar.setStyleSheet(f"background:{col}; border-top-left-radius:10px; border-bottom-left-radius:10px;")
        lay.addWidget(bar)
        box = QVBoxLayout()
        box.setContentsMargins(6, 10, 0, 10)
        t = QLabel(f"<b style='color:{col}'>{CATEGORY_LABEL.get(ev.category, ev.category)}</b>")
        m = QLabel(ev.message)
        m.setWordWrap(True)
        box.addWidget(t)
        box.addWidget(m)
        lay.addLayout(box, 1)
        self.eff = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.eff)
        self.anim = QPropertyAnimation(self.eff, b"opacity", self)
        self.anim.setDuration(180)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.start()

    def mousePressEvent(self, _e):
        self.close_toast()

    def close_toast(self):
        self.anim.stop()
        self.anim.setStartValue(self.eff.opacity())
        self.anim.setEndValue(0.0)
        self.anim.finished.connect(self.deleteLater)
        self.anim.start()


def _alive(w) -> bool:
    try:
        w.objectName()
        return True
    except RuntimeError:
        return False


class Notifier:
    def __init__(self, window, tray: QSystemTrayIcon | None, settings):
        self.window, self.tray, self.settings = window, tray, settings
        self.toasts: list[Toast] = []
        self._last_tray = 0.0

    def notify(self, ev: Event):
        if ev.level not in (ERROR, WARNING, SUCCESS) or not self.settings.get("ui.notifications", True):
            return
        self.toasts = [t for t in self.toasts if _alive(t)]
        while len(self.toasts) >= 4:
            self.toasts.pop(0).close_toast()
        t = Toast(ev, self.window)
        t.adjustSize()
        t.show()
        t.raise_()
        self.toasts.append(t)
        QTimer.singleShot(12000 if ev.level == ERROR else 7000, lambda: _alive(t) and t.close_toast())
        self.reposition()
        active = self.window.isActiveWindow() and not self.window.isMinimized()
        if self.tray and not active and self.settings.get("ui.tray_notifications", True) \
                and time.monotonic() - self._last_tray > 5:
            self._last_tray = time.monotonic()
            icon = QSystemTrayIcon.Critical if ev.level == ERROR else (
                QSystemTrayIcon.Warning if ev.level == WARNING else QSystemTrayIcon.Information)
            self.tray.showMessage("WiFi Audit Pro", ev.message, icon, 6000)
        if ev.level == ERROR:
            QApplication.beep()

    def reposition(self):
        self.toasts = [t for t in self.toasts if _alive(t)]
        y = self.window.height() - 16
        for t in reversed(self.toasts):
            t.adjustSize()
            y -= t.height()
            t.move(self.window.width() - t.width() - 18, y)
            y -= 8
