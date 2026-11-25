"""Point d'entrée de WiFi Audit Pro."""
from __future__ import annotations

import argparse
import ctypes
import logging
import sys
import threading
import traceback

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from .config import APP_NAME, APP_VERSION, Settings, data_dir
from .events import setup_logging

log = logging.getLogger("wifiaudit")


def _excepthook(t, e, tb):
    log.error("Exception non gérée :\n%s", "".join(traceback.format_exception(t, e, tb)))
    if QApplication.instance() and threading.current_thread() is threading.main_thread():
        QMessageBox.warning(None, APP_NAME, f"Erreur inattendue :\n{e}\n\nDétails : {data_dir() / 'wifiaudit.log'}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="WiFiAuditPro")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args(argv)
    setup_logging(args.debug)
    log.info("Démarrage %s %s", APP_NAME, APP_VERSION)
    sys.excepthook = _excepthook
    threading.excepthook = lambda a: log.error("Exception dans %s", a.thread.name,
                                               exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("WiFiAuditPro.App")
        except Exception:
            pass
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))

    from .core.service import WifiService
    from .store.db import Database
    from .ui import theme
    from .ui.common import AppContext
    from .ui.main_window import MainWindow

    settings = Settings()
    theme.apply_theme(settings.get("ui.theme", "dark"))
    app.setWindowIcon(QIcon(theme.app_icon_pixmap(256)))
    db = Database()
    service = WifiService(settings)
    ctx = AppContext(settings, db, service)
    win = MainWindow(ctx)
    service.start()
    ctx.set_context(ctx.client_id, ctx.site_id, ctx.audit_id)
    win.show()
    code = app.exec()
    db.close()
    return code
