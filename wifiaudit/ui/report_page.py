"""Page « Rapport » : aperçu des constats et génération PDF / Excel."""
from __future__ import annotations

import re
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                               QMessageBox, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTextBrowser,
                               QVBoxLayout, QWidget)

from ..audit import findings as fnd
from ..report.build import SEV_COLOR, export_excel, export_pdf, gather
from .common import muted, page_title


class ReportPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.data = None
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        head = QHBoxLayout()
        head.addWidget(page_title("Rapport d'audit"))
        head.addStretch(1)
        self.ctx_lbl = muted("")
        head.addWidget(self.ctx_lbl)
        root.addLayout(head)
        bar = QHBoxLayout()
        b = QPushButton("Analyser l'audit")
        b.clicked.connect(self.analyse)
        self.pdf_btn = QPushButton("Générer le rapport PDF…")
        self.pdf_btn.setObjectName("Primary")
        self.pdf_btn.clicked.connect(self.make_pdf)
        self.xls_btn = QPushButton("Exporter Excel…")
        self.xls_btn.clicked.connect(self.make_excel)
        for w in (b, self.pdf_btn, self.xls_btn):
            bar.addWidget(w)
        bar.addStretch(1)
        self.score = QLabel("")
        self.score.setStyleSheet("font-size: 16pt; font-weight: 600;")
        bar.addWidget(self.score)
        root.addLayout(bar)
        root.addWidget(muted("L'identité de votre société (nom, auditeur, coordonnées, logo) se règle dans les "
                             "Paramètres et figure sur la page de garde."))
        split = QSplitter(Qt.Vertical)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Gravité", "Catégorie", "Constat", "Éléments"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._sel)
        split.addWidget(self.table)
        self.detail = QTextBrowser()
        split.addWidget(self.detail)
        split.setSizes([400, 250])
        root.addWidget(split, 1)
        ctx.context_changed.connect(self._ctx)
        self._ctx()

    def _ctx(self):
        self.ctx_lbl.setText(self.ctx.label())
        self.data = None
        self.table.setRowCount(0)
        self.detail.clear()
        self.score.setText("")
        ok = bool(self.ctx.audit_id)
        self.pdf_btn.setEnabled(ok)
        self.xls_btn.setEnabled(ok)

    def showEvent(self, e):
        super().showEvent(e)
        if self.ctx.audit_id and self.data is None:
            self.analyse()

    def analyse(self):
        if not self.ctx.require_audit(self):
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.data = gather(self.ctx.db, self.ctx.settings, self.ctx.audit_id, with_images=False)
        finally:
            QApplication.restoreOverrideCursor()
        f = self.data.findings
        self.table.setRowCount(len(f))
        for i, x in enumerate(f):
            it = QTableWidgetItem(x.severity)
            it.setForeground(QColor(SEV_COLOR[x.severity]))
            self.table.setItem(i, 0, it)
            self.table.setItem(i, 1, QTableWidgetItem(x.category))
            self.table.setItem(i, 2, QTableWidgetItem(x.title))
            self.table.setItem(i, 3, QTableWidgetItem(str(len(x.items)) if x.items else ""))
        s = self.data.score
        col = "#2b8a3e" if s >= 80 else "#e8590c" if s >= 50 else "#c92a2a"
        self.score.setText(f"<span style='color:{col}'>Note : {s}/100</span>")
        if not f:
            self.detail.setHtml("<p>Aucun écart relevé pour l'instant. Réalisez des scans, un relevé sur plan "
                                "et déclarez les SSID du client pour une analyse complète.</p>")

    def _sel(self):
        r = self.table.currentRow()
        if not self.data or r < 0:
            return
        f = self.data.findings[r]
        items = "".join(f"<li>{x}</li>" for x in f.items)
        self.detail.setHtml(f"<h3 style='color:{SEV_COLOR[f.severity]}'>{f.title}</h3><p>{f.detail}</p>"
                            f"<p><b>Recommandation :</b> {f.recommendation}</p><ul>{items}</ul>")

    def _name(self, ext):
        a = self.ctx.db.audit(self.ctx.audit_id)
        s = self.ctx.site
        c = self.ctx.db.client(s["client_id"])
        base = re.sub(r"[^\w.-]+", "_", f"Audit_WiFi_{c['name']}_{s['name']}_{time.strftime('%Y%m%d')}")
        return str(Path.home() / "Documents" / f"{base}.{ext}")

    def make_pdf(self):
        if not self.ctx.require_audit(self):
            return
        path, _ = QFileDialog.getSaveFileName(self, "Rapport PDF", self._name("pdf"), "PDF (*.pdf)")
        if not path:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            data = gather(self.ctx.db, self.ctx.settings, self.ctx.audit_id, with_images=True)
            export_pdf(data, path, self.ctx.settings.section("thresholds"),
                       self.ctx.settings.get("report.logo", ""))
        except OSError as exc:
            QMessageBox.warning(self, "Rapport", f"Écriture impossible : {exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()
        if QMessageBox.question(self, "Rapport", f"Rapport créé :\n{path}\n\nL'ouvrir maintenant ?") == QMessageBox.Yes:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def make_excel(self):
        if not self.ctx.require_audit(self):
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export Excel", self._name("xlsx"), "Excel (*.xlsx)")
        if not path:
            return
        try:
            export_excel(gather(self.ctx.db, self.ctx.settings, self.ctx.audit_id, with_images=False), path)
            QMessageBox.information(self, "Export", f"Classeur créé :\n{path}")
        except OSError as exc:
            QMessageBox.warning(self, "Export", f"Écriture impossible : {exc}")
