"""Accès à l'API Native Wifi de Windows (wlanapi.dll) via ctypes.

* Aucun droit administrateur ni pilote nécessaire.
* Depuis Windows 11 24H2, la liste des réseaux exige l'autorisation
  « Localisation » pour les applications de bureau : l'erreur est détectée
  et signalée par ``LocationDenied``.
"""
from __future__ import annotations

import ctypes
import logging
import threading
import time
import uuid
from ctypes import wintypes
from dataclasses import dataclass

from .model import Bss

log = logging.getLogger(__name__)

ERROR_SUCCESS = 0
ERROR_ACCESS_DENIED = 5
ERROR_INVALID_STATE = 5023
ERROR_NOT_FOUND = 1168
ERROR_SERVICE_NOT_ACTIVE = 1062
ERROR_NDIS_DOT11_POWER_STATE_INVALID = 0x80342002

WLAN_NOTIFICATION_SOURCE_ACM = 0x08
ACM_SCAN_COMPLETE = 7
ACM_SCAN_FAIL = 8

OPCODE_CURRENT_CONNECTION = 7
OPCODE_CHANNEL_NUMBER = 8
OPCODE_RSSI = 0x10000102

IFACE_STATE = {0: "Non prête", 1: "Connectée", 2: "Ad hoc", 3: "Déconnexion…", 4: "Déconnectée",
               5: "Association…", 6: "Découverte…", 7: "Authentification…"}
PHY_TYPES = {1: "FHSS", 2: "DSSS", 3: "IR", 4: "802.11a", 5: "802.11b", 6: "802.11g",
             7: "802.11n", 8: "802.11ac", 9: "802.11ad", 10: "802.11ax", 11: "802.11be"}


class WlanError(Exception):
    def __init__(self, code: int, where: str):
        self.code = code
        msg = {
            ERROR_SERVICE_NOT_ACTIVE: "Le service Wi‑Fi de Windows (WLAN AutoConfig) est arrêté "
                                      "ou aucune carte Wi‑Fi n'est présente.",
            ERROR_ACCESS_DENIED: "Accès refusé par Windows : autorisez la localisation pour les "
                                 "applications de bureau (Paramètres › Confidentialité › Localisation).",
            ERROR_INVALID_STATE: "La carte Wi‑Fi est dans un état invalide (désactivée ?).",
            ERROR_NOT_FOUND: "Carte Wi‑Fi introuvable (déconnectée ?).",
        }.get(code, f"Erreur Windows {code}")
        super().__init__(f"{where} : {msg}")


class LocationDenied(WlanError):
    pass


# --------------------------------------------------------------------------
# Structures
# --------------------------------------------------------------------------
class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    def __str__(self):
        return str(uuid.UUID(bytes_le=bytes(self)))


class WLAN_INTERFACE_INFO(ctypes.Structure):
    _fields_ = [("InterfaceGuid", GUID), ("strInterfaceDescription", ctypes.c_wchar * 256),
                ("isState", ctypes.c_int)]


class WLAN_INTERFACE_INFO_LIST(ctypes.Structure):
    _fields_ = [("dwNumberOfItems", wintypes.DWORD), ("dwIndex", wintypes.DWORD),
                ("InterfaceInfo", WLAN_INTERFACE_INFO * 1)]


class DOT11_SSID(ctypes.Structure):
    _fields_ = [("uSSIDLength", wintypes.ULONG), ("ucSSID", ctypes.c_ubyte * 32)]


class WLAN_RATE_SET(ctypes.Structure):
    _fields_ = [("uRateSetLength", wintypes.ULONG), ("usRateSet", wintypes.USHORT * 126)]


class WLAN_BSS_ENTRY(ctypes.Structure):
    _fields_ = [
        ("dot11Ssid", DOT11_SSID),
        ("uPhyId", wintypes.ULONG),
        ("dot11Bssid", ctypes.c_ubyte * 6),
        ("dot11BssType", ctypes.c_int),
        ("dot11BssPhyType", ctypes.c_int),
        ("lRssi", wintypes.LONG),
        ("uLinkQuality", wintypes.ULONG),
        ("bInRegDomain", ctypes.c_ubyte),
        ("usBeaconPeriod", wintypes.USHORT),
        ("ullTimestamp", ctypes.c_ulonglong),
        ("ullHostTimestamp", ctypes.c_ulonglong),
        ("usCapabilityInformation", wintypes.USHORT),
        ("ulChCenterFrequency", wintypes.ULONG),
        ("wlanRateSet", WLAN_RATE_SET),
        ("ulIeOffset", wintypes.ULONG),
        ("ulIeSize", wintypes.ULONG),
    ]


class WLAN_BSS_LIST(ctypes.Structure):
    _fields_ = [("dwTotalSize", wintypes.DWORD), ("dwNumberOfItems", wintypes.DWORD),
                ("wlanBssEntries", WLAN_BSS_ENTRY * 1)]


class WLAN_ASSOCIATION_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("dot11Ssid", DOT11_SSID), ("dot11BssType", ctypes.c_int),
                ("dot11Bssid", ctypes.c_ubyte * 6), ("dot11PhyType", ctypes.c_int),
                ("uDot11PhyIndex", wintypes.ULONG), ("wlanSignalQuality", wintypes.ULONG),
                ("ulRxRate", wintypes.ULONG), ("ulTxRate", wintypes.ULONG)]


class WLAN_SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("bSecurityEnabled", wintypes.BOOL), ("bOneXEnabled", wintypes.BOOL),
                ("dot11AuthAlgorithm", ctypes.c_int), ("dot11CipherAlgorithm", ctypes.c_int)]


class WLAN_CONNECTION_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("isState", ctypes.c_int), ("wlanConnectionMode", ctypes.c_int),
                ("strProfileName", ctypes.c_wchar * 256),
                ("wlanAssociationAttributes", WLAN_ASSOCIATION_ATTRIBUTES),
                ("wlanSecurityAttributes", WLAN_SECURITY_ATTRIBUTES)]


class WLAN_NOTIFICATION_DATA(ctypes.Structure):
    _fields_ = [("NotificationSource", wintypes.DWORD), ("NotificationCode", wintypes.DWORD),
                ("InterfaceGuid", GUID), ("dwDataSize", wintypes.DWORD), ("pData", ctypes.c_void_p)]


NOTIF_CB = ctypes.WINFUNCTYPE(None, ctypes.POINTER(WLAN_NOTIFICATION_DATA), ctypes.c_void_p)


@dataclass
class Interface:
    guid: str
    description: str
    state: str
    _guid_struct: GUID = None


@dataclass
class Connection:
    ssid: str
    bssid: str
    profile: str
    state: str
    phy: str
    signal_quality: int          # 0‑100
    rx_mbps: float
    tx_mbps: float
    rssi: int | None
    channel: int | None
    security: bool
    onex: bool


def _mac(b) -> str:
    return ":".join(f"{x:02x}" for x in bytes(b))


def _ssid(s: DOT11_SSID) -> str:
    raw = bytes(s.ucSSID[:min(s.uSSIDLength, 32)])
    if not raw or all(c == 0 for c in raw):
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def filetime_now() -> int:
    ft = ctypes.c_ulonglong()
    ctypes.windll.kernel32.GetSystemTimeAsFileTime(ctypes.byref(ft))
    return ft.value


def interrupt_time() -> int:
    t = ctypes.c_ulonglong()
    ctypes.windll.kernel32.QueryUnbiasedInterruptTime(ctypes.byref(t))
    return t.value


class WlanClient:
    """Client Native Wifi. Thread‑safe (verrou interne)."""

    def __init__(self):
        self._lock = threading.RLock()
        self._dll = None
        self._h = wintypes.HANDLE()
        self._scan_done: dict[str, threading.Event] = {}
        self._cb = None
        self.last_scan_info: dict = {}
        self._open()

    def _open(self) -> None:
        try:
            dll = ctypes.WinDLL("wlanapi.dll", use_last_error=True)
        except OSError as exc:
            raise WlanError(126, "Chargement de wlanapi.dll") from exc
        dll.WlanOpenHandle.argtypes = [wintypes.DWORD, ctypes.c_void_p,
                                       ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.HANDLE)]
        dll.WlanCloseHandle.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        dll.WlanEnumInterfaces.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.WlanScan.argtypes = [wintypes.HANDLE, ctypes.POINTER(GUID), ctypes.c_void_p,
                                 ctypes.c_void_p, ctypes.c_void_p]
        dll.WlanGetNetworkBssList.argtypes = [wintypes.HANDLE, ctypes.POINTER(GUID), ctypes.c_void_p,
                                              ctypes.c_int, wintypes.BOOL, ctypes.c_void_p,
                                              ctypes.POINTER(ctypes.c_void_p)]
        dll.WlanQueryInterface.argtypes = [wintypes.HANDLE, ctypes.POINTER(GUID), ctypes.c_int,
                                           ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
                                           ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_int)]
        dll.WlanRegisterNotification.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL,
                                                 NOTIF_CB, ctypes.c_void_p, ctypes.c_void_p,
                                                 ctypes.POINTER(wintypes.DWORD)]
        dll.WlanFreeMemory.argtypes = [ctypes.c_void_p]
        for f in ("WlanOpenHandle", "WlanCloseHandle", "WlanEnumInterfaces", "WlanScan",
                  "WlanGetNetworkBssList", "WlanQueryInterface", "WlanRegisterNotification"):
            getattr(dll, f).restype = wintypes.DWORD
        ver = wintypes.DWORD()
        rc = dll.WlanOpenHandle(2, None, ctypes.byref(ver), ctypes.byref(self._h))
        if rc != ERROR_SUCCESS:
            raise WlanError(rc, "Ouverture de l'API Wi‑Fi")
        self._dll = dll
        # Notifications de fin de scan
        self._cb = NOTIF_CB(self._on_notification)
        prev = wintypes.DWORD()
        rc = dll.WlanRegisterNotification(self._h, WLAN_NOTIFICATION_SOURCE_ACM, True,
                                          self._cb, None, None, ctypes.byref(prev))
        if rc != ERROR_SUCCESS:
            log.warning("Notifications Wi‑Fi indisponibles (%s)", rc)

    def close(self) -> None:
        with self._lock:
            if self._dll and self._h:
                self._dll.WlanCloseHandle(self._h, None)
                self._dll = None

    def _on_notification(self, pdata, _ctx) -> None:
        try:
            d = pdata.contents
            if d.NotificationSource == WLAN_NOTIFICATION_SOURCE_ACM and \
                    d.NotificationCode in (ACM_SCAN_COMPLETE, ACM_SCAN_FAIL):
                ev = self._scan_done.get(str(d.InterfaceGuid))
                if ev:
                    ev.set()
        except Exception:  # appelé par Windows : ne jamais laisser remonter
            pass

    # -------------------------------------------------------------- API
    def interfaces(self) -> list[Interface]:
        with self._lock:
            p = ctypes.c_void_p()
            rc = self._dll.WlanEnumInterfaces(self._h, None, ctypes.byref(p))
            if rc != ERROR_SUCCESS:
                raise WlanError(rc, "Liste des cartes Wi‑Fi")
            try:
                lst = ctypes.cast(p, ctypes.POINTER(WLAN_INTERFACE_INFO_LIST)).contents
                arr = ctypes.cast(ctypes.byref(lst.InterfaceInfo),
                                  ctypes.POINTER(WLAN_INTERFACE_INFO * lst.dwNumberOfItems)).contents
                out = []
                for it in arr:
                    g = GUID.from_buffer_copy(it.InterfaceGuid)
                    out.append(Interface(str(g), it.strInterfaceDescription,
                                         IFACE_STATE.get(it.isState, str(it.isState)), g))
                return out
            finally:
                self._dll.WlanFreeMemory(p)

    def trigger_scan(self, iface: Interface) -> threading.Event:
        ev = threading.Event()
        self._scan_done[iface.guid] = ev
        with self._lock:
            rc = self._dll.WlanScan(self._h, ctypes.byref(iface._guid_struct), None, None, None)
        if rc != ERROR_SUCCESS:
            log.debug("WlanScan : %s", rc)
            if rc == ERROR_ACCESS_DENIED:
                raise LocationDenied(rc, "Scan Wi‑Fi")
            ev.set()          # on lira la liste en cache
        return ev

    def scan(self, iface: Interface, timeout: float = 6.0, max_age: float = 20.0) -> list[Bss]:
        """Déclenche un scan, attend sa fin, puis renvoie les BSS récents."""
        t0 = time.time()
        ev = self.trigger_scan(iface)
        completed = ev.wait(timeout)
        bss = self.bss_list(iface, max_age=max_age)
        self.last_scan_info.update(duration=round(time.time() - t0, 2), notified=completed)
        return bss

    def bss_list(self, iface: Interface, max_age: float | None = 20.0, raw: bool = False):
        with self._lock:
            p = ctypes.c_void_p()
            rc = self._dll.WlanGetNetworkBssList(self._h, ctypes.byref(iface._guid_struct), None,
                                                 3, False, None, ctypes.byref(p))
            if rc == ERROR_ACCESS_DENIED:
                raise LocationDenied(rc, "Liste des points d'accès")
            if rc != ERROR_SUCCESS:
                raise WlanError(rc, "Liste des points d'accès")
            try:
                lst = ctypes.cast(p, ctypes.POINTER(WLAN_BSS_LIST)).contents
                n = lst.dwNumberOfItems
                base = ctypes.addressof(lst.wlanBssEntries)
                size = ctypes.sizeof(WLAN_BSS_ENTRY)
                entries = []
                for i in range(n):
                    addr = base + i * size
                    e = WLAN_BSS_ENTRY.from_address(addr)
                    ies = ctypes.string_at(addr + e.ulIeOffset, e.ulIeSize) if e.ulIeSize else b""
                    rates = [(r & 0x7FFF) * 0.5 for r in e.wlanRateSet.usRateSet[:min(e.wlanRateSet.uRateSetLength // 2, 126)]]
                    entries.append(dict(
                        ssid=_ssid(e.dot11Ssid), bssid=_mac(e.dot11Bssid), bss_type=e.dot11BssType,
                        phy=e.dot11BssPhyType, rssi=int(e.lRssi), quality=int(e.uLinkQuality),
                        in_reg=bool(e.bInRegDomain), beacon=int(e.usBeaconPeriod),
                        tsf=int(e.ullTimestamp), host_ts=int(e.ullHostTimestamp),
                        cap=int(e.usCapabilityInformation), freq_khz=int(e.ulChCenterFrequency),
                        rates=rates, ies=ies))
            finally:
                self._dll.WlanFreeMemory(p)
        if raw:
            return entries
        entries = self._filter_age(entries, max_age)
        out = []
        for d in entries:
            try:
                out.append(Bss(bssid=d["bssid"], ssid=d["ssid"], rssi=d["rssi"],
                               link_quality=d["quality"], freq_mhz=d["freq_khz"] / 1000.0,
                               phy_type=d["phy"], beacon_period=d["beacon"], capability=d["cap"],
                               rates_win=d["rates"], ie_blob=d["ies"], host_ts=d["host_ts"]))
            except Exception:
                log.exception("BSS ignoré : %s", d.get("bssid"))
        return out

    def _filter_age(self, entries: list[dict], max_age: float | None) -> list[dict]:
        """Écarte les entrées anciennes du cache Windows. L'horloge de
        ``ullHostTimestamp`` n'étant pas documentée, on détecte laquelle
        correspond (FILETIME ou temps d'interruption) ; sinon pas de filtre."""
        if not entries or not max_age:
            return entries
        newest = max(e["host_ts"] for e in entries)
        clock = None
        for name, now in (("filetime", filetime_now()), ("interrupt", interrupt_time())):
            if 0 <= now - newest < 3600 * 10_000_000:
                clock = (name, now)
                break
        self.last_scan_info["clock"] = clock[0] if clock else None
        if clock is None:
            return entries
        limit = clock[1] - int(max_age * 10_000_000)
        kept = [e for e in entries if e["host_ts"] >= limit]
        self.last_scan_info.update(total=len(entries), kept=len(kept))
        return kept or entries

    def connection(self, iface: Interface) -> Connection | None:
        """Connexion en cours (None si déconnecté)."""
        data = self._query(iface, OPCODE_CURRENT_CONNECTION)
        if data is None:
            return None
        p, _ = data
        try:
            c = ctypes.cast(p, ctypes.POINTER(WLAN_CONNECTION_ATTRIBUTES)).contents
            a = c.wlanAssociationAttributes
            conn = Connection(
                ssid=_ssid(a.dot11Ssid), bssid=_mac(a.dot11Bssid), profile=c.strProfileName,
                state=IFACE_STATE.get(c.isState, str(c.isState)),
                phy=PHY_TYPES.get(a.dot11PhyType, str(a.dot11PhyType)),
                signal_quality=int(a.wlanSignalQuality), rx_mbps=a.ulRxRate / 1000.0,
                tx_mbps=a.ulTxRate / 1000.0, rssi=None, channel=None,
                security=bool(c.wlanSecurityAttributes.bSecurityEnabled),
                onex=bool(c.wlanSecurityAttributes.bOneXEnabled))
        finally:
            self._dll.WlanFreeMemory(p)
        for opcode, attr, typ in ((OPCODE_RSSI, "rssi", ctypes.c_long),
                                  (OPCODE_CHANNEL_NUMBER, "channel", ctypes.c_ulong)):
            d = self._query(iface, opcode)
            if d is not None:
                ptr, _ = d
                setattr(conn, attr, int(ctypes.cast(ptr, ctypes.POINTER(typ)).contents.value))
                self._dll.WlanFreeMemory(ptr)
        if conn.rssi is None:   # repli : qualité 0‑100 -> dBm (formule Windows)
            conn.rssi = int(conn.signal_quality / 2 - 100)
        return conn

    def _query(self, iface: Interface, opcode: int):
        with self._lock:
            size = wintypes.DWORD()
            p = ctypes.c_void_p()
            vt = ctypes.c_int()
            rc = self._dll.WlanQueryInterface(self._h, ctypes.byref(iface._guid_struct), opcode, None,
                                              ctypes.byref(size), ctypes.byref(p), ctypes.byref(vt))
        if rc != ERROR_SUCCESS:
            return None
        return p, size.value


# --------------------------------------------------------------------------
_client: WlanClient | None = None
_client_lock = threading.Lock()


def get_client() -> WlanClient:
    """Client partagé ; lève WlanError si le Wi‑Fi est indisponible."""
    global _client
    with _client_lock:
        if _client is None:
            _client = WlanClient()
        return _client


def reset_client() -> None:
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
        _client = None
