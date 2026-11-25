"""Modèle d'un point d'accès observé (BSS) et dérivations : bande, canal,
largeur, norme Wi‑Fi, sécurité, débit théorique."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .ies import ParsedIEs, parse_ies

BAND_24, BAND_5, BAND_6 = "2,4 GHz", "5 GHz", "6 GHz"
BANDS = [BAND_24, BAND_5, BAND_6]

# Débit PHY maximal par flux spatial (Mb/s), meilleur MCS, intervalle de garde court
_RATE_PER_SS = {
    "HT": {20: 72.2, 40: 150.0},
    "VHT": {20: 86.7, 40: 200.0, 80: 433.3, 160: 866.7},
    "HE": {20: 143.4, 40: 286.8, 80: 600.5, 160: 1201.0},
    "EHT": {20: 172.1, 40: 344.1, 80: 720.6, 160: 1441.2, 320: 2882.4},
}

# Canaux 5 GHz soumis au DFS (détection radar) en Europe
DFS_5GHZ = set(range(52, 65, 4)) | set(range(100, 145, 4))


def freq_to_channel(mhz: float) -> tuple[str | None, int | None]:
    f = int(round(mhz))
    if 2412 <= f <= 2472:
        return BAND_24, (f - 2407) // 5
    if f == 2484:
        return BAND_24, 14
    if 5150 <= f <= 5895:
        return BAND_5, (f - 5000) // 5
    if f == 5935:
        return BAND_6, 2
    if 5950 < f <= 7125:
        return BAND_6, (f - 5950) // 5
    return None, None


def channel_to_freq(band: str, ch: int) -> float:
    if band == BAND_24:
        return 2484.0 if ch == 14 else 2407.0 + 5 * ch
    if band == BAND_5:
        return 5000.0 + 5 * ch
    return 5950.0 + 5 * ch


def oui_of(bssid: str) -> str:
    return bssid.upper().replace("-", ":")[:8]


def is_locally_administered(bssid: str) -> bool:
    try:
        return bool(int(bssid[:2], 16) & 0x02)
    except ValueError:
        return False


def radio_key(bssid: str) -> str:
    """Clé regroupant les BSSID d'une même radio (multi‑SSID) : les 5 premiers
    octets, bit « administré localement » neutralisé."""
    parts = bssid.lower().split(":")
    if len(parts) != 6:
        return bssid.lower()
    first = int(parts[0], 16) & ~0x02
    return ":".join([f"{first:02x}"] + parts[1:5])


@dataclass
class Bss:
    bssid: str
    ssid: str
    rssi: int                          # dBm
    link_quality: int                  # 0‑100 (Windows)
    freq_mhz: float
    phy_type: int = 0
    beacon_period: int = 100
    capability: int = 0
    rates_win: list[float] = field(default_factory=list)
    ie_blob: bytes = b""
    host_ts: int = 0                   # horodatage hôte (FILETIME) de la réception
    seen: float = field(default_factory=time.time)
    ies: ParsedIEs = field(default=None)

    def __post_init__(self):
        if self.ies is None:
            self.ies = parse_ies(self.ie_blob)
        if not self.ssid and self.ies.ssid_text:
            self.ssid = self.ies.ssid_text

    # ------------------------------------------------------------ dérivés
    @property
    def band(self) -> str | None:
        return freq_to_channel(self.freq_mhz)[0]

    @property
    def channel(self) -> int | None:
        b, ch = freq_to_channel(self.freq_mhz)
        if b == BAND_6 and self.ies.he_6ghz_primary:
            return self.ies.he_6ghz_primary
        return ch if ch is not None else self.ies.channel

    @property
    def hidden(self) -> bool:
        return not self.ssid

    @property
    def width(self) -> int:
        """Largeur de canal en MHz déduite des éléments d'opération."""
        p = self.ies
        w = 20
        if p.ht and p.ht_sec_offset in (1, 3) and p.ht_sta_width_any:
            w = 40
        if self.band != BAND_24 and p.vht and p.vht_width is not None:
            if p.vht_width == 1:
                w = 80
                if p.vht_seg1:      # 160 MHz (ou 80+80, compté comme 160)
                    w = 160
            elif p.vht_width in (2, 3):
                w = 160
        if p.he_6ghz_width is not None:
            w = {0: 20, 1: 40, 2: 80, 3: 160}.get(p.he_6ghz_width, w)
        if p.eht_width is not None:
            w = max(w, {0: 20, 1: 40, 2: 80, 3: 160, 4: 320}.get(p.eht_width, w))
        return w

    @property
    def center_channel(self) -> int | None:
        """Canal central du bloc occupé (pour l'analyse de recouvrement)."""
        ch, p, w = self.channel, self.ies, self.width
        if ch is None:
            return None
        if w == 20:
            return ch
        if self.band == BAND_24:
            return ch + 2 if p.ht_sec_offset == 1 else ch - 2
        if p.eht_width is not None and w >= 160 and p.eht_ccfs1:
            return p.eht_ccfs1
        if p.he_6ghz_width is not None:
            if w == 160 and p.he_6ghz_seg1:
                return p.he_6ghz_seg1
            return p.he_6ghz_seg0 or ch
        if p.vht and p.vht_width:
            if w == 160 and p.vht_seg1:
                return p.vht_seg1
            return p.vht_seg0 or ch
        if w == 40:
            return ch + 2 if p.ht_sec_offset == 1 else ch - 2
        return ch

    @property
    def span_mhz(self) -> tuple[float, float]:
        """Plage de fréquences occupée."""
        b = self.band or BAND_5
        c = self.center_channel or self.channel or 1
        f = channel_to_freq(b, c)
        half = 11 if (b == BAND_24 and self.width == 20) else self.width / 2
        return f - half, f + half

    @property
    def standard(self) -> str:
        p = self.ies
        if p.eht:
            return "Wi‑Fi 7 (802.11be)"
        if p.he:
            return "Wi‑Fi 6E (802.11ax)" if self.band == BAND_6 else "Wi‑Fi 6 (802.11ax)"
        if p.vht:
            return "Wi‑Fi 5 (802.11ac)"
        if p.ht:
            return "Wi‑Fi 4 (802.11n)"
        if self.band == BAND_24:
            rates = p.rates or self.rates_win
            return "802.11b" if rates and max(rates) <= 11 else "802.11g"
        return "802.11a"

    @property
    def generation(self) -> int:
        p = self.ies
        return 7 if p.eht else 6 if p.he else 5 if p.vht else 4 if p.ht else 3

    @property
    def streams(self) -> int:
        p = self.ies
        return max(p.he_streams, p.vht_streams, p.ht_streams, 1)

    @property
    def max_rate(self) -> float:
        """Débit PHY théorique maximal (Mb/s)."""
        p = self.ies
        fam = "EHT" if p.eht else "HE" if p.he else "VHT" if p.vht else "HT" if p.ht else None
        if fam is None:
            rates = p.rates or self.rates_win
            return max(rates) if rates else (11.0 if self.standard == "802.11b" else 54.0)
        table = _RATE_PER_SS[fam]
        w = self.width if self.width in table else max(k for k in table if k <= self.width)
        return round(table[w] * self.streams, 1)

    @property
    def security(self) -> str:
        p = self.ies
        akm = set(p.rsn_akm)
        if p.rsn:
            ent = akm & {"802.1X", "FT-802.1X", "802.1X-SHA256", "802.1X-SuiteB",
                         "802.1X-SuiteB-192", "FT-802.1X-SHA384"}
            sae = akm & {"SAE", "FT-SAE", "SAE-EXT-KEY", "FT-SAE-EXT-KEY"}
            psk = akm & {"PSK", "FT-PSK", "PSK-SHA256", "PSK-SHA384", "FT-PSK-SHA384"}
            if "OWE" in akm:
                return "Enhanced Open (OWE)"
            if ent:
                wpa3 = p.mfp_required or akm & {"802.1X-SHA256", "802.1X-SuiteB-192"}
                base = "WPA3‑Entreprise" if wpa3 else "WPA2‑Entreprise"
            elif sae and psk:
                base = "WPA2/WPA3‑Personnel"
            elif sae:
                base = "WPA3‑Personnel"
            elif psk:
                base = "WPA2‑Personnel"
            else:
                base = "WPA2"
            if p.wpa1:
                base = "WPA/" + base
            return base
        if p.wpa1:
            return "WPA (obsolète)"
        if self.capability & 0x0010:
            return "WEP (obsolète)"
        return "Ouvert"

    @property
    def ciphers(self) -> list[str]:
        p = self.ies
        return list(dict.fromkeys(p.rsn_pairwise + p.wpa1_pairwise))

    @property
    def insecure(self) -> bool:
        s = self.security
        return s.startswith(("Ouvert", "WEP", "WPA (")) or "TKIP" in self.ciphers

    @property
    def is_dfs(self) -> bool:
        return self.band == BAND_5 and self.channel in DFS_5GHZ

    @property
    def roaming_flags(self) -> str:
        p = self.ies
        return " ".join(f for f, on in (("k", p.dot11k), ("v", p.dot11v), ("r", p.dot11r)) if on) or "—"

    def to_dict(self) -> dict:
        """Résumé sérialisable (base de données, exports)."""
        p = self.ies
        return {
            "bssid": self.bssid, "ssid": self.ssid, "rssi": self.rssi, "freq": self.freq_mhz,
            "band": self.band, "channel": self.channel, "width": self.width,
            "standard": self.standard, "security": self.security, "ciphers": self.ciphers,
            "max_rate": self.max_rate, "streams": self.streams,
            "k": p.dot11k, "v": p.dot11v, "r": p.dot11r, "wps": p.wps, "pmf_required": p.mfp_required,
            "stations": p.station_count, "utilization": p.channel_utilization,
            "country": p.country, "ap_name": p.ap_name, "hidden": self.hidden,
            "basic_rates": p.basic_rates, "vendor_ouis": p.vendor_ouis,
        }
