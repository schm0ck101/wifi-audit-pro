"""Identification du fabricant à partir de l'adresse MAC (BSSID)."""
from __future__ import annotations

import gzip
import json
import sys
import threading
from pathlib import Path

from .model import is_locally_administered

_db: dict[str, str] | None = None
_lock = threading.Lock()


def _path() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    p = base / "wifiaudit" / "data" / "oui.json.gz"
    return p if p.exists() else Path(__file__).resolve().parents[1] / "data" / "oui.json.gz"


def _load() -> dict[str, str]:
    global _db
    with _lock:
        if _db is None:
            try:
                with gzip.open(_path(), "rt", encoding="utf-8") as f:
                    _db = json.load(f)
            except OSError:
                _db = {}
        return _db


def vendor(mac: str, vendor_ouis: list[str] | None = None) -> str:
    """Fabricant d'une adresse MAC. Pour les BSSID « administrés localement »
    (SSID secondaires, MAC virtuelles), on se rabat sur les OUI des éléments
    propriétaires annoncés par la borne."""
    db = _load()
    h = mac.replace(":", "").replace("-", "").upper()
    if not is_locally_administered(mac):
        for n in (9, 7, 6):
            if h[:n] in db:
                return db[h[:n]]
    else:
        # bit local neutralisé : de nombreux constructeurs dérivent leurs MAC ainsi
        first = int(h[:2], 16) & ~0x02
        alt = f"{first:02X}{h[2:6]}"
        if alt in db:
            return db[alt] + " (virtuelle)"
    for o in vendor_ouis or []:
        k = o.replace(":", "").upper()
        if k in db and k not in ("0050F2", "001018", "00904C", "506F9A"):   # MS/Broadcom/Wi‑Fi Alliance
            return db[k] + " (élément propriétaire)"
    return "Inconnu" if not is_locally_administered(mac) else "MAC virtuelle"
