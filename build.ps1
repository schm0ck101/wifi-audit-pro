# Compilation complète de WiFi Audit Pro :
#   1. environnement virtuel + dépendances
#   2. tests unitaires
#   3. icône
#   4. exécutable (PyInstaller) -> build\app\WiFiAuditPro\WiFiAuditPro.exe (interne, utilisé par l'installateur)
#   5. version portable en un seul fichier -> dist\WiFiAuditPro-Portable.exe
#   7. publication GitHub (release vX.Y.Z : portable + installateur), sauf -NoRelease
#   6. installateur (Inno Setup, si présent) -> dist\WiFiAuditPro-Setup-1.0.2.exe
# Utilisation : clic droit > Exécuter avec PowerShell, ou  .\build.ps1 [-SkipTests] [-SkipPortable]
param([switch]$SkipTests, [switch]$SkipPortable, [switch]$NoRelease)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Python 3.13 + Qt 6.9 : Qt 6.10 et suivants exigent icuuc.dll (Windows 10 1703+),
# absent de Windows Server 2016 ; PySide6 6.9 n'existe pas pour Python 3.14+.
$py = ".\.venv\Scripts\python.exe"
if ((Test-Path $py) -and -not ((& $py -c "import sys; print(sys.version_info[:2] == (3, 13))") -eq "True")) {
    Write-Host "Environnement virtuel avec une autre version de Python : recréation..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force .venv
}
if (-not (Test-Path $py)) {
    Write-Host "Création de l'environnement virtuel (Python 3.13)..." -ForegroundColor Cyan
    py -3.13 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Python 3.13 introuvable : installer avec « py install 3.13 »" }
}
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r requirements-dev.txt

if (-not $SkipTests) {
    Write-Host "Tests unitaires..." -ForegroundColor Cyan
    $env:WIFIAUDIT_DATA_DIR = Join-Path $env:TEMP "wifiaudit-build-tests"
    & $py -m pytest -q tests\test_core.py
    if ($LASTEXITCODE -ne 0) { throw "Les tests ont échoué" }
    Remove-Item Env:\WIFIAUDIT_DATA_DIR
}

Write-Host "Icône..." -ForegroundColor Cyan
& $py tools\make_icon.py

Write-Host "Exécutable (PyInstaller)..." -ForegroundColor Cyan
& $py -m PyInstaller --noconfirm --clean --distpath build\app WiFiAuditPro.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller a échoué" }
if (Test-Path dist\WiFiAuditPro) { Remove-Item -Recurse -Force dist\WiFiAuditPro }
Write-Host "Compatibilité Windows Server 2016..." -ForegroundColor Cyan
& $py tools\check_deps.py build\app\WiFiAuditPro\_internal
if ($LASTEXITCODE -ne 0) { throw "L'exécutable ne démarrerait pas sur Windows Server 2016 (voir ci-dessus)" }

if (-not $SkipPortable) {
    Write-Host "Version portable (un seul fichier)..." -ForegroundColor Cyan
    $env:WIFIAUDIT_ONEFILE = "1"
    & $py -m PyInstaller --noconfirm --workpath build\portable WiFiAuditPro.spec
    Remove-Item Env:\WIFIAUDIT_ONEFILE
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller (portable) a échoué" }
}

$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
          "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
    Write-Host "Installateur (Inno Setup)..." -ForegroundColor Cyan
    & $iscc /Q installer\WiFiAuditPro.iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup a échoué" }
} else {
    Write-Host "Inno Setup 6 introuvable : installateur non généré." -ForegroundColor Yellow
    Write-Host "  Installation : winget install JRSoftware.InnoSetup" -ForegroundColor Yellow
}

Copy-Item README.md dist\ -Force
Write-Host "`nTerminé. Résultats dans le dossier dist\ :" -ForegroundColor Green
Get-ChildItem dist | Format-Table Name, @{n="Taille (Mo)";e={ if ($_.PSIsContainer) { "{0:N1}" -f ((Get-ChildItem $_.FullName -Recurse | Measure-Object Length -Sum).Sum/1MB) } else { "{0:N1}" -f ($_.Length/1MB) } }}

if (-not $NoRelease -and -not $SkipPortable) { & "$PSScriptRoot\tools\release.ps1" }
