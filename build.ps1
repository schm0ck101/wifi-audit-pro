# Compilation complète de WiFi Audit Pro :
#   1. environnement virtuel + dépendances
#   2. tests unitaires
#   3. icône
#   4. exécutable (PyInstaller) -> build\app\WiFiAuditPro\WiFiAuditPro.exe (interne, utilisé par l'installateur)
#   5. version portable en un seul fichier -> dist\WiFiAuditPro-Portable.exe
#   7. publication GitHub (release vX.Y.Z : portable + installateur), sauf -NoRelease
#   6. installateur (Inno Setup, si présent) -> dist\WiFiAuditPro-Setup-1.0.0.exe
# Utilisation : clic droit > Exécuter avec PowerShell, ou  .\build.ps1 [-SkipTests] [-SkipPortable]
param([switch]$SkipTests, [switch]$SkipPortable, [switch]$NoRelease)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path .venv\Scripts\python.exe)) {
    Write-Host "Création de l'environnement virtuel..." -ForegroundColor Cyan
    python -m venv .venv
}
$py = ".\.venv\Scripts\python.exe"
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
