# LOUD installer for Windows (PowerShell).
# Run: iwr -useb https://loud.codes/install.ps1 | iex
#   or save & run:  PowerShell -ExecutionPolicy Bypass -File install.ps1

$ErrorActionPreference = "Stop"

# ───────────────────────── style ─────────────────────────
function Brand($text) { Write-Host $text -ForegroundColor Green }
function Step($text)  { Write-Host "  ▸ $text" -ForegroundColor Magenta }
function Ok($text)    { Write-Host "  ✓ $text" -ForegroundColor Green }
function Warn($text)  { Write-Host "  ! $text" -ForegroundColor Yellow }
function Bail($text)  { Write-Host "  ✗ $text" -ForegroundColor Red; exit 1 }

Write-Host ""
Brand "  ██╗      ██████╗ ██╗   ██╗██████╗"
Brand "  ██║     ██╔═══██╗██║   ██║██╔══██╗"
Brand "  ██║     ██║   ██║██║   ██║██║  ██║"
Brand "  ██║     ██║   ██║██║   ██║██║  ██║"
Brand "  ███████╗╚██████╔╝╚██████╔╝██████╔╝"
Brand "  ╚══════╝ ╚═════╝  ╚═════╝ ╚═════╝"
Write-Host "                  Terminal-first AI ─ loud.codes" -ForegroundColor DarkGray
Write-Host ""

# ───────────────────────── checks ─────────────────────────
Step "Checking Python ≥ 3.10"
try {
    $py = (Get-Command python3 -ErrorAction SilentlyContinue) ?? (Get-Command python -ErrorAction SilentlyContinue)
    if (-not $py) { Bail "Python no encontrado. Instala desde python.org (3.10+)" }
    $version = & $py.Source --version 2>&1
    if ($version -notmatch "Python (\d+)\.(\d+)") { Bail "no se pudo detectar versión de Python" }
    $major = [int]$Matches[1]; $minor = [int]$Matches[2]
    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
        Bail "Python $version es muy viejo, necesitas 3.10+"
    }
    Ok "$version"
} catch { Bail $_.Exception.Message }

$LoudHome = if ($env:LOUD_HOME) { $env:LOUD_HOME } else { Join-Path $env:USERPROFILE ".loud" }
$InstallDir = Join-Path $LoudHome "install"
$BinDir = Join-Path $env:USERPROFILE ".local\bin"
New-Item -ItemType Directory -Force -Path $InstallDir, $BinDir | Out-Null
Step "Install path: $InstallDir"

# ───────────────────────── download ─────────────────────────
$DownloadUrl = if ($env:LOUD_DOWNLOAD_URL) { $env:LOUD_DOWNLOAD_URL } else { "https://github.com/loud-codes/loud-cli/archive/refs/heads/main.zip" }
$Zip = Join-Path $env:TEMP "loud.zip"
Step "Downloading $DownloadUrl"
Invoke-WebRequest -UseBasicParsing -Uri $DownloadUrl -OutFile $Zip
Ok "downloaded"

$Src = Join-Path $InstallDir "src"
if (Test-Path $Src) { Remove-Item -Recurse -Force $Src }
New-Item -ItemType Directory -Force -Path $Src | Out-Null
Step "Extracting"
$Tmp = Join-Path $env:TEMP "loud-unzip"
if (Test-Path $Tmp) { Remove-Item -Recurse -Force $Tmp }
Expand-Archive -Path $Zip -DestinationPath $Tmp -Force
$inner = Get-ChildItem $Tmp | Select-Object -First 1
Copy-Item -Recurse -Force (Join-Path $inner.FullName "*") $Src
Remove-Item -Recurse -Force $Tmp
Remove-Item $Zip
Ok "unpacked"

# ───────────────────────── venv + deps ─────────────────────────
$Venv = Join-Path $InstallDir "venv"
Step "Creating isolated Python env"
& $py.Source -m venv $Venv
Ok "venv ready"

Step "Installing dependencies"
& (Join-Path $Venv "Scripts\python.exe") -m pip install --quiet --upgrade pip httpx
Ok "httpx installed"

# ───────────────────────── shim ─────────────────────────
Step "Installing 'loud' command"
$ShimCmd = Join-Path $BinDir "loud.cmd"
$entry = (Join-Path $Src "cli\loud.py")
$python = Join-Path $Venv "Scripts\python.exe"
Set-Content -Path $ShimCmd -Encoding ASCII -Value @"
@echo off
"$python" "$entry" %*
"@
Ok "linked $ShimCmd"

# PATH hint
if (-not ($env:PATH -split ";" -contains $BinDir)) {
    Write-Host ""
    Warn "$BinDir no está en tu PATH."
    Write-Host "     Agrégalo con:" -ForegroundColor Gray
    Write-Host "       [Environment]::SetEnvironmentVariable('Path', `$env:Path + ';$BinDir', 'User')" -ForegroundColor White
    Write-Host ""
}

Write-Host ""
Write-Host "  ✓ LOUD instalado!" -ForegroundColor Green
Write-Host ""
Write-Host "  Empieza:" -ForegroundColor Gray
Write-Host "     loud login              " -NoNewline -ForegroundColor Green; Write-Host "# usuario + contraseña" -ForegroundColor DarkGray
Write-Host "     loud `"hola`"             " -NoNewline -ForegroundColor Green; Write-Host "# one-shot" -ForegroundColor DarkGray
Write-Host "     loud                    " -NoNewline -ForegroundColor Green; Write-Host "# REPL interactivo" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Docs: https://loud.codes" -ForegroundColor Blue
Write-Host ""
