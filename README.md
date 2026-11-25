# WiFi Audit Pro — documentation

![SCHMOCK](assets/schmock.png)

Créé par **SCHMOCK** — <https://schmock.fr>

**Télécharger** (version portable ou installateur) : <https://github.com/schm0ck101/wifi-audit-pro/releases/latest>

Logiciel professionnel d'**audit** et de **supervision** de réseaux Wi‑Fi pour
Windows 10 et 11 : inventaire des points d'accès, analyse des canaux, relevés
sur plan avec cartes de couverture (heatmaps), supervision en direct (mode
régie) avec alertes, tests de performance et rapport d'audit PDF / Excel.

Fonctionne entièrement en local, sans compte ni abonnement, **sans droits
administrateur** et sans pilote spécial (API Wi‑Fi native de Windows).

---

## 1. Installation
- **Installateur** : `WiFiAuditPro-Setup-1.0.1.exe` (installation pour l'utilisateur courant).
- **Portable** : `WiFiAuditPro-Portable.exe`, sans installation (clé USB d'intervention).
- **Sources** : `run.bat` (Python 3.12 ou plus récent).

### Prérequis matériel
- Une carte Wi‑Fi (intégrée ou USB). Pour auditer la bande **6 GHz**, la carte
  doit être compatible **Wi‑Fi 6E ou 7**.
- **Windows 11 (24H2 et suivants)** : activez *Paramètres › Confidentialité et
  sécurité › Localisation* puis « Autoriser les applications de bureau à
  accéder à votre position ». Sans cela, Windows refuse la liste des réseaux ;
  l'application le détecte et affiche un bouton qui ouvre directement ce réglage.

---

## 2. Organisation : Clients › Sites › Audits
Page **Clients & sites** :
1. **+ Client** : nom, contact, coordonnées.
2. **+ Site** : adresse et surtout **les SSID du client** (séparés par des
   virgules). Ils servent à distinguer ses réseaux du voisinage dans toutes les
   analyses.
3. **Importer un plan** pour chaque étage (PNG, JPG ou PDF), puis **Étalonner
   l'échelle** : deux clics sur une distance connue + sa valeur en mètres.
4. **Nouvel audit** (mission ponctuelle) ou **Nouvelle supervision**
   (exploitation en direct) → il devient l'**audit courant** : tous les scans,
   relevés, tests et alertes y sont enregistrés.

Sans audit courant, les scans s'affichent mais ne sont pas enregistrés.

---

## 3. Scan
Inventaire de tous les points d'accès : SSID, BSSID, fabricant, signal, canal,
bande, largeur, norme (Wi‑Fi 4 à 7), sécurité, débit théorique, nombre de clients
et occupation du canal (si la borne les annonce), fonctions d'itinérance
802.11k/v/r, statut (client, voisin, autorisée, **suspecte**).

- Scan manuel ou continu (5 s à 1 min).
- Détail complet de la borne sélectionnée et courbe de son signal.
- **Bornes autorisées** : sélectionnez les bornes du client et marquez‑les
  autorisées (ou « Autoriser toutes les bornes du client » après vérification).
  Toute autre borne diffusant un SSID du client devient **suspecte** (borne
  pirate / « evil twin »).
- **Exporter un diagnostic** : fichier JSON contenant les données brutes reçues
  de Windows (utile pour le support).

## 4. Canaux
Spectre par bande (2,4 / 5 / 6 GHz) : chaque réseau est dessiné sur sa largeur
réelle (les réseaux du client en trait épais). Tableau d'occupation par canal
(réseaux recouvrants, occupation annoncée, score de saturation) et **meilleurs
choix** de canal pour la largeur souhaitée.

## 5. Relevé sur plan (heatmap)
1. Choisissez l'étage, mode **« Relevé : clic = mesure »**.
2. Placez‑vous physiquement à un endroit, cliquez sur votre position sur le plan
   et restez immobile ≈ 4 s (un scan complet est réalisé).
3. Répétez tous les 3 à 5 m, en couvrant chaque pièce.

Options à chaque point : **ping de la passerelle** (latence/pertes) et **test de
débit iperf3**.

Cartes disponibles : signal de la meilleure borne, rapport signal/bruit,
deuxième borne (itinérance), nombre de bornes ≥ seuil, interférences co‑canal,
débit PHY estimé, borne servante, latence et débit mesurés. Filtres par réseau
(client, tous, un SSID) et par bande.

- **Estimer la position des bornes** à partir des mesures, puis ajustez‑les en
  mode « Positionner les bornes » (glisser‑déposer).
- Mode « Consultation » : clic sur un point pour le supprimer.
- **Exporter l'image** de la carte (PNG).

Les valeurs sont interpolées **uniquement dans un rayon** autour des points
mesurés (8 m par défaut) : l'outil n'invente pas de couverture là où rien n'a
été mesuré.

## 6. Supervision (mode régie)
Scan continu, état de chaque borne (autorisée, inconnue, **suspecte**,
**disparue**, voisine), connexion du poste (signal, débit), **ping continu** de
la passerelle et d'Internet, journal d'alertes. Alertes (bandeau dans la fenêtre
et notification Windows) :
- borne non autorisée diffusant un SSID du client, ou sécurité différente ;
- borne autorisée qui disparaît / réapparaît ;
- changement de canal d'une borne ;
- canal saturé (élément BSS Load) ;
- signal faible sur la connexion du poste ;
- itinérance (avec durée de coupure mesurée) ;
- perte de connectivité (passerelle ou Internet).

Les alertes ne se déclenchent qu'aux changements d'état (pas de rafales).

## 7. Tests
- **Ping** (passerelle par défaut) : latence, pertes, jitter.
- **Débit iperf3** (descendant, montant ou les deux) vers un serveur
  `iperf3 -s` (serveur du client, NAS, serveur public) — client intégré, aucun
  logiciel à installer sur le poste d'audit.
- **Test d'itinérance** : déplacez‑vous entre les bornes ; chaque changement de
  borne est enregistré avec la coupure mesurée (ping toutes les 100 ms).

## 8. Rapport
**Analyser l'audit** affiche les constats classés par gravité (Critique, Majeur,
Mineur, Information) et une **note sur 100**. Règles appliquées notamment :
réseau ouvert, WEP, WPA/TKIP, WPS, WPA3/PMF, SSID masqué, borne non autorisée,
canaux 2,4 GHz chevauchants ou en 40 MHz, 160 MHz en multi‑bornes, interférences
co‑canal, saturation des canaux, 802.11k/v/r, trop de SSID par radio, débits
802.11b, matériel ancien, couverture, SNR, recouvrement entre cellules.

- **Rapport PDF** : page de garde (client, site, auditeur, logo de votre
  société), synthèse, constats et recommandations, cartes de couverture, spectre
  par bande, inventaire des bornes, réseaux voisins, tests, méthodologie.
- **Export Excel** : synthèse, constats, points d'accès, relevés, tests,
  itinérances, événements.

L'identité de votre société (nom, auditeur, téléphone, e‑mail, logo) se règle
dans **Paramètres**.

---

## 9. Données
`%APPDATA%\WiFiAuditPro\` : `wifiaudit.db` (base SQLite), `settings.json`,
`wifiaudit.log`. À sauvegarder pour conserver l'historique des clients.

## 10. Limites (cartes Wi‑Fi standard sous Windows)
- Pas d'analyse de spectre (interférences non Wi‑Fi : micro‑ondes, Bluetooth…) :
  il faut un analyseur dédié.
- Pas de capture de trames (mode moniteur non disponible sous Windows).
- Le bruit de fond n'est pas mesurable : le SNR est calculé avec un bruit supposé
  (‑92 dBm par défaut, réglable).
- Le nombre de clients et l'occupation du canal ne sont connus que si les bornes
  les annoncent (élément BSS Load, courant sur le matériel professionnel).
- Windows limite la fréquence des scans (≈ 4 s par scan, davantage quand le
  poste est connecté).

---

## 11. Pour les développeurs
```
wifiaudit/
  wlan/   api.py (wlanapi.dll via ctypes), ies.py (décodage 802.11), model.py, oui.py
  audit/  channels.py, findings.py (règles), heatmap.py (IDW, couverture, position des bornes)
  core/   service.py (scans en thread), supervision.py (alertes), pinger.py, diagnostic.py
  net/    icmp.py (ICMP natif), iperf3.py (client iperf3)
  store/  db.py (SQLite)
  report/ build.py (PDF Qt, Excel openpyxl)
  ui/     pages PySide6 (tableau de bord, clients, scan, canaux, relevé, supervision, tests, rapport, paramètres)
tests/
  test_core.py   tests unitaires (trames 802.11 construites selon la norme)
  smoke_ui.py    test de l'interface complète (banc de test : scans injectés,
                 le poste de développement n'ayant pas de carte Wi‑Fi)
```
Compilation : `powershell -ExecutionPolicy Bypass -File build.ps1` → `dist\`
(exe, version portable, installateur si Inno Setup 6 est présent).
Base des fabricants : `python tools/build_oui.py <fichier manuf de Wireshark>`.
