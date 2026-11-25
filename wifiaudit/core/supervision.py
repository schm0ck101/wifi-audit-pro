"""Supervision en direct (mode régie) : suit chaque point d'accès scan après
scan et lève des alertes aux changements d'état (jamais en rafale)."""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from ..events import ERROR, INFO, SUCCESS, WARNING, Event
from ..wlan.model import Bss

ST_OK, ST_UNKNOWN, ST_ROGUE, ST_MISSING, ST_NEIGHBOR = "Autorisé", "Inconnu", "Suspect", "Disparu", "Voisin"


@dataclass
class ApTrack:
    bss: Bss
    status: str
    history: deque = field(default_factory=lambda: deque(maxlen=360))   # (ts, rssi)
    missing: int = 0
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    busy_alert: bool = False


class Supervisor:
    def __init__(self, own_ssids: set[str], authorized: set[str], thresholds: dict,
                 missing_scans: int = 3, emit=None, audit_id=None):
        self.own = set(own_ssids)
        self.authorized = {b.lower() for b in authorized}
        self.th = thresholds
        self.missing_scans = missing_scans
        self.emit = emit or (lambda ev: None)
        self.audit_id = audit_id
        self.tracks: dict[str, ApTrack] = {}
        self.scans = 0
        self.conn_low = False
        self.last_bssid: str | None = None

    def _ev(self, level, cat, msg):
        self.emit(Event(level, cat, msg, self.audit_id))

    def _classify(self, b: Bss) -> str:
        if b.bssid.lower() in self.authorized:
            return ST_OK
        if b.ssid and b.ssid in self.own:
            return ST_ROGUE if self.authorized else ST_UNKNOWN
        return ST_NEIGHBOR

    def update(self, bss: list[Bss], ts: float | None = None) -> None:
        ts = ts or time.time()
        self.scans += 1
        seen = set()
        auth_security = {t.bss.ssid: t.bss.security for t in self.tracks.values() if t.status == ST_OK}
        for b in bss:
            key = b.bssid.lower()
            seen.add(key)
            tr = self.tracks.get(key)
            status = self._classify(b)
            if tr is None:
                tr = ApTrack(b, status)
                self.tracks[key] = tr
                if self.scans > 1:
                    if status == ST_ROGUE:
                        self._ev(ERROR, "rogue", f"Borne NON AUTORISÉE diffusant « {b.ssid} » : {b.bssid} "
                                 f"(canal {b.channel}, {b.rssi} dBm)")
                    elif status == ST_NEIGHBOR and b.rssi >= -60:
                        self._ev(INFO, "ap", f"Nouveau réseau voisin à fort signal : « {b.ssid or 'masqué'} » "
                                 f"{b.bssid} canal {b.channel} ({b.rssi} dBm)")
            else:
                if tr.status == ST_MISSING:
                    self._ev(SUCCESS, "ap", f"Borne « {b.ssid} » {b.bssid} de nouveau visible")
                if tr.bss.channel != b.channel and status == ST_OK:
                    self._ev(WARNING, "channel", f"Borne « {b.ssid} » {b.bssid} : canal {tr.bss.channel} → {b.channel}")
                tr.bss = b
                tr.status = status
            # sécurité différente sur un SSID du client
            if status in (ST_ROGUE, ST_UNKNOWN) and b.ssid in auth_security \
                    and auth_security[b.ssid] != b.security and tr.first_seen == tr.last_seen:
                self._ev(ERROR, "rogue", f"« {b.ssid} » diffusé avec une sécurité différente ({b.security}) "
                         f"par {b.bssid}")
            tr.missing = 0
            tr.last_seen = ts
            tr.history.append((ts, b.rssi))
            u = b.ies.channel_utilization
            if status == ST_OK and u is not None:
                if not tr.busy_alert and u >= self.th.get("util_crit", 75):
                    tr.busy_alert = True
                    self._ev(WARNING, "load", f"Canal {b.channel} de « {b.ssid} » ({b.bssid}) occupé à {u:.0f} % "
                             f"({b.ies.station_count or 0} clients)")
                elif tr.busy_alert and u < self.th.get("util_crit", 75) * 0.85:
                    tr.busy_alert = False
                    self._ev(SUCCESS, "load", f"Occupation du canal {b.channel} revenue à {u:.0f} %")
        for key, tr in self.tracks.items():
            if key in seen:
                continue
            tr.missing += 1
            tr.history.append((ts, None))
            if tr.missing == self.missing_scans and tr.status == ST_OK:
                tr.status = ST_MISSING
                self._ev(ERROR, "ap", f"Borne autorisée « {tr.bss.ssid} » {tr.bss.bssid} invisible depuis "
                         f"{self.missing_scans} scans")

    def update_connection(self, conn, alert_rssi: int, pinger=None, ping_target=None, audit_cb=None):
        """Suit la connexion du poste : signal faible et itinérances."""
        if conn is None:
            if self.last_bssid:
                self._ev(WARNING, "signal", "Poste déconnecté du Wi‑Fi")
            self.last_bssid = None
            return
        if conn.rssi is not None:
            if not self.conn_low and conn.rssi < alert_rssi:
                self.conn_low = True
                self._ev(WARNING, "signal", f"Signal faible sur la connexion : {conn.rssi} dBm ({conn.ssid})")
            elif self.conn_low and conn.rssi >= alert_rssi + 3:
                self.conn_low = False
                self._ev(SUCCESS, "signal", f"Signal de la connexion revenu à {conn.rssi} dBm")
        if self.last_bssid and conn.bssid != self.last_bssid:
            self._ev(INFO, "roam", f"Itinérance : {self.last_bssid} → {conn.bssid} ({conn.ssid}, {conn.rssi} dBm)")
            if audit_cb:
                audit_cb(self.last_bssid, conn.bssid, conn.rssi)
        self.last_bssid = conn.bssid

    def counts(self) -> dict:
        c = {ST_OK: 0, ST_UNKNOWN: 0, ST_ROGUE: 0, ST_MISSING: 0, ST_NEIGHBOR: 0}
        for t in self.tracks.values():
            c[t.status] += 1
        return c
