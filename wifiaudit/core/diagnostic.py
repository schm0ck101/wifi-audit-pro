"""Export de diagnostic : capture brute de ce que Windows renvoie (cartes,
BSS avec éléments 802.11 en hexadécimal, connexion, horloges), pour analyser
un comportement sur un poste donné. Aucune donnée n'est envoyée : le
fichier est enregistré localement."""
from __future__ import annotations

import json
import platform
import sys
import time
import traceback

from ..config import APP_NAME, APP_VERSION
from ..wlan import api
from ..wlan.model import Bss


def collect(scan_first: bool = True) -> dict:
    out: dict = {"app": f"{APP_NAME} {APP_VERSION}", "python": sys.version,
                 "windows": platform.platform(), "version": platform.version(),
                 "date": time.strftime("%Y-%m-%d %H:%M:%S"), "errors": []}
    try:
        c = api.get_client()
    except api.WlanError as exc:
        out["errors"].append(f"Ouverture : {exc} (code {exc.code})")
        return out
    try:
        ifs = c.interfaces()
        out["interfaces"] = [{"guid": i.guid, "description": i.description, "state": i.state} for i in ifs]
    except api.WlanError as exc:
        out["errors"].append(f"Cartes : {exc}")
        return out
    out["scans"] = []
    for i in ifs:
        entry: dict = {"interface": i.description}
        try:
            if scan_first:
                ev = c.trigger_scan(i)
                entry["scan_notified"] = ev.wait(8)
            entry["clock_filetime"] = api.filetime_now()
            entry["clock_interrupt"] = api.interrupt_time()
            raw = c.bss_list(i, raw=True)
            entry["bss"] = []
            for d in raw:
                b = Bss(bssid=d["bssid"], ssid=d["ssid"], rssi=d["rssi"], link_quality=d["quality"],
                        freq_mhz=d["freq_khz"] / 1000, capability=d["cap"], rates_win=d["rates"],
                        ie_blob=d["ies"])
                dd = {k: v for k, v in d.items() if k != "ies"}
                dd["ies_hex"] = d["ies"].hex()
                try:
                    dd["decoded"] = b.to_dict()
                except Exception as exc:
                    dd["decoded_error"] = repr(exc)
                entry["bss"].append(dd)
        except api.LocationDenied as exc:
            entry["error"] = f"Localisation refusée : {exc}"
        except Exception as exc:
            entry["error"] = "".join(traceback.format_exception(exc))
        try:
            conn = c.connection(i)
            entry["connection"] = conn.__dict__ if conn else None
        except Exception as exc:
            entry["connection_error"] = repr(exc)
        out["scans"].append(entry)
    return out


def export(path: str, scan_first: bool = True) -> dict:
    data = collect(scan_first)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False, default=str)
    return data
