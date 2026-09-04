[CmdletBinding()]
param(
    [switch]$StartWithWindows = $true,
    [switch]$SkipLaunch
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$sourceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$installRoot = Join-Path $env:LOCALAPPDATA "faster-whisper"
$requirementsPath = Join-Path $sourceRoot "requirements.lock"

if (-not (Test-Path -LiteralPath $requirementsPath)) {
    throw "requirements.lock nao foi encontrado ao lado do instalador."
}

function Find-CompatiblePython {
    $pythonLauncher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($pythonLauncher) {
        $previousErrorPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            $runtimeLines = & $pythonLauncher.Source -0p 2>$null
        }
        finally {
            $ErrorActionPreference = $previousErrorPreference
        }
        foreach ($runtimeLine in $runtimeLines) {
            $hasSupportedVersion = $runtimeLine -match "3\.(11|12)"
            $hasExecutablePath = $runtimeLine -match "([A-Za-z]:\\.*python\.exe)$"
            $candidatePath = if ($hasExecutablePath) { $Matches[1] } else { "" }
            if (
                $hasSupportedVersion -and
                $hasExecutablePath -and
                (Test-Path -LiteralPath $candidatePath)
            ) {
                return @{
                    Command = $candidatePath
                    Prefix = @()
                }
            }
        }
    }

    $python = Get-Command "python.exe" -ErrorAction SilentlyContinue
    if ($python) {
        $version = & $python.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
        if ($LASTEXITCODE -eq 0 -and $version -in @("3.11", "3.12")) {
            return @{
                Command = $python.Source
                Prefix = @()
            }
        }
    }

    throw "Python 3.11 ou 3.12 nao foi encontrado. Instale pelo site python.org e execute novamente."
}

Write-Host "Preparando o Ditado Local em $installRoot"
$pythonInfo = Find-CompatiblePython
New-Item -ItemType Directory -Path $installRoot -Force | Out-Null

$venvRoot = Join-Path $installRoot ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    & $pythonInfo.Command @($pythonInfo.Prefix) -m venv $venvRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Nao foi possivel criar o ambiente Python local."
    }
}

& $venvPython -m pip install --disable-pip-version-check --require-hashes -r $requirementsPath
if ($LASTEXITCODE -ne 0) {
    throw "Nao foi possivel instalar as dependencias do Ditado Local."
}

$applicationFiles = @(
    "ditado_ai.py",
    "ditado_audio.py",
    "ditado_chat.py",
    "ditado_cloud.py",
    "ditado_local.pyw",
    "ditado_ollama.py",
    "ditado_storage.py",
    "ditado_theme.py",
    "launch_ditado.vbs",
    "launch_ditado_background.vbs",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md"
)
foreach ($fileName in $applicationFiles) {
    $sourcePath = Join-Path $sourceRoot $fileName
    if (-not (Test-Path -LiteralPath $sourcePath)) {
        throw "Arquivo obrigatorio ausente: $fileName"
    }
    Copy-Item -LiteralPath $sourcePath -Destination (Join-Path $installRoot $fileName) -Force
}

$assetsSource = Join-Path $sourceRoot "assets"
$assetsDestination = Join-Path $installRoot "assets"
$fontsDestination = Join-Path $assetsDestination "fonts"
foreach ($assetFile in @(
    "ditado-local.png",
    "ditado-local-icon.png",
    "ditado-local.ico",
    "fonts\DMSans.ttf",
    "fonts\OFL.txt"
)) {
    if (-not (Test-Path -LiteralPath (Join-Path $assetsSource $assetFile))) {
        throw "Arquivo obrigatorio ausente: assets\$assetFile"
    }
}
New-Item -ItemType Directory -Path $fontsDestination -Force | Out-Null
Copy-Item `
    -LiteralPath (Join-Path $assetsSource "ditado-local.png") `
    -Destination (Join-Path $assetsDestination "ditado-local.png") `
    -Force
foreach ($iconFileName in @("ditado-local-icon.png", "ditado-local.ico")) {
    Copy-Item `
        -LiteralPath (Join-Path $assetsSource $iconFileName) `
        -Destination (Join-Path $assetsDestination $iconFileName) `
        -Force
}
foreach ($fontFileName in @("DMSans.ttf", "OFL.txt")) {
    Copy-Item `
        -LiteralPath (Join-Path $assetsSource "fonts\$fontFileName") `
        -Destination (Join-Path $fontsDestination $fontFileName) `
        -Force
}

$wscript = Join-Path $env:WINDIR "System32\wscript.exe"
$startMenuDirectory = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
New-Item -ItemType Directory -Path $startMenuDirectory -Force | Out-Null
$shortcutShell = New-Object -ComObject WScript.Shell
$startMenuShortcut = $shortcutShell.CreateShortcut(
    (Join-Path $startMenuDirectory "Ditado Local.lnk")
)
$startMenuShortcut.TargetPath = $wscript
$startMenuShortcut.Arguments = "`"$(Join-Path $installRoot 'launch_ditado.vbs')`""
$startMenuShortcut.WorkingDirectory = $installRoot
$startMenuShortcut.Description = "Ditado e acoes por voz executados localmente"
$startMenuShortcut.IconLocation = "$(Join-Path $assetsDestination 'ditado-local.ico'),0"
$startMenuShortcut.Save()

$startupDirectory = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
$startupShortcutPath = Join-Path $startupDirectory "Ditado Local.lnk"
if ($StartWithWindows) {
    New-Item -ItemType Directory -Path $startupDirectory -Force | Out-Null
    $startupShortcut = $shortcutShell.CreateShortcut(
        $startupShortcutPath
    )
    $startupShortcut.TargetPath = $wscript
    $startupShortcut.Arguments = "`"$(Join-Path $installRoot 'launch_ditado_background.vbs')`""
    $startupShortcut.WorkingDirectory = $installRoot
    $startupShortcut.Description = "Inicia o Ditado Local com o Windows"
    $startupShortcut.IconLocation = "$(Join-Path $assetsDestination 'ditado-local.ico'),0"
    $startupShortcut.Save()
}
elseif (Test-Path -LiteralPath $startupShortcutPath -PathType Leaf) {
    Remove-Item -LiteralPath $startupShortcutPath -Force
}

Write-Host ""
Write-Host "Instalacao concluida."
Write-Host "Atalho de ditado: Ctrl + Espaco"
Write-Host "Atalho do agente: Ctrl esquerdo + Alt esquerdo"

if (-not $SkipLaunch) {
    & $wscript (Join-Path $installRoot "launch_ditado.vbs")
}
