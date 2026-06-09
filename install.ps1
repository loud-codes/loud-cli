# LOUD installer for Windows (PowerShell).
# Run: iwr -useb https://loud.codes/install.ps1 | iex
#   or save & run:  PowerShell -ExecutionPolicy Bypass -File install.ps1
#
# Env overrides (optional):
#   LOUD_ASSUME_YES=1   answer "yes" to every prompt (non-interactive installs)
#   LOUD_WITH_TOOLKIT=1 install the optional toolkit without asking
#   LOUD_SKIP_TOOLKIT=1 never install the optional toolkit

$ErrorActionPreference = "Stop"

# ───────────────────────── TLS 1.2 ─────────────────────────
# Windows PowerShell 5.1 (the default on Windows 10 / Server) negotiates TLS 1.0,
# and python.org / github REJECT that -> every HTTPS download fails with
# "Could not create SSL/TLS secure channel". Force TLS 1.2/1.3.
try {
    [Net.ServicePointManager]::SecurityProtocol = `
        [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls11 -bor [Net.SecurityProtocolType]::Tls
    try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls13 } catch {}
} catch {}

# ───────────────────────── encoding ─────────────────────────
# The logo is 100% ASCII (no block chars) so it never shows as `?????`, not even
# when piped through `iwr | iex`. We still force UTF-8 so any non-ASCII output
# from Python / pip renders correctly.
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

# Yes/No prompt. Returns $true / $false. Honors LOUD_ASSUME_YES and falls back to
# the default when running non-interactively (no console to read from).
function Ask-YesNo($question, $defaultYes) {
    if ($env:LOUD_ASSUME_YES) { return $true }
    $suffix = if ($defaultYes) { "[Y/n]" } else { "[y/N]" }
    try {
        $ans = Read-Host "  $question $suffix"
    } catch {
        return $defaultYes
    }
    if ([string]::IsNullOrWhiteSpace($ans)) { return $defaultYes }
    return ($ans.Trim() -match '^(y|yes)$')
}

Write-Host ""
Brand "   _      ___    _   _   ____  "
Brand "  | |    / _ \  | | | | |  _ \ "
Brand "  | |   | | | | | | | | | | | |"
Brand "  | |__ | |_| | | |_| | | |_| |"
Brand "  |____| \___/   \___/  |____/ "
Write-Host "  Terminal-first AI - loud.codes" -ForegroundColor DarkGray
Write-Host ""

# ───────────────────────── checks ─────────────────────────
Step "Checking for Python >= 3.10"

# Find a real Python (not the Microsoft Store stub, which only opens a Store dialog).
# Prefer specific stable versions (3.12 -> 3.11 -> 3.10) via the `py` launcher
# before falling back to whatever python is on PATH.
function Find-RealPython {
    # 1) `py -3.X` for specific stable minors
    foreach ($ver in @('3.12','3.11','3.13','3.10')) {
        $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
        if ($pyLauncher) {
            try {
                # 2>&1 + ErrorActionPreference=Stop would make a `py` WITHOUT a runtime
                # ("No suitable Python runtime found") throw and kill the whole script.
                # Wrapped in try/catch so it just keeps looking.
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
    # 2) Generic launchers on PATH
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

# Installs Python 3.12. Tries winget first; if winget is missing (common on
# Windows Server) it downloads the official python.org installer and runs it
# silently. Returns the new $pyInfo (or $null if something went wrong).
function Install-Python312 {
    # --- Path 1: winget (Win10 1709+ / Win11) ---
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Step "Installing Python 3.12 via winget (~1-2 min)"
        try {
            Start-Process -FilePath 'winget' -ArgumentList @(
                'install', '--id', 'Python.Python.3.12',
                '--silent', '--accept-source-agreements', '--accept-package-agreements',
                '--scope', 'user'
            ) -NoNewWindow -Wait -PassThru | Out-Null
        } catch { Warn "winget failed, falling back to direct download..." }
        Refresh-Path
        Start-Sleep -Seconds 2
        $p = Find-RealPython
        if ($p) { return $p }
    }

    # --- Path 2: direct download of the official installer (no winget) ---
    $pyVer = '3.12.8'
    $arch  = if ([Environment]::Is64BitOperatingSystem) { '-amd64' } else { '' }
    $pyUrl = "https://www.python.org/ftp/python/$pyVer/python-$pyVer$arch.exe"
    $pyExe = Join-Path $env:TEMP "loud-python-$pyVer.exe"
    Step "Downloading official Python $pyVer from python.org (~28 MB)"
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $pyUrl -OutFile $pyExe
    } catch {
        Bail "Could not download Python from $pyUrl . Check your connection and re-run: iwr -useb https://loud.codes/install.ps1 | iex"
    }
    Step "Installing Python $pyVer (silent, adds it to PATH)"
    # /quiet = no UI ; PrependPath=1 = add to PATH ; InstallAllUsers=0 = no admin needed
    $proc = Start-Process -FilePath $pyExe -ArgumentList @(
        '/quiet', 'InstallAllUsers=0', 'PrependPath=1',
        'Include_pip=1', 'Include_launcher=1', 'Include_test=0'
    ) -Wait -PassThru
    Remove-Item $pyExe -ErrorAction SilentlyContinue
    # 0 = ok ; 3010 = ok but wants a reboot. Anything else = real failure.
    if ($proc.ExitCode -ne 0 -and $proc.ExitCode -ne 3010) {
        Bail "Python installer returned exit $($proc.ExitCode). Install it manually from https://www.python.org/downloads/ (check 'Add Python to PATH') and re-run."
    }
    Refresh-Path
    Start-Sleep -Seconds 2
    return Find-RealPython
}

$pyInfo = Find-RealPython

if (-not $pyInfo) {
    Write-Host ""
    Warn "Python 3.10+ was not found on this system."
    Write-Host "      LOUD needs Python to run. I can install Python 3.12 for you." -ForegroundColor Gray
    if (Ask-YesNo "Install Python 3.12 now and continue?" $true) {
        $pyInfo = Install-Python312
        if (-not $pyInfo) {
            Bail "Python was installed but is not on PATH yet. Close and reopen PowerShell, then re-run: iwr -useb https://loud.codes/install.ps1 | iex"
        }
        Ok "Python installed"
    } else {
        Write-Host ""
        Write-Host "  No problem. Install Python 3.10+ yourself, then re-run LOUD's installer:" -ForegroundColor Gray
        Write-Host "    1) Download:  https://www.python.org/downloads/" -ForegroundColor Cyan
        Write-Host "       (tick 'Add Python to PATH' during setup)" -ForegroundColor DarkGray
        Write-Host "    2) Re-run:    iwr -useb https://loud.codes/install.ps1 | iex" -ForegroundColor Cyan
        Write-Host ""
        exit 0
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
Step "Downloading LOUD"
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
    Warn "venv creation failed with your current Python ($($pyInfo.version)). Error:"
    Write-Host "    $errTxt" -ForegroundColor DarkGray
    # If Python is too new (3.14+) or we saw the 'platform independent libraries'
    # error, offer to install stable Python 3.12.
    $is_python_broken = ($pyInfo.version -match 'Python 3\.(1[4-9]|[2-9]\d)' -or $errTxt -match 'platform independent libraries')
    if ($is_python_broken) {
        Warn "Your Python is broken or too new for some packages."
        if (Ask-YesNo "Install stable Python 3.12 and retry?" $true) {
            $newPy = Install-Python312
            if (-not $newPy) {
                Bail "Python 3.12 was installed but is not on PATH yet. Close PowerShell, reopen it, then re-run: iwr -useb https://loud.codes/install.ps1 | iex"
            }
            $pyInfo = $newPy
            Step "Retrying venv with $($pyInfo.version)"
            $venvExit = Try-CreateVenv $pyInfo.cmd.Source $pyInfo.argPrefix
        }
    }
    if ($venvExit -ne 0) {
        Bail "venv creation still failing. Send me the exact output above and we'll sort it out."
    }
}
Ok "venv ready ($($pyInfo.version))"

Step "Installing core dependency (httpx)"
$VenvPython = Join-Path $Venv "Scripts\python.exe"

# Silence pip's noise: no cache (kills the "Cache entry deserialization failed"
# warnings from a corrupt pip cache) and no version-check chatter.
$env:PIP_NO_CACHE_DIR = "1"
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
$env:PYTHONWARNINGS = "ignore"

# Core: httpx is REQUIRED (LOUD won't start without it). Verify the import + retry once.
& $VenvPython -m pip install --quiet --no-cache-dir --no-warn-script-location httpx
& $VenvPython -c "import httpx" 2>$null
if ($LASTEXITCODE -ne 0) {
    Warn "First httpx attempt failed, retrying..."
    & $VenvPython -m pip install --quiet --no-cache-dir httpx
    & $VenvPython -c "import httpx" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Bail "Could not install 'httpx' (core dependency). Check your connection and re-run: iwr -useb https://loud.codes/install.ps1 | iex"
    }
}
Ok "core ready - LOUD can run now"

# NOTE: optional components (browser automation, web scraping, voice) are NOT
# installed here on purpose. The installer only sets up the vital parts: Python
# + LOUD core. LOUD installs extras on demand - when a command actually needs a
# component, it asks you to confirm before installing just that one.

# ───────────────────────── shim ─────────────────────────
Step "Installing the 'loud' command"
$ShimCmd = Join-Path $BinDir "loud.cmd"
$entry = (Join-Path $Src "cli\loud.py")
$python = Join-Path $Venv "Scripts\python.exe"
Set-Content -Path $ShimCmd -Encoding ASCII -Value @"
@echo off
"$python" "$entry" %*
"@
Ok "linked $ShimCmd"

# Add ~/.local/bin to the user PATH automatically.
if (-not (($env:PATH -split ";") -contains $BinDir)) {
    try {
        $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        if (-not (($userPath -split ";") -contains $BinDir)) {
            [Environment]::SetEnvironmentVariable('Path', "$userPath;$BinDir", 'User')
        }
        $env:Path = "$env:Path;$BinDir"
        Ok "added $BinDir to your user PATH"
    } catch {
        Warn "$BinDir is not on your PATH. Add it manually with:"
        Write-Host "       [Environment]::SetEnvironmentVariable('Path', `$env:Path + ';$BinDir', 'User')" -ForegroundColor White
    }
}

Write-Host ""
Write-Host "  [OK] LOUD installed!" -ForegroundColor Green
Write-Host ""
Write-Host "  ===================================================================" -ForegroundColor Yellow
Write-Host "   IMPORTANT: CLOSE THIS TERMINAL AND OPEN A NEW ONE before using LOUD." -ForegroundColor Yellow
Write-Host "   (so it picks up the updated PATH / freshly installed Python)" -ForegroundColor Yellow
Write-Host "  ===================================================================" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Then, in the new terminal, run:" -ForegroundColor Gray
Write-Host "     loud login              " -NoNewline -ForegroundColor Green; Write-Host "# username + password" -ForegroundColor DarkGray
Write-Host "     loud `"hello`"            " -NoNewline -ForegroundColor Green; Write-Host "# one-shot" -ForegroundColor DarkGray
Write-Host "     loud                    " -NoNewline -ForegroundColor Green; Write-Host "# interactive REPL" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Docs: https://loud.codes" -ForegroundColor Blue
Write-Host ""
