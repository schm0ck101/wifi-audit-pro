"""Moteur de constats d'audit : règles de sécurité, de plan de canaux,
d'itinérance, de couverture et de performance. Chaque constat a une gravité,
une explication et une recommandation (destinées au rapport client)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..wlan.model import BAND_5, BAND_24, Bss, radio_key
from .channels import NON_OVERLAP_24

CRIT, MAJOR, MINOR, INFO = "Critique", "Majeur", "Mineur", "Information"
SEVERITY_ORDER = {CRIT: 0, MAJOR: 1, MINOR: 2, INFO: 3}
PENALTY = {CRIT: 25, MAJOR: 10, MINOR: 3, INFO: 0}


@dataclass
class Finding:
    severity: str
    category: str
    title: str
    detail: str
    recommendation: str
    items: list[str] = field(default_factory=list)


@dataclass
class CoverageStats:
    """Résultats de relevé (heatmap) servant aux règles de couverture."""
    points: int = 0
    area_pct_below_target: float = 0.0     # % de surface sous le seuil
    target_dbm: int = -67
    area_pct_low_snr: float = 0.0
    snr_target: int = 25
    area_pct_single_ap: float = 0.0        # % de surface sans 2e borne pour l'itinérance


def _fmt(b: Bss) -> str:
    return f"{b.ssid or '(masqué)'} — {b.bssid} — {b.band} canal {b.channel}"


def evaluate(aps: list[Bss], own_ssids: set[str], authorized: set[str] | None = None,
             coverage: CoverageStats | None = None, thresholds: dict | None = None) -> list[Finding]:
    th = {"util_warn": 50, "util_crit": 75, "max_ssids_per_radio": 4, "cochannel_max": 3}
    th.update(thresholds or {})
    own = [b for b in aps if b.ssid in own_ssids] if own_ssids else []
    out: list[Finding] = []

    if own_ssids and not own:
        out.append(Finding(INFO, "Périmètre", "Aucun réseau du client détecté",
                           f"Les SSID déclarés ({', '.join(sorted(own_ssids))}) n'ont pas été vus "
                           "pendant les scans.", "Vérifiez les SSID déclarés pour ce site."))
    scope = own if own_ssids else aps

    # ------------------------------------------------------------ Sécurité
    for cond, sev, title, detail, reco in (
        (lambda b: b.security == "Ouvert", CRIT, "Réseau ouvert sans chiffrement",
         "Le trafic de ces réseaux circule en clair et peut être intercepté par n'importe qui à portée.",
         "Activer WPA3 (ou WPA2‑AES a minima). Pour un accès invités, utiliser Enhanced Open (OWE) "
         "ou un portail sur un réseau chiffré isolé."),
        (lambda b: b.security.startswith("WEP"), CRIT, "Chiffrement WEP",
         "WEP est cassé depuis 2001 : la clé se récupère en quelques minutes.",
         "Remplacer immédiatement par WPA3 ou WPA2‑AES ; renouveler le matériel si nécessaire."),
        (lambda b: b.ies.wpa1 or "TKIP" in b.ciphers, MAJOR, "WPA / TKIP encore autorisé",
         "TKIP et WPA (1re génération) sont obsolètes et limitent le débit à 54 Mb/s pour les clients concernés.",
         "Configurer « WPA2/WPA3, AES (CCMP) uniquement »."),
        (lambda b: b.ies.wps, MAJOR, "WPS activé",
         "Le code PIN WPS peut être attaqué par force brute et révéler la clé du réseau.",
         "Désactiver WPS sur les points d'accès."),
        (lambda b: b.security in ("WPA2‑Personnel",), MINOR, "WPA3 non activé",
         "WPA2‑Personnel reste sensible aux attaques hors ligne sur la clé partagée.",
         "Activer le mode de transition WPA2/WPA3 puis WPA3 seul quand le parc le permet ; "
         "utiliser une phrase de passe longue (≥ 16 caractères)."),
        (lambda b: b.security.startswith("WPA3") and not b.ies.mfp_required, MINOR,
         "Protection des trames de gestion (PMF) non obligatoire",
         "Sans PMF obligatoire, des attaques de désauthentification restent possibles.",
         "Régler PMF (802.11w) sur « obligatoire »."),
        (lambda b: b.hidden, INFO, "SSID masqué",
         "Masquer un SSID n'apporte aucune sécurité (il apparaît dans les requêtes des clients) et "
         "peut dégrader la connexion de certains appareils.",
         "Diffuser le SSID et s'appuyer sur un chiffrement robuste."),
    ):
        items = sorted({_fmt(b) for b in scope if cond(b)})
        if items:
            out.append(Finding(sev, "Sécurité", title, detail, reco, items))

    # ------------------------------------------------------ Usurpation
    if authorized is not None and own_ssids:
        rogue = sorted({_fmt(b) for b in aps if b.ssid in own_ssids and b.bssid.lower() not in authorized})
        if rogue:
            out.append(Finding(CRIT, "Sécurité", "Point d'accès non autorisé diffusant un SSID du client",
                               "Ces BSSID diffusent le nom d'un réseau du client sans figurer dans la liste "
                               "des bornes autorisées : borne pirate (« evil twin ») ou borne non inventoriée.",
                               "Localiser l'équipement (relevé de signal), vérifier son origine et le "
                               "retirer ou l'ajouter à l'inventaire.", rogue))

    # ----------------------------------------------------- Plan de canaux
    bad24 = sorted({_fmt(b) for b in scope if b.band == BAND_24 and b.channel not in NON_OVERLAP_24})
    if bad24:
        out.append(Finding(MAJOR, "Canaux", "Canaux 2,4 GHz chevauchants",
                           "En 2,4 GHz seuls les canaux 1, 6 et 11 ne se recouvrent pas ; les autres "
                           "créent des interférences avec deux groupes de voisins à la fois.",
                           "Utiliser exclusivement les canaux 1, 6 et 11.", bad24))
    wide24 = sorted({_fmt(b) for b in scope if b.band == BAND_24 and b.width >= 40})
    if wide24:
        out.append(Finding(MAJOR, "Canaux", "Canaux de 40 MHz en 2,4 GHz",
                           "Un canal de 40 MHz occupe plus de la moitié de la bande 2,4 GHz et "
                           "aggrave fortement les interférences.", "Limiter la bande 2,4 GHz à 20 MHz.",
                           wide24))
    wide5 = sorted({_fmt(b) for b in scope if b.band == BAND_5 and b.width >= 160})
    if wide5 and len({radio_key(b.bssid) for b in own if b.band == BAND_5}) > 2:
        out.append(Finding(MINOR, "Canaux", "Canaux de 160 MHz dans un déploiement multi‑bornes",
                           "Avec plusieurs bornes, le 160 MHz réduit le nombre de canaux disponibles et "
                           "provoque des recouvrements entre bornes voisines.",
                           "Privilégier 40 ou 80 MHz en 5 GHz pour les déploiements denses.", wide5))
    # co‑canal entre bornes du client
    per_ch = defaultdict(set)
    for b in own:
        if b.rssi >= -75 and b.channel:
            per_ch[(b.band, b.channel)].add(radio_key(b.bssid))
    cochan = [f"{band} canal {ch} : {len(r)} bornes" for (band, ch), r in sorted(per_ch.items())
              if len(r) > th["cochannel_max"]]
    if cochan:
        out.append(Finding(MAJOR, "Canaux", "Interférences co‑canal entre bornes",
                           "Plusieurs bornes du client se partagent le même canal avec un signal fort : "
                           "elles doivent attendre leur tour pour émettre (temps d'antenne partagé).",
                           "Répartir les bornes sur des canaux différents et ajuster leur puissance.", cochan))
    # charge annoncée
    for lim, sev in ((th["util_crit"], MAJOR), (th["util_warn"], MINOR)):
        busy = sorted({f"{_fmt(b)} : {b.ies.channel_utilization:.0f} % ({b.ies.station_count or 0} clients)"
                       for b in scope if (b.ies.channel_utilization or 0) >= lim})
        if busy:
            out.append(Finding(sev, "Performance", f"Canal occupé à plus de {lim} %",
                               "Les bornes annoncent (élément BSS Load) un temps d'antenne fortement utilisé : "
                               "latence et débit se dégradent.",
                               "Changer de canal, ajouter des bornes ou déplacer des clients vers 5/6 GHz.", busy))
            break

    # ------------------------------------------------------- Itinérance
    radios = {radio_key(b.bssid) for b in own}
    if len(radios) > 1:
        no_roam = sorted({b.ssid for b in own if not (b.ies.dot11k and b.ies.dot11v)})
        if no_roam:
            out.append(Finding(MINOR, "Itinérance", "Aide à l'itinérance 802.11k/v absente",
                               "Sans 802.11k (liste des bornes voisines) et 802.11v (transition guidée), "
                               "les appareils restent accrochés à une borne lointaine.",
                               "Activer 802.11k et 802.11v (et 802.11r pour la voix/vidéo).", no_roam))
        no_ft = sorted({b.ssid for b in own if not b.ies.dot11r})
        if no_ft:
            out.append(Finding(INFO, "Itinérance", "Transition rapide 802.11r non activée",
                               "Sans 802.11r, chaque changement de borne refait l'authentification complète "
                               "(coupure perceptible en VoIP et visioconférence, surtout en 802.1X).",
                               "Activer 802.11r (Fast Transition) si les terminaux le supportent.", no_ft))

    # ------------------------------------------------------- Performance
    by_radio = defaultdict(set)
    for b in own:
        by_radio[radio_key(b.bssid)].add(b.ssid or b.bssid)
    many = [f"{k} : {len(v)} SSID" for k, v in by_radio.items() if len(v) > th["max_ssids_per_radio"]]
    if many:
        out.append(Finding(MINOR, "Performance", "Trop de SSID par borne",
                           "Chaque SSID émet ses propres balises : au‑delà de 3 à 4 SSID, les balises "
                           "consomment une part significative du temps d'antenne.",
                           "Regrouper les usages (VLAN dynamiques, 802.1X) pour réduire le nombre de SSID.",
                           many))
    legacy = sorted({_fmt(b) for b in scope if b.band == BAND_24 and b.ies.basic_rates
                     and min(b.ies.basic_rates) < 6})
    if legacy:
        out.append(Finding(MINOR, "Performance", "Débits 802.11b (1–11 Mb/s) obligatoires",
                           "Les débits de base très bas ralentissent toutes les balises et diffusions.",
                           "Désactiver les débits 802.11b et fixer le débit minimal à 12 Mb/s si possible.",
                           legacy))
    old = sorted({_fmt(b) for b in scope if b.generation <= 4})
    if old:
        out.append(Finding(INFO, "Performance", "Bornes de génération ancienne (Wi‑Fi 4 ou antérieur)",
                           "Ces bornes limitent débit, capacité et sécurité (WPA3 souvent absent).",
                           "Planifier le remplacement par du Wi‑Fi 6/6E ou 7.", old))

    # -------------------------------------------------------- Couverture
    if coverage and coverage.points:
        if coverage.area_pct_below_target > 20:
            sev = MAJOR
        elif coverage.area_pct_below_target > 5:
            sev = MINOR
        else:
            sev = None
        if sev:
            out.append(Finding(sev, "Couverture", f"Couverture insuffisante ({coverage.area_pct_below_target:.0f} % "
                               f"de la surface sous {coverage.target_dbm} dBm)",
                               "Dans ces zones, les débits chutent et la voix/vidéo devient instable.",
                               "Ajouter ou déplacer des bornes (voir la carte de couverture)."))
        if coverage.area_pct_low_snr > 10 and abs(coverage.area_pct_low_snr - coverage.area_pct_below_target) > 2:
            out.append(Finding(MINOR, "Couverture", f"Rapport signal/bruit faible sur {coverage.area_pct_low_snr:.0f} % "
                               "de la surface",
                               f"Un SNR inférieur à {coverage.snr_target} dB limite les débits atteignables.",
                               "Augmenter la densité de bornes ou réduire les sources d'interférence."))
        if coverage.area_pct_single_ap > 30 and len(radios) > 1:
            out.append(Finding(MINOR, "Itinérance", f"Recouvrement entre bornes insuffisant "
                               f"({coverage.area_pct_single_ap:.0f} % de la surface)",
                               "Une seule borne y est reçue correctement : l'itinérance sera brutale.",
                               "Prévoir 15 à 20 % de recouvrement entre cellules (2 bornes ≥ -75 dBm)."))

    out.sort(key=lambda f: SEVERITY_ORDER[f.severity])
    return out


def score(findings: list[Finding]) -> int:
    """Note sur 100 : pénalités plafonnées par gravité, pour qu'un audit très
    dégradé reste différenciable d'un audit catastrophique."""
    n = {s: sum(1 for f in findings if f.severity == s) for s in PENALTY}
    pen = min(60, 20 * n[CRIT]) + min(30, 6 * n[MAJOR]) + min(10, 2 * n[MINOR])
    return max(0, 100 - pen)
