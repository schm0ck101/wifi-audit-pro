"""Page « Clients & sites » : clients, sites (SSID du client), étages
(plans), audits et supervisions."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTableWidget,
                               QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .common import NumItem, fmt_ts, muted, page_title, panel
from .plan_view import CalibrateDialog, image_to_png, import_plan, png_to_image


class FormDialog(QDialog):
    def __init__(self, title: str, fields: list[tuple[str, str, str]], data: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(480)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.w = {}
        for key, label, kind in fields:
            if kind == "text":
                e = QPlainTextEdit(str(data.get(key, "") or ""))
                e.setFixedHeight(80)
            else:
                e = QLineEdit(str(data.get(key, "") or ""))
            self.w[key] = e
            form.addRow(label, e)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Enregistrer")
        bb.button(QDialogButtonBox.Cancel).setText("Annuler")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def values(self) -> dict:
        return {k: (e.toPlainText() if isinstance(e, QPlainTextEdit) else e.text()).strip()
                for k, e in self.w.items()}


CLIENT_FIELDS = [("name", "Nom *", "line"), ("contact", "Contact", "line"), ("email", "E‑mail", "line"),
                 ("phone", "Téléphone", "line"), ("address", "Adresse", "line"), ("notes", "Notes", "text")]
SITE_FIELDS = [("name", "Nom du site *", "line"), ("address", "Adresse", "line"),
               ("own_ssids", "SSID du client (séparés par des virgules)", "line"), ("notes", "Notes", "text")]


class ClientsPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.db = ctx.db
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.addWidget(page_title("Clients & sites"))
        root.addWidget(muted("Organisez vos audits par client et par site. Déclarez les SSID du client : "
                             "l'analyse distingue ses réseaux de ceux du voisinage. Ouvrez un audit pour y "
                             "enregistrer scans, relevés et tests."))
        split = QSplitter(Qt.Horizontal)
        root.addWidget(split, 1)

        # --- arbre clients/sites ------------------------------------------
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Clients et sites"])
        self.tree.itemSelectionChanged.connect(self._tree_sel)
        ll.addWidget(self.tree, 1)
        b = QHBoxLayout()
        for txt, fn in (("+ Client", self.new_client), ("+ Site", self.new_site),
                        ("Modifier", self.edit_item), ("Supprimer", self.delete_item)):
            btn = QPushButton(txt)
            btn.clicked.connect(fn)
            b.addWidget(btn)
        ll.addLayout(b)
        split.addWidget(left)

        # --- détail du site --------------------------------------------------
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 0, 0, 0)
        self.head = QLabel("Sélectionnez un site")
        self.head.setObjectName("SectionTitle")
        self.sub = muted("")
        rl.addWidget(self.head)
        rl.addWidget(self.sub)

        fp, fl = panel()
        fl.addWidget(QLabel("<b>Étages (plans)</b>"))
        self.floors = QListWidget()
        self.floors.setMaximumHeight(130)
        fl.addWidget(self.floors)
        fb = QHBoxLayout()
        for txt, fn in (("Importer un plan…", self.add_floor), ("Étalonner l'échelle", self.calibrate),
                        ("Renommer", self.rename_floor), ("Supprimer", self.delete_floor)):
            btn = QPushButton(txt)
            btn.clicked.connect(fn)
            fb.addWidget(btn)
        fb.addStretch(1)
        fl.addLayout(fb)
        rl.addWidget(fp)

        ap, al = panel()
        al.addWidget(QLabel("<b>Audits et supervisions</b>"))
        self.audits = QTableWidget(0, 6)
        self.audits.setHorizontalHeaderLabels(["Nom", "Type", "Début", "Auditeur", "Points de relevé",
                                               "Points d'accès"])
        self.audits.verticalHeader().hide()
        self.audits.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.audits.setSelectionMode(QAbstractItemView.SingleSelection)
        self.audits.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.audits.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.audits.doubleClicked.connect(self.open_audit)
        al.addWidget(self.audits, 1)
        ab = QHBoxLayout()
        for txt, fn, primary in (("Ouvrir (audit courant)", self.open_audit, True),
                                 ("Nouvel audit", lambda: self.new_audit("audit"), False),
                                 ("Nouvelle supervision", lambda: self.new_audit("supervision"), False),
                                 ("Renommer", self.rename_audit, False), ("Supprimer", self.delete_audit, False)):
            btn = QPushButton(txt)
            if primary:
                btn.setObjectName("Primary")
            btn.clicked.connect(fn)
            ab.addWidget(btn)
        ab.addStretch(1)
        al.addLayout(ab)
        rl.addWidget(ap, 1)
        split.addWidget(right)
        split.setSizes([320, 900])
        self.reload()

    # ------------------------------------------------------------- arbre
    def reload(self, select=None):
        self.tree.clear()
        target = None
        for c in self.db.clients():
            ci = QTreeWidgetItem([c["name"]])
            ci.setData(0, Qt.UserRole, ("client", c["id"]))
            self.tree.addTopLevelItem(ci)
            for s in self.db.sites(c["id"]):
                si = QTreeWidgetItem([s["name"]])
                si.setData(0, Qt.UserRole, ("site", s["id"]))
                ci.addChild(si)
                if select == ("site", s["id"]) or (select is None and s["id"] == self.ctx.site_id):
                    target = si
            if select == ("client", c["id"]):
                target = ci
            ci.setExpanded(True)
        if target:
            self.tree.setCurrentItem(target)
        self._tree_sel()

    def _sel(self):
        it = self.tree.currentItem()
        return it.data(0, Qt.UserRole) if it else None

    def _site_id(self):
        s = self._sel()
        return s[1] if s and s[0] == "site" else None

    def _tree_sel(self):
        sid = self._site_id()
        self.floors.clear()
        self.audits.setRowCount(0)
        if not sid:
            sel = self._sel()
            if sel:
                c = self.db.client(sel[1])
                self.head.setText(c["name"])
                self.sub.setText(" · ".join(x for x in (c["contact"], c["email"], c["phone"], c["address"]) if x)
                                 or "Ajoutez un site à ce client (bouton « + Site »).")
            else:
                self.head.setText("Sélectionnez un site")
                self.sub.setText("Créez un client puis un site pour commencer.")
            return
        s = self.db.site(sid)
        self.head.setText(s["name"])
        self.sub.setText(f"{s['address'] or 'Adresse non renseignée'} · SSID du client : "
                         f"{', '.join(s['own_ssids']) or 'non déclarés'}")
        for f in self.db.floors(sid):
            sc = f"{1 / f['scale']:.1f} px/m" if f["scale"] else "non étalonné"
            it = QListWidgetItem(f"{f['name']}   ({f['width']}×{f['height']} px, {sc})")
            it.setData(Qt.UserRole, f["id"])
            self.floors.addItem(it)
        rows = self.db.audits(sid)
        self.audits.setRowCount(len(rows))
        for i, a in enumerate(rows):
            name = QTableWidgetItem(("▶ " if a["id"] == self.ctx.audit_id else "") + a["name"])
            name.setData(Qt.UserRole, a["id"])
            self.audits.setItem(i, 0, name)
            self.audits.setItem(i, 1, QTableWidgetItem("Supervision" if a["kind"] == "supervision" else "Audit"))
            self.audits.setItem(i, 2, NumItem(fmt_ts(a["started"]), a["started"]))
            self.audits.setItem(i, 3, QTableWidgetItem(a["auditor"] or ""))
            self.audits.setItem(i, 4, NumItem(str(a["points"]), a["points"]))
            self.audits.setItem(i, 5, NumItem(str(a["ap_count"]), a["ap_count"]))

    # ----------------------------------------------------------- clients
    def new_client(self):
        d = FormDialog("Nouveau client", CLIENT_FIELDS, {}, self)
        if d.exec() and d.values()["name"]:
            cid = self.db.save_client(d.values())
            self.reload(("client", cid))

    def new_site(self):
        sel = self._sel()
        if not sel:
            QMessageBox.information(self, "Site", "Sélectionnez d'abord un client.")
            return
        cid = sel[1] if sel[0] == "client" else self.db.site(sel[1])["client_id"]
        d = FormDialog("Nouveau site", SITE_FIELDS, {}, self)
        if d.exec() and d.values()["name"]:
            v = d.values()
            v["own_ssids"] = [x.strip() for x in v["own_ssids"].split(",") if x.strip()]
            v["client_id"] = cid
            sid = self.db.save_site(v)
            self.reload(("site", sid))

    def edit_item(self):
        sel = self._sel()
        if not sel:
            return
        if sel[0] == "client":
            data = self.db.client(sel[1])
            d = FormDialog("Modifier le client", CLIENT_FIELDS, data, self)
            if d.exec() and d.values()["name"]:
                self.db.save_client({**d.values(), "id": sel[1]})
        else:
            data = self.db.site(sel[1])
            data = {**data, "own_ssids": ", ".join(data["own_ssids"])}
            d = FormDialog("Modifier le site", SITE_FIELDS, data, self)
            if d.exec() and d.values()["name"]:
                v = d.values()
                v["own_ssids"] = [x.strip() for x in v["own_ssids"].split(",") if x.strip()]
                self.db.save_site({**v, "id": sel[1]})
        self.reload(sel)
        self.ctx.context_changed.emit()

    def delete_item(self):
        sel = self._sel()
        if not sel:
            return
        what = "ce client, tous ses sites et audits" if sel[0] == "client" else "ce site et tous ses audits"
        if QMessageBox.question(self, "Supprimer", f"Supprimer définitivement {what} ?") != QMessageBox.Yes:
            return
        if sel[0] == "client":
            self.db.delete_client(sel[1])
            if self.ctx.client_id == sel[1]:
                self.ctx.set_context()
        else:
            self.db.delete_site(sel[1])
            if self.ctx.site_id == sel[1]:
                self.ctx.set_context(self.ctx.client_id)
        self.reload()

    # ------------------------------------------------------------ étages
    def _floor_id(self):
        it = self.floors.currentItem()
        return it.data(Qt.UserRole) if it else None

    def add_floor(self):
        sid = self._site_id()
        if not sid:
            QMessageBox.information(self, "Plan", "Sélectionnez un site.")
            return
        res = import_plan(self)
        if not res:
            return
        name, img = res
        name, ok = QInputDialog.getText(self, "Étage", "Nom de l'étage :", text=name)
        if not ok:
            return
        fid = self.db.add_floor(sid, name or "Étage", image_to_png(img), img.width(), img.height())
        self._tree_sel()
        if QMessageBox.question(self, "Échelle", "Étalonner l'échelle maintenant ? "
                                "(nécessaire pour des distances et rayons exacts)") == QMessageBox.Yes:
            self._calibrate(fid)
        self.ctx.context_changed.emit()

    def calibrate(self):
        fid = self._floor_id()
        if fid:
            self._calibrate(fid)

    def _calibrate(self, fid):
        img = png_to_image(self.db.floor_image(fid))
        d = CalibrateDialog(img, self)
        if d.exec() and d.scale:
            self.db.update_floor(fid, scale=d.scale)
            self._tree_sel()
            self.ctx.context_changed.emit()

    def rename_floor(self):
        fid = self._floor_id()
        if not fid:
            return
        name, ok = QInputDialog.getText(self, "Étage", "Nouveau nom :", text=self.floors.currentItem().text().split("   (")[0])
        if ok and name:
            self.db.update_floor(fid, name=name)
            self._tree_sel()
            self.ctx.context_changed.emit()

    def delete_floor(self):
        fid = self._floor_id()
        if fid and QMessageBox.question(self, "Étage", "Supprimer ce plan ? Les points de relevé associés "
                                        "seront détachés du plan.") == QMessageBox.Yes:
            self.db.delete_floor(fid)
            self._tree_sel()
            self.ctx.context_changed.emit()

    # ------------------------------------------------------------ audits
    def _audit_id(self):
        r = self.audits.currentRow()
        it = self.audits.item(r, 0) if r >= 0 else None
        return it.data(Qt.UserRole) if it else None

    def new_audit(self, kind):
        sid = self._site_id()
        if not sid:
            QMessageBox.information(self, "Audit", "Sélectionnez un site.")
            return
        default = ("Audit" if kind == "audit" else "Supervision") + time.strftime(" du %d/%m/%Y")
        name, ok = QInputDialog.getText(self, "Nouvel audit" if kind == "audit" else "Nouvelle supervision",
                                        "Nom :", text=default)
        if not ok:
            return
        aid = self.db.create_audit(sid, name or default, kind, self.ctx.settings.get("report.auditor", ""))
        site = self.db.site(sid)
        self.ctx.set_context(site["client_id"], sid, aid)
        self._tree_sel()

    def open_audit(self, *_):
        aid = self._audit_id()
        sid = self._site_id()
        if aid and sid:
            self.ctx.set_context(self.db.site(sid)["client_id"], sid, aid)
            self._tree_sel()

    def rename_audit(self):
        aid = self._audit_id()
        if not aid:
            return
        a = self.db.audit(aid)
        name, ok = QInputDialog.getText(self, "Renommer", "Nom :", text=a["name"])
        if ok and name:
            self.db.update_audit(aid, name=name)
            self._tree_sel()
            self.ctx.context_changed.emit()

    def delete_audit(self):
        aid = self._audit_id()
        if aid and QMessageBox.question(self, "Supprimer", "Supprimer cet audit et toutes ses mesures ?") \
                == QMessageBox.Yes:
            self.db.delete_audit(aid)
            if self.ctx.audit_id == aid:
                self.ctx.set_context(self.ctx.client_id, self.ctx.site_id, None)
            self._tree_sel()
