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

__version__ = "0.7.7"

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
    "max_iterations": 10,
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


def _load_perms() -> dict:
    if not PERMS_FILE.exists():
        return {}
    try:
        return json.loads(PERMS_FILE.read_text())
    except Exception:
        return {}


def _save_perms(perms: dict) -> None:
    PERMS_FILE.write_text(json.dumps(perms, indent=2))


def _perm_key(tool: str, args: dict) -> str:
    """A stable key for caching always-allow decisions. For bash/ssh we
    cache by the FIRST WORD of the command. For file ops, by the directory."""
    if tool == "bash":
        return f"bash:{(args.get('cmd') or '').strip().split(' ')[0]}"
    if tool == "ssh":
        return f"ssh:{args.get('host', '?')}"
    if tool in ("write_file", "edit_file", "read_file"):
        p = Path(args.get("path", "")).expanduser()
        try:
            return f"{tool}:{p.parent.resolve()}"
        except Exception:
            return f"{tool}:?"
    return tool


def is_destructive(tool: str, args: dict) -> bool:
    if tool not in DESTRUCTIVE_TOOLS:
        return False
    if tool == "bash":
        cmd = (args.get("cmd") or "").strip()
        return any(re.search(p, cmd, re.IGNORECASE) for p in DESTRUCTIVE_BASH_PATTERNS)
    return True


def request_permission(cfg: dict, tool: str, args: dict) -> str:
    """Returns 'allow', 'deny', or 'stop'. Mutates `args` in-place when the
    user picks 'edit' — they get to rewrite the cmd / path / content before
    the tool fires."""
    mode = cfg.get("permission_mode", "ask")
    if mode == "yolo":
        return "allow"
    if mode == "safe" and is_destructive(tool, args):
        return "deny"
    if tool not in DESTRUCTIVE_TOOLS:
        return "allow"
    perms = _load_perms()
    key = _perm_key(tool, args)
    if perms.get(key) == "always":
        return "allow"

    cprint("", "")
    cprint(f"  ┌─ permiso requerido ─ {tool}", C.YELLOW, bold=True)
    if tool == "bash":
        cprint(f"  │  $ {shorten(args.get('cmd', ''), 200)}", C.GRAY)
    elif tool == "ssh":
        cprint(f"  │  ssh {args.get('host')} \"{shorten(args.get('cmd', ''), 160)}\"", C.GRAY)
    elif tool == "write_file":
        cprint(f"  │  → {args.get('path')}  ({len(args.get('content', ''))} bytes)", C.GRAY)
    elif tool == "edit_file":
        cprint(f"  │  ✎ {args.get('path')}", C.GRAY)
    cprint(f"  └─ usa ↑/↓ y Enter (o presiona la letra)", C.YELLOW)
    ans = select_option("", [
        ("y", "Sí, correr esto"),
        ("n", "No, cancelar"),
        ("e", "Editar antes de correr"),
        ("a", "Siempre permitir esto"),
        ("s", "Detener al agente"),
    ], default=0)
    if ans == "esc":
        return "stop"
    if ans == "a":
        perms[key] = "always"
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

async def tool_bash(cmd: str, timeout: int = 120) -> str:
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
]


TOOL_FNS = {
    "bash":       tool_bash,
    "ssh":        tool_ssh,
    "read_file":  tool_read_file,
    "write_file": tool_write_file,
    "edit_file":  tool_edit_file,
    "glob":       tool_glob,
    "grep":       tool_grep,
    "ls":         tool_ls,
    "http_get":   tool_http_get,
}


# ───────────────────── System prompt ─────────────────────

STATIC_SYSTEM_PROMPT = """Estás corriendo en la terminal del usuario. Tools disponibles: bash, read_file, write_file, edit_file, ls, glob, grep, ssh, http_get.

Reglas:
- Si la pregunta NO requiere tocar la máquina del usuario → **responde directo** sin llamar tools (saludos, conceptos, código en abstracto).
- Si la pregunta SÍ pide info de la máquina (versiones, archivos, rutas, procesos, "qué tengo") o pide hacer algo en la máquina → llama bash/read_file/ls/etc. NO inventes.
- Encadena tools sin pedir permiso; el CLI maneja confirmaciones de destructivos. Si una tool falla, corrige; no repitas igual."""


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
    relevance so casual prompts ('hola') don't trigger spurious context."""
    token = get_token()
    if not token:
        yield {"error": "not_logged_in"}
        return
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "tools": TOOLS_SCHEMA,
        "chat_id": cfg.get("_chat_id"),
        "use_rag": True,
        "use_memory": False,
        "use_web": True,
        "stream": True,
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
    "bash":       "Bashing",
    "ssh":        "Tunneling",
    "read_file":  "Reading",
    "write_file": "Writing",
    "edit_file":  "Modifying",
    "glob":       "Globbing",
    "grep":       "Searching",
    "ls":         "Scanning",
    "http_get":   "Fetching",
}
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
                        cprint(f"  → {name}({shorten(json.dumps(args, ensure_ascii=False), 80)})", C.BRAND)
                        decision = request_permission(cfg, name, args)
                        if decision == "stop":
                            raise StopAgent()
                        if decision == "deny":
                            tool_msg = {"role": "tool", "content": f"ERROR: usuario denegó {name}"}
                            if tc_id: tool_msg["tool_call_id"] = tc_id
                            messages.append(tool_msg)
                            cprint("    ← denegado por el usuario", C.YELLOW)
                            continue
                        try:
                            result = await TOOL_FNS[name](**args)
                        except TypeError as e:
                            result = f"ERROR: bad args for {name}: {e}"
                        except Exception as e:
                            result = f"ERROR: {type(e).__name__}: {e}"
                        cprint(f"    ← {shorten(result.replace(chr(10), ' ⏎ '), 140)}", C.GRAY)
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
/save FILE          exporta la conversación a un archivo .md
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
    # Fresh chat_id per REPL launch — guarantees server-side isolation from any
    # other chat. /reset rotates it; one-shot uses its own.
    cfg["_chat_id"] = f"repl-{_uuid.uuid4().hex[:12]}"
    history = load_session()
    if history:
        messages.extend(history)

    sys.stdout.write(render_banner(cfg))
    sys.stdout.flush()
    if history:
        cprint(f"  · {len(history) // 2} intercambios anteriores cargados\n", C.GRAY)

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
                if arg in ("ask", "yolo", "safe"):
                    cfg["permission_mode"] = arg
                    save_config(cfg)
                cprint(f"  · permisos: {cfg.get('permission_mode')}  (ask/yolo/safe)", C.YELLOW)
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
        save_session([m for m in messages if m.get("role") != "system"])


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
    parser.add_argument("--version", action="version", version=f"loud {__version__}")
    args = parser.parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
