"""Tests unitaires du moteur. Les trames sont construites octet par octet
selon IEEE 802.11 pour vérifier le décodeur sur des cas connus.

    .venv\\Scripts\\python -m pytest -q tests
"""
import math
import struct
import time

import numpy as np
import pytest

from wifiaudit.audit import channels, findings, heatmap
from wifiaudit.wlan.ies import parse_ies
from wifiaudit.wlan.model import BAND_5, BAND_6, BAND_24, Bss, freq_to_channel, radio_key
from wifiaudit.wlan.oui import vendor


# --------------------------------------------------------------- outils
def ie(eid, data: bytes) -> bytes:
    return bytes([eid, len(data)]) + data


def ext(eid, data: bytes) -> bytes:
    return ie(255, bytes([eid]) + data)


def rsn(akms, pairwise=(4,), group=4, caps=0) -> bytes:
    d = struct.pack("<H", 1) + b"\x00\x0f\xac" + bytes([group])
    d += struct.pack("<H", len(pairwise)) + b"".join(b"\x00\x0f\xac" + bytes([p]) for p in pairwise)
    d += struct.pack("<H", len(akms)) + b"".join(b"\x00\x0f\xac" + bytes([a]) for a in akms)
    d += struct.pack("<H", caps)
    return ie(48, d)


def ht_cap(streams=2, w40=True):
    info = 0x02 if w40 else 0
    mcs = bytes([0xFF] * streams + [0] * (16 - streams))
    return ie(45, struct.pack("<H", info) + b"\x17" + mcs + b"\x00" * 7)


def ht_op(primary, offset=0, any_width=False):
    return ie(61, bytes([primary, offset | (0x04 if any_width else 0)]) + b"\x00" * 20)


def vht_cap(streams=2):
    m = 0
    for ss in range(8):
        m |= (0 if ss < streams else 3) << (2 * ss)
    return ie(191, struct.pack("<I", 0) + struct.pack("<HHHH", m, 0, m, 0))


def vht_op(width, seg0, seg1=0):
    return ie(192, bytes([width, seg0, seg1]) + b"\x00\x00")


def he_cap(streams=2):
    m = 0
    for ss in range(8):
        m |= (0 if ss < streams else 3) << (2 * ss)
    return ext(35, b"\x00" * 6 + b"\x00" * 11 + struct.pack("<HH", m, m))


def he_op_6ghz(primary, width, seg0, seg1=0):
    params = (1 << 17)
    return ext(36, params.to_bytes(3, "little") + b"\x01" + b"\x00\x00"
               + bytes([primary, width, seg0, seg1, 6]))


def eht_op(width, ccfs0, ccfs1=0):
    return ext(106, b"\x01" + b"\x00" * 4 + bytes([width, ccfs0, ccfs1]))


def bss(ies: bytes, freq: float, rssi=-60, bssid="00:15:6d:00:00:01", ssid="", cap=0x0011) -> Bss:
    return Bss(bssid=bssid, ssid=ssid, rssi=rssi, link_quality=80, freq_mhz=freq, capability=cap,
               ie_blob=ies)


# --------------------------------------------------------- décodage IEs
def test_wpa3_wifi6_5ghz_80mhz_roaming():
    blob = (ie(0, b"Bureau") + ie(1, bytes([0x8c, 0x12, 0x98, 0x24, 0xb0, 0x48, 0x60, 0x6c]))
            + rsn([8], caps=(1 << 6) | (1 << 7)) + ht_cap(2) + ht_op(36, 1, True)
            + vht_cap(2) + vht_op(1, 42) + he_cap(2) + ie(11, struct.pack("<H", 12) + bytes([128]) + b"\x00\x00")
            + ie(54, b"\x34\x12\x01") + ie(70, b"\x00" * 5) + ie(127, b"\x00\x00\x08\x00"))
    b = bss(blob, 5180)
    assert b.ssid == "Bureau" and b.band == BAND_5 and b.channel == 36
    assert b.width == 80 and b.center_channel == 42
    assert b.standard.startswith("Wi‑Fi 6") and b.streams == 2
    assert b.security == "WPA3‑Personnel" and b.ies.mfp_required
    assert b.ies.dot11k and b.ies.dot11v and b.ies.dot11r and b.roaming_flags == "k v r"
    assert b.ies.station_count == 12 and b.ies.channel_utilization == pytest.approx(50.2, abs=0.1)
    assert b.max_rate == pytest.approx(1201.0)
    assert 6.0 in b.ies.basic_rates and not b.insecure


def test_24ghz_wpa2_tkip_wps_40mhz():
    blob = (ie(0, b"Vieux") + ie(1, bytes([0x82, 0x84, 0x8b, 0x96, 0x0c, 0x12, 0x18, 0x24]))
            + ie(3, b"\x03") + rsn([2], pairwise=(2, 4), group=2) + ht_cap(1) + ht_op(3, 1, True)
            + ie(221, b"\x00\x50\xf2\x04\x10\x4a\x00\x01\x10") + ie(221, b"\x00\x50\xf2\x02\x01\x01"))
    b = bss(blob, 2422)
    assert b.band == BAND_24 and b.channel == 3 and b.width == 40
    assert b.security == "WPA2‑Personnel" and "TKIP" in b.ciphers and b.insecure
    assert b.ies.wps and b.ies.wmm and min(b.ies.basic_rates) == 1.0
    assert b.standard == "Wi‑Fi 4 (802.11n)"


def test_open_wep_wpa1_owe_enterprise():
    assert bss(ie(0, b"Libre"), 2437, cap=0x0001).security == "Ouvert"
    assert bss(ie(0, b"Old"), 2437, cap=0x0011).security == "WEP (obsolète)"
    wpa1 = ie(221, b"\x00\x50\xf2\x01\x01\x00" + b"\x00\x50\xf2\x02" + b"\x01\x00\x00\x50\xf2\x02"
              + b"\x01\x00\x00\x50\xf2\x02")
    b = bss(wpa1, 2437)
    assert b.security == "WPA (obsolète)" and "TKIP" in b.ciphers
    assert bss(rsn([18], caps=1 << 7), 5180).security == "Enhanced Open (OWE)"
    assert bss(rsn([1]), 5180).security == "WPA2‑Entreprise"
    assert bss(rsn([5], caps=(1 << 6) | (1 << 7)), 5180).security == "WPA3‑Entreprise"
    assert bss(rsn([2, 8], caps=1 << 7), 5180).security == "WPA2/WPA3‑Personnel"


def test_hidden_ssid():
    b = bss(ie(0, b"\x00\x00\x00") + rsn([2]), 2412)
    assert b.hidden and b.ssid == ""


def test_6ghz_wifi6e_and_wifi7_320():
    b = bss(ie(0, b"SixE") + rsn([8], caps=0xC0) + he_cap(2) + he_op_6ghz(37, 2, 39), 6135)
    assert b.band == BAND_6 and b.channel == 37 and b.width == 80 and b.center_channel == 39
    assert b.standard == "Wi‑Fi 6E (802.11ax)"
    b7 = bss(ie(0, b"Sept") + rsn([24], caps=0xC0) + he_cap(2) + he_op_6ghz(37, 3, 47, 47)
             + ext(108, b"\x00" * 10) + eht_op(4, 47, 63), 6135)
    assert b7.standard == "Wi‑Fi 7 (802.11be)" and b7.width == 320 and b7.center_channel == 63
    assert b7.max_rate == pytest.approx(2882.4 * 2)


def test_truncated_and_garbage_ies_never_raise():
    for blob in (b"\x00\x05ab", b"\x30\x02\x01", b"\xff\x01\x24", bytes(range(256)) * 3, b""):
        parse_ies(blob)


def test_freq_and_radio():
    assert freq_to_channel(2412) == (BAND_24, 1) and freq_to_channel(2484) == (BAND_24, 14)
    assert freq_to_channel(5745) == (BAND_5, 149) and freq_to_channel(5955) == (BAND_6, 1)
    assert radio_key("02:11:22:33:44:55") == radio_key("00:11:22:33:44:66")


def test_vendor():
    assert "Ubiquiti" in vendor("00:15:6d:00:00:01")
    assert "virtuelle" in vendor("02:15:6d:00:00:01") or vendor("02:15:6d:00:00:01") == "MAC virtuelle"


# ----------------------------------------------------- canaux et constats
def _net(ch, freq, rssi, ssid, mac, width40=False, extra=b""):
    blob = ie(0, ssid.encode()) + rsn([2]) + ht_cap(2) + ht_op(ch, 1 if width40 else 0, width40) + extra
    return bss(blob, freq, rssi, mac, ssid)


def test_channel_analysis_and_recommendation():
    aps = [_net(1, 2412, -50, "A", "00:00:00:00:01:01"), _net(1, 2412, -55, "B", "00:00:00:00:02:01"),
           _net(6, 2437, -80, "C", "00:00:00:00:03:01"), _net(4, 2427, -60, "D", "00:00:00:00:04:01")]
    st = channels.analyze(aps, BAND_24)
    by = {s.channel: s for s in st}
    assert by[1].primary == 2 and by[1].overlapping == 3       # A, B + D (canal 4 chevauche 1)
    assert by[11].overlapping == 0 and by[11].score == 0
    reco = channels.recommend(st, BAND_24)
    assert reco[0][0] == "Canal 11"


def test_findings_rules():
    own = {"Corp"}
    aps = [
        _net(3, 2422, -55, "Corp", "00:00:00:00:01:01", width40=True),
        bss(ie(0, b"Corp") + ie(221, b"\x00\x50\xf2\x04\x10"), 5180, -60, "00:00:00:00:02:01", "Corp", cap=0x0001),
        _net(1, 2412, -50, "Voisin", "00:00:00:00:09:01"),
    ]
    f = findings.evaluate(aps, own, authorized={"00:00:00:00:01:01"})
    titles = {x.title for x in f}
    assert "Réseau ouvert sans chiffrement" in titles
    assert "WPS activé" in titles
    assert "Canaux 2,4 GHz chevauchants" in titles
    assert "Canaux de 40 MHz en 2,4 GHz" in titles
    assert "Point d'accès non autorisé diffusant un SSID du client" in titles
    assert f[0].severity == findings.CRIT
    assert all("Voisin" not in i for x in f for i in x.items)       # voisins hors périmètre
    assert 0 <= findings.score(f) < 50


def test_coverage_finding():
    f = findings.evaluate([], set(), coverage=findings.CoverageStats(points=20, area_pct_below_target=30))
    assert any(x.category == "Couverture" and x.severity == findings.MAJOR for x in f)


# ------------------------------------------------------------- heatmap
def test_heatmap_interpolation_and_positions():
    aps = {"a": heatmap.ApInfo("a", "Corp", 36, BAND_5, "ra"), "b": heatmap.ApInfo("b", "Corp", 40, BAND_5, "rb")}
    pts = [heatmap.SurveyPoint(10, 10, {"a": -40, "b": -80}), heatmap.SurveyPoint(90, 10, {"a": -70, "b": -45}),
           heatmap.SurveyPoint(50, 90, {"a": -60, "b": -60}), heatmap.SurveyPoint(12, 14, {"a": -42})]
    r = heatmap.compute(pts, "signal", aps, None, 100, 100, scale_m_per_px=0.1, radius_m=5)
    g = r.grid
    assert g.shape[0] > 10 and np.isnan(g).any() and (~np.isnan(g)).any()
    cy, cx = int(10 / r.cell_px), int(10 / r.cell_px)
    assert -45 <= g[cy, cx] <= -38
    assert np.nanmax(g) <= -38 and np.nanmin(g) >= -81
    best = heatmap.compute(pts, "best_ap", aps, None, 100, 100, 0.1, 5)
    assert set(best.categories) == {"ra", "rb"}
    cnt = heatmap.point_values(pts, "count", aps, None, threshold=-65)[0]
    assert list(cnt) == [1, 1, 2, 1]
    pos = heatmap.estimate_ap_positions(pts, aps)
    assert pos["ra"][0] < 30 and pos["rb"][0] > 50
    assert 0 < heatmap.area_fraction(g, lambda v: v < -50) < 100
    assert heatmap.area_fraction(g, lambda v: v < -67) == 0      # meilleure borne toujours >= -60


# ----------------------------------------------------------------- base
def test_database_roundtrip(tmp_path):
    from wifiaudit.store.db import Database
    db = Database(tmp_path / "t.db")
    cid = db.save_client({"name": "ACME"})
    sid = db.save_site({"client_id": cid, "name": "Siège", "own_ssids": ["Corp"]})
    fid = db.add_floor(sid, "RDC", b"png", 800, 600)
    aid = db.create_audit(sid, "Audit 1")
    a = _net(36, 5180, -50, "Corp", "00:00:00:00:01:01")
    scan = db.save_scan(aid, [a], kind="point", floor_id=fid, x=10, y=20)
    db.add_test(aid, "ping", {"avg": 3.2}, scan_id=scan)
    a2 = _net(36, 5180, -45, "Corp", "00:00:00:00:01:01")
    db.save_scan(aid, [a2])
    aps = db.audit_aps(aid)
    assert len(aps) == 1 and aps[0].rssi == -45 and aps[0].ssid == "Corp" and aps[0].ies.ht
    pts = db.survey_points(aid, fid)
    assert pts[0]["readings"] == {"00:00:00:00:01:01": -50} and pts[0]["tests"][0][1]["avg"] == 3.2
    assert db.site(sid)["own_ssids"] == ["Corp"]
    db.set_authorized(sid, "00:00:00:00:01:01", "Corp")
    assert "00:00:00:00:01:01" in db.authorized(sid)
    db.delete_client(cid)
    assert not db.clients() and not db.q("SELECT * FROM obs")
    db.close()


# -------------------------------------------------------- supervision
def test_supervisor_alerts():
    from wifiaudit.core.supervision import ST_MISSING, ST_ROGUE, Supervisor
    evs = []
    sup = Supervisor({"Corp"}, {"00:00:00:00:01:01"}, {"util_crit": 75}, missing_scans=2, emit=evs.append)
    good = _net(36, 5180, -50, "Corp", "00:00:00:00:01:01")
    sup.update([good])
    rogue = _net(40, 5200, -55, "Corp", "66:66:66:66:66:66")
    sup.update([good, rogue])
    assert sup.tracks["66:66:66:66:66:66"].status == ST_ROGUE
    sup.update([])
    sup.update([])
    assert sup.tracks["00:00:00:00:01:01"].status == ST_MISSING
    sup.update([good])
    msgs = " | ".join(e.message for e in evs)
    assert "NON AUTORISÉE" in msgs and "invisible" in msgs and "de nouveau visible" in msgs


def test_wlan_unavailable_is_reported_cleanly():
    """Sur un poste sans Wi‑Fi, l'API renvoie une erreur explicite."""
    from wifiaudit.wlan.api import WlanError, get_client, reset_client
    reset_client()
    try:
        get_client().interfaces()
    except WlanError as exc:
        assert str(exc)


def test_ping_series_loopback():
    from wifiaudit.core.pinger import ping_series
    r = ping_series("127.0.0.1", count=3, interval=0.05)
    assert r["received"] == 3 and r["loss"] == 0 and r["avg"] is not None
