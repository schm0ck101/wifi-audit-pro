"""Service Wi‑Fi : exécute les scans dans un thread dédié (jamais dans
l'interface), en mode ponctuel ou continu, et suit la connexion en cours."""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

from ..wlan.api import Connection, Interface, LocationDenied, WlanError, get_client, reset_client
from ..wlan.model import Bss

log = logging.getLogger(__name__)


@dataclass
class ScanResult:
    ts: float
    bss: list[Bss]
    tag: object = None
    info: dict = field(default_factory=dict)


class WifiService(QObject):
    scan_done = Signal(object)            # ScanResult
    scan_failed = Signal(str, bool, object)   # message, localisation refusée, tag
    connection_changed = Signal(object)   # Connection | None
    availability = Signal(bool, str)      # Wi‑Fi utilisable, message

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self._q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._continuous = 0.0
        self._next_auto = 0.0
        self.iface: Interface | None = None
        self.available = False
        self.message = ""
        self.connection: Connection | None = None
        self.busy = False
        self._conn_interval = 2.0
        self._thread = threading.Thread(target=self._loop, name="wifi-scan", daemon=True)
        self._cthread = threading.Thread(target=self._conn_loop, name="wifi-conn", daemon=True)

    def start(self):
        self.refresh_interfaces()
        self._thread.start()
        self._cthread.start()

    def shutdown(self):
        self._stop.set()
        self._q.put(None)

    # ----------------------------------------------------------- cartes
    def refresh_interfaces(self) -> list[Interface]:
        try:
            ifs = get_client().interfaces()
        except WlanError as exc:
            reset_client()
            self._set_available(False, str(exc))
            return []
        if not ifs:
            self._set_available(False, "Aucune carte Wi‑Fi détectée.")
            return []
        want = self.settings.get("scan.interface", "")
        self.iface = next((i for i in ifs if i.guid == want), ifs[0])
        self._set_available(True, f"{self.iface.description} — {self.iface.state}")
        return ifs

    def interfaces(self) -> list[Interface]:
        try:
            return get_client().interfaces()
        except WlanError:
            return []

    def select_interface(self, guid: str):
        self.settings.set("scan.interface", guid)
        self.refresh_interfaces()

    def _set_available(self, ok: bool, msg: str):
        changed = ok != self.available or msg != self.message
        self.available, self.message = ok, msg
        if changed:
            self.availability.emit(ok, msg)

    # ------------------------------------------------------------- scans
    def request_scan(self, tag=None):
        """Scan ponctuel (résultat via ``scan_done`` avec le même ``tag``)."""
        self._q.put(("scan", tag))

    def set_continuous(self, interval: float):
        """0 = arrêt ; sinon scan automatique toutes les ``interval`` s."""
        self._continuous = max(0.0, float(interval))
        self._next_auto = 0.0
        self._q.put(("wake", None))

    @property
    def continuous(self) -> float:
        return self._continuous

    def set_connection_interval(self, seconds: float):
        self._conn_interval = max(0.25, seconds)

    def _loop(self):
        while not self._stop.is_set():
            timeout = None
            if self._continuous:
                timeout = max(0.0, self._next_auto - time.monotonic())
            try:
                item = self._q.get(timeout=timeout)
            except queue.Empty:
                item = ("scan", "auto")
            if item is None:
                break
            kind, tag = item
            if kind != "scan":
                continue
            if tag == "auto":
                self._next_auto = time.monotonic() + self._continuous
            self._do_scan(tag)

    def _do_scan(self, tag):
        if self.iface is None and not self.refresh_interfaces():
            self.scan_failed.emit(self.message, False, tag)
            return
        self.busy = True
        try:
            client = get_client()
            bss = client.scan(self.iface, max_age=float(self.settings.get("scan.max_age", 20)))
            self._set_available(True, f"{self.iface.description} — {len(bss)} BSS")
            self.scan_done.emit(ScanResult(time.time(), bss, tag, dict(client.last_scan_info)))
        except LocationDenied as exc:
            self.scan_failed.emit(str(exc), True, tag)
        except WlanError as exc:
            log.warning("Scan : %s", exc)
            reset_client()
            self.iface = None
            self._set_available(False, str(exc))
            self.scan_failed.emit(str(exc), False, tag)
        except Exception as exc:
            log.exception("Scan")
            self.scan_failed.emit(f"Erreur inattendue : {exc}", False, tag)
        finally:
            self.busy = False

    # -------------------------------------------------------- connexion
    def _conn_loop(self):
        last_key = None
        while not self._stop.wait(self._conn_interval):
            if not self.available or self.iface is None:
                if not self.available and int(time.monotonic()) % 15 == 0:
                    self.refresh_interfaces()
                continue
            try:
                c = get_client().connection(self.iface)
            except WlanError:
                c = None
            except Exception:
                log.exception("Connexion")
                c = None
            self.connection = c
            key = (c.bssid, c.rssi, c.rx_mbps, c.channel) if c else None
            if key != last_key:
                last_key = key
                self.connection_changed.emit(c)
