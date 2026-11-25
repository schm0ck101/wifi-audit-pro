"""Événements (alertes de supervision, journal d'audit) et journalisation."""
from __future__ import annotations

import logging
import logging.handlers
import time
from dataclasses import dataclass, field

INFO, SUCCESS, WARNING, ERROR = "info", "success", "warning", "error"
LEVEL_LABEL = {INFO: "Information", SUCCESS: "Rétabli", WARNING: "Avertissement", ERROR: "Critique"}
CATEGORY_LABEL = {"ap": "Point d'accès", "rogue": "Borne suspecte", "signal": "Signal",
                  "roam": "Itinérance", "ping": "Connectivité", "channel": "Canal",
                  "load": "Charge", "system": "Système", "test": "Test", "survey": "Relevé"}


@dataclass(slots=True)
class Event:
    level: str
    category: str
    message: str
    audit_id: int | None = None
    ts: float = field(default_factory=time.time)


def setup_logging(debug: bool = False) -> None:
    from .config import data_dir
    root = logging.getLogger()
    if any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        return
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    fh = logging.handlers.RotatingFileHandler(data_dir() / "wifiaudit.log", maxBytes=2_000_000,
                                              backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(threadName)s %(name)s : %(message)s"))
    root.addHandler(fh)
