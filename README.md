<div align="center">

```
  ██╗      ██████╗ ██╗   ██╗██████╗
  ██║     ██╔═══██╗██║   ██║██╔══██╗
  ██║     ██║   ██║██║   ██║██║  ██║
  ██║     ██║   ██║██║   ██║██║  ██║
  ███████╗╚██████╔╝╚██████╔╝██████╔╝
  ╚══════╝ ╚═════╝  ╚═════╝ ╚═════╝
```

**Terminal-first AI for builders.**
Web dev · scraping · ops. Your tools, your machine, your control.

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)
[![loud.codes](https://img.shields.io/badge/web-loud.codes-ff6b35.svg)](https://loud.codes)

[Install](#install) · [Quickstart](#quickstart) · [Commands](#commands) · [Docs](https://loud.codes) · [🇪🇸 Español](README.es.md)

</div>

---

> ⚠️ **Private / invite-only.** LOUD is not a public service. Installing the
> CLI is free, but using it requires credentials issued by a LOUD admin.
> Without an account, `loud login` will fail.

## What is LOUD?

LOUD is a private AI agent run from your terminal. It connects to a hosted
inference backend (running a large open-source LLM on dedicated infra) and
executes tools **locally on your machine** — so it can read your files, run
your scripts, SSH into your servers, and ship code.

It ships with deep specialization in:

- 🌐 **Web development** — Python/FastAPI/Django, Node/TS, PHP/Laravel, frontend
- 🕷 **Scraping & automation** — Playwright, BeautifulSoup, session-aware crawling
- ⚙️ **DevOps** — Terraform, AWS, DigitalOcean, nginx, Cloudflare, IPFS

## Install

### macOS / Linux (Homebrew)

```bash
brew tap loud-codes/cli
brew install loud
```

### macOS / Linux (one-line curl)

```bash
curl -fsSL https://loud.codes/install.sh | bash
```

### Windows (PowerShell)

```powershell
iwr -useb https://loud.codes/install.ps1 | iex
```

### Pip (any OS with Python 3.10+)

```bash
pip install loud-cli
```

## Quickstart

```bash
loud login                          # email + password (admin invites you)
loud "summarize what's in this repo"
loud "fix the type error in src/auth.ts"
loud "ssh prod and tell me disk usage"
loud                                # interactive REPL
```

## Commands

### Authentication

| Command | What it does |
|---|---|
| `loud login` | Log in (email + password) |
| `loud logout` | Clear local token |
| `loud whoami` | Show current user |

### Chat

| Command | What it does |
|---|---|
| `loud "<prompt>"` | One-shot question |
| `loud` | Interactive REPL |
| `loud --reset` | Clear conversation history |
| `loud --model NAME` | Switch model (qwen2.5:7b, llama3.3:70b, …) |
| `loud --api-url URL` | Point to a custom backend |

### Inside the REPL

| Slash | What it does |
|---|---|
| `/help` | Show commands |
| `/reset` | Clear history |
| `/model X` | Switch model |
| `/tools` | List tools |
| `/save FILE` | Export conversation |
| `/exit` | Quit |

### User management (admin)

| Command | What it does |
|---|---|
| `loud users list` | List all users |
| `loud users create EMAIL PW [ROLE] [NAME]` | Invite a user |
| `loud users delete ID` | Remove a user |
| `loud users password ID NEW` | Reset password |

## Tools available to LOUD

When you ask LOUD to do something, it autonomously chains these tools:

- `bash` — run shell commands locally (or `powershell` on Windows)
- `ssh` — connect to any host in `~/.ssh/config`
- `read_file` / `write_file` — touch local files
- `grep` — recursive search (uses ripgrep if installed)
- `http_get` — fetch any URL
- `pwd_ls` — orient itself in the current directory

## Project context

Drop a `LOUD.md` in your repo root (similar to `CLAUDE.md` or `CURSOR.md`)
and LOUD will read it on every session. Examples:

```markdown
# LOUD.md

This is a Next.js 15 app deployed to Vercel. The API lives at api.example.com.
Use pnpm. Tests run with `pnpm test`. Don't touch prisma/migrations/* directly.
```

You can also put user-specific files in `~/.loud/context/*.md` that apply across
all projects.

## Privacy

- Prompts and files you share are sent only to your configured backend.
- The default backend (`api.loud.codes`) runs your model on dedicated
  infrastructure — no third-party LLM APIs in the loop.
- No telemetry. No data retention beyond your own local history (`~/.loud/`).

## Configuration

LOUD stores config in `~/.loud/`:

```
~/.loud/
├── config.json              # api_url, model, num_ctx
├── auth.json                # JWT token (chmod 600)
├── current_session.json     # local conversation history
└── context/                 # your private context files (loaded by LOUD)
```

## License

[MIT](LICENSE) © [loud.codes](https://loud.codes)
