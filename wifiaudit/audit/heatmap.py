"""Calcul des cartes de couverture (heatmaps) à partir des points de relevé.

Interpolation par pondération inverse à la distance (IDW), limitée à un
rayon autour des mesures : on n'extrapole jamais loin des points réellement
mesurés (les zones non relevées restent transparentes).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

NO_SIGNAL = -100.0

KINDS = {
    "signal": "Signal (meilleure borne)",
    "snr": "Rapport signal / bruit",
    "second": "Deuxième borne (itinérance)",
    "count": "Nombre de bornes ≥ seuil",
    "cochannel": "Interférences co‑canal",
    "rate": "Débit PHY estimé",
    "best_ap": "Borne servante",
    "latency": "Latence (tests aux points)",
    "throughput": "Débit mesuré (tests aux points)",
}

# Débit PHY (Wi‑Fi 6, 80 MHz, 2 flux) selon le signal reçu, mis à l'échelle
# selon le débit maximal de la borne servante.
_RSSI_MCS = [(-51, 1201), (-54, 1081), (-57, 961), (-59, 721), (-64, 576), (-65, 520),
             (-66, 432), (-70, 288), (-74, 216), (-77, 144), (-79, 72), (-82, 36)]


def estimated_rate(rssi: float, ap_max_rate: float = 1201.0) -> float:
    for lim, rate in _RSSI_MCS:
        if rssi >= lim:
            return rate * min(ap_max_rate, 1201.0) / 1201.0
    return 0.0


@dataclass
class SurveyPoint:
    x: float
    y: float
    readings: dict[str, int]                   # bssid -> rssi
    ping: dict | None = None                   # {"avg":…, "loss":…}
    throughput: dict | None = None             # {"down":…, "up":…}
    scan_id: int | None = None


@dataclass
class ApInfo:
    bssid: str
    ssid: str
    channel: int | None
    band: str | None
    radio: str
    max_rate: float = 1201.0


@dataclass
class HeatmapResult:
    grid: np.ndarray                 # valeurs (NaN = non couvert par les mesures)
    cell_px: float
    kind: str
    categories: list[str] = field(default_factory=list)   # pour best_ap


def point_values(points: list[SurveyPoint], kind: str, aps: dict[str, ApInfo],
                 selected: set[str] | None, noise_floor: float = -92.0,
                 threshold: float = -75.0, band: str | None = None):
    """Valeur scalaire de chaque point pour le type de carte demandé.
    ``selected`` : BSSID pris en compte (None = tous)."""
    vals = []
    cats: list[str] = []
    for pt in points:
        rd = {b: r for b, r in pt.readings.items()
              if (selected is None or b in selected) and b in aps
              and (band is None or aps[b].band == band)}
        # meilleure valeur par radio physique (évite de compter les SSID multiples)
        per_radio: dict[str, tuple[int, str]] = {}
        for b, r in rd.items():
            k = aps[b].radio
            if k not in per_radio or r > per_radio[k][0]:
                per_radio[k] = (r, b)
        ranked = sorted(per_radio.values(), reverse=True)
        best = ranked[0][0] if ranked else NO_SIGNAL
        if kind == "signal":
            v = best
        elif kind == "snr":
            v = best - noise_floor if ranked else 0.0
        elif kind == "second":
            v = ranked[1][0] if len(ranked) > 1 else NO_SIGNAL
        elif kind == "count":
            v = float(sum(1 for r, _ in ranked if r >= threshold))
        elif kind == "cochannel":
            if not ranked:
                v = np.nan
            else:
                ap = aps[ranked[0][1]]
                v = float(sum(1 for b, r in pt.readings.items()
                              if b in aps and aps[b].radio != ap.radio and aps[b].channel == ap.channel
                              and aps[b].band == ap.band and r >= -82))
        elif kind == "rate":
            v = estimated_rate(best, aps[ranked[0][1]].max_rate) if ranked else 0.0
        elif kind == "best_ap":
            if not ranked:
                v = np.nan
            else:
                key = aps[ranked[0][1]].radio
                if key not in cats:
                    cats.append(key)
                v = float(cats.index(key))
        elif kind == "latency":
            v = pt.ping.get("avg") if pt.ping and pt.ping.get("avg") is not None else np.nan
        elif kind == "throughput":
            v = pt.throughput.get("down") if pt.throughput else np.nan
        else:
            raise ValueError(kind)
        vals.append(np.nan if v is None else float(v))
    return np.array(vals, dtype=np.float64), cats


def interpolate(xy: np.ndarray, values: np.ndarray, width: int, height: int,
                cell_px: float, radius_px: float, power: float = 2.0,
                nearest: bool = False) -> np.ndarray:
    """Grille (lignes × colonnes) interpolée ; NaN hors du rayon des mesures."""
    ok = ~np.isnan(values)
    xy, values = xy[ok], values[ok]
    gw = max(1, int(math.ceil(width / cell_px)))
    gh = max(1, int(math.ceil(height / cell_px)))
    grid = np.full((gh, gw), np.nan, dtype=np.float32)
    if not len(values):
        return grid
    gx = (np.arange(gw) + 0.5) * cell_px
    px = xy[:, 0].astype(np.float32)
    py = xy[:, 1].astype(np.float32)
    vals = values.astype(np.float32)
    r2 = np.float32(radius_px * radius_px)
    rows_per_chunk = max(1, int(4_000_000 // max(gw * len(vals), 1)))
    for r0 in range(0, gh, rows_per_chunk):
        r1 = min(gh, r0 + rows_per_chunk)
        gy = (np.arange(r0, r1) + 0.5) * cell_px
        dx = gx[None, :, None] - px[None, None, :]
        dy = gy[:, None, None] - py[None, None, :]
        d2 = (dx * dx + dy * dy).astype(np.float32)          # (lignes, colonnes, points)
        inside = d2 <= r2
        any_in = inside.any(axis=2)
        if nearest:
            idx = np.argmin(np.where(inside, d2, np.inf), axis=2)
            block = vals[idx]
        else:
            w = np.where(inside, 1.0 / np.maximum(d2, 1e-6) ** (power / 2), 0.0).astype(np.float32)
            sw = w.sum(axis=2)
            block = (w * vals[None, None, :]).sum(axis=2) / np.where(sw > 0, sw, 1)
        grid[r0:r1] = np.where(any_in, block, np.nan)
    return grid


def compute(points: list[SurveyPoint], kind: str, aps: dict[str, ApInfo], selected: set[str] | None,
            width: int, height: int, scale_m_per_px: float | None, radius_m: float = 8.0,
            noise_floor: float = -92.0, threshold: float = -75.0, band: str | None = None,
            power: float = 2.0) -> HeatmapResult:
    vals, cats = point_values(points, kind, aps, selected, noise_floor, threshold, band)
    xy = np.array([[p.x, p.y] for p in points], dtype=np.float64).reshape(-1, 2)
    if scale_m_per_px:
        radius_px = radius_m / scale_m_per_px
    else:
        radius_px = max(width, height) / 6.0            # plan non étalonné
    cell = max(2.0, max(width, height) / 300.0)
    grid = interpolate(xy, vals, width, height, cell, radius_px, power,
                       nearest=kind in ("best_ap", "count", "cochannel"))
    return HeatmapResult(grid, cell, kind, cats)


def area_fraction(grid: np.ndarray, predicate) -> float:
    """% des cellules couvertes (non NaN) vérifiant ``predicate``."""
    m = ~np.isnan(grid)
    n = int(m.sum())
    if not n:
        return 0.0
    return 100.0 * float(predicate(grid[m]).sum()) / n


def estimate_ap_positions(points: list[SurveyPoint], aps: dict[str, ApInfo],
                          min_points: int = 3) -> dict[str, tuple[float, float]]:
    """Position probable de chaque radio : barycentre des 5 meilleures
    mesures, pondéré par la puissance reçue (en mW)."""
    per_radio: dict[str, list[tuple[float, float, int]]] = {}
    for pt in points:
        best: dict[str, int] = {}
        for b, r in pt.readings.items():
            if b in aps:
                k = aps[b].radio
                best[k] = max(best.get(k, -200), r)
        for k, r in best.items():
            per_radio.setdefault(k, []).append((pt.x, pt.y, r))
    out = {}
    for k, lst in per_radio.items():
        if len(lst) < min_points:
            continue
        top = sorted(lst, key=lambda t: -t[2])[:5]
        w = np.array([10 ** (t[2] / 10) for t in top])
        x = float(np.dot(w, [t[0] for t in top]) / w.sum())
        y = float(np.dot(w, [t[1] for t in top]) / w.sum())
        out[k] = (x, y)
    return out
