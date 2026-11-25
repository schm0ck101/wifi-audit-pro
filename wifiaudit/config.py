"""Configuration persistante (JSON dans %APPDATA%\\WiFiAuditPro)."""
from __future__ import annotations

import copy
import json
import logging
import os
import threading
from pathlib import Path

APP_NAME = "WiFi Audit Pro"
APP_VERSION = "1.0.3"
AUTHOR = "SCHMOCK"
AUTHOR_URL = "https://schmock.fr"
log = logging.getLogger(__name__)


def data_dir() -> Path:
    base = os.environ.get("WIFIAUDIT_DATA_DIR") or os.path.join(
        os.environ.get("APPDATA", str(Path.home())), "WiFiAuditPro")
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


DEFAULTS: dict = {
    "scan": {"interval": 10, "max_age": 20, "interface": ""},
    "thresholds": {
        "rssi_target": -67,      # dBm : objectif voix/vidéo
        "rssi_min": -75,         # dBm : minimum données
        "noise_floor": -92,      # dBm : bruit supposé (Windows ne le mesure pas)
        "snr_target": 25,        # dB
        "util_warn": 50,         # % temps d'antenne (BSS Load)
        "util_crit": 75,
        "max_ssids_per_radio": 4,
        "cochannel_max": 3,
    },
    "heatmap": {"radius_m": 8.0, "power": 2.0, "opacity": 0.6, "ping_at_point": True,
                "ping_target": "", "iperf_at_point": False},
    "supervision": {"interval": 10, "missing_scans": 3, "alert_rssi": -75, "ping_targets": "",
                    "ping_interval": 1.0},
    "iperf": {"server": "", "port": 5201, "seconds": 10, "streams": 1},
    "report": {"company": AUTHOR, "auditor": "", "logo": "", "phone": "", "email": ""},
    "ui": {"theme": "dark", "notifications": True, "tray_notifications": True},
    "context": {"client": None, "site": None, "audit": None},
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(out.get(k), dict) and isinstance(v, dict) else v
    return out


class Settings:
    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "settings.json"
        self._lock = threading.RLock()
        self._d = copy.deepcopy(DEFAULTS)
        try:
            if self.path.exists():
                self._d = _merge(DEFAULTS, json.loads(self.path.read_text(encoding="utf-8")))
        except Exception as exc:
            log.warning("Paramètres illisibles (%s), valeurs par défaut", exc)

    def get(self, key: str, default=None):
        with self._lock:
            node = self._d
            for part in key.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return copy.deepcopy(node)

    def set(self, key: str, value) -> None:
        with self._lock:
            node = self._d
            parts = key.split(".")
            for p in parts[:-1]:
                node = node.setdefault(p, {})
            node[parts[-1]] = value

    def section(self, name: str) -> dict:
        return self.get(name, {}) or {}

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._d, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
