[CmdletBinding()]
param(
    [switch]$StartWithWindows,
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
    "ditado_local.pyw",
    "ditado_storage.py",
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
$startMenuShortcut.Save()

if ($StartWithWindows) {
    $startupDirectory = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
    New-Item -ItemType Directory -Path $startupDirectory -Force | Out-Null
    $startupShortcut = $shortcutShell.CreateShortcut(
        (Join-Path $startupDirectory "Ditado Local.lnk")
    )
    $startupShortcut.TargetPath = $wscript
    $startupShortcut.Arguments = "`"$(Join-Path $installRoot 'launch_ditado_background.vbs')`""
    $startupShortcut.WorkingDirectory = $installRoot
    $startupShortcut.Description = "Inicia o Ditado Local com o Windows"
    $startupShortcut.Save()
}

Write-Host ""
Write-Host "Instalacao concluida."
Write-Host "Atalho de ditado: Ctrl + Espaco"
Write-Host "Atalho do agente: Ctrl esquerdo + Alt esquerdo"

if (-not $SkipLaunch) {
    & $wscript (Join-Path $installRoot "launch_ditado.vbs")
}
