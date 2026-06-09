# LOUD installer for Windows (PowerShell).
# Run: iwr -useb https://loud.codes/install.ps1 | iex
#   or save & run:  PowerShell -ExecutionPolicy Bypass -File install.ps1

$ErrorActionPreference = "Stop"

# ───────────────────────── TLS 1.2 ─────────────────────────
# Windows PowerShell 5.1 (lo que trae Windows 10/Server por defecto) negocia
# TLS 1.0, y python.org / github RECHAZAN eso -> todas las descargas HTTPS
# fallan con "Could not create SSL/TLS secure channel". Forzamos TLS 1.2/1.3.
try {
    [Net.ServicePointManager]::SecurityProtocol = `
        [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls11 -bor [Net.SecurityProtocolType]::Tls
    try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls13 } catch {}
} catch {}

# ───────────────────────── encoding fix ─────────────────────────
# El logo es 100% ASCII (no usa block chars ██╗) asi que NUNCA sale `?????`,
# ni siquiera bajando el script con `iwr | iex`. Igual forzamos UTF-8 en la
# consola para que la salida de Python / pip con acentos se vea bien.
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
    [Console]::InputEncoding  = [System.Text.UTF8Encoding]::new()
    $OutputEncoding           = [System.Text.UTF8Encoding]::new()
    chcp 65001 *> $null
} catch {}

# ───────────────────────── style ─────────────────────────
function Brand($text) { Write-Host $text -ForegroundColor Green }
function Step($text)  { Write-Host "  > $text" -ForegroundColor Magenta }
function Ok($text)    { Write-Host "  [OK] $text" -ForegroundColor Green }
function Warn($text)  { Write-Host "  [!] $text" -ForegroundColor Yellow }
function Bail($text)  { Write-Host "  [X] $text" -ForegroundColor Red; exit 1 }

Write-Host ""
Brand "   _      ___    _   _   ____  "
Brand "  | |    / _ \  | | | | |  _ \ "
Brand "  | |   | | | | | | | | | | | |"
Brand "  | |__ | |_| | | |_| | | |_| |"
Brand "  |____| \___/   \___/  |____/ "
Write-Host "  Terminal-first AI - loud.codes" -ForegroundColor DarkGray
Write-Host ""

# ───────────────────────── checks ─────────────────────────
Step "Checking Python >= 3.10"

# Find a real Python (not the Microsoft Store stub which only triggers a Store dialog).
# Prefer specific stable versions (3.12 → 3.11 → 3.10) via the `py` launcher
# before falling back to whichever python is on PATH. Skips 3.14+ which is
# bleeding-edge and many packages still don't support it.
function Find-RealPython {
    # 1) `py -3.X` for specific stable minors
    foreach ($ver in @('3.12','3.11','3.13','3.10')) {
        $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
        if ($pyLauncher) {
            try {
                # 2>&1 + ErrorActionPreference=Stop haría que un `py` SIN runtime
                # ("No suitable Python runtime found") lance excepción y mate el
                # script. Lo envolvemos para que simplemente siga buscando.
                $out = (& $pyLauncher.Source "-$ver" --version 2>&1 | Out-String)
                if ($out -match 'Python (\d+)\.(\d+)') {
                    $major = [int]$Matches[1]; $minor = [int]$Matches[2]
                    if ($major -ge 3 -and $minor -ge 10) {
                        return @{ cmd = $pyLauncher; version = $out.Trim(); argPrefix = @("-$ver") }
                    }
                }
            } catch {}
        }
    }
    # 2) Generic launchers
    foreach ($candidate in @('python3', 'python', 'py')) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        try {
            $out = & $cmd.Source --version 2>&1
            if ($out -match 'Python (\d+)\.(\d+)') {
                $major = [int]$Matches[1]; $minor = [int]$Matches[2]
                if ($major -ge 3 -and $minor -ge 10 -and $minor -le 13) {
                    return @{ cmd = $cmd; version = $out; argPrefix = @() }
                }
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

# Instala Python 3.12 AUTOMATICAMENTE (sin preguntar). Intenta winget; si no hay
# winget (tipico en Windows Server) baja el instalador oficial de python.org y lo
# corre en silencio. Devuelve el $pyInfo nuevo (o $null si algo salio muy mal).
function Install-Python312 {
    # --- Via 1: winget (Win10 1709+ / Win11) ---
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Step "Instalando Python 3.12 via winget (~1-2 min)"
        try {
            Start-Process -FilePath 'winget' -ArgumentList @(
                'install', '--id', 'Python.Python.3.12',
                '--silent', '--accept-source-agreements', '--accept-package-agreements',
                '--scope', 'user'
            ) -NoNewWindow -Wait -PassThru | Out-Null
        } catch { Warn "winget fallo, paso a descarga directa..." }
        Refresh-Path
        Start-Sleep -Seconds 2
        $p = Find-RealPython
        if ($p) { return $p }
    }

    # --- Via 2: descarga directa del instalador oficial (sin winget) ---
    $pyVer = '3.12.8'
    $arch  = if ([Environment]::Is64BitOperatingSystem) { '-amd64' } else { '' }
    $pyUrl = "https://www.python.org/ftp/python/$pyVer/python-$pyVer$arch.exe"
    $pyExe = Join-Path $env:TEMP "loud-python-$pyVer.exe"
    Step "Descargando Python $pyVer oficial de python.org (~28 MB)"
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $pyUrl -OutFile $pyExe
    } catch {
        Bail "No se pudo descargar Python desde $pyUrl . Revisa tu conexion y reintenta: iwr -useb https://loud.codes/install.ps1 | iex"
    }
    Step "Instalando Python $pyVer (silencioso, lo agrega al PATH)"
    # /quiet = sin UI ; PrependPath=1 = lo agrega al PATH ; InstallAllUsers=0 = sin admin
    $proc = Start-Process -FilePath $pyExe -ArgumentList @(
        '/quiet', 'InstallAllUsers=0', 'PrependPath=1',
        'Include_pip=1', 'Include_launcher=1', 'Include_test=0'
    ) -Wait -PassThru
    Remove-Item $pyExe -ErrorAction SilentlyContinue
    # 0 = ok ; 3010 = ok pero pide reiniciar. Cualquier otro = fallo real.
    if ($proc.ExitCode -ne 0 -and $proc.ExitCode -ne 3010) {
        Bail "El instalador de Python devolvio exit $($proc.ExitCode). Instala manual desde https://www.python.org/downloads/ (marca 'Add Python to PATH') y reintenta."
    }
    Refresh-Path
    Start-Sleep -Seconds 2
    return Find-RealPython
}

$pyInfo = Find-RealPython

if (-not $pyInfo) {
    Write-Host ""
    Warn "Python 3.10+ no encontrado - lo instalo automaticamente (sin preguntar)."
    $pyInfo = Install-Python312
    if (-not $pyInfo) {
        Bail "Python se instalo pero todavia no aparece en PATH. Cerra y reabri PowerShell, despues corre de nuevo: iwr -useb https://loud.codes/install.ps1 | iex"
    }
    Ok "Python instalado correctamente"
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

function Try-CreateVenv($pyCmd, $argPrefix) {
    $venvArgs = @()
    if ($argPrefix) { $venvArgs += $argPrefix }
    $venvArgs += @('-m', 'venv', $Venv)
    Remove-Item -Recurse -Force $Venv -ErrorAction SilentlyContinue
    $proc = Start-Process -FilePath $pyCmd -ArgumentList $venvArgs -NoNewWindow -Wait -PassThru -RedirectStandardError "$env:TEMP\loud-venv-err.txt"
    return $proc.ExitCode
}

Step "Creating isolated Python env"
$venvExit = Try-CreateVenv $pyInfo.cmd.Source $pyInfo.argPrefix
if ($venvExit -ne 0) {
    $errTxt = ""
    if (Test-Path "$env:TEMP\loud-venv-err.txt") { $errTxt = Get-Content "$env:TEMP\loud-venv-err.txt" -Raw }
    Warn "venv fallo con tu Python actual ($($pyInfo.version)). Error:"
    Write-Host "    $errTxt" -ForegroundColor DarkGray
    # Si el Python es demasiado nuevo (3.14+) o vimos el error 'platform independent
    # libraries', instalamos 3.12 estable AUTOMATICAMENTE (winget o descarga directa).
    $is_python_broken = ($pyInfo.version -match 'Python 3\.(1[4-9]|[2-9]\d)' -or $errTxt -match 'platform independent libraries')
    if ($is_python_broken) {
        Warn "Tu Python esta roto o es demasiado nuevo. Instalo Python 3.12 (estable) ahora..."
        $newPy = Install-Python312
        if (-not $newPy) {
            Bail "Python 3.12 instalado pero no se encuentra. Cerra PowerShell y reabri, despues corre: iwr -useb https://loud.codes/install.ps1 | iex"
        }
        $pyInfo = $newPy
        Step "Reintentando venv con $($pyInfo.version)"
        $venvExit = Try-CreateVenv $pyInfo.cmd.Source $pyInfo.argPrefix
    }
    if ($venvExit -ne 0) {
        Bail "venv sigue fallando. Mostrame el output exacto y vemos."
    }
}
Ok "venv ready ($($pyInfo.version))"

Step "Installing dependencies"
$VenvPython = Join-Path $Venv "Scripts\python.exe"

# Core: httpx es OBLIGATORIO (sin esto loud no arranca). Reintenta una vez y verifica.
& $VenvPython -m pip install --upgrade pip 2>$null
& $VenvPython -m pip install --quiet httpx
& $VenvPython -c "import httpx" 2>$null
if ($LASTEXITCODE -ne 0) {
    Warn "Primer intento de httpx fallo, reintentando sin cache..."
    & $VenvPython -m pip install --no-cache-dir httpx
    & $VenvPython -c "import httpx" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Bail "No se pudo instalar 'httpx' (dependencia core). Revisa tu conexion y reintenta: iwr -useb https://loud.codes/install.ps1 | iex"
    }
}
Ok "httpx (core) installed"

# Bundle completo GUI/voz/browser. NO es critico: si algo falla, loud igual corre,
# asi que cada pieza va en su propio try/catch y NUNCA aborta la instalacion.
if (-not $env:LOUD_SKIP_GUI) {
  Step "Installing GUI / browser / voice bundle"
  try {
    & $VenvPython -m pip install --quiet playwright sounddevice numpy pyautogui pillow mss
    Ok "playwright + voice + GUI deps installed"
  } catch { Warn "bundle GUI/voz fallo parcialmente - podes reintentar luego con 'loud setup gui'" }

  Step "Installing Scrapling (scrape / scrape_stealth / scrape_dynamic)"
  try {
    & $VenvPython -m pip install --quiet "scrapling[fetchers]"
    Ok "scrapling[fetchers] installed"
  } catch { Warn "scrapling fallo - reintenta luego con 'loud setup gui'" }

  Step "Downloading Chromium for playwright (~400 MB)"
  try {
    & $VenvPython -m playwright install chromium
    Ok "chromium ready"
  } catch {
    Write-Host "  warn: chromium install hit an error - run 'loud setup gui' later to retry"
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

# PATH hint — lo agregamos automaticamente al PATH de usuario (sin pedir nada)
if (-not (($env:PATH -split ";") -contains $BinDir)) {
    try {
        $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        if (-not (($userPath -split ";") -contains $BinDir)) {
            [Environment]::SetEnvironmentVariable('Path', "$userPath;$BinDir", 'User')
        }
        $env:Path = "$env:Path;$BinDir"
        Ok "Agregue $BinDir a tu PATH de usuario"
    } catch {
        Warn "$BinDir no esta en tu PATH. Agregalo manualmente con:"
        Write-Host "       [Environment]::SetEnvironmentVariable('Path', `$env:Path + ';$BinDir', 'User')" -ForegroundColor White
    }
}

Write-Host ""
Write-Host "  [OK] LOUD instalado!" -ForegroundColor Green
Write-Host ""
Write-Host "  ===================================================================" -ForegroundColor Yellow
Write-Host "   IMPORTANTE: CERRA ESTA TERMINAL Y ABRI UNA NUEVA antes de usar LOUD." -ForegroundColor Yellow
Write-Host "   (es para que tome el PATH y el Python recien instalado)" -ForegroundColor Yellow
Write-Host "  ===================================================================" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Despues, en la terminal nueva escribi:" -ForegroundColor Gray
Write-Host "     loud login              " -NoNewline -ForegroundColor Green; Write-Host "# usuario + contrasena" -ForegroundColor DarkGray
Write-Host "     loud `"hola`"             " -NoNewline -ForegroundColor Green; Write-Host "# one-shot" -ForegroundColor DarkGray
Write-Host "     loud                    " -NoNewline -ForegroundColor Green; Write-Host "# REPL interactivo" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Docs: https://loud.codes" -ForegroundColor Blue
Write-Host ""
