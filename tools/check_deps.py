"""Vérifie que l'exécutable compilé ne dépend que de DLL présentes sur la
plus ancienne cible : Windows Server 2016 / Windows 10 1607 (build 14393).

Exemple d'incident évité : Qt 6.10+ importe icuuc.dll (Windows 10 1703+),
l'exe refuse alors de démarrer sur Windows Server 2016
(« DLL load failed while importing QtCore »).
Usage : python tools/check_deps.py build/app/WiFiAuditPro/_internal
"""
import os
import sys

import pefile

# DLL système disponibles sur Windows 10 1607 / Server 2016 (liste vérifiée)
SYSTEM_1607 = """
advapi32 authz bcrypt comdlg32 crypt32 d3d11 d3d12 d3d9 dwmapi dwrite dxgi gdi32 imm32 iphlpapi kernel32 mpr mscms
netapi32 ntdll ole32 oleaut32 propsys rpcrt4 setupapi shell32 shlwapi uiautomationcore user32 userenv uxtheme version
winmm ws2_32 wtsapi32 comctl32 secur32 ncrypt dnsapi winhttp wininet psapi powrprof cfgmgr32 dbghelp msvcrt
opengl32 glu32 winspool wldap32 normaliz credui cryptui sspicli hid fwpuclnt
""".split()
# API sets (api-ms-win-*) présentes en 1607 ; les « crt » sont fournies par le runtime embarqué ou Windows 10
APISETS_1607 = """
api-ms-win-core-path-l1-1-0 api-ms-win-core-synch-l1-2-0 api-ms-win-core-winrt-error-l1-1-1 api-ms-win-core-winrt-l1-1-0
api-ms-win-core-winrt-string-l1-1-0 api-ms-win-shcore-scaling-l1-1-1 api-ms-win-core-winrt-error-l1-1-0
""".split()
# composants qui n'existent PAS en 1607 (message explicite)
KNOWN_BAD = {"icuuc.dll": "ICU système (Windows 10 1703+) : rester sur Qt 6.9",
             "icuin.dll": "ICU système (Windows 10 1703+)", "icu.dll": "ICU système (Windows 10 1903+)"}
# modules optionnels jamais chargés par l'application
IGNORE_FROM = {"qtuiotouchplugin.dll"}


def main(root: str) -> int:
    bundled = {f.lower() for _r, _d, fs in os.walk(root) for f in fs}
    ok_sys = {f"{n}.dll" for n in SYSTEM_1607} | {f"{n}.dll" for n in APISETS_1607}
    problems = []
    for r, _d, fs in os.walk(root):
        for f in fs:
            if not f.lower().endswith((".dll", ".pyd")) or f.lower() in IGNORE_FROM:
                continue
            try:
                pe = pefile.PE(os.path.join(r, f), fast_load=True)
                pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
            except pefile.PEFormatError:
                continue
            for e in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
                dll = e.dll.decode().lower()
                if dll in bundled or dll in ok_sys or dll.startswith("api-ms-win-crt-"):
                    continue
                problems.append((dll, f))
    if not problems:
        print("Dépendances DLL : compatibles Windows Server 2016 / Windows 10 1607.")
        return 0
    print("DÉPENDANCES INCOMPATIBLES avec Windows Server 2016 / Windows 10 1607 (ou non répertoriées) :")
    for dll, f in sorted(set(problems)):
        print(f"  {dll}  (requis par {f})  {KNOWN_BAD.get(dll, 'à vérifier puis ajouter à la liste si présent en 1607')}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "build/app/WiFiAuditPro/_internal"))
