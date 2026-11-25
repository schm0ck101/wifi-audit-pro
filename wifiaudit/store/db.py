"""Base de données SQLite : clients, sites, étages (plans), audits, scans,
observations, points d'accès, bornes autorisées, tests, itinérances,
événements. Mode WAL, accès protégé par un verrou (écritures peu fréquentes)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from ..config import data_dir
from ..events import Event
from ..wlan.model import Bss

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients(id INTEGER PRIMARY KEY, name TEXT NOT NULL, contact TEXT DEFAULT '',
  email TEXT DEFAULT '', phone TEXT DEFAULT '', address TEXT DEFAULT '', notes TEXT DEFAULT '', created REAL);
CREATE TABLE IF NOT EXISTS sites(id INTEGER PRIMARY KEY, client_id INTEGER NOT NULL, name TEXT NOT NULL,
  address TEXT DEFAULT '', own_ssids TEXT DEFAULT '[]', notes TEXT DEFAULT '', created REAL);
CREATE TABLE IF NOT EXISTS floors(id INTEGER PRIMARY KEY, site_id INTEGER NOT NULL, name TEXT NOT NULL,
  image BLOB, width INTEGER, height INTEGER, scale REAL, position INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS audits(id INTEGER PRIMARY KEY, site_id INTEGER NOT NULL, name TEXT NOT NULL,
  kind TEXT DEFAULT 'audit', auditor TEXT DEFAULT '', started REAL, ended REAL, notes TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS scans(id INTEGER PRIMARY KEY, audit_id INTEGER NOT NULL, ts REAL,
  kind TEXT DEFAULT 'scan', floor_id INTEGER, x REAL, y REAL);
CREATE INDEX IF NOT EXISTS ix_scans ON scans(audit_id, kind);
CREATE TABLE IF NOT EXISTS obs(scan_id INTEGER NOT NULL, bssid TEXT NOT NULL, rssi INTEGER, channel INTEGER);
CREATE INDEX IF NOT EXISTS ix_obs ON obs(scan_id);
CREATE TABLE IF NOT EXISTS aps(audit_id INTEGER NOT NULL, bssid TEXT NOT NULL, ssid TEXT, proto TEXT,
  ies BLOB, first_seen REAL, last_seen REAL, max_rssi INTEGER, last_rssi INTEGER,
  PRIMARY KEY(audit_id, bssid));
CREATE TABLE IF NOT EXISTS authorized(site_id INTEGER NOT NULL, bssid TEXT NOT NULL, ssid TEXT,
  label TEXT DEFAULT '', PRIMARY KEY(site_id, bssid));
CREATE TABLE IF NOT EXISTS ap_positions(site_id INTEGER NOT NULL, radio TEXT NOT NULL, floor_id INTEGER,
  x REAL, y REAL, label TEXT DEFAULT '', manual INTEGER DEFAULT 0, PRIMARY KEY(site_id, radio));
CREATE TABLE IF NOT EXISTS tests(id INTEGER PRIMARY KEY, audit_id INTEGER NOT NULL, ts REAL, kind TEXT,
  scan_id INTEGER, result TEXT);
CREATE TABLE IF NOT EXISTS roams(id INTEGER PRIMARY KEY, audit_id INTEGER NOT NULL, ts REAL,
  from_bssid TEXT, to_bssid TEXT, gap_ms REAL, rssi_before INTEGER, rssi_after INTEGER);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, audit_id INTEGER, ts REAL, level TEXT,
  category TEXT, message TEXT);
CREATE INDEX IF NOT EXISTS ix_events ON events(audit_id, ts);
"""


def _rows(cur) -> list[dict]:
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


class Database:
    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "wifiaudit.db"
        self._c = sqlite3.connect(str(self.path), check_same_thread=False, timeout=15)
        self._c.execute("PRAGMA journal_mode=WAL")
        self._c.execute("PRAGMA foreign_keys=OFF")
        self._c.executescript(SCHEMA)
        self._c.commit()
        self.lock = threading.RLock()

    def close(self):
        with self.lock:
            self._c.close()

    def q(self, sql: str, args=()) -> list[dict]:
        with self.lock:
            return _rows(self._c.execute(sql, args))

    def x(self, sql: str, args=()) -> int:
        with self.lock:
            cur = self._c.execute(sql, args)
            self._c.commit()
            return cur.lastrowid

    # ------------------------------------------------------------ clients
    def clients(self):
        return self.q("SELECT * FROM clients ORDER BY name COLLATE NOCASE")

    def client(self, cid):
        r = self.q("SELECT * FROM clients WHERE id=?", (cid,))
        return r[0] if r else None

    def save_client(self, data: dict) -> int:
        f = ("name", "contact", "email", "phone", "address", "notes")
        if data.get("id"):
            self.x(f"UPDATE clients SET {', '.join(k + '=?' for k in f)} WHERE id=?",
                   [data.get(k, "") for k in f] + [data["id"]])
            return data["id"]
        return self.x(f"INSERT INTO clients({', '.join(f)}, created) VALUES({', '.join('?' * len(f))}, ?)",
                      [data.get(k, "") for k in f] + [time.time()])

    def delete_client(self, cid):
        for s in self.sites(cid):
            self.delete_site(s["id"])
        self.x("DELETE FROM clients WHERE id=?", (cid,))

    # -------------------------------------------------------------- sites
    def sites(self, client_id):
        rows = self.q("SELECT * FROM sites WHERE client_id=? ORDER BY name COLLATE NOCASE", (client_id,))
        for r in rows:
            r["own_ssids"] = json.loads(r["own_ssids"] or "[]")
        return rows

    def site(self, sid):
        r = self.q("SELECT * FROM sites WHERE id=?", (sid,))
        if r:
            r[0]["own_ssids"] = json.loads(r[0]["own_ssids"] or "[]")
        return r[0] if r else None

    def save_site(self, data: dict) -> int:
        ss = json.dumps(sorted(set(data.get("own_ssids", []))))
        if data.get("id"):
            self.x("UPDATE sites SET name=?, address=?, own_ssids=?, notes=? WHERE id=?",
                   (data["name"], data.get("address", ""), ss, data.get("notes", ""), data["id"]))
            return data["id"]
        return self.x("INSERT INTO sites(client_id, name, address, own_ssids, notes, created) "
                      "VALUES(?,?,?,?,?,?)", (data["client_id"], data["name"], data.get("address", ""),
                                             ss, data.get("notes", ""), time.time()))

    def delete_site(self, sid):
        for a in self.audits(sid):
            self.delete_audit(a["id"])
        for t in ("floors", "authorized", "ap_positions"):
            self.x(f"DELETE FROM {t} WHERE site_id=?", (sid,))
        self.x("DELETE FROM sites WHERE id=?", (sid,))

    # ------------------------------------------------------------- étages
    def floors(self, site_id):
        return self.q("SELECT id, site_id, name, width, height, scale, position FROM floors "
                      "WHERE site_id=? ORDER BY position, id", (site_id,))

    def floor_image(self, fid) -> bytes | None:
        r = self.q("SELECT image FROM floors WHERE id=?", (fid,))
        return r[0]["image"] if r else None

    def add_floor(self, site_id, name, png: bytes, w, h) -> int:
        pos = len(self.floors(site_id))
        return self.x("INSERT INTO floors(site_id, name, image, width, height, position) VALUES(?,?,?,?,?,?)",
                      (site_id, name, png, w, h, pos))

    def update_floor(self, fid, **kw):
        for k, v in kw.items():
            if k in ("name", "scale"):
                self.x(f"UPDATE floors SET {k}=? WHERE id=?", (v, fid))

    def delete_floor(self, fid):
        self.x("DELETE FROM floors WHERE id=?", (fid,))
        self.x("UPDATE scans SET floor_id=NULL, x=NULL, y=NULL WHERE floor_id=?", (fid,))
        self.x("DELETE FROM ap_positions WHERE floor_id=?", (fid,))

    # ------------------------------------------------------------- audits
    def audits(self, site_id):
        return self.q("SELECT a.*, (SELECT COUNT(*) FROM scans s WHERE s.audit_id=a.id AND s.kind='point') AS points, "
                      "(SELECT COUNT(*) FROM aps p WHERE p.audit_id=a.id) AS ap_count "
                      "FROM audits a WHERE site_id=? ORDER BY started DESC", (site_id,))

    def audit(self, aid):
        r = self.q("SELECT * FROM audits WHERE id=?", (aid,))
        return r[0] if r else None

    def create_audit(self, site_id, name, kind="audit", auditor="") -> int:
        return self.x("INSERT INTO audits(site_id, name, kind, auditor, started) VALUES(?,?,?,?,?)",
                      (site_id, name, kind, auditor, time.time()))

    def update_audit(self, aid, **kw):
        for k, v in kw.items():
            if k in ("name", "notes", "auditor", "ended"):
                self.x(f"UPDATE audits SET {k}=? WHERE id=?", (v, aid))

    def delete_audit(self, aid):
        self.x("DELETE FROM obs WHERE scan_id IN (SELECT id FROM scans WHERE audit_id=?)", (aid,))
        for t in ("scans", "aps", "tests", "roams", "events"):
            self.x(f"DELETE FROM {t} WHERE audit_id=?", (aid,))
        self.x("DELETE FROM audits WHERE id=?", (aid,))

    # -------------------------------------------------------------- scans
    def save_scan(self, audit_id, bss: list[Bss], kind="scan", floor_id=None, x=None, y=None,
                  ts=None) -> int:
        ts = ts or time.time()
        with self.lock:
            cur = self._c.execute("INSERT INTO scans(audit_id, ts, kind, floor_id, x, y) VALUES(?,?,?,?,?,?)",
                                  (audit_id, ts, kind, floor_id, x, y))
            sid = cur.lastrowid
            self._c.executemany("INSERT INTO obs VALUES(?,?,?,?)",
                                [(sid, b.bssid, b.rssi, b.channel) for b in bss])
            for b in bss:
                proto = json.dumps({"freq": b.freq_mhz, "cap": b.capability, "phy": b.phy_type,
                                    "rates": b.rates_win, "quality": b.link_quality,
                                    "beacon": b.beacon_period})
                self._c.execute(
                    "INSERT INTO aps(audit_id, bssid, ssid, proto, ies, first_seen, last_seen, max_rssi, last_rssi) "
                    "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(audit_id, bssid) DO UPDATE SET "
                    "ssid=excluded.ssid, proto=excluded.proto, ies=excluded.ies, last_seen=excluded.last_seen, "
                    "max_rssi=MAX(max_rssi, excluded.max_rssi), last_rssi=excluded.last_rssi",
                    (audit_id, b.bssid, b.ssid, proto, b.ie_blob, ts, ts, b.rssi, b.rssi))
            self._c.commit()
            return sid

    def delete_scan(self, scan_id):
        self.x("DELETE FROM obs WHERE scan_id=?", (scan_id,))
        self.x("DELETE FROM tests WHERE scan_id=?", (scan_id,))
        self.x("DELETE FROM scans WHERE id=?", (scan_id,))

    def survey_points(self, audit_id, floor_id) -> list[dict]:
        pts = self.q("SELECT id, ts, x, y FROM scans WHERE audit_id=? AND kind='point' AND floor_id=? "
                     "ORDER BY ts", (audit_id, floor_id))
        if not pts:
            return pts
        ids = [p["id"] for p in pts]
        obs: dict[int, dict] = {i: {} for i in ids}
        tests: dict[int, list] = {i: [] for i in ids}
        marks = ",".join("?" * len(ids))
        for r in self.q(f"SELECT scan_id, bssid, rssi FROM obs WHERE scan_id IN ({marks})", ids):
            obs[r["scan_id"]][r["bssid"]] = r["rssi"]
        for r in self.q(f"SELECT scan_id, kind, result FROM tests WHERE scan_id IN ({marks})", ids):
            tests[r["scan_id"]].append((r["kind"], json.loads(r["result"])))
        for p in pts:
            p["readings"] = obs[p["id"]]
            p["tests"] = tests[p["id"]]
        return pts

    def rssi_history(self, audit_id, bssid, since=0.0) -> list[tuple[float, int]]:
        r = self.q("SELECT s.ts, o.rssi FROM obs o JOIN scans s ON s.id=o.scan_id "
                   "WHERE s.audit_id=? AND o.bssid=? AND s.ts>=? ORDER BY s.ts", (audit_id, bssid, since))
        return [(x["ts"], x["rssi"]) for x in r]

    def audit_aps(self, audit_id) -> list[Bss]:
        """Points d'accès vus pendant l'audit (signal = maximum observé)."""
        out = []
        for r in self.q("SELECT * FROM aps WHERE audit_id=?", (audit_id,)):
            p = json.loads(r["proto"] or "{}")
            b = Bss(bssid=r["bssid"], ssid=r["ssid"] or "", rssi=r["max_rssi"],
                    link_quality=p.get("quality", 0), freq_mhz=p.get("freq", 0), phy_type=p.get("phy", 0),
                    beacon_period=p.get("beacon", 100), capability=p.get("cap", 0),
                    rates_win=p.get("rates", []), ie_blob=r["ies"] or b"", seen=r["last_seen"])
            b.first_seen, b.last_seen, b.last_rssi = r["first_seen"], r["last_seen"], r["last_rssi"]
            out.append(b)
        return out

    def scan_count(self, audit_id) -> int:
        return self.q("SELECT COUNT(*) n FROM scans WHERE audit_id=?", (audit_id,))[0]["n"]

    # --------------------------------------------------- bornes autorisées
    def authorized(self, site_id) -> dict[str, dict]:
        return {r["bssid"]: r for r in self.q("SELECT * FROM authorized WHERE site_id=?", (site_id,))}

    def set_authorized(self, site_id, bssid, ssid, on=True, label=""):
        if on:
            self.x("INSERT OR REPLACE INTO authorized VALUES(?,?,?,?)", (site_id, bssid.lower(), ssid, label))
        else:
            self.x("DELETE FROM authorized WHERE site_id=? AND bssid=?", (site_id, bssid.lower()))

    # --------------------------------------------------- positions bornes
    def ap_positions(self, site_id, floor_id=None) -> dict[str, dict]:
        sql, args = "SELECT * FROM ap_positions WHERE site_id=?", [site_id]
        if floor_id is not None:
            sql += " AND floor_id=?"
            args.append(floor_id)
        return {r["radio"]: r for r in self.q(sql, args)}

    def set_ap_position(self, site_id, radio, floor_id, x, y, label="", manual=True):
        self.x("INSERT OR REPLACE INTO ap_positions VALUES(?,?,?,?,?,?,?)",
               (site_id, radio, floor_id, x, y, label, int(manual)))

    # ---------------------------------------------- tests, itinérances…
    def add_test(self, audit_id, kind, result: dict, scan_id=None) -> int:
        return self.x("INSERT INTO tests(audit_id, ts, kind, scan_id, result) VALUES(?,?,?,?,?)",
                      (audit_id, time.time(), kind, scan_id, json.dumps(result)))

    def tests(self, audit_id, kind=None):
        sql, args = "SELECT * FROM tests WHERE audit_id=?", [audit_id]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        rows = self.q(sql + " ORDER BY ts", args)
        for r in rows:
            r["result"] = json.loads(r["result"])
        return rows

    def add_roam(self, audit_id, ts, frm, to, gap_ms, rb, ra):
        self.x("INSERT INTO roams(audit_id, ts, from_bssid, to_bssid, gap_ms, rssi_before, rssi_after) "
               "VALUES(?,?,?,?,?,?,?)", (audit_id, ts, frm, to, gap_ms, rb, ra))

    def roams(self, audit_id):
        return self.q("SELECT * FROM roams WHERE audit_id=? ORDER BY ts", (audit_id,))

    def add_event(self, ev: Event):
        self.x("INSERT INTO events(audit_id, ts, level, category, message) VALUES(?,?,?,?,?)",
               (ev.audit_id, ev.ts, ev.level, ev.category, ev.message))

    def events(self, audit_id=None, limit=2000):
        if audit_id is None:
            r = self.q("SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,))
        else:
            r = self.q("SELECT * FROM events WHERE audit_id=? ORDER BY ts DESC LIMIT ?", (audit_id, limit))
        return [Event(x["level"], x["category"], x["message"], x["audit_id"], x["ts"]) for x in r]
