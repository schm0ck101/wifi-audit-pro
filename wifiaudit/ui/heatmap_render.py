"""Mise en couleur des cartes de couverture et légendes (interface + rapport)."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPen

from ..audit.heatmap import HeatmapResult

CATEGORICAL = ["#4dabf7", "#f783ac", "#69db7c", "#ffa94d", "#b197fc", "#ffe066", "#38d9a9",
               "#ff8787", "#74c0fc", "#d0bfff", "#8ce99a", "#ffc078"]


def stops_for(kind: str, th: dict) -> tuple[list[tuple[float, str]], str, bool]:
    """(paliers valeur→couleur, unité, discret)."""
    t, m = th.get("rssi_target", -67), th.get("rssi_min", -75)
    if kind in ("signal", "second"):
        return ([(-90, "#7f1d1d"), (-82, "#e03131"), (m, "#ff922b"), (m + 4, "#ffd43b"), (t, "#a9e34b"),
                 (t + 7, "#40c057"), (-45, "#1b7a33")], "dBm", False)
    if kind == "snr":
        s = th.get("snr_target", 25)
        return ([(0, "#7f1d1d"), (10, "#e03131"), (15, "#ff922b"), (20, "#ffd43b"), (s, "#a9e34b"),
                 (s + 10, "#40c057"), (50, "#1b7a33")], "dB", False)
    if kind == "count":
        return ([(0, "#e03131"), (1, "#ff922b"), (2, "#a9e34b"), (3, "#40c057")], "bornes", True)
    if kind == "cochannel":
        return ([(0, "#40c057"), (1, "#ffd43b"), (2, "#ff922b"), (3, "#e03131")], "bornes", True)
    if kind == "rate":
        return ([(0, "#7f1d1d"), (36, "#e03131"), (144, "#ff922b"), (288, "#ffd43b"), (576, "#a9e34b"),
                 (961, "#40c057"), (1201, "#1b7a33")], "Mb/s", False)
    if kind == "latency":
        return ([(2, "#1b7a33"), (10, "#40c057"), (30, "#a9e34b"), (60, "#ffd43b"), (100, "#ff922b"),
                 (200, "#e03131")], "ms", False)
    if kind == "throughput":
        return ([(0, "#7f1d1d"), (10, "#e03131"), (30, "#ff922b"), (60, "#ffd43b"), (120, "#a9e34b"),
                 (300, "#40c057"), (600, "#1b7a33")], "Mb/s", False)
    return ([(0, "#888888"), (1, "#888888")], "", False)


def _rgb(hexs):
    return np.array([[QColor(h).red(), QColor(h).green(), QColor(h).blue()] for h in hexs], dtype=np.float32)


def colorize(res: HeatmapResult, th: dict, alpha: float = 0.6) -> QImage:
    g = res.grid
    h, w = g.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    mask = ~np.isnan(g)
    if res.kind == "best_ap":
        cols = _rgb(CATEGORICAL)
        idx = np.where(mask, g, 0).astype(int) % len(CATEGORICAL)
        rgb = cols[idx]
    else:
        stops, _u, discrete = stops_for(res.kind, th)
        xs = np.array([s[0] for s in stops], dtype=np.float32)
        cols = _rgb([s[1] for s in stops])
        v = np.where(mask, g, xs[0])
        if discrete:
            idx = np.clip(np.searchsorted(xs, v, side="right") - 1, 0, len(xs) - 1)
            rgb = cols[idx]
        else:
            rgb = np.stack([np.interp(v, xs, cols[:, c]) for c in range(3)], axis=-1)
    rgba[..., :3] = rgb.astype(np.uint8)
    rgba[..., 3] = np.where(mask, int(alpha * 255), 0)
    img = QImage(rgba.data, w, h, w * 4, QImage.Format_RGBA8888)
    return img.copy()       # détache du tampon numpy


def legend_image(kind: str, th: dict, categories: list[str] | None = None, width=300, height=64,
                 text_color="#e6ebf2") -> QImage:
    img = QImage(width, height, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    f = QFont("Segoe UI")
    f.setPixelSize(11)
    p.setFont(f)
    p.setPen(QColor(text_color))
    if kind == "best_ap":
        x, y = 4, 4
        for i, c in enumerate(categories or []):
            p.fillRect(QRectF(x, y, 12, 12), QColor(CATEGORICAL[i % len(CATEGORICAL)]))
            p.drawText(QRectF(x + 16, y - 2, 130, 16), Qt.AlignLeft | Qt.AlignVCenter, c)
            x += 148
            if x > width - 140:
                x, y = 4, y + 18
        p.end()
        return img
    stops, unit, discrete = stops_for(kind, th)
    lo, hi = stops[0][0], stops[-1][0]
    bar = QRectF(14, 6, width - 28, 18)
    if discrete:
        n = len(stops)
        for i, (v, c) in enumerate(stops):
            r = QRectF(bar.x() + i * bar.width() / n, bar.y(), bar.width() / n, bar.height())
            p.fillRect(r, QColor(c))
            lab = f"{v:g}" + ("+" if i == n - 1 else "")
            p.drawText(QRectF(r.x(), bar.bottom() + 2, r.width(), 16), Qt.AlignCenter, f"{lab} {unit}")
    else:
        grad = QLinearGradient(bar.left(), 0, bar.right(), 0)
        for v, c in stops:
            grad.setColorAt((v - lo) / (hi - lo), QColor(c))
        p.fillRect(bar, grad)
        p.setPen(QPen(QColor(text_color)))
        for v, _c in stops:
            x = bar.left() + bar.width() * (v - lo) / (hi - lo)
            p.drawLine(int(x), int(bar.bottom()), int(x), int(bar.bottom() + 3))
            r = QRectF(min(max(x - 24, 0), width - 48), bar.bottom() + 3, 48, 16)
            p.drawText(r, Qt.AlignCenter, f"{v:g}")
        p.drawText(QRectF(0, bar.bottom() + 20, width, 16), Qt.AlignCenter, unit)
    p.end()
    return img


def composite(plan: QImage, res: HeatmapResult | None, th: dict, alpha: float,
              points=None, aps=None, max_side: int = 1800) -> QImage:
    """Plan + carte + points + bornes (pour le rapport / l'export PNG)."""
    k = min(1.0, max_side / max(plan.width(), plan.height()))
    out = plan.scaled(int(plan.width() * k), int(plan.height() * k), Qt.KeepAspectRatio,
                      Qt.SmoothTransformation).convertToFormat(QImage.Format_ARGB32)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    if res is not None:
        over = colorize(res, th, alpha)
        gh, gw = res.grid.shape
        p.drawImage(QRectF(0, 0, gw * res.cell_px * k, gh * res.cell_px * k), over)
    p.setPen(QPen(QColor("#222222"), 1))
    for (x, y) in points or []:
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(x * k - 4, y * k - 4, 8, 8))
    f = QFont("Segoe UI")
    f.setPixelSize(13)
    f.setBold(True)
    p.setFont(f)
    for (x, y, label) in aps or []:
        p.setBrush(QColor("#1f6fd1"))
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.drawEllipse(QRectF(x * k - 9, y * k - 9, 18, 18))
        p.setPen(QColor("#000000"))
        p.drawText(QRectF(x * k + 11, y * k - 9, 260, 20), Qt.AlignLeft | Qt.AlignVCenter, label)
    p.end()
    return out
