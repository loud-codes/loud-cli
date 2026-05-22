<div align="center">

```
                ██╗      ██████╗ ██╗   ██╗██████╗
                ██║     ██╔═══██╗██║   ██║██╔══██╗
                ██║     ██║   ██║██║   ██║██║  ██║
                ██║     ██║   ██║██║   ██║██║  ██║
                ███████╗╚██████╔╝╚██████╔╝██████╔╝
                ╚══════╝ ╚═════╝  ╚═════╝ ╚═════╝
```

### **A terminal-first AI agent that lives on _your_ infrastructure.**

The model is yours. The data is yours. The terminal is yours.

[![License: MIT](https://img.shields.io/badge/license-MIT-a2cd65?style=flat-square)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.3.0-a2cd65?style=flat-square)](#)
[![Python](https://img.shields.io/badge/python-3.10+-a2cd65?style=flat-square&logo=python&logoColor=white)](#)
[![Platform](https://img.shields.io/badge/macOS%20·%20Linux%20·%20Windows-success-a2cd65?style=flat-square)](#)
[![loud.codes](https://img.shields.io/badge/web-loud.codes-a2cd65?style=flat-square)](https://loud.codes)

[Install](#install) · [Quickstart](#quickstart) · [Tools](#tools) · [Permissions](#permissions) · [Self-host](https://loud.codes) · [README en español](README.es.md)

</div>

---

## What is this

**LOUD** is a private AI that runs on infrastructure you own. The `loud` CLI is the on-machine half — it lives in your shell, talks to your LOUD server over the network, and acts as a coding agent that can read, edit, and run things on your computer with explicit consent.

Think Claude Code or Cursor, but the model never leaves your data center.

```
┌──────────────┐         ┌──────────────────┐         ┌─────────────────┐
│  your shell  │ ──────▶ │   loud CLI       │ ──────▶ │  your LOUD api  │
│              │         │  (local agent)   │         │  (your server)  │
│  ↑ output    │ ◀────── │  read/write/run  │ ◀────── │  qwen / RAG /   │
│              │         │  with permission │         │  memory         │
└──────────────┘         └──────────────────┘         └─────────────────┘
```

The CLI does not phone home. Every byte goes to the LOUD server you point it at (default `https://api.loud.codes`, or your own at `loud --api-url https://yours.example.com`).

---

## Install

> LOUD is **invite-only** while in private beta. You need a user account on a running LOUD server before the CLI is useful. Get an invite at [loud.codes](https://loud.codes).

### macOS · Linux

```bash
curl -fsSL https://loud.codes/install.sh | bash
```

### macOS (Homebrew)

```bash
brew tap loud-codes/cli
brew install loud
```

### Windows (PowerShell)

```powershell
iwr -useb https://loud.codes/install.ps1 | iex
```

### From source

```bash
git clone https://github.com/loud-codes/loud-cli ~/.loud/install/src
python3 -m venv ~/.loud/install/venv
~/.loud/install/venv/bin/pip install httpx
printf '#!/usr/bin/env bash\nexec ~/.loud/install/venv/bin/python3 ~/.loud/install/src/cli/loud.py "$@"\n' > ~/.local/bin/loud
chmod +x ~/.local/bin/loud
```

---

## Quickstart

```
$ loud
```

First run walks you through a 30-second setup: confirm the server URL, pick a permission mode (`ask` / `yolo` / `safe`), and log in. After that you land in a Claude-Code-style REPL.

```
╭────────────────────────────────────────────────────────────╮
│                                                            │
│   ██╗      ██████╗ ██╗   ██╗██████╗                        │
│   ██║     ██╔═══██╗██║   ██║██╔══██╗                       │
│   ██║     ██║   ██║██║   ██║██║  ██║                       │
│   ██║     ██║   ██║██║   ██║██║  ██║                       │
│   ███████╗╚██████╔╝╚██████╔╝██████╔╝                       │
│   ╚══════╝ ╚═════╝  ╚═════╝ ╚═════╝                        │
│                                                            │
│   ✻ Welcome to LOUD — terminal-first AI                    │
│                                                            │
│   sesión:  you@example.com · admin                         │
│   modelo:  loud-go   permisos:  ask                        │
│   cwd:     ~/my-project                                    │
│   server:  https://api.loud.codes                          │
│                                                            │
│   /help para comandos · Esc detiene el agente · Ctrl+C sale│
│                                                            │
╰────────────────────────────────────────────────────────────╯

loud❯ refactor utils.py so the parsing logic is in a separate module
```

### Daily usage

```bash
loud                              # interactive REPL
loud "explain the bug in app.py"  # one-shot prompt
loud login                        # auth
loud whoami                       # check session
loud --model loud-pro             # switch model: loud-go · loud-pro · loud-ultra
loud --reset                      # clear session history
loud update                       # self-update
loud --version
```

### Slash commands inside the REPL

| | |
|---|---|
| `/help` | list commands |
| `/reset` | clear history |
| `/model NAME` | switch model |
| `/tools` | list available tools |
| `/permissions [ask\|yolo\|safe]` | view / change permission mode |
| `/save FILE` | export conversation to markdown |
| `/cwd` | print current directory |
| `/exit` | quit |

---

## Tools

The agent has full access to your machine through these tools. Destructive operations are gated by the [permission system](#permissions).

| Tool | Description | Permission |
|---|---|:---:|
| **`bash`** | Run a shell command on the local machine. | 🔐 |
| **`ssh`** | Run a command on a remote host. | 🔐 |
| **`read_file`** | Read a text file. | ✅ |
| **`write_file`** | Create or overwrite a file. | 🔐 |
| **`edit_file`** | Replace first occurrence of `old` with `new`. | 🔐 |
| **`glob`** | Find files matching a pattern. | ✅ |
| **`grep`** | Recursive search (uses ripgrep if installed). | ✅ |
| **`ls`** | Show `pwd` + directory contents. | ✅ |
| **`http_get`** | Fetch the body of a URL. | ✅ |

The CLI auto-detects destructive `bash` commands by pattern (`rm -rf`, `sudo`, `curl | sh`, `git push --force`, `terraform destroy`, etc.) and prompts even in default mode.

---

## Permissions

Three modes, set during first-run setup or with `/permissions <mode>`:

```
┌──────┬─────────────────────────────────────────────────────────────────┐
│ ask  │ prompts on every destructive call (default, recommended)        │
│      │ [y]es · [n]o · [a]lways · [s]top                                │
│      │ "always" is cached per command-prefix + per directory           │
├──────┼─────────────────────────────────────────────────────────────────┤
│ yolo │ never asks, executes everything (use only on disposable boxes)  │
├──────┼─────────────────────────────────────────────────────────────────┤
│ safe │ refuses every destructive tool — read-only agent                │
└──────┴─────────────────────────────────────────────────────────────────┘
```

State lives in:

```
~/.loud/
├── auth.json           # JWT + user        (0600 on Unix)
├── config.json         # api_url, model, permission_mode
├── permissions.json    # cached "always allow" decisions
└── current_session.json
```

---

## Models

The CLI exposes a set of aliases that map to whatever the backend has loaded.
Since **LOUD 2.0** (May 2026) the server runs on an NVIDIA **L40S 48 GB GPU**,
which unlocks 32B-class models at production latency.

| Alias | Default backing | Use for |
|---|---|---|
| **`loud-go`** | qwen2.5:3b | fast chat, quick refactors |
| **`loud-pro`** | qwen2.5:7b | reasoning, multi-file edits |
| **`loud-ultra`** | qwen2.5:14b | deep refactors, complex debugging |
| **`loud-2.0`** ⚡ | qwen2.5:32b | LOUD 2.0 — the new flagship · GPU only |
| **`loud-eye`** 👁️ | qwen2-vl:7b | screenshots & images (vision) |

Switch with `loud --model loud-2.0` or `/model loud-2.0` inside the REPL.

> The "loud-2.0" and "loud-eye" aliases require the GPU backend. If you're
> running the CLI against a CPU-only LOUD server, only the first three are
> available.

---

## Security

- Internal IPs and infra hostnames are **scrubbed** from every line the CLI prints.
- Auth lives in `~/.loud/auth.json` with `0600` perms on Unix.
- The CLI does **not** phone home. Every byte goes to your LOUD server.
- The agent never names external model providers in its output.
- Destructive operations route through the permission gate unless you explicitly opted into `yolo`.

---

## Self-update

The CLI keeps itself up to date — **no need to uninstall and reinstall**.

```bash
loud update         # check + install the latest version
```

Or from inside the REPL:

```
loud❯ /update
```

What it does:
- Detects how you installed it (Homebrew · curl-installer · git clone · Windows ps1)
- Re-runs the right updater path
- Preserves your `~/.loud/` config, sessions, and permissions
- Survives all your settings — only the binary swaps

There's a **quiet daily check** baked in: if a newer release is on `main`,
the welcome banner shows `↑ nueva versión disponible: X — corre 'loud update'`.
So you'll always know without polling manually.

### Manual one-liners (if `loud update` ever fails)

```bash
# macOS · Linux
curl -fsSL https://loud.codes/install.sh | bash

# macOS Homebrew
brew update && brew upgrade loud

# Windows
iwr -useb https://loud.codes/install.ps1 | iex
```

Your `~/.loud/auth.json` and config persist across updates.

---

## Uninstall

```bash
rm -rf ~/.loud
rm   -f ~/.local/bin/loud
# or
brew uninstall loud
```

---

## Roadmap

- [x] Streaming responses with typewriter rendering
- [x] Per-action permission gate with cached "always" decisions
- [x] Self-update (`loud update`)
- [x] Windows + macOS + Linux installers
- [x] **LOUD 2.0 — GPU backend with NVIDIA L40S 48GB**  *(May 2026)*
- [x] **`loud-2.0` model (qwen 32B) usable in production**
- [x] **`loud-eye` vision model — chat with screenshots**
- [ ] Local model fallback when the LOUD server is unreachable *(WIP)*
- [ ] Dreamer: autonomous background learning + daily report
- [ ] Built-in `diff` view for `edit_file` before applying
- [ ] Multi-file refactor sessions
- [ ] Shell completions (zsh / bash / fish / powershell)
- [ ] Subscriptions + paid tier integration

---

## Changelog

### v0.5.0 — Vision + authority · 2026-05-22

- 👁️ **Vision live in production** — drop any `.jpg/.png/.webp/.gif/.bmp` into
  the web chat and LOUD describes + reasons about it (not just captioning,
  actual reasoning about what you want from the image)
- 🔀 **Auto-routing** — when an image is attached, the backend auto-swaps to
  `loud-eye` (llama3.2-vision:11b) without the user having to pick the model
- 🧠 **System prompt strengthened** — LOUD now recognizes Nassib (owner) as
  absolute authority. Admin role users get 100% obedience, no hedging
- ⚙️ Backend: `/v1/chat` accepts an `images: [base64,…]` field and forwards
  it to Ollama's chat API natively
- 📦 Frontend: images travel as base64 in a separate field (not embedded in
  text), so the model sees them as actual pixels not as garbage text

### v0.4.0 — LOUD 2.0 GPU release · 2026-05-22

- 🚀 **Backend migrated to NVIDIA L40S 48 GB GPU** — 3–10× faster across all models
- ⚡ **New model `loud-2.0`** (qwen 32B) — flagship quality at production speed
- 👁️ **New model `loud-eye`** — vision (qwen2-vl), drop screenshots into chat
- 📝 `/update` slash command added inside the REPL
- 📋 README + roadmap updated with the LOUD 2.0 timeline

### v0.3.0 — Agentic CLI · 2026-05-21

- Streaming chat with token-by-token typewriter rendering
- Local tool suite: bash · ssh · read_file · write_file · edit_file · glob · grep · ls · http_get
- Per-action permission system: ask / yolo / safe
- Welcome banner with status panel (session · model · permissions · cwd · server)
- First-run setup wizard
- `loud update` self-updater + daily background check
- Cross-platform installers (macOS · Linux · Windows)

### v0.1.0 — Initial release · 2026-05-19

- Basic REPL connected to the LOUD API
- Login / logout / whoami
- Three model aliases: loud-go · loud-pro · loud-ultra

---

<div align="center">

**Built for builders who don't want their code to leave the building.**

[loud.codes](https://loud.codes) · MIT license

</div>
