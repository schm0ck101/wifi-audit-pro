"""Rapport d'audit : collecte des données, constats, PDF (Qt) et Excel (openpyxl)."""
from __future__ import annotations

import html
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..audit import findings as fnd
from ..audit import heatmap as hm
from ..audit.channels import analyze
from ..config import APP_NAME, APP_VERSION, AUTHOR, AUTHOR_URL
from ..wlan.model import BANDS, Bss, radio_key
from ..wlan.oui import vendor


@dataclass
class FloorReport:
    floor: dict
    points: list[dict]
    coverage: dict = field(default_factory=dict)
    images: dict = field(default_factory=dict)        # type -> HeatmapResult
    png: bytes = b""
    ap_positions: list = field(default_factory=list)  # (x, y, libellé)


@dataclass
class ReportData:
    client: dict
    site: dict
    audit: dict
    aps: list[Bss]
    own: list[Bss]
    own_ssids: set[str]
    authorized: set[str]
    findings: list[fnd.Finding]
    score: int
    floors: list[FloorReport]
    tests: list[dict]
    roams: list[dict]
    events: list
    settings: dict


def _floor_points(db, audit_id, floor_id):
    pts = db.survey_points(audit_id, floor_id)
    out = []
    for p in pts:
        sp = hm.SurveyPoint(p["x"], p["y"], p["readings"], scan_id=p["id"])
        for kind, r in p["tests"]:
            if kind == "ping":
                sp.ping = r
            elif kind == "iperf":
                sp.throughput = {"down": r.get("mbps")}
        out.append(sp)
    return pts, out


def gather(db, settings, audit_id, with_images: bool = True) -> ReportData:
    audit = db.audit(audit_id)
    site = db.site(audit["site_id"])
    client = db.client(site["client_id"])
    th = settings.section("thresholds")
    hc = settings.section("heatmap")
    aps = db.audit_aps(audit_id)
    own_ssids = set(site["own_ssids"])
    own = [b for b in aps if b.ssid in own_ssids]
    auth = set(db.authorized(site["id"]))
    info = {b.bssid: hm.ApInfo(b.bssid, b.ssid, b.channel, b.band, radio_key(b.bssid), b.max_rate) for b in aps}
    selected = {b.bssid for b in own} if own_ssids else None

    floors = []
    cov_all = fnd.CoverageStats(target_dbm=th["rssi_target"], snr_target=th["snr_target"])
    weights = 0
    for f in db.floors(site["id"]):
        raw, pts = _floor_points(db, audit_id, f["id"])
        fr = FloorReport(f, raw)
        if with_images and raw:
            fr.png = db.floor_image(f["id"]) or b""
            labels = {}
            for b in sorted(aps, key=lambda x: -x.rssi):
                labels.setdefault(radio_key(b.bssid), f"{b.ssid or 'masqué'} ·{radio_key(b.bssid)[-5:]}")
            fr.ap_positions = [(p["x"], p["y"], p["label"] or labels.get(r, r))
                               for r, p in db.ap_positions(site["id"], f["id"]).items()]
        if pts:
            args = (info, selected, f["width"], f["height"], f["scale"], float(hc.get("radius_m", 8)),
                    float(th["noise_floor"]), float(th["rssi_min"]))
            sig = hm.compute(pts, "signal", *args)
            snr = hm.compute(pts, "snr", *args)
            second = hm.compute(pts, "second", *args)
            below = hm.area_fraction(sig.grid, lambda v: v < th["rssi_target"])
            below_min = hm.area_fraction(sig.grid, lambda v: v < th["rssi_min"])
            low_snr = hm.area_fraction(snr.grid, lambda v: v < th["snr_target"])
            single = hm.area_fraction(second.grid, lambda v: v < th["rssi_min"])
            fr.coverage = {"points": len(pts), "below_target": below, "below_min": below_min,
                           "low_snr": low_snr, "single_ap": single}
            n = len(pts)
            weights += n
            cov_all.points += n
            cov_all.area_pct_below_target += below * n
            cov_all.area_pct_low_snr += low_snr * n
            cov_all.area_pct_single_ap += single * n
            if with_images:
                fr.images = {"signal": sig, "snr": snr,
                             "best_ap": hm.compute(pts, "best_ap", *args),
                             "count": hm.compute(pts, "count", *args)}
                if any(p.ping for p in pts):
                    fr.images["latency"] = hm.compute(pts, "latency", *args)
                if any(p.throughput for p in pts):
                    fr.images["throughput"] = hm.compute(pts, "throughput", *args)
        floors.append(fr)
    if weights:
        cov_all.area_pct_below_target /= weights
        cov_all.area_pct_low_snr /= weights
        cov_all.area_pct_single_ap /= weights
    f_list = fnd.evaluate(aps, own_ssids, auth if auth else None, cov_all if weights else None, th)
    return ReportData(client, site, audit, aps, own, own_ssids, auth, f_list, fnd.score(f_list), floors,
                      db.tests(audit_id), db.roams(audit_id), db.events(audit_id), settings.section("report"))


# ====================================================================== PDF
SEV_COLOR = {fnd.CRIT: "#c92a2a", fnd.MAJOR: "#e8590c", fnd.MINOR: "#d4a600", fnd.INFO: "#1c7ed6"}


def _e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _dt(ts):
    return time.strftime("%d/%m/%Y", time.localtime(ts)) if ts else "—"


def export_pdf(data: ReportData, path: str | Path, th: dict, logo_path: str = "") -> Path:
    from PySide6.QtCore import QMarginsF, QSizeF, QUrl
    from PySide6.QtGui import QImage, QPageLayout, QPageSize, QPdfWriter, QTextDocument

    import pyqtgraph as pg

    from ..ui.channels_page import spectrum_items
    from ..ui.heatmap_render import composite, legend_image
    from ..ui.plan_view import png_to_image
    from ..ui.survey_page import radio_labels

    path = Path(path)
    doc = QTextDocument()
    res_i = 0

    def add_img(img: QImage) -> str:
        nonlocal res_i
        res_i += 1
        name = f"img://{res_i}"
        doc.addResource(QTextDocument.ImageResource, QUrl(name), img)
        return name

    rep = data.settings
    labels = radio_labels(data.aps)
    sc = data.score
    sc_col = "#2b8a3e" if sc >= 80 else "#e8590c" if sc >= 50 else "#c92a2a"
    sev_count = Counter(f.severity for f in data.findings)
    radios = {radio_key(b.bssid) for b in data.own}
    css = """
      body { font-family: 'Segoe UI'; font-size: 9pt; color: #1d2733; }
      h1 { font-size: 24pt; color: #0b3d3d; margin: 0; }
      h2 { font-size: 14pt; color: #0b3d3d; margin-top: 18px; border-bottom: 2px solid #12a39a; }
      h3 { font-size: 11pt; color: #0b3d3d; margin-top: 12px; }
      .muted { color: #5b6879; }
      table.grid { border-collapse: collapse; }
      table.grid th { background: #e6f2f1; color: #24414a; padding: 4px 6px; text-align: left; font-size: 8.5pt; }
      table.grid td { padding: 3px 6px; border-bottom: 1px solid #e3e8ee; font-size: 8.5pt; }
    """
    h = [f"<html><head><style>{css}</style></head><body>"]
    # ---------------------------------------------------- page de garde
    if not (logo_path and Path(logo_path).exists()):
        from ..ui.theme import asset_path
        logo_path = str(asset_path("schmock.png"))  # logo de l'auteur par défaut
    if Path(logo_path).exists():
        logo = QImage(logo_path)
        if not logo.isNull():
            h.append(f"<p><img src='{add_img(logo.scaledToHeight(min(70, logo.height())))}'></p>")
    h.append(f"""<p style='margin-top:90px'><span class='muted' style='font-size:12pt'>RAPPORT D'AUDIT</span></p>
    <h1>Réseau Wi‑Fi</h1>
    <p style='font-size:16pt; margin-top:20px'><b>{_e(data.client['name'])}</b></p>
    <p style='font-size:12pt'>{_e(data.site['name'])}<br><span class='muted'>{_e(data.site['address'])}</span></p>
    <p style='margin-top:40px'>Audit : <b>{_e(data.audit['name'])}</b><br>
    Date : {_dt(data.audit['started'])}<br>
    Auditeur : {_e(data.audit['auditor'] or rep.get('auditor', ''))}<br>
    {_e(rep.get('company', ''))} {_e(rep.get('phone', ''))} {_e(rep.get('email', ''))}</p>
    <p style='margin-top:60px; font-size:20pt; color:{sc_col}'><b>Note globale : {sc} / 100</b></p>
    <p class='muted'>Document généré par {APP_NAME} {APP_VERSION} ({AUTHOR} · {AUTHOR_URL.removeprefix('https://')}) le {time.strftime('%d/%m/%Y à %H:%M')}.</p>
""")

    # ---------------------------------------------------------- synthèse
    h.append("<h2 style='page-break-before: always'>1. Synthèse</h2><table cellpadding='4'>")
    rows = [("Réseaux du client (SSID)", ", ".join(sorted(data.own_ssids)) or "non déclarés"),
            ("Bornes du client détectées", f"{len(radios)} radio(s), {len(data.own)} BSSID"),
            ("Bandes utilisées", ", ".join(b for b in BANDS if any(x.band == b for x in data.own)) or "—"),
            ("Réseaux voisins", f"{len(data.aps) - len(data.own)} BSSID"),
            ("Constats", f"{sev_count[fnd.CRIT]} critique(s), {sev_count[fnd.MAJOR]} majeur(s), "
                         f"{sev_count[fnd.MINOR]} mineur(s), {sev_count[fnd.INFO]} information(s)")]
    for fr in data.floors:
        if fr.coverage:
            c = fr.coverage
            rows.append((f"Couverture — {fr.floor['name']}",
                         f"{100 - c['below_target']:.0f} % de la surface ≥ {th['rssi_target']} dBm, "
                         f"{100 - c['below_min']:.0f} % ≥ {th['rssi_min']} dBm ({c['points']} points)"))
    pings = [t["result"] for t in data.tests if t["kind"] == "ping" and t["result"].get("avg") is not None]
    if pings:
        rows.append(("Latence (pings)", f"moyenne {statistics.mean(p['avg'] for p in pings):.1f} ms, "
                                        f"pertes moyennes {statistics.mean(p['loss'] for p in pings):.1f} %"))
    ip = [t["result"] for t in data.tests if t["kind"] == "iperf"]
    for d, lab in (("down", "Débit descendant"), ("up", "Débit montant")):
        v = [r["mbps"] for r in ip if r.get("direction") == d]
        if v:
            rows.append((lab, f"moyenne {statistics.mean(v):.0f} Mb/s (min {min(v):.0f}, max {max(v):.0f})"))
    gaps = [r["gap_ms"] for r in data.roams if r["gap_ms"] is not None]
    if gaps:
        rows.append(("Itinérance", f"{len(data.roams)} changement(s) de borne, coupure moyenne "
                                   f"{statistics.mean(gaps):.0f} ms (max {max(gaps):.0f} ms)"))
    h += [f"<tr><td class='muted'>{_e(a)}</td><td>{_e(b)}</td></tr>" for a, b in rows]
    h.append("</table>")

    # ---------------------------------------------------------- constats
    h.append("<h2>2. Constats et recommandations</h2>")
    if not data.findings:
        h.append("<p>Aucun écart relevé.</p>")
    for i, f in enumerate(data.findings, 1):
        col = SEV_COLOR[f.severity]
        items = "".join(f"<li>{_e(x)}</li>" for x in f.items[:15])
        more = f"<li class='muted'>… et {len(f.items) - 15} autre(s)</li>" if len(f.items) > 15 else ""
        h.append(f"""<h3><span style='color:{col}'>[{f.severity}]</span> {i}. {_e(f.title)}
        <span class='muted' style='font-size:9pt'>— {_e(f.category)}</span></h3>
        <p>{_e(f.detail)}</p><p><b>Recommandation :</b> {_e(f.recommendation)}</p>
        {('<ul>' + items + more + '</ul>') if items else ''}""")

    # -------------------------------------------------------- couverture
    titles = {"signal": "Signal de la meilleure borne", "snr": "Rapport signal / bruit",
              "best_ap": "Borne servante", "count": f"Nombre de bornes ≥ {th['rssi_min']} dBm",
              "latency": "Latence mesurée", "throughput": "Débit mesuré"}
    if any(fr.images for fr in data.floors):
        h.append("<h2 style='page-break-before: always'>3. Cartes de couverture</h2>"
                 "<p class='muted'>Les valeurs sont interpolées entre les points de mesure, dans un rayon "
                 "limité autour de chacun ; les zones non relevées restent sans couleur. Points blancs : "
                 "emplacements de mesure ; ronds bleus : bornes.</p>")
        for fr in data.floors:
            if not fr.images:
                continue
            plan = png_to_image(fr.png)
            pts = [(p["x"], p["y"]) for p in fr.points]
            for kind, res in fr.images.items():
                img = composite(plan, res, th, 0.6, pts, fr.ap_positions, max_side=1400)
                cats = [labels.get(c, c) for c in res.categories]
                leg = legend_image(kind, th, cats, 520, 44 if kind != "best_ap" else 22 + 18 * ((len(cats) + 2) // 3),
                                   "#1d2733")
                h.append(f"<h3>{_e(fr.floor['name'])} — {titles.get(kind, kind)}</h3>"
                         f"<p><img src='{add_img(img)}' width='640'></p><p><img src='{add_img(leg)}'></p>")

    # ------------------------------------------------------------ canaux
    h.append("<h2 style='page-break-before: always'>4. Occupation des canaux</h2>")
    for band in BANDS:
        if not any(b.band == band for b in data.aps):
            continue
        pw = pg.PlotWidget()
        pw.setBackground("#ffffff")
        pl = pw.getPlotItem()
        pl.showGrid(x=True, y=True, alpha=0.4)
        for ax in ("left", "bottom"):
            pl.getAxis(ax).setTextPen("#333333")
            pl.getAxis(ax).setPen("#888888")
        pl.setLabel("left", "dBm")
        pl.setLabel("bottom", f"canal ({band})")
        spectrum_items(pl, data.aps, band, data.own_ssids, -90)
        pw.resize(1200, 380)
        h.append(f"<h3>{band}</h3><p><img src='{add_img(pw.grab().toImage())}' width='660'></p>")
        st = [s for s in analyze(data.aps, band, data.own_ssids) if s.own]
        if st:
            h.append("<table class='grid' width='100%'><tr><th>Canal du client</th><th>Réseaux recouvrants</th>"
                     "<th>Occupation annoncée</th><th>Saturation</th></tr>")
            for s in st:
                h.append(f"<tr><td>{s.channel}{' (DFS)' if s.dfs else ''}</td><td>{s.overlapping}</td>"
                         f"<td>{'—' if s.utilization is None else f'{s.utilization:.0f} %'}</td>"
                         f"<td>{s.score:.0f}/100</td></tr>")
            h.append("</table>")
        pw.deleteLater()

    # ------------------------------------------------------- inventaire
    h.append("<h2 style='page-break-before: always'>5. Inventaire des points d'accès du client</h2>")
    if not data.own:
        h.append("<p class='muted'>Aucun réseau du client identifié (déclarez les SSID du site).</p>")
    else:
        h.append("<table class='grid' width='100%'><tr><th>SSID</th><th>BSSID</th><th>Fabricant</th><th>Bande / canal</th>"
                 "<th>Largeur</th><th>Norme</th><th>Sécurité</th><th>k/v/r</th><th>Signal max</th><th>Autorisée</th></tr>")
        for b in sorted(data.own, key=lambda x: (x.ssid, x.band or "", x.channel or 0)):
            h.append(f"<tr><td>{_e(b.ssid)}</td><td>{b.bssid}</td><td>{_e(vendor(b.bssid, b.ies.vendor_ouis))}</td>"
                     f"<td>{b.band} / {b.channel}</td><td>{b.width} MHz</td><td>{_e(b.standard)}</td>"
                     f"<td>{_e(b.security)}</td><td>{b.roaming_flags}</td><td>{b.rssi} dBm</td>"
                     f"<td>{'oui' if b.bssid.lower() in data.authorized else ('—' if not data.authorized else 'NON')}</td></tr>")
        h.append("</table>")
    neigh = sorted((b for b in data.aps if b not in data.own), key=lambda x: -x.rssi)[:25]
    if neigh:
        h.append("<h3>Réseaux voisins les plus puissants</h3><table class='grid' width='100%'><tr><th>SSID</th>"
                 "<th>BSSID</th><th>Bande / canal</th><th>Largeur</th><th>Sécurité</th><th>Signal max</th></tr>")
        for b in neigh:
            h.append(f"<tr><td>{_e(b.ssid or '(masqué)')}</td><td>{b.bssid}</td><td>{b.band} / {b.channel}</td>"
                     f"<td>{b.width} MHz</td><td>{_e(b.security)}</td><td>{b.rssi} dBm</td></tr>")
        h.append("</table>")

    # ------------------------------------------------------------- tests
    if data.tests or data.roams:
        h.append("<h2>6. Tests de performance</h2>")
        if data.tests:
            h.append("<table class='grid' width='100%'><tr><th>Date</th><th>Test</th><th>Résultat</th><th>Contexte</th></tr>")
            for t in data.tests:
                r = t["result"]
                if t["kind"] == "ping":
                    res = (f"{_e(r.get('target'))} : moy. {r.get('avg')} ms, pertes {r.get('loss')} %, "
                           f"jitter {r.get('jitter')} ms")
                else:
                    res = f"{'Descendant' if r.get('direction') == 'down' else 'Montant'} : {r.get('mbps')} Mb/s"
                h.append(f"<tr><td>{time.strftime('%d/%m %H:%M', time.localtime(t['ts']))}</td>"
                         f"<td>{'Ping' if t['kind'] == 'ping' else 'Débit'}{' (relevé)' if t['scan_id'] else ''}</td>"
                         f"<td>{res}</td><td>{_e(r.get('context', ''))}</td></tr>")
            h.append("</table>")
        if data.roams:
            h.append("<h3>Itinérance</h3><table class='grid' width='100%'><tr><th>Heure</th><th>De</th><th>Vers</th>"
                     "<th>Coupure</th></tr>")
            for r in data.roams:
                h.append(f"<tr><td>{time.strftime('%H:%M:%S', time.localtime(r['ts']))}</td><td>{r['from_bssid']}</td>"
                         f"<td>{r['to_bssid']}</td><td>{'—' if r['gap_ms'] is None else str(round(r['gap_ms'])) + ' ms'}</td></tr>")
            h.append("</table>")

    # -------------------------------------------------------- méthodologie
    h.append(f"""<h2>Annexe — méthodologie et limites</h2>
    <p>Mesures passives réalisées avec la carte Wi‑Fi du poste d'audit via l'API Native Wifi de Windows
    (balises et réponses de sonde des points d'accès), complétées de tests actifs (ICMP, iperf3).
    Le bruit de fond n'étant pas mesurable par une carte Wi‑Fi standard, le rapport signal/bruit est
    calculé avec un bruit de fond supposé de {th['noise_floor']} dBm. Les interférences non Wi‑Fi
    (micro‑ondes, Bluetooth, équipements industriels) nécessitent un analyseur de spectre dédié et ne
    sont pas couvertes. Seuils retenus : objectif {th['rssi_target']} dBm (voix/vidéo), minimum
    {th['rssi_min']} dBm (données), SNR ≥ {th['snr_target']} dB.</p></body></html>""")

    doc.setHtml("".join(h))
    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.A4))
    writer.setPageMargins(QMarginsF(14, 14, 14, 14), QPageLayout.Millimeter)
    writer.setResolution(110)
    writer.setTitle(f"Audit Wi‑Fi — {data.client['name']} — {data.site['name']}")
    writer.setCreator(f"{APP_NAME} {APP_VERSION} — {AUTHOR}")
    rect = writer.pageLayout().paintRectPixels(writer.resolution())
    doc.setPageSize(QSizeF(rect.width(), rect.height()))
    doc.print_(writer)
    return path


# ===================================================================== Excel
def export_excel(data: ReportData, path: str | Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="12A39A")

    def sheet(title, headers, rows, first=False):
        ws = wb.active if first else wb.create_sheet()
        ws.title = title
        ws.append(headers)
        for c in ws[1]:
            c.font, c.fill = head, fill
        for r in rows:
            ws.append(list(r))
        for i, hd in enumerate(headers, 1):
            width = max([len(str(hd))] + [len(str(r[i - 1])) for r in rows[:500] if i - 1 < len(r)])
            ws.column_dimensions[get_column_letter(i)].width = min(60, width + 2)
        ws.freeze_panes = "A2"
        return ws

    sheet("Synthèse", ["Élément", "Valeur"], [
        ("Client", data.client["name"]), ("Site", data.site["name"]), ("Audit", data.audit["name"]),
        ("Date", _dt(data.audit["started"])), ("Note globale", data.score),
        ("SSID du client", ", ".join(sorted(data.own_ssids))), ("BSSID du client", len(data.own)),
        ("BSSID voisins", len(data.aps) - len(data.own))] +
        [(f"Couverture {fr.floor['name']} (% surface sous l'objectif)", round(fr.coverage["below_target"], 1))
         for fr in data.floors if fr.coverage], first=True)
    ws = sheet("Constats", ["Gravité", "Catégorie", "Constat", "Détail", "Recommandation", "Éléments concernés"],
               [(f.severity, f.category, f.title, f.detail, f.recommendation, "\n".join(f.items))
                for f in data.findings])
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    sheet("Points d'accès", ["Client", "SSID", "BSSID", "Fabricant", "Bande", "Canal", "Largeur (MHz)", "Norme",
                             "Sécurité", "Chiffrement", "802.11k", "802.11v", "802.11r", "WPS", "PMF obligatoire",
                             "Débit max (Mb/s)", "Signal max (dBm)", "Clients", "Occupation (%)", "Autorisée"],
          [("oui" if b.ssid in data.own_ssids else "non", b.ssid, b.bssid, vendor(b.bssid, b.ies.vendor_ouis),
            b.band, b.channel, b.width, b.standard, b.security, ", ".join(b.ciphers),
            b.ies.dot11k, b.ies.dot11v, b.ies.dot11r, b.ies.wps, b.ies.mfp_required, b.max_rate, b.rssi,
            b.ies.station_count, b.ies.channel_utilization,
            b.bssid.lower() in data.authorized) for b in sorted(data.aps, key=lambda x: (x.ssid not in data.own_ssids, -x.rssi))])
    pts_rows = []
    own_b = {b.bssid for b in data.own}
    for fr in data.floors:
        for p in fr.points:
            best = max((r for b, r in p["readings"].items() if b in own_b), default=None)
            pts_rows.append((fr.floor["name"], time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(p["ts"])),
                             round(p["x"]), round(p["y"]), best, len(p["readings"])))
    sheet("Relevés", ["Étage", "Heure", "X (px)", "Y (px)", "Meilleur signal client (dBm)", "Réseaux vus"], pts_rows)
    sheet("Tests", ["Date", "Type", "Cible / sens", "Moyenne (ms) / Débit (Mb/s)", "Pertes (%)", "Contexte"],
          [(time.strftime("%d/%m/%Y %H:%M", time.localtime(t["ts"])), t["kind"],
            t["result"].get("target") or t["result"].get("direction"),
            t["result"].get("avg") if t["kind"] == "ping" else t["result"].get("mbps"),
            t["result"].get("loss"), t["result"].get("context", "")) for t in data.tests])
    sheet("Itinérances", ["Heure", "De", "Vers", "Coupure (ms)"],
          [(time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(r["ts"])), r["from_bssid"], r["to_bssid"], r["gap_ms"])
           for r in data.roams])
    sheet("Événements", ["Date", "Niveau", "Catégorie", "Message"],
          [(time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(e.ts)), e.level, e.category, e.message)
           for e in data.events])
    path = Path(path)
    wb.save(path)
    return path
