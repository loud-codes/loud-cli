#!/usr/bin/env bash
# LOUD installer — curl -fsSL https://loud.codes/install.sh | bash
# Works on macOS, Linux. For Windows: use install.ps1

set -e

# ───────────────────────── style ─────────────────────────
ESC=$'\033'
BOLD="${ESC}[1m"
DIM="${ESC}[2m"
RED="${ESC}[31m"
GREEN="${ESC}[32m"
YELLOW="${ESC}[33m"
BLUE="${ESC}[34m"
MAGENTA="${ESC}[35m"
CYAN="${ESC}[36m"
ORANGE="${ESC}[38;5;208m"
BRAND="${ESC}[38;5;149m"
GRAY="${ESC}[90m"
RESET="${ESC}[0m"

print_banner() {
  printf "${BRAND}${BOLD}\n"
  cat <<'EOF'
   ██╗      ██████╗ ██╗   ██╗██████╗
   ██║     ██╔═══██╗██║   ██║██╔══██╗
   ██║     ██║   ██║██║   ██║██║  ██║
   ██║     ██║   ██║██║   ██║██║  ██║
   ███████╗╚██████╔╝╚██████╔╝██████╔╝
   ╚══════╝ ╚═════╝  ╚═════╝ ╚═════╝
EOF
  printf "${RESET}${DIM}                    Terminal-first AI ─ loud.codes${RESET}\n\n"
}

# Simple animated spinner
spin() {
  local pid=$1
  local msg=$2
  local frames=(⣷ ⣯ ⣟ ⡿ ⢿ ⣻ ⣽ ⣾)
  local i=0
  while kill -0 "$pid" 2>/dev/null; do
    printf "\r  ${CYAN}${frames[i]}${RESET}  %s" "$msg"
    i=$(( (i + 1) % ${#frames[@]} ))
    sleep 0.08
  done
  printf "\r  ${GREEN}✓${RESET}  %-60s\n" "$msg"
}

step() {
  printf "  ${MAGENTA}▸${RESET}  %s\n" "$1"
}

ok() {
  printf "  ${GREEN}✓${RESET}  %s\n" "$1"
}

fail() {
  printf "  ${RED}✗${RESET}  %s\n" "$1"
  exit 1
}

# ───────────────────────── checks ─────────────────────────

print_banner

step "Detecting system"
OS=$(uname -s)
ARCH=$(uname -m)
case "$OS" in
  Darwin)  PLATFORM="macOS"  ;;
  Linux)   PLATFORM="Linux"  ;;
  *)       fail "unsupported OS: $OS (try install.ps1 for Windows)" ;;
esac
ok "${PLATFORM} (${ARCH})"

step "Checking Python ≥ 3.10"

# Detect distro/package-manager for auto-install offer
detect_pm() {
  if [ "$OS" = "Darwin" ]; then
    command -v brew >/dev/null && echo "brew" && return
    echo "macos-no-brew"; return
  fi
  if command -v apt-get >/dev/null; then echo "apt"; return; fi
  if command -v dnf     >/dev/null; then echo "dnf"; return; fi
  if command -v yum     >/dev/null; then echo "yum"; return; fi
  if command -v pacman  >/dev/null; then echo "pacman"; return; fi
  if command -v zypper  >/dev/null; then echo "zypper"; return; fi
  if command -v apk     >/dev/null; then echo "apk"; return; fi
  echo "unknown"
}

py_install_cmd() {
  case "$1" in
    brew)   echo "brew install python@3.12" ;;
    apt)    echo "sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip" ;;
    dnf)    echo "sudo dnf install -y python3 python3-pip" ;;
    yum)    echo "sudo yum install -y python3 python3-pip" ;;
    pacman) echo "sudo pacman -S --noconfirm python python-pip" ;;
    zypper) echo "sudo zypper install -y python3 python3-pip" ;;
    apk)    echo "sudo apk add python3 py3-pip" ;;
    *) echo "" ;;
  esac
}

offer_python_install() {
  local pm="$1"
  local cmd
  cmd=$(py_install_cmd "$pm")
  if [ -z "$cmd" ]; then
    printf "\n  ${YELLOW}!${RESET}  No detecté package manager. Instalá Python 3.10+ manualmente:\n"
    printf "        ${CYAN}https://www.python.org/downloads/${RESET}\n\n"
    fail "Python no disponible y no puedo auto-instalarlo."
  fi
  printf "\n  ${YELLOW}!${RESET}  Python 3.10+ no encontrado. Puedo instalarlo por vos.\n"
  printf "        Comando que voy a correr: ${CYAN}%s${RESET}\n" "$cmd"
  printf "        Instalar Python 3.12 ahora? [Y/n] "
  read -r ans </dev/tty || ans="y"
  case "$ans" in
    [nN]*) fail "OK, instalá Python manualmente y volvé a correr este installer." ;;
    *) ;;
  esac
  step "Instalando Python via ${pm}"
  eval "$cmd"
  if [ $? -ne 0 ]; then
    fail "instalación de Python falló — revisá el output arriba y reintentá."
  fi
  ok "Python instalado"
}

find_python() {
  for candidate in python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      local v
      v=$("$candidate" --version 2>&1 | awk '{print $2}')
      local maj min
      maj=$(echo "$v" | cut -d. -f1)
      min=$(echo "$v" | cut -d. -f2)
      if [ "$maj" -ge 3 ] 2>/dev/null && [ "$min" -ge 10 ] 2>/dev/null; then
        PY_BIN="$candidate"
        PY_VERSION="$v"
        return 0
      fi
    fi
  done
  return 1
}

if ! find_python; then
  PM=$(detect_pm)
  if [ "$PM" = "macos-no-brew" ]; then
    printf "\n  ${YELLOW}!${RESET}  No tenés Homebrew. Instalalo primero:\n"
    printf "        ${CYAN}/bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"${RESET}\n"
    printf "        Después corré este installer de nuevo.\n\n"
    fail "Homebrew no instalado en macOS."
  fi
  offer_python_install "$PM"
  if ! find_python; then
    fail "Python instalado pero no se encuentra en PATH — abrí una nueva terminal y reintentá."
  fi
fi
ok "python ${PY_VERSION}"

# ───────────────────────── install dir ─────────────────────────

LOUD_HOME="${LOUD_HOME:-$HOME/.loud}"
INSTALL_DIR="$LOUD_HOME/install"
BIN_DIR="${LOUD_BIN_DIR:-$HOME/.local/bin}"

step "Install location: ${INSTALL_DIR}"
mkdir -p "$INSTALL_DIR" "$BIN_DIR"

# ───────────────────────── download ─────────────────────────

step "Downloading latest LOUD"
DOWNLOAD_URL="${LOUD_DOWNLOAD_URL:-https://github.com/loud-codes/loud-cli/archive/refs/heads/main.tar.gz}"
( curl -fsSL "$DOWNLOAD_URL" -o /tmp/loud.tgz ) &
spin $! "fetching $DOWNLOAD_URL"

rm -rf "$INSTALL_DIR/src"
mkdir -p "$INSTALL_DIR/src"
tar -xzf /tmp/loud.tgz -C "$INSTALL_DIR/src" --strip-components=1 2>/dev/null || fail "extraction failed"
rm /tmp/loud.tgz
ok "source unpacked"

# ───────────────────────── venv + deps ─────────────────────────

step "Creating isolated Python env"
( "$PY_BIN" -m venv "$INSTALL_DIR/venv" ) &
spin $! "creating venv at $INSTALL_DIR/venv"

step "Installing dependencies"
( "$INSTALL_DIR/venv/bin/pip" install --quiet -U pip httpx ) &
spin $! "pip install httpx (core)"

# Full bundle by default — browser + voice + screen control are part of LOUD.
# Set LOUD_SKIP_GUI=1 to opt out (faster install, no GUI tools).
if [ -z "$LOUD_SKIP_GUI" ]; then
  step "Installing GUI / browser / voice bundle (loud setup is in the install)"
  (
    "$INSTALL_DIR/venv/bin/pip" install --quiet \
      playwright sounddevice numpy pyautogui pillow mss
  ) &
  spin $! "pip install playwright + voice + GUI deps"

  step "Installing Scrapling (scrape / scrape_stealth / scrape_dynamic)"
  (
    "$INSTALL_DIR/venv/bin/pip" install --quiet "scrapling[fetchers]"
  ) &
  spin $! "pip install scrapling[fetchers]"

  step "Downloading Chromium for playwright (~400 MB · 1-2 min)"
  (
    "$INSTALL_DIR/venv/bin/python3" -m playwright install chromium 2>/dev/null || true
  ) &
  spin $! "playwright install chromium"
fi

# ───────────────────────── shim ─────────────────────────

step "Installing 'loud' command"
SHIM="$BIN_DIR/loud"
cat >"$SHIM" <<EOF
#!/usr/bin/env bash
exec "$INSTALL_DIR/venv/bin/python3" "$INSTALL_DIR/src/cli/loud.py" "\$@"
EOF
chmod +x "$SHIM"
ok "linked $SHIM"

# ───────────────────────── PATH check ─────────────────────────

if ! echo ":$PATH:" | grep -q ":$BIN_DIR:"; then
  printf "\n  ${YELLOW}!${RESET}  ${BIN_DIR} is not in your \$PATH yet.\n"
  printf "     Add this to your shell profile (~/.zshrc or ~/.bashrc):\n\n"
  printf "       ${BOLD}export PATH=\"\$HOME/.local/bin:\$PATH\"${RESET}\n\n"
fi

# ───────────────────────── done ─────────────────────────

printf "\n${GREEN}${BOLD}  ✓ LOUD installed!${RESET}\n\n"
printf "  Get started:\n"
printf "     ${CYAN}loud login${RESET}                ${GRAY}# email + password${RESET}\n"
printf "     ${CYAN}loud \"hello\"${RESET}              ${GRAY}# one-shot${RESET}\n"
printf "     ${CYAN}loud${RESET}                      ${GRAY}# interactive REPL${RESET}\n"
printf "\n  Docs: ${BLUE}https://loud.codes${RESET}\n\n"
