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
if ! command -v python3 >/dev/null; then
  fail "python3 not found. Install Python first: brew install python (macOS) or apt-get install python3 (Linux)"
fi
PY_VERSION=$(python3 --version | awk '{print $2}')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
  fail "python ${PY_VERSION} too old, need 3.10+"
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
( python3 -m venv "$INSTALL_DIR/venv" ) &
spin $! "creating venv at $INSTALL_DIR/venv"

step "Installing dependencies"
( "$INSTALL_DIR/venv/bin/pip" install --quiet -U pip httpx ) &
spin $! "pip install httpx"

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
