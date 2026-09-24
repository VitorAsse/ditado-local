[CmdletBinding()]
param(
    [string]$Version = "0.4.9"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($Version -notmatch "^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$") {
    throw "Versao invalida. Use o formato 1.2.3 ou 1.2.3-beta.1."
}

$repositoryRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$releaseRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $repositoryRoot "release")
)
$packageName = "DitadoLocal-$Version"
$stagingRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $releaseRoot $packageName)
)
$archivePath = [System.IO.Path]::GetFullPath(
    (Join-Path $releaseRoot "$packageName.zip")
)
$releasePrefix = $releaseRoot.TrimEnd("\") + "\"
if (
    -not $stagingRoot.StartsWith(
        $releasePrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    ) -or
    -not $archivePath.StartsWith(
        $releasePrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )
) {
    throw "Os caminhos calculados para a Release sairam da pasta permitida."
}

if (Test-Path -LiteralPath $stagingRoot) {
    Remove-Item -LiteralPath $stagingRoot -Recurse -Force
}
if (Test-Path -LiteralPath $archivePath) {
    Remove-Item -LiteralPath $archivePath -Force
}

New-Item -ItemType Directory -Path $stagingRoot -Force | Out-Null

$packageFiles = @(
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "THIRD_PARTY_NOTICES.md",
    "config.example.json",
    "ditado_ai.py",
    "ditado_harness.py",
    "ditado_audio.py",
    "ditado_chat.py",
    "ditado_desktop.py",
    "ditado_notification.py",
    "ditado_quick_correction.py",
    "ditado_hotkey.py",
    "ditado_cloud.py",
    "ditado_local.pyw",
    "ditado_ollama.py",
    "ditado_storage.py",
    "ditado_skill_catalog.py",
    "builtin_skills.json",
    "ditado_theme.py",
    "install.ps1",
    "launch_ditado.vbs",
    "launch_ditado_background.vbs",
    "requirements.lock",
    "requirements.txt"
)

foreach ($fileName in $packageFiles) {
    $sourcePath = Join-Path $repositoryRoot $fileName
    if (-not (Test-Path -LiteralPath $sourcePath)) {
        throw "Arquivo obrigatorio ausente: $fileName"
    }
    Copy-Item -LiteralPath $sourcePath -Destination (Join-Path $stagingRoot $fileName)
}

$supabaseSource = Join-Path $repositoryRoot "supabase"
$supabaseDestination = Join-Path $stagingRoot "supabase"
if (-not (Test-Path -LiteralPath (Join-Path $supabaseSource "ditado_cloud_schema.sql"))) {
    throw "Schema do Supabase ausente: supabase\ditado_cloud_schema.sql"
}
New-Item -ItemType Directory -Path $supabaseDestination -Force | Out-Null
Copy-Item `
    -LiteralPath (Join-Path $supabaseSource "ditado_cloud_schema.sql") `
    -Destination (Join-Path $supabaseDestination "ditado_cloud_schema.sql")

$docsSource = Join-Path $repositoryRoot "docs"
$docsDestination = Join-Path $stagingRoot "docs"
New-Item -ItemType Directory -Path $docsDestination -Force | Out-Null
foreach ($documentName in @("CLOUD_SYNC.md", "AGENT_HARNESS.md", "SKILLS.md")) {
    $documentSource = Join-Path $docsSource $documentName
    if (-not (Test-Path -LiteralPath $documentSource)) {
        throw "Documentacao obrigatoria ausente: docs\$documentName"
    }
    Copy-Item -LiteralPath $documentSource -Destination (Join-Path $docsDestination $documentName)
}

$scriptsSource = Join-Path $repositoryRoot "scripts"
$scriptsDestination = Join-Path $stagingRoot "scripts"
if (-not (Test-Path -LiteralPath (Join-Path $scriptsSource "configure-supabase-secure.ps1"))) {
    throw "Bootstrap seguro ausente: scripts\configure-supabase-secure.ps1"
}
if (-not (Test-Path -LiteralPath (Join-Path $scriptsSource "configure-supabase-secure.cmd"))) {
    throw "Launcher seguro ausente: scripts\configure-supabase-secure.cmd"
}
New-Item -ItemType Directory -Path $scriptsDestination -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $scriptsSource 'install-gpu.py') -Destination $scriptsDestination
Copy-Item `
    -LiteralPath (Join-Path $scriptsSource "configure-supabase-secure.ps1") `
    -Destination (Join-Path $scriptsDestination "configure-supabase-secure.ps1")
Copy-Item `
    -LiteralPath (Join-Path $scriptsSource "configure-supabase-secure.cmd") `
    -Destination (Join-Path $scriptsDestination "configure-supabase-secure.cmd")

$assetsSource = Join-Path $repositoryRoot "assets"
$assetsDestination = Join-Path $stagingRoot "assets"
foreach ($assetFileName in @(
    "ditado-local.png",
    "ditado-local-icon.png",
    "ditado-local.ico"
)) {
    if (-not (Test-Path -LiteralPath (Join-Path $assetsSource $assetFileName))) {
        throw "Asset obrigatorio ausente: assets\$assetFileName"
    }
}
New-Item -ItemType Directory -Path $assetsDestination -Force | Out-Null
foreach ($assetFileName in @(
    "ditado-local.png",
    "ditado-local-icon.png",
    "ditado-local.ico"
)) {
    Copy-Item `
        -LiteralPath (Join-Path $assetsSource $assetFileName) `
        -Destination (Join-Path $assetsDestination $assetFileName)
}

$fontsSource = Join-Path $assetsSource "fonts"
$fontsDestination = Join-Path $assetsDestination "fonts"
foreach ($fontFileName in @("DMSans.ttf", "OFL.txt")) {
    $fontSourcePath = Join-Path $fontsSource $fontFileName
    if (-not (Test-Path -LiteralPath $fontSourcePath)) {
        throw "Fonte obrigatoria ausente: assets\fonts\$fontFileName"
    }
}
New-Item -ItemType Directory -Path $fontsDestination -Force | Out-Null
Copy-Item `
    -LiteralPath (Join-Path $fontsSource "DMSans.ttf") `
    -Destination (Join-Path $fontsDestination "DMSans.ttf")
Copy-Item `
    -LiteralPath (Join-Path $fontsSource "OFL.txt") `
    -Destination (Join-Path $fontsDestination "OFL.txt")

Compress-Archive -LiteralPath $stagingRoot -DestinationPath $archivePath
$checksum = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
[System.IO.File]::WriteAllText(
    "$archivePath.sha256",
    "$checksum  $packageName.zip" + [Environment]::NewLine,
    [System.Text.Encoding]::ASCII
)
Write-Output $archivePath
