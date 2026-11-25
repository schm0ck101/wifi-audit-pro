"""Décodage des « Information Elements » 802.11 contenus dans les balises
(beacons) et réponses de sonde des points d'accès.

Références : IEEE 802.11-2020 (§9.4.2), 802.11ax-2021 (HE), 802.11be (EHT).
Le décodeur est tolérant : un élément tronqué ou inconnu est ignoré, jamais
une exception ne remonte à l'appelant.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Identifiants d'éléments
# --------------------------------------------------------------------------
IE_SSID = 0
IE_RATES = 1
IE_DS = 3
IE_COUNTRY = 7
IE_BSS_LOAD = 11
IE_HT_CAP = 45
IE_RSN = 48
IE_EXT_RATES = 50
IE_MOBILITY_DOMAIN = 54
IE_HT_OP = 61
IE_RM_CAPS = 70
IE_MULTI_BSSID = 71
IE_EXT_CAPS = 127
IE_CISCO_AIRONET = 133
IE_VHT_CAP = 191
IE_VHT_OP = 192
IE_RNR = 201
IE_VENDOR = 221
IE_EXTENSION = 255

EXT_HE_CAP = 35
EXT_HE_OP = 36
EXT_EHT_OP = 106
EXT_EHT_CAP = 108

OUI_IEEE = b"\x00\x0f\xac"
OUI_MS = b"\x00\x50\xf2"

CIPHERS = {1: "WEP-40", 2: "TKIP", 4: "CCMP", 5: "WEP-104", 6: "BIP-CMAC-128",
           8: "GCMP-128", 9: "GCMP-256", 10: "CCMP-256", 11: "BIP-GMAC-128",
           12: "BIP-GMAC-256", 13: "BIP-CMAC-256"}
AKMS = {1: "802.1X", 2: "PSK", 3: "FT-802.1X", 4: "FT-PSK", 5: "802.1X-SHA256",
        6: "PSK-SHA256", 8: "SAE", 9: "FT-SAE", 11: "802.1X-SuiteB", 12: "802.1X-SuiteB-192",
        13: "FT-802.1X-SHA384", 18: "OWE", 19: "FT-PSK-SHA384", 20: "PSK-SHA384",
        22: "FILS-SHA256", 23: "FILS-SHA384", 24: "SAE-EXT-KEY", 25: "FT-SAE-EXT-KEY"}


@dataclass
class ParsedIEs:
    ssid: bytes = b""
    ssid_present: bool = False
    channel: int | None = None             # DS Parameter Set
    country: str | None = None
    rates: list[float] = field(default_factory=list)       # Mb/s
    basic_rates: list[float] = field(default_factory=list)
    # Charge (BSS Load, 802.11e)
    station_count: int | None = None
    channel_utilization: float | None = None   # %
    # HT / VHT / HE / EHT
    ht: bool = False
    ht_40mhz_cap: bool = False
    ht_sec_offset: int = 0                 # 0 aucun, 1 au‑dessus, 3 en dessous
    ht_sta_width_any: bool = False
    ht_streams: int = 0
    vht: bool = False
    vht_width: int | None = None           # champ « channel width » (0..3)
    vht_seg0: int = 0
    vht_seg1: int = 0
    vht_streams: int = 0
    he: bool = False
    he_streams: int = 0
    he_6ghz_width: int | None = None       # 0=20 1=40 2=80 3=160
    he_6ghz_primary: int | None = None
    he_6ghz_seg0: int = 0
    he_6ghz_seg1: int = 0
    he_bss_color: int | None = None
    eht: bool = False
    eht_width: int | None = None           # 0=20 1=40 2=80 3=160 4=320
    eht_ccfs0: int = 0
    eht_ccfs1: int = 0
    # Sécurité
    rsn: bool = False
    rsn_group: str | None = None
    rsn_pairwise: list[str] = field(default_factory=list)
    rsn_akm: list[str] = field(default_factory=list)
    mfp_capable: bool = False
    mfp_required: bool = False
    wpa1: bool = False
    wpa1_pairwise: list[str] = field(default_factory=list)
    wpa1_akm: list[str] = field(default_factory=list)
    wps: bool = False
    wmm: bool = False
    # Itinérance
    dot11k: bool = False                   # RM Enabled Capabilities
    dot11v: bool = False                   # BSS Transition (ext caps bit 19)
    dot11r: bool = False                   # Mobility Domain
    mobility_domain: int | None = None
    # Divers
    ap_name: str | None = None             # Cisco Aironet / autres
    vendor_ouis: list[str] = field(default_factory=list)
    multi_bssid: bool = False
    rnr: bool = False
    unknown_ids: list[int] = field(default_factory=list)

    @property
    def hidden(self) -> bool:
        return not self.ssid or all(b == 0 for b in self.ssid)

    @property
    def ssid_text(self) -> str:
        if self.hidden:
            return ""
        try:
            return self.ssid.decode("utf-8")
        except UnicodeDecodeError:
            return self.ssid.decode("latin-1")


def iter_ies(blob: bytes):
    """Itère (id, données) ; s'arrête proprement sur un élément tronqué."""
    i, n = 0, len(blob)
    while i + 2 <= n:
        eid, ln = blob[i], blob[i + 1]
        if i + 2 + ln > n:
            break
        yield eid, blob[i + 2:i + 2 + ln]
        i += 2 + ln


def _streams_from_mcs_map(m: int) -> int:
    """Carte MCS VHT/HE : 2 bits par flux spatial, 3 = non pris en charge."""
    n = 0
    for ss in range(8):
        if (m >> (2 * ss)) & 3 != 3:
            n = ss + 1
    return n


def _parse_suites(data: bytes, off: int, oui: bytes, table: dict):
    if off + 2 > len(data):
        return [], off
    (cnt,) = struct.unpack_from("<H", data, off)
    off += 2
    out = []
    for _ in range(cnt):
        if off + 4 > len(data):
            break
        s_oui, s_type = data[off:off + 3], data[off + 3]
        out.append(table.get(s_type, f"type {s_type}") if s_oui == oui
                   else f"{s_oui.hex(':')}/{s_type}")
        off += 4
    return out, off


def _parse_rsn(p: ParsedIEs, d: bytes) -> None:
    if len(d) < 2:
        return
    p.rsn = True
    off = 2  # version
    if off + 4 <= len(d):
        p.rsn_group = CIPHERS.get(d[off + 3], f"type {d[off + 3]}") if d[off:off + 3] == OUI_IEEE else "?"
        off += 4
    p.rsn_pairwise, off = _parse_suites(d, off, OUI_IEEE, CIPHERS)
    p.rsn_akm, off = _parse_suites(d, off, OUI_IEEE, AKMS)
    if off + 2 <= len(d):
        (caps,) = struct.unpack_from("<H", d, off)
        p.mfp_required = bool(caps & (1 << 6))
        p.mfp_capable = bool(caps & (1 << 7))


def _parse_vendor(p: ParsedIEs, d: bytes) -> None:
    if len(d) < 3:
        return
    oui = d[:3]
    o = oui.hex(":").upper()
    if o not in p.vendor_ouis:
        p.vendor_ouis.append(o)
    if oui == OUI_MS and len(d) >= 4:
        t = d[3]
        if t == 1:                         # WPA (première génération)
            p.wpa1 = True
            off = 4 + 2                    # type + version
            off += 4                       # suite de groupe
            p.wpa1_pairwise, off = _parse_suites(d, off, OUI_MS, CIPHERS)
            p.wpa1_akm, off = _parse_suites(d, off, OUI_MS, AKMS)
        elif t == 2:
            p.wmm = True
        elif t == 4:
            p.wps = True


def _parse_ext(p: ParsedIEs, d: bytes) -> None:
    if not d:
        return
    ext, body = d[0], d[1:]
    if ext == EXT_HE_CAP:
        p.he = True
        # MAC caps (6) + PHY caps (11) puis carte MCS Rx <= 80 MHz (2 octets)
        if len(body) >= 6 + 11 + 2:
            (m,) = struct.unpack_from("<H", body, 17)
            p.he_streams = _streams_from_mcs_map(m)
    elif ext == EXT_HE_OP:
        p.he = True
        if len(body) < 6:
            return
        params = body[0] | (body[1] << 8) | (body[2] << 16)
        p.he_bss_color = body[3] & 0x3F
        off = 6                             # params(3) + couleur(1) + MCS de base(2)
        if params & (1 << 14):              # VHT Operation Information présente
            off += 3
        if params & (1 << 15):              # Co‑hosted BSS
            off += 1
        if params & (1 << 17) and off + 5 <= len(body):   # 6 GHz Operation Information
            p.he_6ghz_primary = body[off]
            p.he_6ghz_width = body[off + 1] & 0x03
            p.he_6ghz_seg0 = body[off + 2]
            p.he_6ghz_seg1 = body[off + 3]
    elif ext == EXT_EHT_CAP:
        p.eht = True
    elif ext == EXT_EHT_OP:
        p.eht = True
        if len(body) >= 1 + 4 and body[0] & 0x01 and len(body) >= 1 + 4 + 3:
            p.eht_width = body[5] & 0x07
            p.eht_ccfs0 = body[6]
            p.eht_ccfs1 = body[7]


def parse_ies(blob: bytes) -> ParsedIEs:
    p = ParsedIEs()
    for eid, d in iter_ies(bytes(blob)):
        try:
            if eid == IE_SSID:
                p.ssid, p.ssid_present = bytes(d), True
            elif eid in (IE_RATES, IE_EXT_RATES):
                for b in d:
                    rate = (b & 0x7F) * 0.5
                    if b == 0xFF or rate == 0 or (b & 0x7F) >= 0x7E:   # sélecteurs BSS (HT/VHT/SAE…)
                        continue
                    p.rates.append(rate)
                    if b & 0x80:
                        p.basic_rates.append(rate)
            elif eid == IE_DS and d:
                p.channel = d[0]
            elif eid == IE_COUNTRY and len(d) >= 2:
                p.country = d[:2].decode("ascii", "replace")
            elif eid == IE_BSS_LOAD and len(d) >= 3:
                p.station_count = struct.unpack_from("<H", d, 0)[0]
                p.channel_utilization = round(d[2] * 100 / 255, 1)
            elif eid == IE_HT_CAP and len(d) >= 2:
                p.ht = True
                (info,) = struct.unpack_from("<H", d, 0)
                p.ht_40mhz_cap = bool(info & 0x02)
                if len(d) >= 3 + 4:
                    p.ht_streams = sum(1 for b in d[3:7] if b)
            elif eid == IE_HT_OP and len(d) >= 2:
                p.ht = True
                p.ht_sec_offset = d[1] & 0x03
                p.ht_sta_width_any = bool(d[1] & 0x04)
            elif eid == IE_VHT_CAP and len(d) >= 12:
                p.vht = True
                (m,) = struct.unpack_from("<H", d, 4)
                p.vht_streams = _streams_from_mcs_map(m)
            elif eid == IE_VHT_OP and len(d) >= 3:
                p.vht = True
                p.vht_width, p.vht_seg0, p.vht_seg1 = d[0], d[1], d[2]
            elif eid == IE_RSN:
                _parse_rsn(p, d)
            elif eid == IE_MOBILITY_DOMAIN and len(d) >= 2:
                p.dot11r = True
                p.mobility_domain = struct.unpack_from("<H", d, 0)[0]
            elif eid == IE_RM_CAPS:
                p.dot11k = True
            elif eid == IE_EXT_CAPS:
                if len(d) >= 3 and d[2] & 0x08:      # bit 19 : BSS Transition
                    p.dot11v = True
            elif eid == IE_CISCO_AIRONET and len(d) >= 26:
                name = d[10:26].split(b"\x00")[0].decode("ascii", "replace").strip()
                p.ap_name = name or p.ap_name
            elif eid == IE_MULTI_BSSID:
                p.multi_bssid = True
            elif eid == IE_RNR:
                p.rnr = True
            elif eid == IE_VENDOR:
                _parse_vendor(p, d)
            elif eid == IE_EXTENSION:
                _parse_ext(p, d)
            elif eid not in (5, 42, 47, 59, 66, 67, 68, 74, 107, 111):
                p.unknown_ids.append(eid)
        except (struct.error, IndexError):
            continue
    return p
