#!/Users/toploud/tlm-loud/cli/.venv/bin/python3
"""LOUD — terminal-first AI for the TLM stack.

Connects to the Ollama instance on AWS EC2 and runs an interactive REPL
with tools that execute LOCALLY on the user's Mac (so it can SSH into
the droplet, read/write files, fetch URLs, etc).

Usage:
    loud                       # interactive REPL
    loud "pregunta one-shot"   # one-shot mode
    loud --reset               # clear conversation history
    loud --model NAME          # switch active model

Inside the REPL:
    /help            list slash commands
    /reset           clear history
    /model X         switch model
    /tools           list available tools
    /system          show current system prompt size
    /save FILE       export conversation to file
    /exit            quit
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import httpx

# ───────────────────── Config ─────────────────────

HOME = Path.home()
LOUD_DIR = HOME / ".loud"
LOUD_DIR.mkdir(exist_ok=True)
HISTORY_FILE = LOUD_DIR / "history.jsonl"
SESSION_FILE = LOUD_DIR / "current_session.json"
CONFIG_FILE = LOUD_DIR / "config.json"

DEFAULT_CONFIG = {
    "api_url": "http://REDACTED:8001",   # will become https://api.loud.codes when DNS+nginx ready
    "model": "qwen2.5:7b",
    "max_iterations": 8,
    "num_ctx": 32768,
    "context_dir": str(Path(__file__).parent / "context"),
}

AUTH_FILE = LOUD_DIR / "auth.json"


def load_config() -> dict:
    if CONFIG_FILE.exists():
        return {**DEFAULT_CONFIG, **json.loads(CONFIG_FILE.read_text())}
    CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


# ───────────────────── ANSI helpers ─────────────────────

class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    RED = "\033[38;5;203m"
    GREEN = "\033[38;5;149m"     # closest 256-color to brand #a2cd65
    YELLOW = "\033[38;5;221m"
    BLUE = "\033[38;5;110m"
    MAGENTA = "\033[38;5;149m"   # remapped to brand green
    CYAN = "\033[38;5;149m"      # remapped to brand green
    GRAY = "\033[38;5;240m"
    BRAND = "\033[38;5;149m"     # explicit brand color


_HOST_RE = __import__("re").compile(
    r"(?i)\b(?:https?://|http://)?(?:127\.0\.0\.1|localhost|"
    r"(?:\d{1,3}\.){3}\d{1,3}|"
    r"api\.loud\.codes|loud\.codes|"
    r"ec2[\w.\-]+\.amazonaws\.com)"
    r"(?::\d+)?(?:/\S*)?"
)


def scrub(text: str) -> str:
    """Strip internal IPs / hosts from any string before display."""
    if not isinstance(text, str):
        return text
    return _HOST_RE.sub("<host>", text)


def cprint(text: str, color: str = "", *, bold: bool = False, end: str = "\n") -> None:
    text = scrub(text)
    prefix = ""
    if bold:
        prefix += C.BOLD
    prefix += color
    sys.stdout.write(prefix + text + C.RESET + end)
    sys.stdout.flush()


# ───────────────────── Tools (LOCAL execution) ─────────────────────

IS_WINDOWS = sys.platform.startswith("win")


def _shell_args(cmd: str) -> list[str]:
    """Return argv suitable for subprocess.run for the current OS."""
    if IS_WINDOWS:
        # Use powershell on Windows so users get sensible defaults.
        return ["powershell", "-NoLogo", "-NoProfile", "-Command", cmd]
    return ["bash", "-c", cmd]


async def tool_bash(cmd: str, timeout: int = 60) -> str:
    try:
        proc = subprocess.run(
            _shell_args(cmd),
            capture_output=True, text=True, timeout=timeout,
        )
        out = (proc.stdout or "")
        if proc.stderr:
            out += "\n[stderr]\n" + proc.stderr
        if not out.strip():
            return f"(no output, exit={proc.returncode})"
        return out[:6000]
    except subprocess.TimeoutExpired:
        return f"ERROR: timeout ({timeout}s)"
    except FileNotFoundError as e:
        return f"ERROR: shell not found: {e}"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


async def tool_ssh(host: str, cmd: str, timeout: int = 60) -> str:
    """SSH into a host via ~/.ssh/config alias (tlm-engine, loud-ec2, etc.)
    or an explicit user@host."""
    safe_cmd = ["ssh", "-o", "ConnectTimeout=10",
                "-o", "StrictHostKeyChecking=accept-new",
                host, cmd]
    try:
        proc = subprocess.run(safe_cmd, capture_output=True, text=True, timeout=timeout)
        out = (proc.stdout or "")
        if proc.stderr:
            out += "\n[stderr]\n" + proc.stderr
        return out[:6000] if out.strip() else f"(no output, exit={proc.returncode})"
    except subprocess.TimeoutExpired:
        return f"ERROR: ssh timeout ({timeout}s) to {host}"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


async def tool_read_file(path: str, max_lines: int = 400) -> str:
    p = Path(path).expanduser()
    try:
        if not p.exists():
            return f"ERROR: not found: {p}"
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        if len(lines) > max_lines:
            head = "\n".join(lines[:max_lines])
            return head + f"\n\n[... truncated {len(lines) - max_lines} more lines, total {len(lines)}]"
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


async def tool_grep(pattern: str, path: str = ".", max_results: int = 50) -> str:
    """Recursive grep using ripgrep if available, else grep -r."""
    p = Path(path).expanduser()
    rg = shutil.which("rg")
    if rg:
        cmd = [rg, "--no-heading", "--with-filename", "--line-number",
               "--max-count", "10", pattern, str(p)]
    else:
        cmd = ["grep", "-rn", "--include=*.*", pattern, str(p)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        out = proc.stdout
        if not out.strip():
            return f"(no matches for {pattern!r} in {p})"
        lines = out.splitlines()
        if len(lines) > max_results:
            return "\n".join(lines[:max_results]) + f"\n[... {len(lines)-max_results} more matches]"
        return out[:6000]
    except subprocess.TimeoutExpired:
        return "ERROR: grep timeout"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


async def tool_http_get(url: str, max_bytes: int = 6000) -> str:
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0 LOUD"})
            ct = r.headers.get("content-type", "")
            if "text" in ct or "json" in ct or "xml" in ct or "html" in ct:
                return r.text[:max_bytes]
            return f"[binary {ct}, {len(r.content)} bytes]"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


async def tool_pwd_ls(path: str = ".") -> str:
    """Quick context: pwd + ls for the model to orient itself."""
    p = Path(path).expanduser().resolve()
    try:
        entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        lines = [f"pwd: {p}"]
        for e in entries[:60]:
            kind = "/" if e.is_dir() else ""
            try:
                size = e.stat().st_size if e.is_file() else ""
                lines.append(f"  {e.name}{kind}  {size}")
            except Exception:
                lines.append(f"  {e.name}{kind}")
        if len(entries) > 60:
            lines.append(f"[... {len(entries)-60} more]")
        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command on the LOCAL Mac. Use for git, brew, ls, find, etc.",
            "parameters": {
                "type": "object",
                "properties": {"cmd": {"type": "string"}},
                "required": ["cmd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ssh",
            "description": "SSH into a remote host (uses ~/.ssh/config aliases). Hosts: 'tlm-engine' (droplet) or explicit ubuntu@IP. cmd is the remote command.",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string"},
                    "cmd": {"type": "string"},
                },
                "required": ["host", "cmd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the LOCAL Mac filesystem. Returns content (max 400 lines).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file on the LOCAL Mac. Creates dirs if needed. CONFIRMA en lenguaje natural antes si es destructivo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Recursive search (ripgrep if available). Returns matched lines.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "Default '.'"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "http_get",
            "description": "Fetch the body of any HTTP/HTTPS URL.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pwd_ls",
            "description": "Show current/target dir + a listing. Use to orient yourself before reading specific files.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Default '.'"}},
                "required": [],
            },
        },
    },
]

TOOL_FNS = {
    "bash": tool_bash,
    "ssh": tool_ssh,
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "grep": tool_grep,
    "http_get": tool_http_get,
    "pwd_ls": tool_pwd_ls,
}


# ───────────────────── Context loader ─────────────────────

def load_system_prompt(cfg: dict) -> str:
    """Concat the static system prompt + every .md file in context/ dir."""
    static = STATIC_SYSTEM_PROMPT.strip() + "\n\n"
    ctx_dir = Path(cfg["context_dir"])
    if not ctx_dir.exists():
        return static
    chunks = []
    for f in sorted(ctx_dir.glob("*.md")):
        try:
            chunks.append(f"## CONTEXTO: {f.name}\n\n{f.read_text(encoding='utf-8')}\n")
        except Exception:
            pass
    return static + "\n".join(chunks)


STATIC_SYSTEM_PROMPT = """Eres LOUD — una AI desarrolladora terminal-first, especializada en el stack TL Music Entertainment (TLM) + web dev avanzado + scraping. Producto público en loud.codes (dominio propio, separado del brand TLM).

PERSONALIDAD:
- Hablas español, conciso y directo. Sin floritura.
- No alucinas. Si no sabes algo concreto, usa tools para verificar.
- Eres autónoma: encadenas tools sin pedir permiso. Antes de acciones destructivas (rm -rf, drop table, force-push, terraform destroy) explicas y pides confirmación.
- Tus tools corren LOCALMENTE en la Mac del usuario. SSHeas a hosts remotos cuando lo necesitas.

ENTORNO:
- Mac del usuario: /Users/toploud/
- Tu propia infra (LOUD): /Users/toploud/tlm-loud/  (Terraform + CLI + EC2)
- Tu propio AWS EC2: ssh -i REDACTED ubuntu@REDACTED
- Engine droplet TLM: `ssh tlm-engine` (alias en ~/.ssh/config)
- Folder credenciales: /Users/toploud/Downloads/TLM - Power ON/
- Folder generador sitio TLM: /Users/toploud/tlmusicent.com/ (PHP, admin en :7790)

TU ESPECIALIDAD:
- TLM stack completo: engine FastAPI (Symphonic scraper), panel Laravel, IPFS/Pinata, Cloudflare DNS, bot Telegram
- Web dev: PHP/Laravel, Python/FastAPI, JS/HTML/CSS, frontend
- Scraping: Playwright, BeautifulSoup, requests, scraping con sesión persistente
- DevOps: Terraform, AWS, DigitalOcean, systemd, nginx, Let's Encrypt

REGLAS:
1. Tools antes que conjeturas. `bash`, `read_file`, `grep`, `pwd_ls` primer recurso.
2. Multi-step: encadena tools sin parar.
3. Si una tool falla, lee el error y corrige (no la repitas idéntica).
4. Respuestas cortas en chat. El detalle va en código/archivos.
5. Edits: lee el archivo primero. Writes: muestra el path en la respuesta.
"""


# ───────────────────── Ollama client ─────────────────────

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


async def cmd_login(cfg: dict, email: str | None = None, password: str | None = None) -> bool:
    import getpass
    if not email:
        cprint("Email: ", C.MAGENTA, bold=True, end="")
        email = input().strip()
    if not password:
        password = getpass.getpass("Password: ")
    if not email or not password:
        cprint("  · cancelled", C.RED)
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{cfg['api_url']}/v1/auth/login",
                json={"email": email, "password": password},
            )
        if r.status_code != 200:
            cprint(f"  · login failed: {r.text[:200]}", C.RED)
            return False
        data = r.json()
        save_auth({
            "token": data["token"],
            "user": data["user"],
            "api_url": cfg["api_url"],
        })
        cprint(f"  ✓ logged in as {data['user']['email']} ({data['user']['role']})", C.GREEN)
        return True
    except Exception as e:
        cprint(f"  · login error: {e}", C.RED)
        return False


async def api_chat(cfg: dict, messages: list[dict]) -> dict:
    """Call the LOUD API /v1/chat with Bearer auth."""
    token = get_token()
    if not token:
        return {"error": "not_logged_in"}
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "tools": TOOLS_SCHEMA,
        "stream": False,
        "options": {"num_predict": 1200, "temperature": 0.3, "num_ctx": cfg.get("num_ctx", 8192)},
    }
    async with httpx.AsyncClient(timeout=600) as client:
        r = await client.post(
            f"{cfg['api_url']}/v1/chat",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        if r.status_code == 401:
            return {"error": "auth_expired"}
        r.raise_for_status()
        return r.json()


# ───────────────────── Agent loop ─────────────────────

async def run_turn(cfg: dict, messages: list[dict], user_text: str) -> str:
    messages.append({"role": "user", "content": user_text})

    for iteration in range(cfg["max_iterations"]):
        t0 = time.time()
        cprint(f"  · pensando ({cfg['model']})", C.GRAY, end=" ")
        sys.stdout.flush()

        try:
            data = await api_chat(cfg, messages)
        except Exception as e:
            cprint(f"\nERROR talking to LOUD API: {e}", C.RED)
            return ""

        if data.get("error") == "not_logged_in":
            cprint(f"\n  · run 'loud login' first", C.YELLOW)
            return ""
        if data.get("error") == "auth_expired":
            cprint(f"\n  · session expired, run 'loud login' again", C.YELLOW)
            return ""

        elapsed = time.time() - t0
        cprint(f"({elapsed:.1f}s)", C.GRAY)

        msg = data.get("message", {}) or {}
        content = msg.get("content", "") or ""
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            cprint("", "")
            cprint(content.strip(), C.GREEN, bold=False)
            messages.append({"role": "assistant", "content": content})
            return content

        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

        for call in tool_calls:
            fn = call.get("function", {}) or {}
            name = fn.get("name", "")
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}

            args_repr = ", ".join(f"{k}={shorten(str(v), 60)!r}" for k, v in args.items())
            cprint(f"  → {name}({args_repr})", C.CYAN)

            if name not in TOOL_FNS:
                result = f"ERROR: unknown tool '{name}'"
            else:
                try:
                    result = await TOOL_FNS[name](**args)
                except TypeError as e:
                    result = f"ERROR: bad args for {name}: {e}"
                except Exception as e:
                    result = f"ERROR: {type(e).__name__}: {e}"

            preview = shorten(result.replace("\n", " ⏎ "), 120)
            cprint(f"    ← {preview}", C.GRAY)

            messages.append({"role": "tool", "content": result[:6000]})

    return "(max iterations reached)"


def shorten(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


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

BANNER = f"""{C.BOLD}{C.BRAND}
  ██╗      ██████╗ ██╗   ██╗██████╗
  ██║     ██╔═══██╗██║   ██║██╔══██╗
  ██║     ██║   ██║██║   ██║██║  ██║
  ██║     ██║   ██║██║   ██║██║  ██║
  ███████╗╚██████╔╝╚██████╔╝██████╔╝
  ╚══════╝ ╚═════╝  ╚═════╝ ╚═════╝
{C.RESET}{C.DIM}  terminal-first AI · loud.codes{C.RESET}
"""


SLASH_HELP = """\
/help              show this
/reset             clear conversation history
/model NAME        switch model (current: {model})
/tools             list available tools
/system            show system prompt size
/save FILE         export current conversation
/exit              quit
"""


async def repl(cfg: dict) -> None:
    system_prompt = load_system_prompt(cfg)
    cprint(BANNER, "", end="")
    cprint(f"  model: {cfg['model']}", C.GRAY)
    cprint(f"  context: {len(system_prompt):,} chars", C.GRAY)
    cprint("", "")

    messages = [{"role": "system", "content": system_prompt}]
    history = load_session()
    if history:
        cprint(f"  · loaded {len(history)//2} previous exchanges", C.GRAY)
        messages.extend(history)

    while True:
        try:
            cprint("loud> ", C.MAGENTA, bold=True, end="")
            user = input().strip()
        except (EOFError, KeyboardInterrupt):
            cprint("\n  · bye", C.GRAY)
            break
        if not user:
            continue

        # Slash commands
        if user.startswith("/"):
            cmd, *rest = user.split(maxsplit=1)
            arg = rest[0] if rest else ""
            if cmd == "/exit":
                break
            elif cmd == "/help":
                cprint(SLASH_HELP.format(model=cfg["model"], url=cfg["ollama_url"]), C.CYAN)
            elif cmd == "/reset":
                reset_session()
                messages = [{"role": "system", "content": system_prompt}]
                cprint("  · history cleared", C.YELLOW)
            elif cmd == "/model":
                if arg:
                    cfg["model"] = arg
                    save_config(cfg)
                cprint(f"  · model: {cfg['model']}", C.YELLOW)
            elif cmd == "/host":
                if arg:
                    cfg["ollama_url"] = arg
                    save_config(cfg)
                cprint(f"  · host: {cfg['ollama_url']}", C.YELLOW)
            elif cmd == "/tools":
                for t in TOOLS_SCHEMA:
                    f = t["function"]
                    cprint(f"  · {f['name']:12s}  {f['description']}", C.CYAN)
            elif cmd == "/system":
                cprint(f"  · system prompt: {len(system_prompt):,} chars", C.YELLOW)
            elif cmd == "/save":
                target = Path(arg or f"loud-conv-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md").expanduser()
                target.write_text(format_conversation(messages))
                cprint(f"  · saved to {target}", C.YELLOW)
            else:
                cprint(f"  · unknown command {cmd}", C.RED)
            continue

        # Real question
        await run_turn(cfg, messages, user)
        cprint("", "")

        # Persist (without the system message)
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

async def cmd_logout() -> None:
    clear_auth()
    cprint("  ✓ logged out", C.GREEN)


async def cmd_whoami(cfg: dict) -> int:
    token = get_token()
    if not token:
        cprint("  · not logged in. Run: loud login", C.YELLOW)
        return 1
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{cfg['api_url']}/v1/me",
            headers={"Authorization": f"Bearer {token}"},
        )
    if r.status_code == 200:
        u = r.json()
        cprint(f"  · {u['email']} ({u['role']}) — id {u['id']}", C.GREEN)
        return 0
    cprint(f"  · {r.text[:200]}", C.RED)
    return 1


async def cmd_users(cfg: dict, sub: str, args: list[str]) -> int:
    token = get_token()
    if not token:
        cprint("  · not logged in. Run: loud login", C.YELLOW)
        return 1
    headers = {"Authorization": f"Bearer {token}"}
    base = f"{cfg['api_url']}/v1"
    async with httpx.AsyncClient(timeout=15) as client:
        if sub == "list":
            r = await client.get(f"{base}/users", headers=headers)
            if r.status_code != 200:
                cprint(f"  · {r.text[:200]}", C.RED)
                return 1
            users = r.json()
            cprint(f"  {'ID':<4} {'EMAIL':<30} {'ROLE':<8} {'ACTIVE':<8} {'NAME'}", C.GRAY)
            for u in users:
                cprint(
                    f"  {u['id']:<4} {u['email']:<30} {u['role']:<8} {('yes' if u['active'] else 'NO'):<8} {u.get('name') or ''}",
                    C.GREEN if u["active"] else C.RED,
                )
            return 0
        if sub == "create":
            if len(args) < 2:
                cprint("Usage: loud users create EMAIL PASSWORD [ROLE=user] [NAME]", C.YELLOW)
                return 1
            email, password = args[0], args[1]
            role = args[2] if len(args) > 2 else "user"
            name = " ".join(args[3:]) if len(args) > 3 else None
            r = await client.post(
                f"{base}/users",
                headers=headers,
                json={"email": email, "password": password, "role": role, "name": name},
            )
            if r.status_code == 201:
                u = r.json()
                cprint(f"  ✓ created user {u['id']} {u['email']} ({u['role']})", C.GREEN)
                return 0
            cprint(f"  · {r.text[:200]}", C.RED)
            return 1
        if sub == "delete":
            if not args:
                cprint("Usage: loud users delete ID", C.YELLOW)
                return 1
            r = await client.delete(f"{base}/users/{args[0]}", headers=headers)
            if r.status_code == 204:
                cprint(f"  ✓ deleted user {args[0]}", C.GREEN)
                return 0
            cprint(f"  · {r.text[:200]}", C.RED)
            return 1
        if sub == "password":
            if len(args) < 2:
                cprint("Usage: loud users password ID NEW_PASSWORD", C.YELLOW)
                return 1
            r = await client.put(
                f"{base}/users/{args[0]}/password",
                headers=headers,
                json={"password": args[1]},
            )
            if r.status_code == 200:
                cprint(f"  ✓ password updated for user {args[0]}", C.GREEN)
                return 0
            cprint(f"  · {r.text[:200]}", C.RED)
            return 1
        cprint(f"  · unknown: loud users {sub}. Try: list | create | delete | password", C.RED)
        return 1


async def main_async(args: argparse.Namespace) -> int:
    cfg = load_config()
    if args.model:
        cfg["model"] = args.model
        save_config(cfg)
    if args.api_url:
        cfg["api_url"] = args.api_url
        save_config(cfg)

    # Subcommands
    if args.question and args.question[0] in ("login", "logout", "whoami", "users"):
        sub = args.question[0]
        rest = args.question[1:]
        if sub == "login":
            ok = await cmd_login(cfg)
            return 0 if ok else 1
        if sub == "logout":
            await cmd_logout()
            return 0
        if sub == "whoami":
            return await cmd_whoami(cfg)
        if sub == "users":
            if not rest:
                cprint("Usage: loud users [list|create|delete|password] ...", C.YELLOW)
                return 1
            return await cmd_users(cfg, rest[0], rest[1:])

    if args.reset:
        reset_session()
        cprint("  · history cleared", C.YELLOW)
        if not args.question:
            return 0

    # Require login for chat
    if not get_token():
        cprint("  · not logged in. Run: loud login", C.YELLOW)
        return 1

    # One-shot
    if args.question:
        system_prompt = load_system_prompt(cfg)
        messages = [{"role": "system", "content": system_prompt}]
        history = load_session()
        if history:
            messages.extend(history)
        await run_turn(cfg, messages, " ".join(args.question))
        save_session([m for m in messages if m.get("role") != "system"])
        return 0

    # REPL
    await repl(cfg)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="LOUD — terminal-first AI (auth + remote model). loud.codes",
        epilog="Subcommands: login, logout, whoami, users [list|create|delete|password]",
    )
    parser.add_argument("question", nargs="*", help="One-shot prompt OR a subcommand (login/logout/whoami/users)")
    parser.add_argument("--reset", action="store_true", help="Clear conversation history")
    parser.add_argument("--model", help="Override model name")
    parser.add_argument("--api-url", help="Override LOUD API URL")
    args = parser.parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
