"""Affichage d'un plan d'étage (zoom molette, déplacement clic droit/milieu),
import de plans (image ou PDF) et étalonnage de l'échelle."""
from __future__ import annotations

import math

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QGraphicsScene,
                               QGraphicsView, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QVBoxLayout)

MAX_SIDE = 3000


class PlanView(QGraphicsView):
    """Vue de plan. ``clicked(x, y)`` en coordonnées image (clic gauche)."""

    clicked = Signal(float, float)
    hovered = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setMouseTracking(True)
        self.pix_item = None
        self._pan = None
        self.setBackgroundBrush(QColor("#2a2f38"))

    def set_image(self, img: QImage | None):
        self.scene().clear()
        self.pix_item = None
        if img is None or img.isNull():
            return
        self.pix_item = self.scene().addPixmap(QPixmap.fromImage(img))
        self.pix_item.setZValue(-10)
        self.scene().setSceneRect(QRectF(img.rect()).adjusted(-200, -200, 200, 200))
        self.fit()

    def fit(self):
        if self.pix_item:
            self.fitInView(self.pix_item, Qt.KeepAspectRatio)

    def wheelEvent(self, e):
        f = 1.2 if e.angleDelta().y() > 0 else 1 / 1.2
        self.scale(f, f)

    def mousePressEvent(self, e):
        if e.button() in (Qt.RightButton, Qt.MiddleButton):
            self._pan = e.position()
            self.setCursor(Qt.ClosedHandCursor)
            return
        if e.button() == Qt.LeftButton and self.pix_item is not None:
            p = self.mapToScene(e.position().toPoint())
            r = self.pix_item.boundingRect()
            if r.contains(p):
                self.clicked.emit(p.x(), p.y())
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._pan is not None:
            d = e.position() - self._pan
            self._pan = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            return
        p = self.mapToScene(e.position().toPoint())
        self.hovered.emit(p.x(), p.y())
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._pan is not None:
            self._pan = None
            self.setCursor(Qt.ArrowCursor)
            return
        super().mouseReleaseEvent(e)


def image_to_png(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def png_to_image(data: bytes) -> QImage:
    img = QImage()
    img.loadFromData(data)
    return img


def import_plan(parent) -> tuple[str, QImage] | None:
    """Demande un fichier (image ou PDF) et renvoie (nom proposé, image)."""
    path, _ = QFileDialog.getOpenFileName(parent, "Importer un plan d'étage", "",
                                          "Plans (*.png *.jpg *.jpeg *.bmp *.gif *.pdf);;Tous (*.*)")
    if not path:
        return None
    name = path.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
    if path.lower().endswith(".pdf"):
        from PySide6.QtPdf import QPdfDocument
        doc = QPdfDocument(parent)
        doc.load(path)
        n = doc.pageCount()
        if n <= 0:
            QMessageBox.warning(parent, "Plan", "PDF illisible ou vide.")
            return None
        page = 0
        if n > 1:
            page, ok = QInputDialog.getInt(parent, "Plan PDF", f"Page à importer (1 à {n}) :", 1, 1, n)
            if not ok:
                return None
            page -= 1
        sz = doc.pagePointSize(page)
        k = MAX_SIDE / max(sz.width(), sz.height())
        img = doc.render(page, QSize(int(sz.width() * k), int(sz.height() * k)))
        # fond blanc (les PDF sont souvent transparents)
        out = QImage(img.size(), QImage.Format_RGB32)
        out.fill(Qt.white)
        p = QPainter(out)
        p.drawImage(0, 0, img)
        p.end()
        img = out
    else:
        img = QImage(path)
        if img.isNull():
            QMessageBox.warning(parent, "Plan", "Image illisible.")
            return None
    if max(img.width(), img.height()) > MAX_SIDE:
        img = img.scaled(MAX_SIDE, MAX_SIDE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return name, img.convertToFormat(QImage.Format_RGB32)


class CalibrateDialog(QDialog):
    """Étalonnage : deux clics sur le plan + distance réelle -> mètres/pixel."""

    def __init__(self, img: QImage, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Étalonner l'échelle du plan")
        self.resize(1000, 750)
        self.pts: list[QPointF] = []
        self.scale = None
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Cliquez sur deux points dont vous connaissez la distance réelle "
                             "(par exemple les extrémités d'un mur ou d'une cote), puis saisissez cette distance."))
        self.view = PlanView()
        self.view.set_image(img)
        self.view.clicked.connect(self._click)
        lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel("Distance réelle :"))
        self.dist = QDoubleSpinBox()
        self.dist.setRange(0.1, 10000)
        self.dist.setValue(10)
        self.dist.setSuffix(" m")
        row.addWidget(self.dist)
        self.info = QLabel("Points : 0 / 2")
        row.addWidget(self.info, 1)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Valider")
        bb.button(QDialogButtonBox.Cancel).setText("Annuler")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _click(self, x, y):
        if len(self.pts) == 2:
            self.pts = []
            for it in list(self.view.scene().items()):
                if it is not self.view.pix_item:
                    self.view.scene().removeItem(it)
        self.pts.append(QPointF(x, y))
        pen = QPen(QColor("#ff4d4f"), 3)
        pen.setCosmetic(True)
        self.view.scene().addEllipse(x - 6, y - 6, 12, 12, pen)
        if len(self.pts) == 2:
            self.view.scene().addLine(self.pts[0].x(), self.pts[0].y(), x, y, pen)
        self.info.setText(f"Points : {len(self.pts)} / 2")

    def _ok(self):
        if len(self.pts) != 2:
            QMessageBox.information(self, "Étalonnage", "Cliquez deux points sur le plan.")
            return
        d = math.hypot(self.pts[1].x() - self.pts[0].x(), self.pts[1].y() - self.pts[0].y())
        if d < 5:
            QMessageBox.information(self, "Étalonnage", "Les deux points sont trop proches.")
            return
        self.scale = self.dist.value() / d
        self.accept()
