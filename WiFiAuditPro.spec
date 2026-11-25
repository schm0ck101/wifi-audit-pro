# -*- mode: python ; coding: utf-8 -*-
# Spécification PyInstaller : application fenêtrée (sans console), dossier
# autonome dist\WiFiAuditPro\ contenant WiFiAuditPro.exe et ses dépendances.
import os

from PyInstaller.utils.hooks import collect_submodules

hidden = collect_submodules("pyqtgraph.graphicsItems") + collect_submodules("pyqtgraph.exporters") + ["openpyxl"]

# Modules Qt / Python inutiles : exclus pour alléger l'exécutable
excludes = [
    "tkinter", "matplotlib", "scipy", "pandas", "IPython", "pytest", "PIL",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtQuick", "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets", "PySide6.QtQml", "PySide6.Qt3DCore", "PySide6.Qt3DRender",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtPdfWidgets",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtDesigner", "PySide6.QtHelp",  # QtOpenGL(Widgets) : requis par pyqtgraph 0.14
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech",
    "PySide6.QtHttpServer", "PySide6.QtNetworkAuth", "PySide6.QtVirtualKeyboard",
    "OpenGL", "pyqtgraph.opengl", "cupy", "numba", "h5py", "colorcet",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[("assets/WiFiAuditPro.ico", "assets"), ("assets/schmock.png", "assets"), ("wifiaudit/data/oui.json.gz", "wifiaudit/data")],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=1,
)

# Bibliothèques Qt embarquées par les hooks mais jamais utilisées ici
# (rendu logiciel OpenGL, Qt Quick/QML, lecteur PDF, Direct2D…)
DROP = ("opengl32sw.dll", "qt6quick", "qt6qml", "qt6virtualkeyboard",
        "qdirect2d", "qt6network", "qtnetwork", "qtiff", "qwebp", "qicns", "qtga", "qwbmp")


def _keep(entry):
    name = entry[0].replace("\\", "/").lower()
    base = name.rsplit("/", 1)[-1]
    if any(base.startswith(d) or base == d for d in DROP):
        return False
    # traductions Qt : seulement le français
    if "/translations/" in name and not base.endswith("_fr.qm"):
        return False
    return True


a.binaries = [b for b in a.binaries if _keep(b)]
a.datas = [d for d in a.datas if _keep(d)]

pyz = PYZ(a.pure)

# WIFIAUDIT_ONEFILE=1 : version portable en un seul fichier (build.ps1)
ONEFILE = os.environ.get("WIFIAUDIT_ONEFILE") == "1"
common = dict(debug=False, strip=False, upx=False, console=False,  # aucune fenêtre de terminal
              icon="assets/WiFiAuditPro.ico", version="tools/version_info.txt")

if ONEFILE:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="WiFiAuditPro-Portable",
              runtime_tmpdir=None, **common)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="WiFiAuditPro", **common)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="WiFiAuditPro")

