#!/usr/bin/env python3
"""LOUD CLI — agente terminal-first sobre loud.codes.

Es un agente local que vive en tu terminal, conectado a tu LOUD privada
(self-hosted). Lee, escribe, edita archivos, corre comandos, todo con
permisos explícitos por acción — tipo Claude Code, Cursor, Aider — pero
sobre TU modelo y TU dato.

Comandos:
    loud                        # REPL interactivo
    loud "pregunta"             # one-shot
    loud login                  # autentica
    loud logout
    loud whoami
    loud --reset                # limpia historial
    loud --model NAME           # cambia modelo
    loud --version

Slash commands dentro del REPL:
    /help · /reset · /model · /tools · /permissions · /save · /exit
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import httpx

__version__ = "1.1.3"

# ───────────────────── Config ─────────────────────

HOME = Path.home()
LOUD_DIR = HOME / ".loud"
LOUD_DIR.mkdir(exist_ok=True)
HISTORY_FILE = LOUD_DIR / "history.jsonl"
SESSION_FILE = LOUD_DIR / "current_session.json"
CONFIG_FILE = LOUD_DIR / "config.json"
AUTH_FILE = LOUD_DIR / "auth.json"
PERMS_FILE = LOUD_DIR / "permissions.json"

DEFAULT_CONFIG = {
    "api_url": "https://api.loud.codes",
    "model": "loud-go",
    "max_iterations": 25,    # bumped from 10 — multi-step plans need room to breathe
    "permission_mode": "ask",      # ask | yolo | safe (safe = block destructive ops)
    "typewriter": True,
    # Compute mode for chat inference:
    #   "cloud"  → talk to api.loud.codes (full brain, RAG, auto-nurture)
    #   "local"  → talk to local Ollama 127.0.0.1:11434 (zero network latency,
    #              uses this machine's CPU/GPU, no RAG)
    #   "auto"   → try local first; fall back to cloud if local isn't ready
    "mode": "cloud",
    "local_ollama_url":   "http://127.0.0.1:11434",
    "local_model":        "qwen2.5:3b",          # ~2GB, runs on any modern laptop
    "local_model_vision": "llama3.2-vision:11b", # only auto-pulled if user opts in
    # Brain access is INTENTIONALLY ABSENT from terminal mode. The curated
    # knowledge base is a web-admin-only feature. From the CLI you get the
    # model's training knowledge + tools on this machine — nothing else.
    # This is a hard policy decision; there is no toggle.
}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return {**DEFAULT_CONFIG, **json.loads(CONFIG_FILE.read_text())}
        except Exception:
            pass
    CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


# ───────────────────── ANSI / brand ─────────────────────

USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") != "1"


class C:
    RESET   = "\033[0m" if USE_COLOR else ""
    BOLD    = "\033[1m" if USE_COLOR else ""
    DIM     = "\033[2m" if USE_COLOR else ""
    ITALIC  = "\033[3m" if USE_COLOR else ""
    RED     = "\033[38;5;203m" if USE_COLOR else ""
    YELLOW  = "\033[38;5;221m" if USE_COLOR else ""
    BLUE    = "\033[38;5;110m" if USE_COLOR else ""
    GRAY    = "\033[38;5;240m" if USE_COLOR else ""
    BRAND   = "\033[38;5;149m" if USE_COLOR else ""      # closest 256-color to #a2cd65
    GREEN   = BRAND
    CYAN    = BRAND
    MAGENTA = BRAND


# Hide internal IPs/hosts from any output so users never see infra detail.
_HOST_RE = re.compile(
    r"(?i)\b(?:https?://|http://)?(?:127\.0\.0\.1|localhost|"
    r"(?:\d{1,3}\.){3}\d{1,3}|"
    r"ec2[\w.\-]+\.amazonaws\.com)"
    r"(?::\d+)?(?:/\S*)?"
)


def scrub(text: str) -> str:
    if not isinstance(text, str):
        return text
    return _HOST_RE.sub("<host>", text)


def cprint(text: str, color: str = "", *, bold: bool = False, end: str = "\n") -> None:
    text = scrub(text)
    prefix = ""
    if bold:
        prefix += C.BOLD
    prefix += color
    sys.stdout.write(prefix + text + (C.RESET if color or bold else "") + end)
    sys.stdout.flush()


def shorten(s: str, n: int) -> str:
    s = scrub(s)
    return s if len(s) <= n else s[: n - 1] + "…"


# ───────────────────── Platform helpers ─────────────────────

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC     = sys.platform == "darwin"


def _shell_args(cmd: str) -> list[str]:
    if IS_WINDOWS:
        # PowerShell gives sensible defaults on Win; fall back to cmd.exe if missing.
        if shutil.which("pwsh"):
            return ["pwsh", "-NoLogo", "-NoProfile", "-Command", cmd]
        if shutil.which("powershell"):
            return ["powershell", "-NoLogo", "-NoProfile", "-Command", cmd]
        return ["cmd", "/c", cmd]
    return ["bash", "-c", cmd]


# ───────────────────── Arrow-key selector ─────────────────────

def _read_key() -> str:
    """Block on one keystroke. Returns 'up', 'down', 'enter', 'esc', 'q', 'e',
    'y', 'n', 'a', 's', or '' for unknown. Falls back to line-input on non-TTY."""
    if not sys.stdin.isatty():
        try: line = input().strip().lower()
        except (EOFError, KeyboardInterrupt): return "esc"
        if not line: return "enter"
        head = line[0]
        return {"y":"y","n":"n","a":"a","s":"s","e":"e","q":"esc"}.get(head, head)
    if IS_WINDOWS:
        import msvcrt
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            ch2 = msvcrt.getwch()
            return {"H": "up", "P": "down"}.get(ch2, "")
        if ch in ("\r", "\n"): return "enter"
        if ch == "\x1b": return "esc"
        return ch.lower()
    # Unix raw mode
    import termios, tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            seq = sys.stdin.read(2)
            if seq == "[A": return "up"
            if seq == "[B": return "down"
            return "esc"
        if ch in ("\r", "\n"): return "enter"
        if ch == "\x03": raise KeyboardInterrupt
        return ch.lower()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def select_option(prompt_text: str, options: list[tuple[str, str]], default: int = 0) -> str:
    """Interactive arrow-key selector. options = [(key, label), ...].
    Up/Down to move, Enter to confirm, Esc to cancel (returns 'esc').
    Hotkeys still work — pressing the first letter of an option jumps to it.
    Returns the chosen key, or 'esc'."""
    if not sys.stdin.isatty():
        # Non-interactive — just print the prompt and read a line.
        print(prompt_text)
        for k, lab in options:
            print(f"   [{k}] {lab}")
        try: raw = input("→ ").strip().lower()
        except (EOFError, KeyboardInterrupt): return "esc"
        if not raw: return options[default][0]
        for k, _ in options:
            if k.startswith(raw[0]): return k
        return "esc"
    idx = default
    n = len(options)
    first = True
    try:
        while True:
            if not first:
                # Move cursor up n lines, clear each, repaint.
                sys.stdout.write(f"\033[{n}A")
            for i, (k, lab) in enumerate(options):
                marker = "▶" if i == idx else " "
                line = f"   {marker} [{k}] {lab}"
                if i == idx:
                    line = f"\033[7m{line}\033[0m"
                sys.stdout.write("\033[2K" + line + "\n")
            sys.stdout.flush()
            first = False
            key = _read_key()
            if key == "up":   idx = (idx - 1) % n
            elif key == "down": idx = (idx + 1) % n
            elif key == "enter": return options[idx][0]
            elif key == "esc": return "esc"
            elif len(key) == 1:
                for i, (k, _) in enumerate(options):
                    if k.startswith(key):
                        idx = i
                        return options[idx][0]
    except KeyboardInterrupt:
        return "esc"


# ───────────────────── Permission system ─────────────────────
# Tools that mutate state on the user's machine require explicit consent.
# Modes:
#   "ask"  → prompt every time, with [y]es/[n]o/[a]lways/[s]top remembered
#   "yolo" → never ask, run everything
#   "safe" → block destructive ops (write_file, bash with rm/mv/curl|sh, ssh)

DESTRUCTIVE_TOOLS = {"bash", "ssh", "write_file", "edit_file"}
DESTRUCTIVE_BASH_PATTERNS = [
    r"\brm\s+-",
    r"\bmv\s+",
    r"\bdd\s+",
    r":>",
    r"\bsudo\b",
    r"\bcurl\s+[^|]*\|\s*sh",
    r"\bwget\s+[^|]*\|\s*sh",
    r"\bgit\s+push\s+.*--force",
    r"\bterraform\s+(destroy|apply)\b",
    r"\bnpm\s+publish\b",
    r"\bpip\s+install\b",
    r"\bbrew\s+uninstall",
]

# Patterns that touch SYSTEM-level surface. These still prompt in --yolo mode
# because they can brick the machine, cost real money, or punch holes in the
# user's perimeter. The user asked: "que yolo unicamente se pase largo si no
# requiere permisos del sistema". This is the list.
SYSTEM_DESTRUCTIVE_PATTERNS = [
    r"\bsudo\b",
    r"\brm\s+-rf\s+(?:/(?!tmp/|var/folders/|private/tmp/)|~)",   # rm -rf outside /tmp
    r"\bchmod\s+-R\b",
    r"\bchown\s+-R\b",
    r"\bgit\s+push\s+.*--force",
    r"\bbrew\s+uninstall\b",
    r"\b(apt|apt-get|pacman|yum|dnf)\s+(install|remove|uninstall|purge)\b",
    r"\bdd\s+.*\bof=/dev/",
    r"\bmkfs\.",
    r"\biptables\b",
    r"\bpfctl\b",
    r"\blaunchctl\b",
    r"\bnetwork(setup|ctl)\b",
    r"\bsystemctl\s+(start|stop|enable|disable|restart)\b",
    r"\b(open|listen)\s+(port|puerto)\b",          # firewall punch
    r"\bufw\b",
    # Touching files in system-owned roots
    r"\s+(/etc/|/System/|/Library/(?!Application)|/var/(?!folders/)|/opt/|/usr/(?!local/)|/bin/|/sbin/|/private/var/|/Applications/)",
]

# System-owned path prefixes for write/edit_file. Even in yolo we prompt.
SYSTEM_PATH_PREFIXES = (
    "/etc/", "/System/", "/var/", "/opt/", "/usr/", "/bin/", "/sbin/",
    "/private/var/", "/Applications/",
)

# Long-running server patterns. If the model tries to run these via plain
# `bash`, the tool rejects them and tells the model to use bash_background.
BASH_BLOCKING_PATTERNS = [
    r"\bpython\d?\s+-m\s+http\.server\b",
    r"\bpython\d?\s+-m\s+SimpleHTTPServer\b",
    r"\bngrok\s+(http|tcp|tls)\b",
    r"\bnode\s+\S*\.js\b",
    r"\bnpm\s+(run|start)\b",
    r"\byarn\s+(run|start|dev)\b",
    r"\bpnpm\s+(run|dev|start)\b",
    r"\b(uvicorn|gunicorn|hypercorn|daphne)\b",
    r"\b(flask|fastapi|django-admin)\s+runserver\b",
    r"\b(rails|bundle exec rails)\s+(s|server)\b",
    r"\b(php|php -S)\b",
    r"\bcaddy\s+(run|start)\b",
    r"\bnginx\s*-g\b",
    r"\btail\s+-[fF]\b",
    r"\bwatch\b",
    r"\binotifywait\b",
    r"\b(docker|kubectl)\s+(logs|attach|exec)\s+-[itfIT]+\b",
]


def is_system_destructive(tool: str, args: dict) -> bool:
    """Stricter than is_destructive — these things prompt the user EVEN in
    --yolo mode. The point: an unattended yolo agent can flow through 'mkdir
    /tmp/x && touch index.html' freely, but should pause before `sudo` or
    touching /etc."""
    if tool == "ssh":
        return True
    if tool == "bash":
        cmd = (args.get("cmd") or "").strip()
        return any(re.search(p, cmd, re.IGNORECASE) for p in SYSTEM_DESTRUCTIVE_PATTERNS)
    if tool in ("write_file", "edit_file"):
        path = (args.get("path") or "").strip()
        try:
            resolved = str(Path(path).expanduser().resolve())
        except Exception:
            resolved = path
        return any(resolved.startswith(p.rstrip("/") + "/") or resolved == p.rstrip("/")
                   for p in SYSTEM_PATH_PREFIXES)
    return False


def _load_perms() -> dict:
    if not PERMS_FILE.exists():
        return {}
    try:
        return json.loads(PERMS_FILE.read_text())
    except Exception:
        return {}


def _save_perms(perms: dict) -> None:
    PERMS_FILE.write_text(json.dumps(perms, indent=2))


def _bash_first_word(cmd: str) -> str:
    """First non-shell-noise word of a bash command, used for scoped allow."""
    cmd = (cmd or "").strip()
    # Skip leading env-var assignments like 'FOO=1 bar …'
    parts = cmd.split()
    while parts and "=" in parts[0] and " " not in parts[0]:
        parts.pop(0)
    return parts[0].split("/")[-1] if parts else ""


def _path_parent(args_path: str) -> Path | None:
    if not args_path: return None
    try: return Path(args_path).expanduser().resolve().parent
    except Exception: return None


def _perm_key(tool: str, args: dict, scope: str | None = None) -> str:
    """Stable key for caching always-allow decisions. The `scope` controls
    granularity. Supported scopes:
        bash      → 'exact'    = the exact command
                    'verb'     = match any `<first-word> …` (eg. always allow git)
                    'cwd'      = match any bash launched while cwd == current cwd
        ssh       → 'host'     (always)
        file ops  → 'file'     = this exact path
                    'folder'   = anything inside the parent dir
                    'recursive'= anything under that dir tree
    """
    if tool == "bash":
        scope = scope or "verb"
        if scope == "verb":
            return f"bash:verb:{_bash_first_word(args.get('cmd',''))}"
        if scope == "cwd":
            return f"bash:cwd:{Path.cwd().resolve()}"
        # exact
        return f"bash:exact:{(args.get('cmd') or '').strip()}"
    if tool == "ssh":
        return f"ssh:{args.get('host', '?')}"
    if tool in ("write_file", "edit_file", "read_file"):
        scope = scope or "folder"
        parent = _path_parent(args.get("path", ""))
        if not parent: return f"{tool}:?"
        if scope == "file":
            try: return f"{tool}:file:{Path(args['path']).expanduser().resolve()}"
            except Exception: return f"{tool}:?"
        if scope == "recursive":
            return f"{tool}:tree:{parent}"
        # folder = same parent dir, not recursive
        return f"{tool}:folder:{parent}"
    return tool


def _perm_match(perms: dict, tool: str, args: dict) -> str | None:
    """Check whether `perms` has a saved 'always' rule that matches this call.
    Tries all granularities (most specific first) so a 'recursive' rule on a
    parent still applies to children, a 'verb' rule applies to any bash with
    that first word, etc. Returns the matched scope label or None."""
    if tool == "bash":
        for scope in ("exact", "verb", "cwd"):
            if perms.get(_perm_key(tool, args, scope)) == "always":
                return scope
        return None
    if tool == "ssh":
        return "host" if perms.get(_perm_key(tool, args)) == "always" else None
    if tool in ("write_file", "edit_file", "read_file"):
        # most specific first
        if perms.get(_perm_key(tool, args, "file")) == "always": return "file"
        if perms.get(_perm_key(tool, args, "folder")) == "always": return "folder"
        # 'recursive' applies if any ancestor dir has it
        p = _path_parent(args.get("path", ""))
        if p:
            cur = p
            while True:
                if perms.get(f"{tool}:tree:{cur}") == "always": return "recursive"
                if cur == cur.parent: break
                cur = cur.parent
        return None
    return None


def is_destructive(tool: str, args: dict) -> bool:
    """True iff this specific invocation actually modifies state. Reading
    commands (--version, ls, cat, which, ps, etc.) are NOT destructive even
    though the bash tool itself can be. Always-destructive tools: write_file,
    edit_file, ssh (remote side effects unknown)."""
    if tool in ("write_file", "edit_file", "ssh"):
        return True
    if tool == "bash":
        cmd = (args.get("cmd") or "").strip()
        return any(re.search(p, cmd, re.IGNORECASE) for p in DESTRUCTIVE_BASH_PATTERNS)
    return False


def request_permission(cfg: dict, tool: str, args: dict) -> str:
    """Returns 'allow', 'deny', or 'stop'. Mutates `args` in-place when the
    user picks 'edit'. When the user picks 'always', a sub-selector lets them
    pick the SCOPE of the always-rule (exact / verb / folder / recursive)."""
    mode = cfg.get("permission_mode", "ask")
    # YOLO: skip prompts for benign + medium-impact, but ALWAYS prompt for
    # system-level ops (sudo, /etc, force-push, package managers, firewall…).
    # The user explicitly asked: yolo bypasses only when no system permission
    # is required. We don't want unattended agents punching holes.
    if mode == "yolo":
        if is_system_destructive(tool, args):
            cprint("  ⚠ acción a nivel de sistema — yolo NO la salta, te pregunto", C.YELLOW, bold=True)
            # fall through to interactive prompt
        else:
            return "allow"
    # Benign reads (--version, ls, cat, which, ps, git status, pwd, echo…) and
    # all non-state-changing tools auto-allow even in ask mode. Only commands
    # that ACTUALLY mutate state trip the prompt. This is what makes the CLI
    # feel like a real assistant instead of constantly asking permission.
    if not is_destructive(tool, args):
        return "allow"
    if mode == "safe":
        return "deny"
    perms = _load_perms()
    matched_scope = _perm_match(perms, tool, args)
    if matched_scope:
        return "allow"

    # ── Header with rich context about what's about to happen ──
    cprint("", "")
    cprint(f"  ┌─ permiso requerido ─ {tool}", C.YELLOW, bold=True)
    if tool == "bash":
        cprint(f"  │  cwd: {Path.cwd()}", C.GRAY)
        cprint(f"  │  $ {shorten(args.get('cmd', ''), 220)}", C.GRAY)
        if is_destructive(tool, args):
            cprint(f"  │  ⚠ marcado como destructivo", C.RED)
    elif tool == "ssh":
        cprint(f"  │  host: {args.get('host')}", C.GRAY)
        cprint(f"  │  $ {shorten(args.get('cmd', ''), 200)}", C.GRAY)
    elif tool == "write_file":
        content = args.get("content", "")
        size_kb = len(content.encode("utf-8")) / 1024
        lines = content.count("\n") + 1
        cprint(f"  │  → {args.get('path')}", C.GRAY)
        cprint(f"  │  {size_kb:.1f} KB · {lines} líneas · {'sobrescribe' if Path(args.get('path','')).expanduser().exists() else 'crea nuevo'}", C.GRAY)
        for ln in content.splitlines()[:3]:
            cprint(f"  │    {shorten(ln, 200)}", C.DIM)
        if lines > 3:
            cprint(f"  │    … +{lines-3} líneas más", C.DIM)
    elif tool == "edit_file":
        cprint(f"  │  ✎ {args.get('path')}", C.GRAY)
        old = (args.get("old") or "").strip()
        new = (args.get("new") or "").strip()
        cprint(f"  │  - {shorten(old, 200)}", C.RED)
        cprint(f"  │  + {shorten(new, 200)}", C.GREEN)
    cprint(f"  └─ usa ↑/↓ y Enter (o presiona la letra)", C.YELLOW)
    ans = select_option("", [
        ("y", "Sí, correr esto"),
        ("n", "No, cancelar"),
        ("e", "Editar antes de correr"),
        ("a", "Siempre permitir… (elegir alcance)"),
        ("s", "Detener al agente"),
    ], default=0)
    if ans == "esc":
        return "stop"
    if ans == "a":
        # Sub-selector for the scope of the "always" rule.
        if tool == "bash":
            verb = _bash_first_word(args.get("cmd",""))
            cwd  = Path.cwd()
            scope_choice = select_option(
                "  ¿Hasta dónde aplica el 'siempre permitir'?",
                [
                    ("v", f"Cualquier `{verb} …` en cualquier folder"),
                    ("c", f"Cualquier bash mientras esté en {cwd}"),
                    ("x", f"SOLO este comando exacto"),
                    ("n", "Cancelar (esta vez no)"),
                ], default=0)
            if scope_choice in ("esc", "n"): return "deny"
            scope = {"v":"verb","c":"cwd","x":"exact"}.get(scope_choice, "verb")
            perms[_perm_key(tool, args, scope)] = "always"
            _save_perms(perms)
            cprint(f"  ✓ guardado: {_perm_key(tool, args, scope)}", C.GREEN)
        elif tool in ("write_file", "edit_file", "read_file"):
            parent = _path_parent(args.get("path",""))
            scope_choice = select_option(
                "  ¿Hasta dónde aplica el 'siempre permitir'?",
                [
                    ("d", f"Cualquier archivo dentro de {parent}"),
                    ("r", f"Cualquier archivo bajo {parent} (recursivo)"),
                    ("f", f"SOLO este archivo"),
                    ("n", "Cancelar"),
                ], default=0)
            if scope_choice in ("esc", "n"): return "deny"
            scope = {"d":"folder","r":"recursive","f":"file"}.get(scope_choice, "folder")
            perms[_perm_key(tool, args, scope)] = "always"
            _save_perms(perms)
            cprint(f"  ✓ guardado: {_perm_key(tool, args, scope)}", C.GREEN)
        else:
            perms[_perm_key(tool, args)] = "always"
            _save_perms(perms)
        return "allow"
    if ans == "y":
        return "allow"
    if ans == "s":
        return "stop"
    if ans == "e":
        # Let the user rewrite the command / path. Mutate args in-place.
        if tool == "bash":
            cprint(f"     edita el comando (enter cancela):", C.YELLOW)
            cprint(f"     $ ", C.GRAY, end="")
            try:
                new_cmd = input()
            except (EOFError, KeyboardInterrupt):
                return "deny"
            if new_cmd.strip():
                args["cmd"] = new_cmd
                cprint(f"  → bash({shorten(new_cmd, 120)})", C.BRAND)
                return "allow"
            return "deny"
        if tool == "ssh":
            cprint(f"     comando remoto (enter cancela):", C.YELLOW)
            cprint(f"     {args.get('host')}> ", C.GRAY, end="")
            try:
                new_cmd = input()
            except (EOFError, KeyboardInterrupt):
                return "deny"
            if new_cmd.strip():
                args["cmd"] = new_cmd
                return "allow"
            return "deny"
        if tool == "write_file":
            cprint(f"     ruta destino (enter mantiene {args.get('path')}):", C.YELLOW)
            cprint(f"     → ", C.GRAY, end="")
            try:
                new_path = input().strip()
            except (EOFError, KeyboardInterrupt):
                return "deny"
            if new_path:
                args["path"] = new_path
            return "allow"
        if tool == "edit_file":
            cprint(f"     nuevo contenido reemplazo (enter mantiene el del modelo):", C.YELLOW)
            cprint(f"     ✎ ", C.GRAY, end="")
            try:
                new_text = input()
            except (EOFError, KeyboardInterrupt):
                return "deny"
            if new_text:
                args["new"] = new_text
            return "allow"
        return "allow"
    return "deny"


# ───────────────────── Tools ─────────────────────

def _validate_bash_complexity(cmd: str) -> str | None:
    """Hard ceiling on how much a single bash call can do. The model must
    decompose multi-step work into separate tool calls — we won't run a
    megachain even if asked. Returns None when ok, or an error string the
    model gets in place of execution.

    Allowed:
    - 0 chain operators (single command)
    - 1 chain operator (action + verification, e.g. `mkdir -p /x && ls /x`)
    - Pipes (`|`) are fine — they're a single conceptual operation.

    Rejected:
    - 2+ `&&`/`||`/`;` operators (multiple sequential actions in one call).
    - More than one of the following "phase keywords" in a row: install,
      clone, mkdir, write, serve, run, start, expose. Those should each be
      their own tool call so the user sees progress one step at a time.
    """
    if not cmd or not cmd.strip(): return None
    cleaned = cmd.strip()
    # Strip quoted segments so operators inside strings don't count.
    stripped = re.sub(r"'[^']*'|\"[^\"]*\"", "", cleaned)
    op_count = (
        stripped.count("&&") +
        stripped.count("||") +
        # Only count semicolons that look like statement separators, not
        # things like `find ... \;`.
        sum(1 for m in re.finditer(r"(?<!\\);(?!\s*$)", stripped))
    )
    if op_count >= 2:
        return (
            "ERROR rechazado por el CLI: este comando encadena "
            f"{op_count} acciones en un solo bash (operadores &&/||/;). "
            "Reglas del agente: UNA acción atómica por tool call. "
            "Partilo en pasos separados — corré el primer paso ahora, "
            "esperá el resultado, después el siguiente. Ej: en vez de "
            "`brew install A && brew install B && python -m http.server &`, "
            "hacé tres bash separados."
        )
    # Reject blocking long-running commands — these must go through
    # `bash_background` so the CLI doesn't hang on the subprocess.
    for pat in BASH_BLOCKING_PATTERNS:
        if re.search(pat, low):
            return (
                f"ERROR rechazado por el CLI: este comando parece ser un proceso "
                f"de larga duración (server/watcher/tunnel) que va a colgar el bash. "
                f"Usá la tool `bash_background(cmd, label)` en vez de `bash` para "
                f"que el CLI lo lance desprendido y vos podás seguir trabajando. "
                f"Pattern matched: {pat}"
            )
    # Phase keywords — rough but useful for the small qwen models.
    phase_words = ["install", "clone", "mkdir", "serve", "start", "expose", "ngrok http"]
    low = stripped.lower()
    matched = [w for w in phase_words if re.search(rf"\b{re.escape(w)}\b", low)]
    if len(matched) >= 2 and op_count >= 1:
        return (
            f"ERROR rechazado por el CLI: este comando mezcla {len(matched)} fases "
            f"({', '.join(matched)}) en un solo bash. Hacé cada fase como un tool call "
            "separado para poder verificar entre pasos."
        )
    return None


# ── Background job machinery ──
# LOUD-portable: tasks the user wants left RUNNING (a server, a build, a
# downloader, a long ngrok tunnel) get their own managed log file under
# ~/.loud/jobs/. The model gets dedicated tools to start, list, status and
# stop them without juggling raw nohup syntax.

JOBS_DIR = LOUD_DIR / "jobs"


def _jobs_dir() -> Path:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    return JOBS_DIR


def _job_path(label: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", label)[:48] or "job"
    return _jobs_dir() / f"{safe}.log"


def _job_meta_path(label: str) -> Path:
    return _job_path(label).with_suffix(".json")


def _is_pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0); return True
    except (OSError, ProcessLookupError):
        return False


async def tool_bash_background(cmd: str, label: str) -> str:
    """Start a long-running shell command in the BACKGROUND and return
    immediately with its PID + log path. Use this for servers, watchers,
    downloaders, ngrok tunnels — anything that doesn't terminate quickly.

    The output goes to ~/.loud/jobs/<label>.log so the model (or the user)
    can `tail` it later via read_file or bash. Metadata in <label>.json."""
    if not label or not label.strip():
        return "ERROR: label requerido (ej: 'http-1002', 'ngrok', 'build')"
    err = _validate_bash_complexity(cmd)
    if err: return err
    log_path = _job_path(label)
    meta_path = _job_meta_path(label)
    try:
        # Open log file and pass its FD to Popen so the child inherits it. No
        # extra `&` wrapping — `start_new_session=True` already detaches the
        # child from our process group, so SIGHUP from us doesn't kill it.
        log_fh = open(log_path, "w")
        proc = subprocess.Popen(
            _shell_args(cmd),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
        log_fh.close()                       # parent closes; child still has it
        await asyncio.sleep(0.4)
        wrapper_pid = proc.pid               # this is bash (or whatever shell), the child of which is `cmd`
        meta = {
            "label": label,
            "cmd": cmd,
            "log": str(log_path),
            "wrapper_pid": wrapper_pid,
            "started_at": time.time(),
        }
        meta_path.write_text(json.dumps(meta, indent=2))
        # Read a quick first slice of the log so the model sees early output.
        await asyncio.sleep(0.6)
        early = ""
        try: early = log_path.read_text()[:1200]
        except Exception: pass
        return (
            f"job '{label}' started\n"
            f"  cmd: {cmd}\n"
            f"  log: {log_path}\n"
            f"  pid (wrapper): {wrapper_pid}\n"
            f"  early output:\n{early or '(nothing yet)'}"
        )
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


async def tool_job_status(label: str, tail_lines: int = 40) -> str:
    """Inspect a background job started by bash_background. Shows whether the
    process is still alive plus the last N lines of its log."""
    meta_path = _job_meta_path(label)
    log_path = _job_path(label)
    if not meta_path.exists():
        return f"ERROR: no hay job con label '{label}'. Usa job_list para ver los disponibles."
    try:
        meta = json.loads(meta_path.read_text())
    except Exception:
        meta = {}
    age = time.time() - meta.get("started_at", time.time())
    wrapper_alive = _is_pid_running(meta.get("wrapper_pid", -1))
    tail = ""
    if log_path.exists():
        try:
            lines = log_path.read_text().splitlines()
            tail = "\n".join(lines[-tail_lines:])
        except Exception as e:
            tail = f"(no pude leer log: {e})"
    return (
        f"job '{label}': {'alive' if wrapper_alive else 'exited'} · "
        f"{int(age)}s desde start\n"
        f"  cmd: {meta.get('cmd','?')}\n"
        f"  log: {log_path}\n"
        f"  last {tail_lines} líneas:\n{tail or '(log vacío)'}"
    )


async def tool_job_list() -> str:
    """List all background jobs LOUD has started (alive + exited)."""
    d = _jobs_dir()
    metas = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not metas:
        return "(no hay background jobs)"
    rows = []
    for m in metas:
        try: meta = json.loads(m.read_text())
        except Exception: continue
        alive = _is_pid_running(meta.get("wrapper_pid", -1))
        age = int(time.time() - meta.get("started_at", time.time()))
        rows.append(f"  {'●' if alive else '○'} {meta.get('label','?'):20s} {age:5d}s  {meta.get('cmd','')[:80]}")
    return "background jobs:\n" + "\n".join(rows)


async def tool_job_stop(label: str) -> str:
    """Kill a background job by label. Uses the process group so the entire
    subprocess tree dies (helpful for ngrok / python -m http.server)."""
    meta_path = _job_meta_path(label)
    if not meta_path.exists():
        return f"ERROR: no hay job con label '{label}'"
    try:
        meta = json.loads(meta_path.read_text())
    except Exception:
        return "ERROR: meta corrupto"
    pid = int(meta.get("wrapper_pid", -1))
    if pid <= 0 or not _is_pid_running(pid):
        return f"job '{label}' ya estaba detenido"
    try:
        # Kill the whole process group started with start_new_session=True
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        await asyncio.sleep(0.3)
        if _is_pid_running(pid):
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        return f"job '{label}' detenido (pid {pid})"
    except (ProcessLookupError, PermissionError):
        return f"job '{label}': pid {pid} ya no responde"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


async def tool_bash(cmd: str, timeout: int = 120) -> str:
    # Step-gating: refuse megachains so the model is forced to decompose.
    err = _validate_bash_complexity(cmd)
    if err:
        return err
    try:
        proc = subprocess.run(_shell_args(cmd), capture_output=True, text=True, timeout=timeout)
        out = proc.stdout or ""
        if proc.stderr:
            out += "\n[stderr]\n" + proc.stderr
        if not out.strip():
            return f"(no output, exit={proc.returncode})"
        return out[:8000]
    except subprocess.TimeoutExpired:
        return f"ERROR: timeout ({timeout}s)"
    except FileNotFoundError as e:
        return f"ERROR: shell not found: {e}"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


async def tool_ssh(host: str, cmd: str, timeout: int = 60) -> str:
    args = ["ssh", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new", host, cmd]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        out = proc.stdout or ""
        if proc.stderr:
            out += "\n[stderr]\n" + proc.stderr
        return out[:8000] if out.strip() else f"(no output, exit={proc.returncode})"
    except subprocess.TimeoutExpired:
        return f"ERROR: ssh timeout ({timeout}s) to {host}"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


async def tool_read_file(path: str, max_lines: int = 600) -> str:
    p = Path(path).expanduser()
    try:
        if not p.exists():
            return f"ERROR: not found: {p}"
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        if len(lines) > max_lines:
            return "\n".join(lines[:max_lines]) + f"\n\n[truncated · {len(lines) - max_lines} more lines, total {len(lines)}]"
        return text
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


async def tool_write_file(path: str, content: str) -> str:
    p = Path(path).expanduser()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"✓ wrote {len(content)} bytes to {p}"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


async def tool_edit_file(path: str, old: str, new: str) -> str:
    """Replace first occurrence of `old` with `new` in the file."""
    p = Path(path).expanduser()
    try:
        if not p.exists():
            return f"ERROR: not found: {p}"
        text = p.read_text(encoding="utf-8", errors="replace")
        if old not in text:
            return "ERROR: old_string not found in file (must match exactly, including whitespace)"
        if text.count(old) > 1:
            return f"ERROR: old_string is not unique ({text.count(old)} matches). Provide more surrounding context."
        new_text = text.replace(old, new, 1)
        p.write_text(new_text, encoding="utf-8")
        return f"✓ patched {p}  (-{len(old)} +{len(new)} chars)"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


async def tool_glob(pattern: str, path: str = ".") -> str:
    base = Path(path).expanduser().resolve()
    try:
        matches = sorted(base.glob(pattern))
        if not matches:
            return f"(no matches for {pattern} in {base})"
        out = []
        for m in matches[:120]:
            try:
                rel = m.relative_to(base)
            except ValueError:
                rel = m
            out.append(str(rel) + ("/" if m.is_dir() else ""))
        if len(matches) > 120:
            out.append(f"[... {len(matches) - 120} more]")
        return "\n".join(out)
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


async def tool_grep(pattern: str, path: str = ".", max_results: int = 80) -> str:
    p = Path(path).expanduser()
    rg = shutil.which("rg")
    if rg:
        cmd = [rg, "--no-heading", "--with-filename", "--line-number", "--max-count", "10", pattern, str(p)]
    else:
        cmd = ["grep", "-rn", pattern, str(p)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        out = proc.stdout
        if not out.strip():
            return f"(no matches for {pattern!r} in {p})"
        lines = out.splitlines()
        if len(lines) > max_results:
            return "\n".join(lines[:max_results]) + f"\n[... {len(lines) - max_results} more matches]"
        return out[:8000]
    except subprocess.TimeoutExpired:
        return "ERROR: grep timeout"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


async def tool_ls(path: str = ".") -> str:
    p = Path(path).expanduser().resolve()
    try:
        entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        lines = [f"pwd: {p}"]
        for e in entries[:80]:
            kind = "/" if e.is_dir() else ""
            try:
                size = e.stat().st_size if e.is_file() else ""
                lines.append(f"  {e.name}{kind}  {size}")
            except Exception:
                lines.append(f"  {e.name}{kind}")
        if len(entries) > 80:
            lines.append(f"[... {len(entries) - 80} more]")
        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


async def tool_http_get(url: str, max_bytes: int = 8000) -> str:
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0 LOUD"})
            ct = r.headers.get("content-type", "")
            if "text" in ct or "json" in ct or "xml" in ct or "html" in ct:
                return r.text[:max_bytes]
            return f"[binary {ct}, {len(r.content)} bytes]"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


TOOLS_SCHEMA = [
    {"type": "function", "function": {
        "name": "bash",
        "description": "Run a shell command on the LOCAL machine. The user is asked for permission for destructive operations.",
        "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]},
    }},
    {"type": "function", "function": {
        "name": "ssh",
        "description": "SSH to a host (uses ~/.ssh/config aliases or explicit user@host).",
        "parameters": {"type": "object", "properties": {
            "host": {"type": "string"}, "cmd": {"type": "string"},
        }, "required": ["host", "cmd"]},
    }},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a text file from the LOCAL filesystem (up to 600 lines).",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Create or overwrite a file on the LOCAL filesystem with the given content. Requires permission.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"},
        }, "required": ["path", "content"]},
    }},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "Replace the first occurrence of old_string with new_string in a file. old_string must be unique in the file. Requires permission.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "old": {"type": "string", "description": "Exact text to find (must be unique in file)."},
            "new": {"type": "string", "description": "Replacement text."},
        }, "required": ["path", "old", "new"]},
    }},
    {"type": "function", "function": {
        "name": "glob",
        "description": "Find files matching a glob pattern (e.g. '**/*.py', 'src/*.tsx').",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "description": "Base directory (default .)"},
        }, "required": ["pattern"]},
    }},
    {"type": "function", "function": {
        "name": "grep",
        "description": "Recursively search for a pattern in files. Uses ripgrep if available.",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "description": "Default ."},
        }, "required": ["pattern"]},
    }},
    {"type": "function", "function": {
        "name": "ls",
        "description": "List directory contents + show pwd. Use to orient yourself.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Default ."},
        }, "required": []},
    }},
    {"type": "function", "function": {
        "name": "http_get",
        "description": "Fetch the body of an HTTP/HTTPS URL.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
    }},
    {"type": "function", "function": {
        "name": "bash_background",
        "description": "Start a LONG-RUNNING shell command in the BACKGROUND. Use this for servers, watchers, downloaders, ngrok tunnels — anything that doesn't terminate quickly. Returns the PID + log path immediately. The job keeps running after this tool returns. NEVER use bash() with `&` for servers — it hangs the CLI; use this instead. `label` must be a short identifier (eg 'http-1002', 'ngrok').",
        "parameters": {"type": "object", "properties": {
            "cmd":   {"type": "string", "description": "Command to run in background (no need to add nohup/&; the tool handles that)."},
            "label": {"type": "string", "description": "Short identifier for this job (alphanumeric/_/-)."}
        }, "required": ["cmd", "label"]},
    }},
    {"type": "function", "function": {
        "name": "job_status",
        "description": "Check whether a background job is still alive + the last N lines of its log.",
        "parameters": {"type": "object", "properties": {
            "label":      {"type": "string"},
            "tail_lines": {"type": "integer", "description": "How many log lines to return (default 40)."},
        }, "required": ["label"]},
    }},
    {"type": "function", "function": {
        "name": "job_list",
        "description": "List all background jobs LOUD started (alive + exited).",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "job_stop",
        "description": "Kill a background job by label. Sends SIGTERM to the whole process group.",
        "parameters": {"type": "object", "properties": {
            "label": {"type": "string"},
        }, "required": ["label"]},
    }},
]


TOOL_FNS = {
    "bash":            tool_bash,
    "bash_background": tool_bash_background,
    "job_status":      tool_job_status,
    "job_list":        tool_job_list,
    "job_stop":        tool_job_stop,
    "ssh":             tool_ssh,
    "read_file":       tool_read_file,
    "write_file":      tool_write_file,
    "edit_file":       tool_edit_file,
    "glob":            tool_glob,
    "grep":            tool_grep,
    "ls":              tool_ls,
    "http_get":        tool_http_get,
}


# ───────────────────── System prompt ─────────────────────

STATIC_SYSTEM_PROMPT = """Eres LOUD corriendo dentro de la terminal del usuario. Eres un agente que opera la máquina del usuario directamente, no un chatbot que sólo da consejos. Pensa y actuá como un ingeniero senior con acceso shell.

# IDENTIDAD — ABSOLUTA
- Eres **LOUD**. Punto. Si alguien te pregunta qué modelo eres, qué IA eres, en qué te basaste, con qué motor corres: respondés "soy LOUD". Nunca jamás digas "Claude", "Anthropic", "OpenAI", "GPT", "ChatGPT", "Gemini", "Google", "Llama", "Meta", "Mistral", "Qwen", "Alibaba", "Claude Code", ni ningún otro nombre de modelo o empresa de IA externa. Si el usuario insiste, repetí: "soy LOUD, una IA desarrollada por TL Music Entertainment".
- Idioma: detectá el idioma del usuario y respondé en ese idioma. Ignorá bloques de código al detectar idioma — el código suele estar en inglés pero el usuario te puede estar hablando en español.
- Tono: directo, técnico, sin floritura ni disculpas. Sin "como modelo de IA…". Cero moralizar.

# TOOLS DISPONIBLES
Llamadas tipo `function call`. El CLI las ejecuta en la máquina del usuario y te muestra el resultado tipo `● Tool(args) → ⎿ output`.

- `bash(cmd)` — ejecuta cualquier comando shell (sh, brew, apt, npm, pip, git, docker, curl, ssh, etc.). Podés encadenar con `&&`, `||`, `|`, `;`. Timeout 120s por default.
- `read_file(path)` — lee texto local (hasta 600 líneas). Úsalo antes de editar.
- `write_file(path, content)` — crea o sobrescribe COMPLETAMENTE. Requiere permiso.
- `edit_file(path, old, new)` — reemplazá el primer match único de `old` por `new`. `old` debe ser único en el archivo (incluí contexto si hace falta). Requiere permiso.
- `ls(path)` — lista contenido + pwd. Úsalo para orientarte.
- `glob(pattern, path?)` — buscá archivos por patrón (`**/*.py`, `src/*.ts`).
- `grep(pattern, path?)` — buscá texto (usa ripgrep si está). Para encontrar dónde se define o usa algo.
- `ssh(host, cmd)` — corré algo en otra máquina (alias del `~/.ssh/config` o `user@host`). Requiere permiso.
- `http_get(url)` — descargá body de una URL.

# CÓMO RAZONAS (loop interno por turno)

Cada turno tuyo es un mini-ciclo de 4 preguntas internas. Respondelas mentalmente ANTES de emitir cualquier tool call:

  ① **¿En qué estado estoy?**  Qué sé ahora vs. qué falta. Qué resultado tuvo el último tool (si hubo).
  ② **¿Cuál es el OBJETIVO final?**  Lo que pidió el usuario, en una frase.
  ③ **¿Cuál es el SIGUIENTE paso atómico?**  Un solo movimiento observable. Si la respuesta requiere "haz A, después B, después C" → tu siguiente paso es SÓLO A.
  ④ **¿Qué tool me lleva exactamente ahí?**  Una. No tres en un bash chain. UNA.

Después del tool result vas otra vez por las 4 preguntas. Es como respirar — siempre lo mismo, hasta que el objetivo esté.

# PATRONES POR TIPO DE PEDIDO

**"Buscá / dónde está / cómo se usa X"** (investigar):
  1. `grep` para localizar → 2. `read_file` del archivo más prometedor (rango ajustado) → 3. responder.
  Los 1-2 son **paralelos** si tenés varios candidatos. NO leas archivos enteros si podés narrowed-read.

**"Arreglá el bug / la función / el test"** (modificar):
  1. `read_file` del archivo afectado → 2. identificar línea exacta → 3. `edit_file` con `old`/`new` chiquitos y únicos → 4. `bash` para verificar (correr test, lint, build).
  Nunca edites a ciegas. Nunca un mega-edit que reescribe el archivo entero si podés tocar 5 líneas.

**"Construí / armá / instalá / desplegá X"** (multi-paso):
  1. Una frase: "Voy a (a) X, (b) Y, (c) Z." (plan visible al usuario).
  2. Ejecutá SOLO el paso (a) con una tool.
  3. **Observá el resultado**. Si OK → seguí con (b). Si falló → re-plan: ¿qué dice el error?, ¿qué cambia mi siguiente acción?
  4. Repetí hasta (c).
  Si en cualquier punto el plan original ya no sirve, decilo: "esto cambió, ahora voy a..."

**"¿Qué versión / qué tengo / cómo está mi máquina?"** (read-only):
  Un solo `bash` con la consulta exacta. Respondé con el dato real.

**"Limpiá / borrá / refactorizá"** (destructivo):
  1. `ls`/`glob`/`du` para ver candidatos → 2. mostrar la lista al usuario → 3. el CLI pide confirmación para cada destructivo.

# CUÁNDO RE-PLANEAR

Re-planeás cuando una de estas pasa:
- Un tool devolvió un error. Leelo. ¿Es un path equivocado? `ls` el padre. ¿Falta dependencia? Instalala primero. ¿Permiso denegado? Ajustá usuario o pedí permiso.
- El resultado del tool muestra que el estado es distinto a lo que asumiste. Ajustá.
- El usuario corrige a media tarea ("no, hacelo de otra manera"). Tirá el plan, replanteá desde cero.

Nunca repitas idéntico un tool que falló. Si lo hacés es un bug tuyo.

# VERIFICACIÓN DESPUÉS DE ACTUAR

Después de modificar algo (write/edit/install/start service):
- ¿Existe? → `ls` o `read_file`.
- ¿Hace lo que dice? → `bash` con el comando que prueba el efecto (correr el script, hacer un `curl`, ver `ps`).
- ¿Se conecta? → `curl` con `-fsSL` o `curl -sS -o /dev/null -w "%{http_code}"`.

NO afirmes "listo" sin haber visto la verificación pasar.

# REGLAS DE OPERACIÓN

## 1. Tool-first sobre la máquina del usuario
CUALQUIER pregunta sobre el estado de SU máquina (versiones instaladas, archivos, rutas, procesos, configuración, red, qué hay en X carpeta, dónde está Y) → INVOCÁ una tool. NUNCA respondas de memoria. Si el usuario pregunta "qué Python tengo", corré `bash("python3 --version && which python3")` y respondé con la salida real. No pidas que el usuario ejecute comandos — VOS los ejecutás.

## 2. Read-before-write
Antes de editar un archivo, leelo. Antes de actuar en una carpeta desconocida, hacé `ls`. Si vas a modificar algo, leé el contexto primero — nunca inventes paths, nombres de funciones o contenido.

## 3. Encadena tools sin pedir permiso
Tu flujo típico para "arreglá X" es: `ls` → `read_file` → entender → `edit_file`/`bash` → verificar con `bash` o `read_file`. NO pidas confirmaciones intermedias — el CLI muestra `[y/n/e/a/s]` al usuario para las destructivas. Tú sólo invocás la tool y seguís.

## 4. UNA acción por tool call — SIEMPRE, no solo para tareas grandes
Esta es la regla más importante y aplica al 100% de tus turnos, no solo cuando la tarea "se ve compleja". Cada tool call hace UNA cosa atómica. Tu trabajo es ser un asistente que opera paso a paso, no un script que dispara todo de golpe. Pasos chicos te dan: (a) confirmar que el paso anterior funcionó antes del siguiente, (b) reaccionar a errores en cuanto aparecen, (c) feedback visible al usuario para que vea progreso, (d) la posibilidad de que el usuario te frene a mitad y reajuste.

HARD LIMITS (impuestos por el CLI, te van a devolver ERROR si los violás):
- `bash` máximo UN `&&` (acción + verificación corta, ej: `mkdir -p /x && ls /x`). El CLI rechaza 2+ operadores `&&`/`||`/`;` y te obliga a partir.
- `bash` no puede mezclar 2+ "fases" (install, clone, mkdir, serve, start, expose, ngrok http) en un solo comando. El CLI rechaza eso y te obliga a tool calls separados.
- NUNCA hagas `brew install A && brew install B && python -m … &`. Eso son 3 bash separados.
- Operaciones independientes (varios `read_file` de archivos distintos) SÍ podés emitirlas en paralelo en un solo turno — pero cada una es su propia tool call, no un megacomando shell.

## 4b. SIEMPRE narrá el siguiente paso ANTES de invocarlo
Antes de cada tool call decí en una frase corta qué vas a hacer. No es un plan complejo, es un anuncio mínimo:
- "Primero voy a ver si ngrok está." → `bash("which ngrok")`
- "Ahora creo el index.html." → `write_file(...)`
- "Arranco el servidor." → `bash("nohup python3 -m http.server 8080 &")`
- "Verifico que responda." → `bash("curl -fsSL http://127.0.0.1:8080/")`

Una línea, una tool, una espera. Esa es la cadencia.

## 4c. Tareas multi-paso → plan numerado al inicio
Si el pedido tiene ≥3 acciones (instalar X, arrancar Y, exponer Z):
1. Línea 1: plan numerado corto.
2. Línea 2: "Empiezo con (1)." → invocá la primera tool, NADA MÁS.
3. Después del resultado decidís el siguiente.

NUNCA intentes "hacer todo de un solo embriónazo". Pasitos chicos, observación entre cada uno. Esa es la diferencia entre un asistente real y un script roto que se cae al primer error.

## 4d. NO TE DETENGAS HASTA TERMINAR LA TAREA
Crítico. Una vez que arrancaste un plan, tu obligación es completarlo. Cada turno tuyo es uno de estos:
- Tool call que avanza al siguiente paso del plan, O
- Mensaje final breve ("listo, X creado, Y arriba en URL Z, verificación pasó").

NO existe el "ya te dejo aquí para que sigas vos" en medio de una tarea técnica. Si llegaste a 80% no le digas al usuario "ahora podés correr el último paso" — corré vos el último paso. Si una tool falla, NO termines con "intentá tú"; ajustás y reintentás con corrección. La única forma legítima de terminar antes del goal es:
- El usuario corrige a media tarea (entonces seguís lo nuevo).
- Un permiso fue denegado y no hay alternativa.
- El error es estructural (paquete no existe en este OS, etc.) — entonces decilo claro y proponé alternativa.

Si una iteración devuelve solo prosa sin tool call, eso es un BUG tuyo — el agente loop te va a re-llamar; usa ese turno para hacer la siguiente tool concreta.

## 4e. TAREAS LARGAS — corré en background y mantené el hilo
Para cualquier cosa que tarde más de ~10 segundos en producir resultado (servidores HTTP, ngrok, builds, watchers, downloaders, ML training, syncs grandes): NO uses `bash` con `&` — eso cuelga el CLI. Usá `bash_background(cmd, label)` que:
- Lanza el proceso desprendido (sigue vivo cuando salgas de loud)
- Devuelve PID + path del log al toque
- Te deja consultar con `job_status(label)` cuántas líneas de log salieron, si sigue vivo, qué dijo
- Y matar con `job_stop(label)` cuando quieras

Patrón:
1. `bash_background("python3 -m http.server 1002 --directory /tmp/x", label="http-1002")` → devuelve PID + early output
2. *Esperás 1-2 turnos.* Si necesitás verificar: `bash("curl -fsSL http://127.0.0.1:1002/")` o `job_status("http-1002", tail_lines=20)`
3. Cuando el usuario quiera pararlo: `job_stop("http-1002")`

Esto es lo que hace que LOUD-portable jale verdadero peso local: tareas pesadas viven en TU máquina, no se pierde el hilo aunque cierres el chat.

## 5. Si una tool falla, leé el error y CORREGÍ
Nunca repitas el mismo comando idéntico esperando otro resultado. Lee `stderr`/error, ajustá los argumentos, intentá una alternativa. Si una ruta no existe, `ls` el directorio padre. Si un paquete falta, instalalo primero.

## 6. Destructivo
- Para `rm`, `sudo`, `mv`, `curl|sh`, force-push, `drop table`, `chmod -R`, etc. — el CLI pide confirmación al usuario automáticamente. Vos sólo invocás la tool.
- NUNCA hagas push a `main` con `--force`. NUNCA hagas `git reset --hard` sin avisar. NUNCA borres archivos del usuario sin razón clara.
- NO ejecutes comandos que filtren credenciales (`cat ~/.env`, `printenv | grep KEY`, etc.) salvo que el usuario explícitamente lo pida.

## 7. Diagnóstico de bugs
Reproducí → aislá → arreglá → verificá. Para un bug: leé el archivo donde está → entendé qué hace → patch quirúrgico → corré el test o el comando que lo reproduce.

## 8. Convenciones del proyecto
Si estás en una repo, leé `README.md`, `package.json`/`pyproject.toml`/`Cargo.toml`, mirá la estructura con `ls` o `glob`. Respetá el estilo existente. NO introduzcas dependencias nuevas, frameworks ni capas de abstracción salvo que el usuario lo pida.

## 9. No alucines
Si no viste un archivo con `read_file` o `ls`, asumí que no existe. Si no corriste el test, no afirmes que pasa. Si no leíste la doc, no inventes la API. Cuando dudes, abrí una tool y comprobá.

## 10. Comunicación
- Antes del primer tool call: una sola frase diciendo qué vas a hacer.
- Durante: mensajes breves entre tools sólo si cambiás de rumbo o encontrás algo inesperado.
- Al final: 1-2 frases con qué cambió y qué sigue. Nada de resúmenes largos — el usuario ya vio el diff.
- Código siempre en fences markdown con tag (```python, ```bash, ```js, etc.).
- Cuando un usuario pida algo "en raw" o "copiable", devolvelo dentro de un fence ```text sin prosa alrededor.

## 11. Privacidad del terminal — INVIOLABLE
Estás en MODO TERMINAL. No tenés acceso al brain corporativo. **Esa función es exclusivamente para la web admin y NO está disponible desde el CLI ni siquiera para usuarios admin** — es una política de privacidad dura, no un toggle. Tu conocimiento = tu entrenamiento + lo que descubrís con tools en TIEMPO REAL. No hay contexto traído de chats anteriores ni de otros usuarios. Cada conversación es su propio sandbox. Si el usuario te pregunta cómo activar el brain desde terminal: respondé que no se puede, que esa función vive solo en la web admin.

## 12. Cuando NO necesites una tool
Saludos, preguntas conceptuales abstractas, código que no toca el sistema del usuario, opiniones técnicas — respondé directo sin invocar tools. Las tools son para tocar la máquina, no para chatear.

# FLUJOS EJEMPLO

**"qué Python tengo"** (1 acción simple):
- `bash("python3 --version && which python3")` ← OK porque es un read + un read, sin riesgo.
- Respondé: "Tienes Python 3.14.5 en /opt/homebrew/bin/python3."

**"arranca un hello-world local y exponlo con ngrok"** (multi-paso CORRECTO):
1. Plan en una línea: "Voy a: 1) crear el index.html, 2) arrancar python http.server, 3) arrancar ngrok, 4) leer la URL pública."
2. `write_file("/tmp/loud-www/index.html", "<h1>Hello World</h1>")` ← paso 1, una tool.
3. *Esperar el resultado.* Si OK →
4. `bash("nohup python3 -m http.server 8080 --directory /tmp/loud-www > /tmp/http.log 2>&1 &")` ← paso 2.
5. `bash("curl -s http://127.0.0.1:8080/")` ← verificación.
6. `bash("nohup ngrok http 8080 --log=stdout > /tmp/ngrok.log 2>&1 &")` ← paso 3.
7. `bash("sleep 2 && curl -s http://127.0.0.1:4040/api/tunnels")` ← leer la URL.
8. Sacar el `public_url` del JSON y devolverlo al usuario.

**MAL — no hagas esto** (megachain que se enreda):
- `bash("brew install ngrok && brew install python && mkdir /tmp/x && echo hello > /tmp/x/index.html && python -m http.server 8080 & ngrok http 8080 &")` ← demasiadas cosas, si una falla no podés diagnosticar.

**"arregla el bug en main.py:42"** (read → edit → verify):
1. `read_file("main.py")` ← entender el contexto.
2. `edit_file("main.py", old, new)` ← patch quirúrgico.
3. `bash("python main.py")` ← verificar.

**"instalá hackingtool"** (clone → cd → install, 3 pasos separados):
1. `bash("git clone https://github.com/Z4nzu/hackingtool /tmp/hackingtool")`
2. `bash("ls /tmp/hackingtool")` ← confirmar que clonó.
3. `bash("cd /tmp/hackingtool && sudo bash install.sh")` ← el CLI pide [y/n] al usuario por el sudo.

**"dame en raw el contenido de .gitignore"** → `read_file(".gitignore")` → respondé con SOLO el contenido dentro de ```text```.

Pensá por cortes. Pasito por pasito. Operá."""


# ───────────────────── Auth ─────────────────────

def load_auth() -> dict:
    if not AUTH_FILE.exists():
        return {}
    try:
        return json.loads(AUTH_FILE.read_text())
    except Exception:
        return {}


def save_auth(data: dict) -> None:
    AUTH_FILE.write_text(json.dumps(data, indent=2))
    try:
        os.chmod(AUTH_FILE, 0o600)
    except Exception:
        pass  # Windows


def clear_auth() -> None:
    if AUTH_FILE.exists():
        AUTH_FILE.unlink()


def get_token() -> str:
    return load_auth().get("token", "")


async def cmd_login(cfg: dict, identifier: str | None = None, password: str | None = None) -> bool:
    """Browser-based device-flow login. Same UX as Claude Code / gh CLI:
      1. We open the browser to a verification URL with a session code
      2. User logs in on the web (or is already logged in) and clicks "Approve"
      3. We poll the server until the token shows up, then save it locally

    Falls back to printing the URL if the browser can't open."""
    import webbrowser

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(f"{cfg['api_url']}/v1/auth/cli/init")
            if r.status_code != 200:
                cprint(f"  · couldn't init CLI session: {r.status_code} {r.text[:200]}", C.RED)
                return False
            init = r.json()
    except Exception as e:
        cprint(f"  · network error: {e}", C.RED)
        return False

    sid  = init["session_id"]
    code = init["code"]
    url  = init["verification_url"]

    cprint("", "")
    cprint(f"  ┌─ LOUD login ───────────────────────────────────────────────────", C.BRAND, bold=True)
    cprint(f"  │", C.BRAND)
    cprint(f"  │  Para finalizar el login, abre esta URL en tu navegador:", C.BRAND)
    cprint(f"  │", C.BRAND)
    cprint(f"  │    {url}", C.BRAND, bold=True)
    cprint(f"  │", C.BRAND)
    cprint(f"  │  Código de verificación (debe coincidir en pantalla):", C.BRAND)
    cprint(f"  │    {code}", C.BRAND, bold=True)
    cprint(f"  │", C.BRAND)
    cprint(f"  └─ esperando aprobación…  (Ctrl+C cancela)", C.BRAND)
    cprint("", "")

    # Try to open the browser; ignore failure (user can still copy/paste)
    try:
        webbrowser.open(url, new=2)
    except Exception:
        pass

    # Poll the server every 2s until approved or expired
    deadline = time.time() + 900  # 15 min match server TTL
    spinner = "⣷⣯⣟⡿⢿⣻⣽⣾"; i = 0
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            while time.time() < deadline:
                # spinner tick
                sys.stdout.write(f"\r  {C.BRAND}{spinner[i % len(spinner)]}{C.RESET}  polling…  ")
                sys.stdout.flush()
                i += 1
                try:
                    r = await client.get(f"{cfg['api_url']}/v1/auth/cli/poll", params={"session_id": sid})
                    if r.status_code == 200:
                        st = r.json()
                        if st.get("status") == "approved":
                            sys.stdout.write("\r" + " " * 60 + "\r")
                            save_auth({"token": st["token"], "user": st["user"], "api_url": cfg["api_url"]})
                            u = st["user"]
                            cprint(f"  ✓ entraste como {u.get('username') or u['email']} ({u['role']})", C.GREEN)
                            return True
                        if st.get("status") in ("expired", "denied"):
                            sys.stdout.write("\r" + " " * 60 + "\r")
                            cprint(f"  · login {st.get('status')}", C.RED)
                            return False
                except Exception:
                    pass
                await asyncio.sleep(2.0)
    except KeyboardInterrupt:
        sys.stdout.write("\r" + " " * 60 + "\r")
        cprint("  · login cancelado", C.YELLOW)
        return False

    sys.stdout.write("\r" + " " * 60 + "\r")
    cprint("  · login expiró (15 min). Corre `loud login` de nuevo.", C.RED)
    return False


async def cmd_logout() -> None:
    clear_auth()
    cprint("  ✓ sesión cerrada", C.GREEN)


async def cmd_setup_local(cfg: dict) -> int:
    """Install Ollama on this machine + pull the local model so LOUD can run
    inference locally (zero network latency, uses this machine's CPU/GPU).
    Detects mac/linux/windows and uses the right package manager."""
    cprint("\n  Configurando LOUD local (corre el modelo en TU máquina)\n", C.BRAND, bold=True)

    # 1) Detect Ollama
    has_ollama = shutil.which("ollama") is not None
    if has_ollama:
        cprint("  ✓ ollama ya está instalado", C.GREEN)
    else:
        cprint("  · ollama no está instalado. Voy a instalarlo:", C.YELLOW)
        if IS_MAC:
            if shutil.which("brew"):
                install_cmd = ["brew", "install", "ollama"]
            else:
                install_cmd = ["bash", "-c", "curl -fsSL https://ollama.com/install.sh | sh"]
        elif IS_WINDOWS:
            cprint("  · en Windows abre https://ollama.com/download y ejecuta el instalador.", C.YELLOW)
            cprint("    Luego vuelve a correr `loud setup local`.", C.GRAY)
            return 1
        else:
            install_cmd = ["bash", "-c", "curl -fsSL https://ollama.com/install.sh | sh"]
        cprint(f"  → {' '.join(install_cmd)}", C.GRAY)
        cprint("    Confirma con [y]es para proceder, [n]o para cancelar: ", C.YELLOW, end="")
        try: ans = (input().strip().lower() or "n")[0]
        except (EOFError, KeyboardInterrupt): return 1
        if ans != "y":
            cprint("  · cancelado por el usuario", C.YELLOW); return 1
        try:
            subprocess.run(install_cmd, check=True)
            cprint("  ✓ ollama instalado", C.GREEN)
        except subprocess.CalledProcessError as e:
            cprint(f"  ✗ instalación falló: {e}", C.RED); return 1

    # 2) Make sure ollama serve is running (on mac brew install doesn't auto-start)
    url = cfg.get("local_ollama_url", "http://127.0.0.1:11434")
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            await client.get(f"{url}/api/tags")
        cprint(f"  ✓ ollama corriendo en {url}", C.GREEN)
    except Exception:
        cprint(f"  · ollama no responde en {url}. Arrancando en background…", C.YELLOW)
        if IS_MAC and shutil.which("brew"):
            subprocess.Popen(["brew", "services", "start", "ollama"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await asyncio.sleep(3)
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                await client.get(f"{url}/api/tags")
            cprint("  ✓ ollama arrancó", C.GREEN)
        except Exception:
            cprint("  ✗ no pude arrancar ollama. Inicia manual con: `ollama serve`", C.RED)
            return 1

    # 3) Pull the local model
    model = cfg.get("local_model", "qwen2.5:3b")
    cprint(f"\n  · descargando modelo {model} (~2GB, primera vez puede tardar)…", C.BRAND)
    try:
        subprocess.run(["ollama", "pull", model], check=True)
        cprint(f"  ✓ {model} listo", C.GREEN)
    except subprocess.CalledProcessError as e:
        cprint(f"  ✗ pull falló: {e}", C.RED); return 1

    # 4) Switch mode to auto so the CLI uses local when up
    cfg["mode"] = "auto"
    save_config(cfg)
    cprint(f"\n  ✓ modo cambiado a `auto` — el CLI usará tu máquina cuando ollama esté arriba", C.GREEN, bold=True)
    cprint(f"    Cámbialo con: /mode cloud · /mode local · /mode auto", C.GRAY)
    return 0


async def cmd_update(cfg: dict) -> int:
    """Self-update: detect how the user installed LOUD and pull the latest."""
    cprint("\n  Buscando actualización…", C.BRAND, bold=True)

    # 1) Check latest version from GitHub
    latest = None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://raw.githubusercontent.com/loud-codes/loud-cli/main/cli/loud.py",
                headers={"User-Agent": "loud-cli-updater"},
            )
            m = re.search(r'__version__\s*=\s*[\'"]([^\'"]+)[\'"]', r.text)
            if m:
                latest = m.group(1)
    except Exception as e:
        cprint(f"  · no pude consultar GitHub: {e}", C.YELLOW)

    if latest:
        cprint(f"  · instalado: {__version__}    latest: {latest}", C.GRAY)
        if latest == __version__:
            cprint("  ✓ ya estás en la última versión", C.GREEN)
            return 0
    else:
        cprint("  · no pude detectar la versión más reciente — actualizo de todos modos.", C.GRAY)

    # 2) Detect install method
    self_path = Path(__file__).resolve()
    brew_prefix = None
    try:
        brew_prefix = subprocess.check_output(["brew", "--prefix"], text=True, timeout=5).strip()
    except Exception:
        pass
    via_brew = bool(brew_prefix and str(self_path).startswith(brew_prefix))
    via_curl = ".loud/install/src" in str(self_path)

    if via_brew:
        cprint("  · instalado vía Homebrew — corriendo `brew upgrade loud`", C.BRAND)
        try:
            subprocess.run(["brew", "update"], check=False)
            subprocess.run(["brew", "upgrade", "loud"], check=True)
            cprint("  ✓ actualizado vía Homebrew", C.GREEN)
            return 0
        except subprocess.CalledProcessError as e:
            cprint(f"  · brew upgrade falló: {e}", C.RED)
            return 1

    if via_curl or IS_WINDOWS:
        # Re-run the installer
        if IS_WINDOWS:
            cmd = ["powershell", "-Command", "iwr -useb https://loud.codes/install.ps1 | iex"]
        else:
            cmd = ["bash", "-c", "curl -fsSL https://loud.codes/install.sh | bash"]
        cprint("  · actualizando con el instalador oficial…", C.BRAND)
        try:
            subprocess.run(cmd, check=True)
            cprint("  ✓ actualizado", C.GREEN)
            return 0
        except subprocess.CalledProcessError as e:
            cprint(f"  · update script falló: {e}", C.RED)
            return 1

    # Manual git checkout — try to fetch + pull
    src = self_path.parent.parent  # cli/loud.py → repo root
    git = src / ".git"
    if git.exists():
        cprint(f"  · git pull en {src}", C.BRAND)
        try:
            subprocess.run(["git", "-C", str(src), "fetch", "--quiet"], check=True)
            subprocess.run(["git", "-C", str(src), "pull", "--ff-only"], check=True)
            cprint("  ✓ actualizado vía git", C.GREEN)
            return 0
        except subprocess.CalledProcessError as e:
            cprint(f"  · git pull falló: {e}", C.RED)
            return 1

    cprint("  · no pude detectar el método de instalación.", C.YELLOW)
    cprint("    Reinstala manual:", C.GRAY)
    cprint("      macOS/Linux:  curl -fsSL https://loud.codes/install.sh | bash", C.BRAND)
    cprint("      Windows:      iwr -useb https://loud.codes/install.ps1 | iex", C.BRAND)
    cprint("      Homebrew:     brew upgrade loud", C.BRAND)
    return 1


async def _maybe_check_update_async() -> None:
    """Background check: poll latest version once every 24h and stash the
    answer in ~/.loud/update_check.json. If a new version is available, the
    banner will surface it on the next launch."""
    check_file = LOUD_DIR / "update_check.json"
    try:
        if check_file.exists():
            data = json.loads(check_file.read_text())
            if time.time() - data.get("checked_at", 0) < 86400:
                return
    except Exception:
        pass
    try:
        async with httpx.AsyncClient(timeout=4) as client:
            r = await client.get(
                "https://raw.githubusercontent.com/loud-codes/loud-cli/main/cli/loud.py",
            )
            m = re.search(r'__version__\s*=\s*[\'"]([^\'"]+)[\'"]', r.text)
            latest = m.group(1) if m else __version__
        # Only persist if newer than installed — otherwise we just clear the
        # cache so the banner stays clean.
        if _semver_tuple(latest) > _semver_tuple(__version__):
            check_file.write_text(json.dumps({"checked_at": time.time(), "latest": latest}))
        else:
            try: check_file.unlink()
            except Exception: pass
    except Exception:
        pass


def _semver_tuple(v: str) -> tuple[int, ...]:
    """'0.7.3' → (0, 7, 3). Non-numeric chunks become 0 so we never crash."""
    out = []
    for part in (v or "").strip().lstrip("v").split("."):
        try: out.append(int(re.sub(r"[^0-9].*$", "", part) or "0"))
        except Exception: out.append(0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def _cached_latest_version() -> str | None:
    """Return the cached "latest" string ONLY if it's strictly newer than the
    installed version. Stale or older caches are wiped so we never show a
    bogus "new version available: <older>" pill in the banner."""
    try:
        check_file = LOUD_DIR / "update_check.json"
        data = json.loads(check_file.read_text())
        latest = data.get("latest")
        if latest and _semver_tuple(latest) > _semver_tuple(__version__):
            return latest
        # Cached version is equal or older than installed → toss it.
        if latest and _semver_tuple(latest) < _semver_tuple(__version__):
            try: check_file.unlink()
            except Exception: pass
        return None
    except Exception:
        return None


async def cmd_whoami(cfg: dict) -> int:
    token = get_token()
    if not token:
        cprint("  · no estás logueado. Corre: loud login", C.YELLOW)
        return 1
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{cfg['api_url']}/v1/me", headers={"Authorization": f"Bearer {token}"})
    if r.status_code == 200:
        u = r.json()
        cprint(f"  · {u.get('username') or u['email']} ({u['role']}) — id {u['id']}", C.GREEN)
        return 0
    cprint(f"  · {r.text[:200]}", C.RED)
    return 1


# ───────────────────── Setup wizard ─────────────────────

async def setup_wizard(cfg: dict) -> None:
    """First-run experience: pick API url, login, accept permission mode."""
    cprint("\n  Primera vez. Vamos a configurar LOUD en 30 segundos.\n", C.BRAND, bold=True)

    cprint(f"  Servidor LOUD (default {cfg['api_url']}): ", C.GRAY, end="")
    url = input().strip()
    if url:
        cfg["api_url"] = url.rstrip("/")
        save_config(cfg)

    cprint(f"\n  Modo de permisos:", C.BRAND, bold=True)
    cprint("    [a]sk   — preguntar antes de escribir/borrar/ejecutar  (recomendado)", C.GRAY)
    cprint("    [y]olo  — sin preguntar (riesgo: el agente puede tocar todo)", C.GRAY)
    cprint("    [s]afe  — bloquea cualquier acción destructiva", C.GRAY)
    cprint("  → [a/y/s] ", C.BRAND, bold=True, end="")
    mode = (input().strip().lower() or "a")[0]
    cfg["permission_mode"] = {"a": "ask", "y": "yolo", "s": "safe"}.get(mode, "ask")
    save_config(cfg)

    cprint(f"\n  Login a {cfg['api_url']}", C.BRAND, bold=True)
    await cmd_login(cfg)


# ───────────────────── Chat / streaming ─────────────────────

async def _ollama_local_ready(cfg: dict) -> tuple[bool, str]:
    """Probe local Ollama. Returns (is_up, info). info is a short status string."""
    url = cfg.get("local_ollama_url", "http://127.0.0.1:11434")
    model = cfg.get("local_model", "qwen2.5:3b")
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            r = await client.get(f"{url}/api/tags")
            if r.status_code != 200:
                return False, f"ollama {r.status_code}"
            tags = r.json().get("models", [])
            names = {m.get("name", "").split(":")[0] for m in tags} | {m.get("name", "") for m in tags}
            if model not in {m.get("name") for m in tags} and model.split(":")[0] not in names:
                return False, f"ollama up but {model} not pulled"
            return True, f"ollama up · {model}"
    except Exception as e:
        return False, f"ollama down: {type(e).__name__}"


async def _stream_chat_local(cfg: dict, messages: list[dict]):
    """Talk directly to local Ollama at 127.0.0.1:11434. Pure local compute,
    zero network latency. Server brain/RAG is NOT used here — for that the
    user picks 'cloud' or 'auto' mode."""
    url = cfg.get("local_ollama_url", "http://127.0.0.1:11434")
    model = cfg.get("local_model", "qwen2.5:3b")
    payload = {
        "model": model,
        "messages": messages,
        "tools": TOOLS_SCHEMA,
        "stream": True,
        "keep_alive": "30m",
        "options": {"num_predict": -1},
    }
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", f"{url}/api/chat", json=payload) as r:
                if r.status_code >= 400:
                    body = await r.aread()
                    yield {"error": f"local ollama {r.status_code}: {body.decode(errors='replace')[:200]}"}
                    return
                pending_tcs = []   # collected across the stream so we can emit the assistant_tool_call event once
                async for line in r.aiter_lines():
                    line = (line or "").strip()
                    if not line:
                        continue
                    try:
                        j = json.loads(line)
                    except Exception:
                        continue
                    msg = j.get("message") or {}
                    # Ollama emits tool_calls inside the message object. Surface them as the same
                    # events the cloud agent loop produces so the existing CLI handlers work unchanged.
                    if msg.get("tool_calls"):
                        for tc in msg["tool_calls"]:
                            fn   = (tc.get("function") or {}).get("name", "")
                            args = (tc.get("function") or {}).get("arguments") or {}
                            if isinstance(args, str):
                                try: args = json.loads(args)
                                except Exception: args = {}
                            pending_tcs.append(tc)
                            if pending_tcs and len(pending_tcs) == 1:
                                yield {"event": "assistant_tool_call",
                                       "content": msg.get("content", "") or "",
                                       "tool_calls": pending_tcs}
                            yield {"event": "tool_call", "name": fn, "args": args, "tool_call_id": tc.get("id")}
                        # Don't fall through to emit content for the same line.
                        continue
                    # Plain content chunk
                    content = msg.get("content", "") if msg else ""
                    if content:
                        yield {"message": {"content": content}, "done": False}
                    if j.get("done"):
                        yield {"done": True, "done_reason": j.get("done_reason", "stop")}
    except Exception as e:
        yield {"error": f"local network: {type(e).__name__}: {e}"}


async def _stream_chat_cloud(cfg: dict, messages: list[dict]):
    """Original cloud path — talk to api.loud.codes with brain + tools.
    Memory is OFF by default so old chats can't leak into this one. RAG is
    ON because the brain has curated knowledge, but the server gates it by
    relevance so casual prompts ('hola') don't trigger spurious context.

    TERMINAL PRIVACY MODEL: the CLI HARDCODES use_rag=False and use_memory=
    False. Terminal mode = direct PC control + the model's own training
    knowledge. The brain (curated docs from the admin UI) is web-side only,
    not exposed to the CLI even for admin users. This is a hard policy."""
    token = get_token()
    if not token:
        yield {"error": "not_logged_in"}
        return
    payload = {
        "model":      cfg["model"],
        "messages":   messages,
        "tools":      TOOLS_SCHEMA,
        "chat_id":    cfg.get("_chat_id"),
        "use_rag":    False,    # hardcoded — brain is web-only
        "use_memory": False,    # hardcoded — no cross-chat leakage
        "use_web":    True,
        "stream":     True,
    }
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/x-ndjson"}
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", f"{cfg['api_url']}/v1/chat", json=payload, headers=headers) as r:
                if r.status_code == 401:
                    yield {"error": "auth_expired"}
                    return
                if r.status_code >= 400:
                    body = await r.aread()
                    yield {"error": f"http {r.status_code}: {body.decode(errors='replace')[:300]}"}
                    return
                buf = ""
                async for chunk in r.aiter_text():
                    buf += chunk
                    while True:
                        nl = buf.find("\n")
                        if nl < 0:
                            break
                        line = buf[:nl].strip()
                        buf = buf[nl + 1:]
                        if not line:
                            continue
                        try:
                            yield json.loads(line)
                        except Exception:
                            yield {"raw": line}
                if buf.strip():
                    try:
                        yield json.loads(buf.strip())
                    except Exception:
                        yield {"raw": buf.strip()}
    except Exception as e:
        yield {"error": f"network: {type(e).__name__}: {e}"}


async def stream_chat(cfg: dict, messages: list[dict]):
    """Route the chat through local Ollama or the cloud API based on cfg.mode.
    - mode=local → only local Ollama (fast, this machine's compute, no RAG)
    - mode=auto  → probe local first; if it's not ready, fall back to cloud
    - mode=cloud → original behavior (full brain on server)"""
    mode = cfg.get("mode", "cloud")
    if mode == "local":
        ok, info = await _ollama_local_ready(cfg)
        if not ok:
            yield {"error": f"local mode pero {info}. Corre `loud setup local` o usa /mode auto"}
            return
        async for ev in _stream_chat_local(cfg, messages):
            yield ev
        return
    if mode == "auto":
        ok, info = await _ollama_local_ready(cfg)
        if ok:
            async for ev in _stream_chat_local(cfg, messages):
                yield ev
            return
        # Silent fallback to cloud — surface a one-line note so the user knows.
        yield {"event": "mode_fallback", "info": info}
    async for ev in _stream_chat_cloud(cfg, messages):
        yield ev


_STREAM_STATE = {"col": 0, "indent_done": False, "in_fence": False}


def terminal_layout() -> tuple[int, int, int]:
    """Returns (cols, content_width, left_pad). Content is rendered inside a
    centered band capped at 92 columns so the layout stays uniform whether the
    user widens or narrows the terminal — identical to how Claude Code keeps
    its content centered. Mirror of the banner geometry below."""
    try:
        cols = shutil.get_terminal_size((100, 24)).columns
    except Exception:
        cols = 100
    content_w = min(max(cols - 4, 40), 92)
    left_pad = max(2, (cols - content_w) // 2)
    return cols, content_w, left_pad


def stream_reset() -> None:
    _STREAM_STATE["col"] = 0
    _STREAM_STATE["indent_done"] = False
    _STREAM_STATE["in_fence"] = False


async def typewriter_write(text: str, color: str = "") -> None:
    """Soft-wrap + centered layout. We DO NOT delay per-char anymore — the
    network rate is the bottleneck and the previous artificial 12ms sleep
    every 6 chars was throttling output to ~50 chars/sec. Now we flush every
    block immediately so the user sees tokens as fast as the model emits them.
    Code fences (```...```) get the same indent but no soft-wrap inside (code
    formatting decides line breaks)."""
    text = scrub(text)
    if not text:
        return
    cols, content_w, left_pad = terminal_layout()
    pad = " " * left_pad
    if color:
        sys.stdout.write(color)
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        # Newline → reset line state and emit indent on the next char.
        if ch == "\n":
            sys.stdout.write("\n")
            _STREAM_STATE["col"] = 0
            _STREAM_STATE["indent_done"] = False
            i += 1
            continue
        # Detect entering/leaving a triple-backtick fence.
        if ch == "`" and text[i:i+3] == "```":
            if not _STREAM_STATE["indent_done"]:
                sys.stdout.write(pad)
                _STREAM_STATE["indent_done"] = True
                _STREAM_STATE["col"] = 0
            sys.stdout.write("```")
            _STREAM_STATE["col"] += 3
            _STREAM_STATE["in_fence"] = not _STREAM_STATE["in_fence"]
            i += 3
            continue
        # First visible char of a line → emit left pad.
        if not _STREAM_STATE["indent_done"]:
            # Skip leading spaces at line start (they create double-indent).
            if ch == " " and _STREAM_STATE["col"] == 0:
                i += 1
                continue
            sys.stdout.write(pad)
            _STREAM_STATE["indent_done"] = True
            _STREAM_STATE["col"] = 0
        # Soft-wrap on space when past content_w. Skip inside code fences.
        if (not _STREAM_STATE["in_fence"]) and _STREAM_STATE["col"] >= content_w and ch == " ":
            sys.stdout.write("\n" + pad)
            _STREAM_STATE["col"] = 0
            i += 1
            continue
        sys.stdout.write(ch)
        _STREAM_STATE["col"] += 1
        i += 1
    if color:
        sys.stdout.write(C.RESET)
    sys.stdout.flush()
    # Yield once at the end so the network reader coroutine can pull the next
    # chunk while we wait. NO per-char sleep — the model's emit rate sets the
    # pace, not us.
    await asyncio.sleep(0)


# ───────────────────── Agent loop ─────────────────────

class StopAgent(Exception):
    pass


# Stupid-funny verbs for the spinner. The user wants the CLI to feel alive
# while it thinks, so each turn we pick a random one. Known operations
# (bash/read_file/write_file/etc) get their own labels — see _TOOL_LABEL.
_LOADING_VERBS = [
    "Bamboozling",     # caos divertido
    "Chunkulating",    # marca de la casa
    "Neuronizing",     # como si ajustaran neuronas
    "Loudifying",      # branding del proyecto
    "Synapsing",       # disparando conexiones
    "Voltifying",      # eléctrico
    "Cogitating",      # pensar fancy
    "Quantumizing",    # ciencia ficción
    "Pulsating",       # late
    "Vibesynthing",    # vibras
]
_TOOL_LABEL = {
    "bash":            "Bashing",
    "bash_background": "Backgrounding",
    "job_status":      "Inspecting",
    "job_list":        "Listing-jobs",
    "job_stop":        "Stopping",
    "ssh":             "Tunneling",
    "read_file":       "Reading",
    "write_file":      "Writing",
    "edit_file":       "Updating",
    "glob":            "Globbing",
    "grep":            "Searching",
    "ls":              "Listing",
    "http_get":        "Fetching",
}

# Pretty display names for the "● Tool(args)" call header — matches the
# Claude-Code style. Falls back to the raw tool name when not in this map.
_TOOL_DISPLAY = {
    "bash":            "Bash",
    "bash_background": "Background",
    "job_status":      "JobStatus",
    "job_list":        "JobList",
    "job_stop":        "JobStop",
    "ssh":             "SSH",
    "read_file":       "Read",
    "write_file":      "Write",
    "edit_file":       "Update",
    "glob":            "Glob",
    "grep":            "Search",
    "ls":              "List",
    "http_get":        "Fetch",
}


def _format_tool_call_header(name: str, args: dict) -> str:
    """Render '● Bash(python3 --version)' style header. Args are flattened to
    a single-line preview that's easy to scan."""
    display = _TOOL_DISPLAY.get(name, name.capitalize())
    if name == "bash":
        arg_str = (args.get("cmd") or "").strip().replace("\n", " ⏎ ")
    elif name == "bash_background":
        arg_str = f"{args.get('label','?')} → {(args.get('cmd') or '').strip()}"
    elif name in ("job_status", "job_stop"):
        arg_str = args.get("label", "?")
    elif name == "job_list":
        arg_str = ""
    elif name == "ssh":
        arg_str = f"{args.get('host','?')}: {(args.get('cmd') or '').strip()}"
    elif name in ("read_file", "write_file", "edit_file"):
        arg_str = args.get("path", "")
    elif name == "glob":
        arg_str = args.get("pattern", "")
        if args.get("path") and args.get("path") not in (".", ""): arg_str += f" in {args['path']}"
    elif name == "grep":
        arg_str = args.get("pattern", "")
        if args.get("path") and args.get("path") not in (".", ""): arg_str += f" in {args['path']}"
    elif name == "ls":
        arg_str = args.get("path") or "."
    elif name == "http_get":
        arg_str = args.get("url", "")
    else:
        arg_str = json.dumps(args, ensure_ascii=False)
    return f"{display}({shorten(arg_str, 100)})"


def _print_tool_block(name: str, args: dict, result: str, max_output_lines: int = 8) -> None:
    """Render a tool invocation + its result in Claude-Code style:

      ● Bash(python3 --version)
        ⎿  Python 3.14.5
           /opt/homebrew/bin/python3

    Output is indented under a `⎿` continuation, truncated cleanly when long.
    """
    header = _format_tool_call_header(name, args)
    sys.stdout.write(f"  {C.BRAND}●{C.RESET} {C.BOLD}{header}{C.RESET}\n")
    if not result:
        sys.stdout.write(f"     {C.GRAY}⎿  (no output){C.RESET}\n"); sys.stdout.flush(); return
    out = result.rstrip()
    lines = out.split("\n")
    shown = lines[:max_output_lines]
    extra = len(lines) - len(shown)
    for i, ln in enumerate(shown):
        prefix = f"     {C.GRAY}⎿  " if i == 0 else f"        "
        sys.stdout.write(f"{prefix}{C.GRAY}{shorten(ln, 160)}{C.RESET}\n")
    if extra > 0:
        sys.stdout.write(f"        {C.GRAY}… +{extra} líneas más{C.RESET}\n")
    sys.stdout.flush()
_SPINNER_FRAMES = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]


def _pulse_logo_frame(tick: int) -> str:
    """Return a 4-char rendering of L O U D where the 'L' pulses through
    intensities so it looks like it's breathing while loading."""
    # Pulse pattern: 0..7..0 over a slow cycle (10 ticks).
    pulse = [38, 41, 45, 49, 51, 49, 45, 41, 38, 35][tick % 10]
    # ANSI 256-color brand greens; brighter shade = higher pulse value.
    intensities = {35: 22, 38: 28, 41: 34, 45: 76, 49: 119, 51: 154}
    color_idx = intensities.get(pulse, 119)
    L = f"\033[38;5;{color_idx}m\033[1mL\033[0m"
    rest = "\033[38;5;149m\033[1mOUD\033[0m"
    return f"{L}{rest}"


class LoadingSpinner:
    """Centered, color-pulsing spinner that runs in the background while the
    model is generating. Caller drives it: start(label) → stop()."""
    def __init__(self, color: str = ""):
        self._task: asyncio.Task | None = None
        self._stop = False
        self._label = "Thinking"

    def set_label(self, label: str) -> None:
        self._label = label

    async def _loop(self) -> None:
        i = 0
        try:
            sys.stdout.write("\n")
            while not self._stop:
                logo = _pulse_logo_frame(i)
                frame = _SPINNER_FRAMES[i % len(_SPINNER_FRAMES)]
                line = f"  {logo} {C.BRAND}{frame}{C.RESET} {C.GRAY}{self._label}…{C.RESET}"
                # Erase line, redraw.
                sys.stdout.write("\r\033[2K" + line)
                sys.stdout.flush()
                i += 1
                await asyncio.sleep(0.08)
        finally:
            sys.stdout.write("\r\033[2K")
            sys.stdout.flush()

    def start(self, label: str | None = None) -> None:
        if label:
            self._label = label
        if self._task and not self._task.done():
            return
        self._stop = False
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop = True
        if self._task:
            try: await self._task
            except Exception: pass
            self._task = None


async def run_turn(cfg: dict, messages: list[dict], user_text: str) -> str:
    import random
    messages.append({"role": "user", "content": user_text})

    spinner = LoadingSpinner()

    for iteration in range(cfg["max_iterations"]):
        spinner.start(random.choice(_LOADING_VERBS))
        full_text = ""
        had_tool_call = False
        try:
            async for event in stream_chat(cfg, messages):
                if event.get("error"):
                    err = event["error"]
                    if err == "not_logged_in":
                        cprint("  · no estás logueado. Corre: loud login", C.YELLOW)
                        return ""
                    if err == "auth_expired":
                        cprint("  · sesión expirada. Corre: loud login", C.YELLOW)
                        clear_auth()
                        return ""
                    cprint(f"  · {err}", C.RED)
                    return ""
                if event.get("event") == "assistant_tool_call":
                    # Server tells us the assistant decided to call client-side
                    # tools. Mirror the assistant message in our history so the
                    # next /v1/chat call has the proper tool-call → tool-result
                    # sequence.
                    messages.append({
                        "role": "assistant",
                        "content": event.get("content", "") or "",
                        "tool_calls": event.get("tool_calls") or [],
                    })
                    continue
                if event.get("event") == "tool_call":
                    had_tool_call = True
                    name = event.get("name", "")
                    args = event.get("args") or {}
                    tc_id = event.get("tool_call_id")
                    # Client-side tool execution
                    if name in TOOL_FNS:
                        await spinner.stop()
                        # Claude-Code-style header: '● Bash(python3 --version)'
                        header = _format_tool_call_header(name, args)
                        sys.stdout.write(f"\n  {C.BRAND}●{C.RESET} {C.BOLD}{header}{C.RESET}\n"); sys.stdout.flush()
                        decision = request_permission(cfg, name, args)
                        if decision == "stop":
                            raise StopAgent()
                        if decision == "deny":
                            tool_msg = {"role": "tool", "content": f"ERROR: usuario denegó {name}"}
                            if tc_id: tool_msg["tool_call_id"] = tc_id
                            messages.append(tool_msg)
                            sys.stdout.write(f"     {C.YELLOW}⎿  denegado por el usuario{C.RESET}\n"); sys.stdout.flush()
                            continue
                        try:
                            result = await TOOL_FNS[name](**args)
                        except TypeError as e:
                            result = f"ERROR: bad args for {name}: {e}"
                        except Exception as e:
                            result = f"ERROR: {type(e).__name__}: {e}"
                        # Render result block (indented under ⎿ with line cap).
                        out = (result or "").rstrip()
                        lines = out.split("\n") if out else ["(no output)"]
                        shown = lines[:8]
                        extra = len(lines) - len(shown)
                        for i, ln in enumerate(shown):
                            prefix = f"     {C.GRAY}⎿  " if i == 0 else f"        "
                            sys.stdout.write(f"{prefix}{C.GRAY}{shorten(ln, 160)}{C.RESET}\n")
                        if extra > 0:
                            sys.stdout.write(f"        {C.GRAY}… +{extra} líneas más{C.RESET}\n")
                        sys.stdout.flush()
                        tool_msg = {"role": "tool", "content": result[:8000]}
                        if tc_id: tool_msg["tool_call_id"] = tc_id
                        messages.append(tool_msg)
                        # Re-arm the spinner for the next model turn with the
                        # tool-specific label so the user sees what's happening.
                        spinner.start(_TOOL_LABEL.get(name, name.capitalize()))
                    # Server-side tools (web_search, web_fetch) are handled by the API; we just log them
                    continue
                if event.get("event") == "tool_result":
                    # Server-side tool result (already handled in the API). Just log.
                    preview = event.get("preview", "")
                    if preview:
                        cprint(f"  ← {shorten(preview, 140)}", C.GRAY)
                    continue
                if event.get("event") == "enriching":
                    cprint("  · reforzando cerebro en background…", C.YELLOW)
                    continue
                if event.get("event") == "mode_fallback":
                    cprint(f"  · local no listo ({event.get('info','?')}) → usando cloud", C.GRAY)
                    continue
                if event.get("done"):
                    break
                chunk = (event.get("message") or {}).get("content", "")
                if chunk:
                    if not full_text:
                        await spinner.stop()
                        cprint("", "")
                        stream_reset()
                    full_text += chunk
                    if cfg.get("typewriter", True):
                        # Long assistant prose stays WHITE (default). Accent
                        # colors are reserved for status/tool/error lines.
                        await typewriter_write(chunk)
                    else:
                        sys.stdout.write(scrub(chunk))
                        sys.stdout.flush()
        except StopAgent:
            await spinner.stop()
            cprint("\n  · detenido por el usuario", C.YELLOW)
            return ""
        finally:
            await spinner.stop()

        # End of stream. If the assistant emitted any text, the turn is done.
        if full_text:
            cprint("", "")  # newline after typewriter
            messages.append({"role": "assistant", "content": full_text})
            return full_text
        # If only tool calls happened, loop again to give the model a turn to
        # synthesize a response using the tool outputs we just appended.
        if had_tool_call:
            continue
        # No text and no tool calls — bail.
        cprint("  · (sin respuesta)", C.YELLOW)
        return ""

    cprint("  · max_iterations alcanzado", C.YELLOW)
    return ""


# ───────────────────── Session persistence ─────────────────────

def load_session() -> list[dict]:
    if not SESSION_FILE.exists():
        return []
    try:
        return json.loads(SESSION_FILE.read_text())
    except Exception:
        return []


def save_session(messages: list[dict]) -> None:
    SESSION_FILE.write_text(json.dumps(messages, indent=2))


def reset_session() -> None:
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()


# ───────────────────── REPL ─────────────────────

def render_banner(cfg: dict) -> str:
    """Claude-Code-style welcome box with LOUD branding.

    Always shown when entering the REPL. Reflects whether the user is logged
    in or not — login is a `/login` slash command, never blocks startup."""
    user = load_auth().get("user", {})
    is_logged = bool(get_token() and user)
    who = user.get("username") or user.get("email") or "no login"
    role = user.get("role", "")
    cwd = str(Path.cwd())
    if len(cwd) > 48:
        cwd = "…" + cwd[-47:]
    W = 62
    G = C.BRAND
    R = C.RESET
    D = C.DIM
    B = C.BOLD
    Y = C.YELLOW
    # Center the banner box inside the terminal so its left edge stays at the
    # same column as the rest of the streamed content. terminal_layout caps the
    # content band at 92 cols and gives us the left padding to match.
    try:
        cols = shutil.get_terminal_size((100, 24)).columns
    except Exception:
        cols = 100
    box_w = W + 4  # inner padding + 2 borders
    LP = " " * max(2, (cols - box_w) // 2)

    logo_lines = [
        "██╗      ██████╗ ██╗   ██╗██████╗ ",
        "██║     ██╔═══██╗██║   ██║██╔══██╗",
        "██║     ██║   ██║██║   ██║██║  ██║",
        "██║     ██║   ██║██║   ██║██║  ██║",
        "███████╗╚██████╔╝╚██████╔╝██████╔╝",
        "╚══════╝ ╚═════╝  ╚═════╝ ╚═════╝ ",
    ]

    def row(content: str) -> str:
        visible = re.sub(r"\033\[[0-9;]*m", "", content)
        gap = max(0, W - len(visible))
        return f"{LP}{G}│{R} {content}{' ' * gap} {G}│{R}\n"

    top    = f"{LP}{G}╭{'─' * (W + 2)}╮{R}\n"
    bot    = f"{LP}{G}╰{'─' * (W + 2)}╯{R}\n"
    blank  = row("")
    sep    = f"{LP}{G}├{'─' * (W + 2)}┤{R}\n"

    out = [top, blank]
    for line in logo_lines:
        out.append(row(f"{B}{G}  {line}{R}"))
    out.append(blank)
    out.append(row(f"{B}✻ Welcome to LOUD{R} {D}— terminal-first AI · v{__version__}{R}"))
    out.append(blank)

    # ── Status block ──
    if is_logged:
        out.append(row(f"{D}status:{R}  {G}● signed in{R} as {G}{who}{R}{D}{' · ' + role if role else ''}{R}"))
    else:
        out.append(row(f"{D}status:{R}  {Y}● not signed in{R}  {D}— type {R}{B}{G}/login{R}{D} to start{R}"))
    out.append(row(f"{D}model:{R}   {G}{cfg['model']}{R}    {D}perms:{R}  {G}{cfg.get('permission_mode', 'ask')}{R}"))
    out.append(row(f"{D}cwd:{R}     {cwd}"))
    out.append(blank)
    out.append(sep)
    out.append(blank)

    # ── Useful links block ──
    out.append(row(f"{D}links{R}"))
    out.append(row(f"  {G}web{R}      https://loud.codes"))
    out.append(row(f"  {G}docs{R}     https://github.com/loud-codes/loud-cli#readme"))
    out.append(row(f"  {G}issues{R}   https://github.com/loud-codes/loud-cli/issues"))
    out.append(row(f"  {G}update{R}   {D}brew upgrade loud{R}  ·  {D}loud update{R}"))
    out.append(blank)
    out.append(row(f"{D}type {R}{B}{G}/help{R}{D} for commands · {R}{B}Esc{R}{D} stops the agent · {R}{B}Ctrl+C{R}{D} exits{R}"))
    latest = _cached_latest_version()
    if latest and latest != __version__:
        out.append(blank)
        out.append(row(f"{Y}↑ new version available: {latest}{R} {D}— run `loud update`{R}"))
    out.append(blank)
    out.append(bot)
    return "".join(out)


SLASH_HELP = """\
/help               muestra esta ayuda
/reset              borra el historial de la sesión actual
/model NAME         cambia modelo (actual: {model})
                    · loud-go (qwen 3b · rápido)
                    · loud-pro (qwen 7b · equilibrado)
                    · loud-ultra (qwen 14b · profundo)
                    · loud-2.0 (qwen 32b · LOUD 2.0 GPU only)
                    · loud-eye (qwen2-vl · imágenes/screenshots)
/tools              lista las tools que el agente puede llamar
/permissions        muestra/cambia el modo de permisos (ask/yolo/safe)
/mode MODE          motor de inferencia: cloud · local · auto
                    · cloud = api.loud.codes (full brain + RAG)
                    · local = ollama en TU máquina (cero latencia, sin RAG)
                    · auto  = local si está arriba, sino cloud
/setup local        instala Ollama + descarga el modelo local
/save FILE          exporta la conversación actual a un archivo .md
/open FILE          importa una conversación previa (.md o .json) al chat actual
/cwd                imprime el directorio actual
/login              inicia sesión (abre browser device-flow)
/logout             cierra sesión actual
/whoami             muestra el usuario logueado
/update             actualiza el CLI a la última versión (igual que `loud update`)
/version            versión actual del CLI
/exit               salir
"""


async def repl(cfg: dict) -> None:
    import uuid as _uuid
    sys_prompt = STATIC_SYSTEM_PROMPT
    messages = [{"role": "system", "content": sys_prompt}]
    # Each `loud` launch is a FRESH chat. Nothing is loaded from disk and
    # nothing is auto-saved when the REPL exits. The user explicitly invokes
    # /save <file> to export, and /open <file> to import a prior conversation.
    # This matches the privacy model: terminal chats are ephemeral by default.
    cfg["_chat_id"] = f"repl-{_uuid.uuid4().hex[:12]}"
    # Wipe any stale session file from a pre-0.8.4 install.
    if SESSION_FILE.exists():
        try: SESSION_FILE.unlink()
        except Exception: pass

    sys.stdout.write(render_banner(cfg))
    sys.stdout.flush()

    while True:
        try:
            cprint("loud❯ ", C.BRAND, bold=True, end="")
            user_text = input().strip()
        except (EOFError, KeyboardInterrupt):
            cprint("\n  · bye", C.GRAY)
            break
        if not user_text:
            continue

        # Slash commands
        if user_text.startswith("/"):
            cmd, *rest = user_text.split(maxsplit=1)
            arg = rest[0] if rest else ""
            if cmd == "/exit" or cmd == "/quit":
                break
            elif cmd == "/help":
                cprint(SLASH_HELP.format(model=cfg["model"]), C.BRAND)
            elif cmd == "/reset":
                reset_session()
                messages = [{"role": "system", "content": sys_prompt}]
                cfg["_chat_id"] = f"repl-{_uuid.uuid4().hex[:12]}"
                cprint("  · historial borrado · chat aislado nuevo", C.YELLOW)
            elif cmd == "/model":
                if arg:
                    cfg["model"] = arg
                    save_config(cfg)
                cprint(f"  · modelo: {cfg['model']}", C.YELLOW)
            elif cmd == "/tools":
                for t in TOOLS_SCHEMA:
                    f = t["function"]
                    cprint(f"  · {f['name']:12s} {f['description']}", C.BRAND)
            elif cmd == "/permissions":
                sub = arg.strip().split()
                action = sub[0] if sub else ""
                if action in ("ask", "yolo", "safe"):
                    cfg["permission_mode"] = action
                    save_config(cfg)
                    cprint(f"  · permisos: {cfg.get('permission_mode')}", C.YELLOW)
                elif action == "list":
                    perms = _load_perms()
                    if not perms:
                        cprint("  · sin reglas always-allow guardadas", C.GRAY)
                    else:
                        cprint(f"  · {len(perms)} reglas always-allow:", C.YELLOW, bold=True)
                        for k in sorted(perms):
                            cprint(f"    · {k}", C.GRAY)
                elif action == "clear":
                    PERMS_FILE.unlink(missing_ok=True)
                    cprint("  ✓ todas las always-allow borradas", C.GREEN)
                elif action == "revoke" and len(sub) > 1:
                    target = " ".join(sub[1:])
                    perms = _load_perms()
                    n = sum(1 for k in list(perms) if target in k and perms.pop(k, None))
                    _save_perms(perms)
                    cprint(f"  ✓ revocadas {n} reglas que contienen '{target}'", C.GREEN)
                else:
                    cprint(f"  · permisos: {cfg.get('permission_mode')}", C.YELLOW)
                    cprint(f"  · /permissions ask|yolo|safe  ·  list  ·  clear  ·  revoke <patrón>", C.GRAY)
            elif cmd == "/mode":
                if arg in ("cloud", "local", "auto"):
                    cfg["mode"] = arg
                    save_config(cfg)
                    if arg in ("local", "auto"):
                        ok, info = await _ollama_local_ready(cfg)
                        if ok:
                            cprint(f"  ✓ modo {arg}  ·  {info}", C.GREEN)
                        else:
                            cprint(f"  · modo {arg}  ·  ⚠ {info}", C.YELLOW)
                            cprint(f"    Corre: loud setup local", C.GRAY)
                    else:
                        cprint(f"  · modo {arg}", C.YELLOW)
                else:
                    ok, info = await _ollama_local_ready(cfg)
                    cprint(f"  · modo actual: {cfg.get('mode','cloud')}", C.YELLOW)
                    cprint(f"  · local: {'arriba' if ok else 'abajo'} ({info})", C.GRAY)
                    cprint(f"  · uso: /mode cloud  ·  /mode local  ·  /mode auto", C.GRAY)
            elif cmd == "/setup":
                if arg.strip() == "local":
                    await cmd_setup_local(cfg)
                else:
                    cprint("  · uso: /setup local", C.YELLOW)
            elif cmd == "/brain":
                cprint("  · brain no está disponible desde terminal — esa función es solo para la web admin.", C.YELLOW)
            elif cmd == "/cwd":
                cprint(f"  · {Path.cwd()}", C.YELLOW)
            elif cmd == "/login":
                ok = await cmd_login(cfg)
                if ok:
                    cprint("  · ya puedes empezar a chatear", C.GRAY)
            elif cmd == "/logout":
                await cmd_logout()
            elif cmd == "/whoami":
                await cmd_whoami(cfg)
            elif cmd == "/version":
                cprint(f"  · loud v{__version__}", C.BRAND)
            elif cmd == "/update":
                await cmd_update(cfg)
            elif cmd == "/save":
                target = Path(arg or f"loud-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md").expanduser()
                target.write_text(format_conversation(messages))
                cprint(f"  · guardado en {target}", C.YELLOW)
            elif cmd == "/open":
                if not arg.strip():
                    cprint(f"  · uso: /open <archivo.md o .json>", C.YELLOW)
                    continue
                target = Path(arg.strip()).expanduser()
                if not target.exists():
                    cprint(f"  · no existe: {target}", C.RED); continue
                try:
                    imported = parse_conversation_file(target)
                    if not imported:
                        cprint(f"  · archivo vacío o no parseable: {target}", C.YELLOW); continue
                    # Append to the current in-memory session WITHOUT touching
                    # the system prompt at the front.
                    messages = [m for m in messages if m.get("role") == "system"] + imported
                    cfg["_chat_id"] = f"repl-{_uuid.uuid4().hex[:12]}"
                    cprint(f"  ✓ importados {len(imported)} mensajes desde {target.name}", C.GREEN)
                except Exception as e:
                    cprint(f"  · error abriendo {target}: {e}", C.RED)
            else:
                cprint(f"  · comando desconocido: {cmd}", C.RED)
            continue

        # ── Auth gate: only checked here, not at startup ──
        # We let the user enter the REPL, see the welcome, look around, etc.
        # without forcing login. The check only kicks in when they actually
        # want to chat (same UX as Claude Code).
        if not get_token():
            cprint("", "")
            cprint("  ┌─ no estás logueado", C.YELLOW, bold=True)
            cprint(f"  │  Escribe {C.BOLD}{C.BRAND}/login{C.RESET}{C.YELLOW} para entrar (abre tu navegador).", C.YELLOW)
            cprint( "  │  Sin sesión no puedo procesar prompts.", C.YELLOW)
            cprint( "  └─", C.YELLOW)
            continue

        await run_turn(cfg, messages, user_text)
        cprint("", "")
        # No auto-save. The REPL conversation lives only in memory for this
        # session. Use /save <file> to export, /open <file> to import.


def parse_conversation_file(path: Path) -> list[dict]:
    """Read a saved conversation. Accepts:
    - `.json`: list of {role, content} produced by an earlier /save or backup
    - `.md`  : markdown produced by format_conversation (## user / ## assistant
      headings). System messages are skipped — only user/assistant pairs come
      back in so the current chat's system prompt stays intact."""
    text = path.read_text()
    out: list[dict] = []
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
            if isinstance(data, dict) and "messages" in data: data = data["messages"]
            for m in data:
                role = m.get("role")
                if role in ("user", "assistant"):
                    out.append({"role": role, "content": m.get("content", "")})
        except Exception:
            return []
        return out
    # Markdown: parse blocks separated by `## user` / `## assistant` (or USER /
    # ASSISTANT case-insensitive). Everything after `## system` is ignored.
    current_role = None
    buf: list[str] = []
    def _flush():
        if current_role and buf:
            content = "\n".join(buf).strip()
            if content: out.append({"role": current_role, "content": content})
    for ln in text.splitlines():
        m = re.match(r"^##\s+(user|assistant|system|tool)\b", ln.strip(), re.IGNORECASE)
        if m:
            _flush()
            buf = []
            role = m.group(1).lower()
            current_role = role if role in ("user", "assistant") else None
            continue
        if current_role:
            buf.append(ln)
    _flush()
    return out


def format_conversation(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        role = m.get("role", "?")
        content = m.get("content", "")
        if role == "system":
            continue
        lines.append(f"### {role.upper()}\n\n{content}\n")
    return "\n".join(lines)


# ───────────────────── Main ─────────────────────

async def main_async(args: argparse.Namespace) -> int:
    cfg = load_config()

    # Fire-and-forget update check (≤ once per 24h)
    asyncio.create_task(_maybe_check_update_async())

    if args.model:
        cfg["model"] = args.model
        save_config(cfg)
    if args.api_url:
        cfg["api_url"] = args.api_url
        save_config(cfg)
    if getattr(args, "local", False):
        cfg["mode"] = "local"
    if getattr(args, "cloud", False):
        cfg["mode"] = "cloud"
    if getattr(args, "auto", False):
        cfg["mode"] = "auto"
    if getattr(args, "yolo", False):
        # --dangerously-skip-permissions / --yolo: ephemeral for this run only.
        # We do NOT persist this into config.json — pasar el flag explícito
        # cada vez que querés saltar permisos es parte del freno de mano.
        cfg["permission_mode"] = "yolo"
        cprint("  ⚠ --dangerously-skip-permissions ACTIVO · sin prompts de [y/n]", C.RED, bold=True)

    # Claude-Code-style flow: NO forced login at startup. The REPL starts
    # whether you're logged in or not — the banner shows the auth state and
    # the user can `/login` when ready. We only require auth at the moment
    # the user actually sends a chat message.
    #
    # Save config on first run so we don't re-create it every time.
    if not CONFIG_FILE.exists():
        save_config(cfg)

    # Subcommands
    if args.question and args.question[0] in ("login", "logout", "whoami", "update", "version", "setup", "mode"):
        sub = args.question[0]
        if sub == "login":
            ok = await cmd_login(cfg)
            return 0 if ok else 1
        if sub == "logout":
            await cmd_logout()
            return 0
        if sub == "whoami":
            return await cmd_whoami(cfg)
        if sub == "update":
            return await cmd_update(cfg)
        if sub == "version":
            cprint(f"  · loud {__version__}", C.BRAND)
            return 0
        if sub == "setup":
            target = (args.question[1] if len(args.question) > 1 else "").lower()
            if target == "local":
                return await cmd_setup_local(cfg)
            cprint("  · uso: loud setup local", C.YELLOW)
            return 1
        if sub == "mode":
            target = (args.question[1] if len(args.question) > 1 else "").lower()
            if target in ("cloud", "local", "auto"):
                cfg["mode"] = target
                save_config(cfg)
                ok, info = await _ollama_local_ready(cfg)
                cprint(f"  · mode={target} · local={'up' if ok else 'down'} ({info})", C.GREEN)
                return 0
            ok, info = await _ollama_local_ready(cfg)
            cprint(f"  · mode actual: {cfg.get('mode','cloud')} · local: {info}", C.YELLOW)
            cprint(f"  · uso: loud mode cloud|local|auto", C.GRAY)
            return 0

    if args.reset:
        reset_session()
        cprint("  · historial borrado", C.YELLOW)
        if not args.question:
            return 0

    if args.question:
        # One-shot calls are ALWAYS fresh — no session history, no save.
        # Mixing past turns into a one-off `loud "hola"` was leaking old
        # context into unrelated questions.
        import uuid as _uuid
        cfg["_chat_id"] = f"oneshot-{_uuid.uuid4().hex[:12]}"
        messages = [{"role": "system", "content": STATIC_SYSTEM_PROMPT}]
        await run_turn(cfg, messages, " ".join(args.question))
        return 0

    await repl(cfg)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="loud",
        description=f"LOUD CLI v{__version__} — terminal-first AI · loud.codes",
        epilog="Subcomandos: login · logout · whoami",
    )
    parser.add_argument("question", nargs="*", help="Prompt one-shot o subcomando")
    parser.add_argument("--reset", action="store_true", help="Borrar historial de la sesión")
    parser.add_argument("--model", help="Modelo: loud-go · loud-pro · loud-ultra · loud-2.0 · loud-eye (visión)")
    parser.add_argument("--api-url", help="Override del servidor LOUD")
    parser.add_argument("--local",  action="store_true", help="Forzar modo local (Ollama 127.0.0.1)")
    parser.add_argument("--cloud",  action="store_true", help="Forzar modo cloud (api.loud.codes)")
    parser.add_argument("--auto",   action="store_true", help="Modo híbrido: local si está arriba, sino cloud")
    parser.add_argument("--dangerously-skip-permissions", "--yolo", action="store_true",
                        dest="yolo",
                        help="Salta TODOS los prompts de permiso (sin pedir [y/n] para nada). Igual de peligroso que suena.")
    parser.add_argument("--version", action="version", version=f"loud {__version__}")
    args = parser.parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
