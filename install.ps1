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
Step "Checking Python >= 3.10"

# Find a real Python (not the Microsoft Store stub which only triggers a Store dialog).
function Find-RealPython {
    foreach ($candidate in @('py', 'python3', 'python')) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        try {
            $out = & $cmd.Source --version 2>&1
            if ($out -match 'Python (\d+)\.(\d+)') {
                $major = [int]$Matches[1]; $minor = [int]$Matches[2]
                if ($major -ge 3 -and $minor -ge 10) { return @{ cmd = $cmd; version = $out } }
            }
        } catch {}
    }
    return $null
}

function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user    = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = ($machine, $user -join ';')
}

$pyInfo = Find-RealPython

if (-not $pyInfo) {
    Write-Host ""
    Warn "Python 3.10+ no encontrado en este sistema."
    $hasWinget = (Get-Command winget -ErrorAction SilentlyContinue) -ne $null
    if ($hasWinget) {
        Write-Host "  Puedo instalarlo automáticamente con winget (oficial de Microsoft)." -ForegroundColor White
        $answer = Read-Host "  Instalar Python 3.12 ahora? [Y/n]"
        if ($answer -eq '' -or $answer -match '^[yYsS]') {
            Step "Instalando Python 3.12 via winget (~1-2 min)"
            $process = Start-Process -FilePath 'winget' -ArgumentList @(
                'install', '--id', 'Python.Python.3.12',
                '--silent', '--accept-source-agreements', '--accept-package-agreements'
            ) -NoNewWindow -Wait -PassThru
            if ($process.ExitCode -ne 0) {
                Bail "winget install falló (exit $($process.ExitCode)). Instalá manual desde https://www.python.org/downloads/ y volvé a correr este installer."
            }
            Ok "Python instalado"
            Refresh-Path
            Start-Sleep -Seconds 2
            $pyInfo = Find-RealPython
            if (-not $pyInfo) {
                Bail "Python se instaló pero el shim no aparece en PATH. Cerrá y reabrí PowerShell, después corré: iwr -useb https://loud.codes/install.ps1 | iex"
            }
        } else {
            Bail "OK, instalá Python manualmente y volvé a correr este installer."
        }
    } else {
        Write-Host ""
        Write-Host "  Necesitás Python 3.10+. Dos opciones:" -ForegroundColor White
        Write-Host "    1) Descargá el instalador oficial:" -ForegroundColor White
        Write-Host "       https://www.python.org/downloads/" -ForegroundColor Cyan
        Write-Host "       (marcá 'Add Python to PATH' al instalar)" -ForegroundColor DarkGray
        Write-Host "    2) Instalá winget desde Microsoft Store y volvé a correr este script" -ForegroundColor White
        Bail "Python no disponible y winget tampoco — instalá manual y reintentá."
    }
}

Ok "$($pyInfo.version)"

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
& $pyInfo.cmd.Source -m venv $Venv
Ok "venv ready"

Step "Installing dependencies"
$VenvPython = Join-Path $Venv "Scripts\python.exe"
& $VenvPython -m pip install --quiet --upgrade pip httpx
Ok "httpx (core) installed"

# Full GUI/voice/browser bundle by default — same idea as install.sh.
if (-not $env:LOUD_SKIP_GUI) {
  Step "Installing GUI / browser / voice bundle"
  & $VenvPython -m pip install --quiet playwright sounddevice numpy pyautogui pillow mss
  Ok "playwright + voice + GUI deps installed"

  Step "Installing Scrapling (scrape / scrape_stealth / scrape_dynamic)"
  & $VenvPython -m pip install --quiet "scrapling[fetchers]"
  Ok "scrapling[fetchers] installed"

  Step "Downloading Chromium for playwright (~400 MB)"
  try {
    & $VenvPython -m playwright install chromium
    Ok "chromium ready"
  } catch {
    Write-Host "  warn: chromium install hit an error — run 'loud setup gui' later to retry"
  }
}

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
