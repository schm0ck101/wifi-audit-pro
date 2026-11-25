"""Thèmes clair / sombre : palette, feuille de style Qt et icônes."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication

ICON_FONT = "Segoe Fluent Icons"
ICON_FONT_FALLBACK = "Segoe MDL2 Assets"

# Glyphes Segoe MDL2 / Fluent Icons
G_DASHBOARD = ""
G_MONITOR = ""
G_HISTORY = ""
G_EVENTS = ""
G_NETWORK = ""
G_SETTINGS = ""
G_INFO = ""
G_PLAY = ""
G_PAUSE = ""
G_STOP = ""
G_REFRESH = ""
G_EXPORT = ""
G_PDF = ""
G_DELETE = ""
G_COMPARE = ""
G_OPEN = ""
G_LIVE = ""
G_ADD = ""
G_STAR = ""


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str
    surface: str
    surface2: str
    border: str
    text: str
    muted: str
    accent: str
    accent_text: str
    ok: str
    warn: str
    bad: str
    crit: str
    info: str
    grid: str
    chart_bg: str
    selection: str


DARK = Palette("dark", bg="#0f131a", surface="#161c25", surface2="#1d2531",
               border="#283241", text="#e6ebf2", muted="#8a96a8", accent="#3d9bff",
               accent_text="#ffffff", ok="#2ecc71", warn="#f4c430", bad="#ff8c1a",
               crit="#ff4d4f", info="#6c7a8e", grid="#263040", chart_bg="#121821",
               selection="#24476e")
LIGHT = Palette("light", bg="#eef1f5", surface="#ffffff", surface2="#f4f6f9",
                border="#d6dde6", text="#18202c", muted="#5b6879", accent="#1f6fd1",
                accent_text="#ffffff", ok="#1e9e55", warn="#d4a600", bad="#e67300",
                crit="#d9343a", info="#8a96a6", grid="#e3e8ee", chart_bg="#ffffff",
                selection="#cfe2fb")

_current = DARK


def palette() -> Palette:
    return _current


def level_color(level: int) -> str:
    """Couleur associée à un niveau (-1 info, 0 ok, 1 jaune, 2 orange, 3 rouge)."""
    p = _current
    return {-1: p.info, 0: p.ok, 1: p.warn, 2: p.bad, 3: p.crit}.get(level, p.muted)


def icon_font(size: int) -> QFont:
    f = QFont(ICON_FONT)
    f.setFamilies([ICON_FONT, ICON_FONT_FALLBACK])
    f.setPixelSize(size)
    return f


def glyph_icon(glyph: str, color: str | None = None, size: int = 20) -> QIcon:
    """Icône générée à partir d'un glyphe de police système Windows."""
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setFont(icon_font(int(size * 0.8)))
    p.setPen(QColor(color or _current.text))
    p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, glyph)
    p.end()
    return QIcon(pm)


def app_icon_pixmap(size: int = 256) -> QPixmap:
    """Icône de l'application : ondes Wi‑Fi colorées (vert → rouge) sur une
    loupe d'audit, dessinées par programme (aussi utilisée pour le .ico)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    r = size * 0.18
    path = QPainterPath()
    path.addRoundedRect(QRectF(size * 0.04, size * 0.04, size * 0.92, size * 0.92), r, r)
    p.fillPath(path, QColor("#123a3a"))
    cx, cy = size * 0.44, size * 0.66
    cols = ["#2ecc71", "#f4c430", "#ff8c1a"]
    for i, col in enumerate(cols):
        rad = size * (0.16 + 0.13 * i)
        pen = QPen(QColor(col), size * 0.065)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(QRectF(cx - rad, cy - rad, 2 * rad, 2 * rad), 45 * 16, 90 * 16)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#ffffff"))
    p.drawEllipse(QPointF(cx, cy), size * 0.055, size * 0.055)
    # loupe
    pen = QPen(QColor("#ffffff"), size * 0.05)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    lx, ly, lr = size * 0.70, size * 0.68, size * 0.12
    p.drawEllipse(QPointF(lx, ly), lr, lr)
    p.drawLine(QPointF(lx + lr * 0.7, ly + lr * 0.7), QPointF(size * 0.88, size * 0.88))
    p.end()
    return pm



# Glyphes propres à WiFi Audit Pro
G_CLIENTS = "\uE716"
G_WIFI = "\uE701"
G_CHANNELS = "\uE9D2"
G_MAP = "\uE707"
G_SUPERVISION = "\uE7B3"
G_TESTS = "\uE9F5"
G_REPORT = "\uE8A5"


def stylesheet(p: Palette) -> str:
    return f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI"; font-size: 10pt; }}
QMainWindow, QWidget#Central, QDialog, QMessageBox, QInputDialog {{ background: {p.bg}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}
QWidget {{ color: {p.text}; }}
QToolTip {{ background: {p.surface2}; color: {p.text}; border: 1px solid {p.border}; padding: 4px; }}

/* ---------- Barre latérale ---------- */
QFrame#Sidebar {{ background: {p.surface}; border-right: 1px solid {p.border}; }}
QLabel#AppTitle {{ font-size: 13pt; font-weight: 600; }}
QLabel#AppSubtitle {{ color: {p.muted}; font-size: 8.5pt; }}
QPushButton#NavButton {{
    text-align: left; padding: 9px 12px; border: none; border-radius: 8px;
    background: transparent; color: {p.muted}; font-size: 10.5pt;
}}
QPushButton#NavButton:hover {{ background: {p.surface2}; color: {p.text}; }}
QPushButton#NavButton:checked {{ background: {p.selection}; color: {p.text}; font-weight: 600; }}

/* ---------- Cartes et panneaux ---------- */
QFrame#Card, QFrame#Panel {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 10px; }}
QFrame#Card:hover {{ border-color: {p.accent}; }}
QFrame#StatCard {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 10px; }}
QLabel#StatTitle {{ color: {p.muted}; font-size: 8.5pt; }}
QLabel#StatValue {{ font-size: 17pt; font-weight: 600; }}
QLabel#StatSub {{ color: {p.muted}; font-size: 8pt; }}
QLabel#PageTitle {{ font-size: 16pt; font-weight: 600; }}
QLabel#SectionTitle {{ font-size: 11pt; font-weight: 600; }}
QLabel#Muted {{ color: {p.muted}; }}
QLabel#TargetTitle {{ font-size: 14pt; font-weight: 600; }}

/* ---------- Champs ---------- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateTimeEdit, QPlainTextEdit, QTextEdit, QTextBrowser {{
    background: {p.surface2}; border: 1px solid {p.border}; border-radius: 6px;
    padding: 5px 8px; selection-background-color: {p.accent};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {p.accent}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {p.surface}; border: 1px solid {p.border};
    selection-background-color: {p.selection}; selection-color: {p.text}; outline: none; }}
QCheckBox {{ spacing: 8px; }}

/* ---------- Boutons ---------- */
QPushButton {{
    background: {p.surface2}; border: 1px solid {p.border}; border-radius: 6px;
    padding: 6px 14px;
}}
QPushButton:hover {{ border-color: {p.accent}; }}
QPushButton:pressed {{ background: {p.border}; }}
QPushButton:disabled {{ color: {p.muted}; border-color: {p.border}; }}
QPushButton#Primary {{ background: {p.accent}; color: {p.accent_text}; border: 1px solid {p.accent}; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {QColor(p.accent).lighter(112).name()}; }}
QPushButton#Danger:hover {{ border-color: {p.crit}; color: {p.crit}; }}
QPushButton#Chip {{ border-radius: 12px; padding: 4px 12px; font-size: 9pt; }}

/* ---------- Onglets ---------- */
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{
    background: transparent; color: {p.muted}; padding: 8px 14px; margin-right: 2px;
    border: none; border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{ color: {p.text}; border-bottom: 2px solid {p.accent}; }}
QTabBar::tab:hover {{ color: {p.text}; }}

/* ---------- Tableaux ---------- */
QTableView, QTreeView, QListWidget {{
    background: {p.surface}; alternate-background-color: {p.surface2};
    border: 1px solid {p.border}; border-radius: 8px; gridline-color: {p.border};
    selection-background-color: {p.selection}; selection-color: {p.text};
}}
QHeaderView::section {{
    background: {p.surface2}; color: {p.muted}; border: none;
    border-bottom: 1px solid {p.border}; padding: 6px; font-weight: 600;
}}
QTableCornerButton::section {{ background: {p.surface2}; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {p.border}; border-radius: 5px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {p.border}; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QSplitter::handle {{ background: {p.bg}; }}
QGroupBox {{ border: 1px solid {p.border}; border-radius: 8px; margin-top: 14px; padding: 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {p.muted}; font-weight: 600; }}
QMenu {{ background: {p.surface}; border: 1px solid {p.border}; padding: 4px; }}
QMenu::item {{ padding: 6px 20px; border-radius: 4px; }}
QMenu::item:selected {{ background: {p.selection}; }}

/* ---------- Notifications ---------- */
QFrame#Toast {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 10px; }}
QFrame#TopBar {{ background: {p.surface}; border-bottom: 1px solid {p.border}; }}
"""


def apply_theme(name: str) -> Palette:
    global _current
    _current = LIGHT if name == "light" else DARK
    app = QApplication.instance()
    if app:
        app.setStyleSheet(stylesheet(_current))
    return _current


# ---------------------------------------------------------------- signature
def asset_path(name: str):
    """Chemin d'un fichier de assets/ (sources ou exécutable PyInstaller)."""
    import sys
    from pathlib import Path
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / "assets" / name


def brand_pixmap(height: int = 18) -> QPixmap:
    """Logo SCHMOCK (orange, fond transparent) à la hauteur demandée."""
    pm = QPixmap(str(asset_path("schmock.png")))
    if pm.isNull():
        return pm
    return pm.scaledToHeight(height, Qt.SmoothTransformation)
