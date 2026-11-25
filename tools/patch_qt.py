"""Compatibilité Windows Server 2016 / Windows 10 1607 des DLL Qt 6.

Qt 6 ne prend officiellement en charge que Windows 10 1809+. Deux imports
statiques empêchent son chargement sur 1607 (« La procédure spécifiée est
introuvable ») :

- Qt6Core.dll   -> kernel32!SetThreadDescription : sur 1607 / Server 2016, cette
  fonction n'existe que dans KernelBase.dll (documentation Microsoft). Elle ne
  sert qu'à nommer les threads pour le débogage : redirigée vers
  GetCurrentThreadId (aucun effet, résultat positif = succès).
- qwindows.dll  -> user32!SetProcessDpiAwarenessContext (Windows 10 1703+) :
  redirigée vers SetProcessDPIAware (Vista+). Le mode d'échelle (PerMonitorV2,
  PerMonitor en repli) est déclaré dans le manifeste de l'exe (manifest.xml) et
  appliqué par Windows avant tout code : l'appel de Qt n'est alors qu'une
  confirmation.
- qwindows.dll  -> UIAutomationCore!UiaRaiseNotificationEvent (Windows 10 1709+,
  annonces des lecteurs d'écran) : redirigée vers UiaClientsAreListening (sans
  effet, renvoie 0/1 = S_OK/S_FALSE).

Le nom importé est remplacé sur place (même longueur ou plus court, complété
par des zéros) : le chargeur Windows résout l'import par son nom.
Utilisé par le fichier .spec de PyInstaller : copie corrigée dans build/qtpatch.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pefile

PATCHES = {
    "qt6core.dll": [("kernel32.dll", b"SetThreadDescription", b"GetCurrentThreadId")],
    "qwindows.dll": [("user32.dll", b"SetProcessDpiAwarenessContext", b"SetProcessDPIAware"),
                     ("uiautomationcore.dll", b"UiaRaiseNotificationEvent", b"UiaClientsAreListening")],
}


def patch_file(src: str | Path, dst: str | Path, rules) -> list[str]:
    data = bytearray(Path(src).read_bytes())
    pe = pefile.PE(data=bytes(data), fast_load=True)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
    done = []
    for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
        dll = entry.dll.decode().lower()
        for imp in entry.imports:
            for rdll, old, new in rules:
                if dll == rdll and imp.name == old:
                    assert len(new) <= len(old)
                    off = imp.name_offset
                    assert bytes(data[off:off + len(old)]) == old, "décalage inattendu"
                    data[off:off + len(old)] = new + b"\0" * (len(old) - len(new))
                    done.append(f"{Path(src).name}: {rdll}!{old.decode()} -> {new.decode()}")
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Path(dst).write_bytes(bytes(data))
    return done


def patch_binaries(binaries, workdir: str | Path) -> list:
    """Remplace, dans la liste a.binaries de PyInstaller, les DLL Qt concernées
    par une copie corrigée. Renvoie la nouvelle liste."""
    out = []
    work = Path(workdir)
    for entry in binaries:
        dest, src, kind = entry[0], entry[1], entry[2]
        base = Path(dest).name.lower()
        if base in PATCHES:
            target = work / dest
            changes = patch_file(src, target, PATCHES[base])
            for c in changes:
                print(f"[compat 1607] {c}")
            if not changes:
                print(f"[compat 1607] {base} : rien à corriger")
                shutil.copy2(src, target)
            out.append((dest, str(target), kind))
        else:
            out.append(entry)
    return out
