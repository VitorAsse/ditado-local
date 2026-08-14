[CmdletBinding()]
param(
    [string]$Version = "0.2.2"
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
    "ditado_audio.py",
    "ditado_chat.py",
    "ditado_local.pyw",
    "ditado_storage.py",
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

$assetsSource = Join-Path $repositoryRoot "assets"
$assetsDestination = Join-Path $stagingRoot "assets"
if (-not (Test-Path -LiteralPath (Join-Path $assetsSource "ditado-local.png"))) {
    throw "Captura publica obrigatoria ausente: assets\ditado-local.png"
}
New-Item -ItemType Directory -Path $assetsDestination -Force | Out-Null
Copy-Item `
    -LiteralPath (Join-Path $assetsSource "ditado-local.png") `
    -Destination (Join-Path $assetsDestination "ditado-local.png")

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
Write-Output $archivePath
