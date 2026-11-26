# Publie la version courante sur GitHub (Release vX.Y.Z) avec la version
# portable et l'installateur de dist\. Appelé à la fin de build.ps1.
# - Version et nom lus dans installer\*.iss (#define AppVersion / AppName).
# - Refuse de publier s'il reste des modifications non enregistrées (commit).
# - Release déjà existante : les fichiers sont remplacés (même version recompilée).
param([switch]$FromCI)
$ErrorActionPreference = "Continue"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Say($msg, $color = "Cyan") { Write-Host $msg -ForegroundColor $color }

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { Say "GitHub CLI (gh) absent : release non publiée." Yellow; return }
$iss = Get-ChildItem installer\*.iss | Select-Object -First 1
$ver = ([regex]::Match((Get-Content $iss.FullName -Raw), '#define AppVersion "([^"]+)"')).Groups[1].Value
$app = ([regex]::Match((Get-Content $iss.FullName -Raw), '#define AppName "([^"]+)"')).Groups[1].Value
if (-not $ver) { Say "Version introuvable dans $($iss.Name)." Yellow; return }
$tag = "v$ver"

if (-not $FromCI -and (git status --porcelain)) {
    Say "Modifications non enregistrées dans git : release $tag NON publiée (faire un commit d'abord)." Yellow
    return
}
# le compte gh actif doit être le propriétaire du dépôt
$url = git remote get-url origin
$owner = ([regex]::Match($url, 'github\.com[:/]([^/]+)/')).Groups[1].Value
$active = gh api user --jq .login 2>$null
if (-not $FromCI) {
    if ($owner -and $active -ne $owner) { gh auth switch -h github.com -u $owner | Out-Null }
    git push -q origin HEAD 2>$null
    if ($LASTEXITCODE -ne 0) { Say "git push a échoué : release non publiée." Yellow; return }
}

$assets = @(Get-ChildItem dist -File | Where-Object { $_.Name -like "*-Portable.exe" -or $_.Name -like "*-Setup-$ver.exe" } | ForEach-Object FullName)
if ($assets.Count -lt 2) { Say "Exécutables manquants dans dist\ : release non publiée." Yellow; return }

gh release view $tag *> $null
if ($LASTEXITCODE -eq 0) {
    Say "Release $tag existante : remplacement des fichiers..."
    gh release upload $tag $assets --clobber
} else {
    $prev = git describe --tags --abbrev=0 2>$null
    $range = if ($LASTEXITCODE -eq 0 -and $prev) { "$prev..HEAD" } else { "HEAD" }
    $changes = (git log $range --format="- %s" --no-merges) -join "`n"
    $portable = Split-Path -Leaf ($assets | Where-Object { $_ -like "*-Portable.exe" })
    $setup = Split-Path -Leaf ($assets | Where-Object { $_ -like "*-Setup-*" })
    $notes = @"
## Téléchargement
- **$portable** : version portable, un seul fichier, rien à installer (clé USB).
- **$setup** : installateur Windows (sans droits administrateur).

Windows 10 / 11 (64 bits). Créé par SCHMOCK — https://schmock.fr

## Changements
$changes
"@
    $nf = [IO.Path]::GetTempFileName()
    [IO.File]::WriteAllText($nf, $notes, (New-Object Text.UTF8Encoding($false)))
    Say "Publication de la release $tag..."
    gh release create $tag $assets --title "$app $ver" --notes-file $nf --target (git rev-parse --abbrev-ref HEAD)
    Remove-Item $nf -Force
}
if ($LASTEXITCODE -eq 0) { Say "Release $tag publiée : $(gh release view $tag --json url --jq .url)" Green }
else { Say "La publication de la release a échoué." Yellow }
