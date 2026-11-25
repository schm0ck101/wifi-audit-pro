"""Analyse de l'occupation des canaux et recommandations de plan de canaux."""
from __future__ import annotations

from dataclasses import dataclass

from ..wlan.model import BAND_5, BAND_6, BAND_24, DFS_5GHZ, Bss, channel_to_freq

CHANNELS = {
    BAND_24: list(range(1, 14)),
    BAND_5: list(range(36, 65, 4)) + list(range(100, 145, 4)) + list(range(149, 166, 4)),
    BAND_6: list(range(1, 234, 4)),
}
NON_OVERLAP_24 = [1, 6, 11]
BLOCKS_80 = {BAND_5: [(36, 48), (52, 64), (100, 112), (116, 128), (132, 144), (149, 161)],
             BAND_6: [(c, c + 12) for c in range(1, 222, 16)]}


def signal_weight(rssi: int) -> float:
    """Poids d'un voisin selon sa puissance : un réseau faible gêne peu."""
    if rssi >= -67:
        return 1.0
    if rssi >= -75:
        return 0.7
    if rssi >= -82:
        return 0.4
    return 0.0


@dataclass
class ChannelStat:
    band: str
    channel: int
    primary: int = 0           # réseaux dont c'est le canal principal
    overlapping: int = 0       # réseaux recouvrant ce canal (largeur comprise)
    load: float = 0.0          # charge pondérée par le signal
    utilization: float | None = None   # % annoncé par les bornes (BSS Load)
    stations: int = 0
    strongest: int | None = None
    own: int = 0               # réseaux du client
    dfs: bool = False

    @property
    def score(self) -> float:
        """0 = libre … 100 = saturé."""
        s = self.load * 18
        if self.utilization is not None:
            s = max(s, self.utilization)
        return round(min(100.0, s), 1)


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def analyze(bss: list[Bss], band: str, own_ssids: set[str] | None = None) -> list[ChannelStat]:
    own_ssids = own_ssids or set()
    aps = [b for b in bss if b.band == band and b.channel]
    out = []
    for ch in CHANNELS[band]:
        f = channel_to_freq(band, ch)
        st = ChannelStat(band, ch, dfs=(band == BAND_5 and ch in DFS_5GHZ))
        for b in aps:
            if not _overlap(b.span_mhz, (f - 10, f + 10)):
                continue
            st.overlapping += 1
            st.load += signal_weight(b.rssi)
            st.strongest = b.rssi if st.strongest is None else max(st.strongest, b.rssi)
            if b.ssid in own_ssids:
                st.own += 1
            u = b.ies.channel_utilization
            if u is not None and b.channel == ch:
                st.utilization = u if st.utilization is None else max(st.utilization, u)
            if b.channel == ch:
                st.primary += 1
                st.stations += b.ies.station_count or 0
        out.append(st)
    return out


def recommend(stats: list[ChannelStat], band: str, width: int = 20) -> list[tuple[str, float]]:
    """Meilleurs choix de canal (libellé, score) pour une nouvelle borne."""
    by = {s.channel: s for s in stats}
    if band == BAND_24:
        cands = [(f"Canal {c}", by[c].score) for c in NON_OVERLAP_24 if c in by]
        return sorted(cands, key=lambda x: x[1])
    if width >= 80 and band in BLOCKS_80:
        res = []
        for lo, hi in BLOCKS_80[band]:
            chans = [by[c] for c in range(lo, hi + 1, 4) if c in by]
            if not chans:
                continue
            sc = max(c.score for c in chans)
            dfs = any(c.dfs for c in chans)
            res.append((f"Canaux {lo}–{hi}" + (" (DFS)" if dfs else ""), sc))
        return sorted(res, key=lambda x: (x[1], "DFS" in x[0]))
    res = [(f"Canal {s.channel}" + (" (DFS)" if s.dfs else ""), s.score) for s in stats]
    return sorted(res, key=lambda x: (x[1], "DFS" in x[0]))[:6]
