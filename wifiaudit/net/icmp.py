"""Envoi de requêtes ICMP Echo via l'API native Windows (iphlpapi.dll).

Pourquoi l'API IcmpSendEcho2 plutôt que des sockets brutes (Scapy) ?
  * elle fonctionne SANS droits administrateur (c'est ce qu'utilisent
    ``ping.exe`` et ``tracert.exe``) ;
  * elle permet de fixer le TTL / Hop Limit, donc de faire du traceroute ;
  * elle gère IPv4 (IcmpSendEcho2) et IPv6 (Icmp6SendEcho2) ;
  * aucun pilote (Npcap) n'est nécessaire.

Les appels sont bloquants mais ctypes relâche le GIL pendant l'appel : ils
sont donc exécutés dans un pool de threads (voir ``tracer.py``).
"""
from __future__ import annotations

import ctypes
import socket
import struct
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

# --------------------------------------------------------------------------
# Codes d'état (ipexport.h)
# --------------------------------------------------------------------------
IP_SUCCESS = 0
IP_BUF_TOO_SMALL = 11001
IP_DEST_NET_UNREACHABLE = 11002
IP_DEST_HOST_UNREACHABLE = 11003
IP_DEST_PROT_UNREACHABLE = 11004
IP_DEST_PORT_UNREACHABLE = 11005
IP_NO_RESOURCES = 11006
IP_HW_ERROR = 11008
IP_PACKET_TOO_BIG = 11009
IP_REQ_TIMED_OUT = 11010
IP_BAD_REQ = 11011
IP_BAD_ROUTE = 11012
IP_TTL_EXPIRED_TRANSIT = 11013  # = IP_HOP_LIMIT_EXCEEDED en IPv6
IP_TTL_EXPIRED_REASSEM = 11014
IP_PARAM_PROBLEM = 11015
IP_SOURCE_QUENCH = 11016
IP_BAD_DESTINATION = 11018
IP_GENERAL_FAILURE = 11050

STATUS_TEXT = {
    IP_SUCCESS: "Réponse",
    IP_DEST_NET_UNREACHABLE: "Réseau de destination injoignable",
    IP_DEST_HOST_UNREACHABLE: "Hôte de destination injoignable",
    IP_DEST_PROT_UNREACHABLE: "Protocole injoignable",
    IP_DEST_PORT_UNREACHABLE: "Port injoignable",
    IP_NO_RESOURCES: "Ressources insuffisantes",
    IP_HW_ERROR: "Erreur matérielle",
    IP_PACKET_TOO_BIG: "Paquet trop grand",
    IP_REQ_TIMED_OUT: "Délai d'attente dépassé",
    IP_BAD_REQ: "Requête invalide",
    IP_BAD_ROUTE: "Route invalide",
    IP_TTL_EXPIRED_TRANSIT: "TTL expiré en transit",
    IP_TTL_EXPIRED_REASSEM: "TTL expiré au réassemblage",
    IP_PARAM_PROBLEM: "Problème de paramètre",
    IP_SOURCE_QUENCH: "Source quench",
    IP_BAD_DESTINATION: "Destination invalide",
    IP_GENERAL_FAILURE: "Défaillance générale (réseau absent ?)",
    1231: "Réseau injoignable (pas de route)",
    1232: "Hôte injoignable",
    1214: "Nom de réseau invalide",
}

# Statuts pour lesquels un routeur a effectivement répondu (on connaît son IP)
_ROUTER_REPLY = {
    IP_TTL_EXPIRED_TRANSIT,
    IP_TTL_EXPIRED_REASSEM,
    IP_DEST_NET_UNREACHABLE,
    IP_DEST_HOST_UNREACHABLE,
    IP_DEST_PROT_UNREACHABLE,
    IP_DEST_PORT_UNREACHABLE,
}


@dataclass(slots=True)
class ProbeResult:
    """Résultat d'une sonde ICMP."""

    status: int            # code IP_* ci‑dessus
    address: str | None    # IP de l'équipement qui a répondu (None si timeout)
    rtt: float | None      # temps aller‑retour en ms (None si pas de réponse)

    @property
    def replied(self) -> bool:
        return self.address is not None and self.rtt is not None

    @property
    def reached(self) -> bool:
        """True si la destination finale elle‑même a répondu."""
        return self.status == IP_SUCCESS

    @property
    def status_text(self) -> str:
        return STATUS_TEXT.get(self.status, f"Code {self.status}")


# --------------------------------------------------------------------------
# Structures ctypes
# --------------------------------------------------------------------------
class IP_OPTION_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("Ttl", ctypes.c_ubyte),
        ("Tos", ctypes.c_ubyte),
        ("Flags", ctypes.c_ubyte),
        ("OptionsSize", ctypes.c_ubyte),
        ("OptionsData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class ICMP_ECHO_REPLY(ctypes.Structure):
    _fields_ = [
        ("Address", wintypes.ULONG),
        ("Status", wintypes.ULONG),
        ("RoundTripTime", wintypes.ULONG),
        ("DataSize", wintypes.USHORT),
        ("Reserved", wintypes.USHORT),
        ("Data", ctypes.c_void_p),
        ("Options", IP_OPTION_INFORMATION),
    ]


class IPV6_ADDRESS_EX(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("sin6_port", wintypes.USHORT),
        ("sin6_flowinfo", wintypes.ULONG),
        ("sin6_addr", ctypes.c_ubyte * 16),
        ("sin6_scope_id", wintypes.ULONG),
    ]


class ICMPV6_ECHO_REPLY(ctypes.Structure):
    _fields_ = [
        ("Address", IPV6_ADDRESS_EX),
        ("Status", wintypes.ULONG),
        ("RoundTripTime", ctypes.c_uint),
    ]


class SOCKADDR_IN6(ctypes.Structure):
    _fields_ = [
        ("sin6_family", ctypes.c_short),
        ("sin6_port", wintypes.USHORT),
        ("sin6_flowinfo", wintypes.ULONG),
        ("sin6_addr", ctypes.c_ubyte * 16),
        ("sin6_scope_id", wintypes.ULONG),
    ]


AF_INET6_WIN = 23
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

_iphlpapi = None
_lock = threading.Lock()


def _api():
    """Charge iphlpapi.dll une seule fois et déclare les prototypes."""
    global _iphlpapi
    if _iphlpapi is not None:
        return _iphlpapi
    with _lock:
        if _iphlpapi is not None:
            return _iphlpapi
        if sys.platform != "win32":
            raise OSError("NetTrace Pro nécessite Windows (API ICMP iphlpapi).")
        dll = ctypes.WinDLL("iphlpapi.dll", use_last_error=True)

        dll.IcmpCreateFile.restype = wintypes.HANDLE
        dll.IcmpCreateFile.argtypes = []
        dll.Icmp6CreateFile.restype = wintypes.HANDLE
        dll.Icmp6CreateFile.argtypes = []
        dll.IcmpCloseHandle.restype = wintypes.BOOL
        dll.IcmpCloseHandle.argtypes = [wintypes.HANDLE]

        dll.IcmpSendEcho2.restype = wintypes.DWORD
        dll.IcmpSendEcho2.argtypes = [
            wintypes.HANDLE,  # IcmpHandle
            wintypes.HANDLE,  # Event
            ctypes.c_void_p,  # ApcRoutine
            ctypes.c_void_p,  # ApcContext
            wintypes.ULONG,   # DestinationAddress (IPAddr, ordre réseau)
            ctypes.c_void_p,  # RequestData
            wintypes.WORD,    # RequestSize
            ctypes.POINTER(IP_OPTION_INFORMATION),
            ctypes.c_void_p,  # ReplyBuffer
            wintypes.DWORD,   # ReplySize
            wintypes.DWORD,   # Timeout (ms)
        ]

        dll.Icmp6SendEcho2.restype = wintypes.DWORD
        dll.Icmp6SendEcho2.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(SOCKADDR_IN6),  # SourceAddress
            ctypes.POINTER(SOCKADDR_IN6),  # DestinationAddress
            ctypes.c_void_p,
            wintypes.WORD,
            ctypes.POINTER(IP_OPTION_INFORMATION),
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        _iphlpapi = dll
        return dll


# Un handle ICMP par thread : évite toute contention entre sondes parallèles.
_tls = threading.local()


def _handle(v6: bool):
    attr = "h6" if v6 else "h4"
    h = getattr(_tls, attr, None)
    if h is None:
        api = _api()
        h = api.Icmp6CreateFile() if v6 else api.IcmpCreateFile()
        if not h or h == INVALID_HANDLE_VALUE:
            raise ctypes.WinError(ctypes.get_last_error())
        setattr(_tls, attr, h)
    return h


def _pick_rtt(reported_ms: int, measured_ms: float) -> float:
    """L'API ne donne qu'une résolution de 1 ms : on préfère la mesure
    haute précision (perf_counter) tant qu'elle est cohérente avec la valeur
    rapportée (elle peut être gonflée si le thread a été retardé)."""
    if reported_ms - 0.5 <= measured_ms <= reported_ms + 1.5:
        return round(max(measured_ms, 0.01), 3)
    return float(max(reported_ms, 0))


def probe(address: str, ttl: int = 128, timeout_ms: int = 2000,
          size: int = 32, v6: bool | None = None) -> ProbeResult:
    """Envoie une requête Echo ICMP vers ``address`` (IP littérale).

    ``ttl`` limite le nombre de sauts : un routeur intermédiaire répondra
    alors « TTL expiré », ce qui révèle son adresse (principe du traceroute).
    Ne lève jamais d'exception pour une erreur réseau : renvoie un statut.
    """
    if v6 is None:
        v6 = ":" in address
    size = max(0, min(int(size), 65000))
    timeout_ms = max(50, int(timeout_ms))
    payload = (b"NetTracePro-" * (size // 12 + 1))[:size]
    req = ctypes.create_string_buffer(payload, size) if size else None
    opts = IP_OPTION_INFORMATION(Ttl=max(1, min(int(ttl), 255)))
    api = _api()
    try:
        handle = _handle(v6)
    except OSError:
        return ProbeResult(IP_GENERAL_FAILURE, None, None)

    if not v6:
        reply_size = ctypes.sizeof(ICMP_ECHO_REPLY) + size + 8 + 64
        reply = ctypes.create_string_buffer(reply_size)
        try:
            dest = int.from_bytes(socket.inet_aton(address), "little")
        except OSError:
            return ProbeResult(IP_BAD_DESTINATION, None, None)
        t0 = time.perf_counter()
        n = api.IcmpSendEcho2(handle, None, None, None, dest, req, size,
                              ctypes.byref(opts), reply, reply_size, timeout_ms)
        measured = (time.perf_counter() - t0) * 1000.0
        if n == 0:
            err = ctypes.get_last_error()
            return ProbeResult(err or IP_REQ_TIMED_OUT, None, None)
        r = ICMP_ECHO_REPLY.from_buffer(reply)
        status = int(r.Status)
        addr = socket.inet_ntoa(struct.pack("<I", r.Address))
        reported = int(r.RoundTripTime)
    else:
        reply_size = ctypes.sizeof(ICMPV6_ECHO_REPLY) + size + 8 + 64
        reply = ctypes.create_string_buffer(reply_size)
        src = SOCKADDR_IN6(sin6_family=AF_INET6_WIN)
        dst = SOCKADDR_IN6(sin6_family=AF_INET6_WIN)
        try:
            raw = socket.inet_pton(socket.AF_INET6, address.split("%")[0])
        except OSError:
            return ProbeResult(IP_BAD_DESTINATION, None, None)
        ctypes.memmove(dst.sin6_addr, raw, 16)
        t0 = time.perf_counter()
        n = api.Icmp6SendEcho2(handle, None, None, None, ctypes.byref(src),
                               ctypes.byref(dst), req, size, ctypes.byref(opts),
                               reply, reply_size, timeout_ms)
        measured = (time.perf_counter() - t0) * 1000.0
        if n == 0:
            err = ctypes.get_last_error()
            return ProbeResult(err or IP_REQ_TIMED_OUT, None, None)
        r = ICMPV6_ECHO_REPLY.from_buffer(reply)
        status = int(r.Status)
        addr = socket.inet_ntop(socket.AF_INET6, bytes(r.Address.sin6_addr))
        reported = int(r.RoundTripTime)

    if status == IP_SUCCESS or status in _ROUTER_REPLY:
        return ProbeResult(status, addr, _pick_rtt(reported, measured))
    return ProbeResult(status, None, None)
