<div align="center">

```
  ██╗      ██████╗ ██╗   ██╗██████╗
  ██║     ██╔═══██╗██║   ██║██╔══██╗
  ██║     ██║   ██║██║   ██║██║  ██║
  ██║     ██║   ██║██║   ██║██║  ██║
  ███████╗╚██████╔╝╚██████╔╝██████╔╝
  ╚══════╝ ╚═════╝  ╚═════╝ ╚═════╝
```

**Terminal-first AI agent · self-hosted · cross-platform**

[loud.codes](https://loud.codes) · [README en español](README.es.md)

</div>

LOUD is a private AI that runs on your own infrastructure and follows you into the terminal. Same idea as Claude Code or Cursor, but the model lives on **your** server — no third-party data flow.

The `loud` CLI is the on-machine agent. It:

- talks to your LOUD API over the network,
- holds a streaming conversation with token-by-token typewriter rendering,
- reads, writes, and edits files on your machine,
- runs shell commands (with permission prompts on destructive ones),
- SSHes into your servers.

LOUD is **invite-only** while it's in private beta — you need an account on a running LOUD server before the CLI is useful.

---

## Install

### macOS / Linux — one-liner

```bash
curl -fsSL https://loud.codes/install.sh | bash
```

### macOS — Homebrew

```bash
brew tap loud-codes/cli
brew install loud
```

### Windows — PowerShell

```powershell
iwr -useb https://loud.codes/install.ps1 | iex
```

### Manual

```bash
git clone https://github.com/loud-codes/loud-cli.git ~/.loud/install/src
python3 -m venv ~/.loud/install/venv
~/.loud/install/venv/bin/pip install httpx
echo '#!/usr/bin/env bash
exec ~/.loud/install/venv/bin/python3 ~/.loud/install/src/cli/loud.py "$@"' > ~/.local/bin/loud
chmod +x ~/.local/bin/loud
```

---

## First run

```
$ loud
```

The CLI walks you through a 30-second setup: it confirms the server URL (default `https://api.loud.codes`), asks you to pick a permission mode (`ask` / `yolo` / `safe`), and logs you in with your LOUD username (or email) + password. After that you land in an interactive REPL with a status panel and the `loud❯` prompt.

---

## Daily usage

```bash
loud                              # interactive REPL
loud "fix the test that broke"    # one-shot
loud login
loud logout
loud whoami
loud --model loud-pro             # switch model: loud-go | loud-pro | loud-ultra
loud --reset                      # clear session history
loud --version
```

### Slash commands (inside the REPL)

| Command | Description |
|---|---|
| `/help` | list commands |
| `/reset` | clear conversation history |
| `/model NAME` | switch model (`loud-go`, `loud-pro`, `loud-ultra`) |
| `/tools` | list tools the agent can call |
| `/permissions [ask\|yolo\|safe]` | view or change permission mode |
| `/save FILE` | export conversation to markdown |
| `/cwd` | print current directory |
| `/exit` | quit |

---

## Tools the agent can use locally

| Tool | What it does | Permission gate |
|---|---|---|
| `bash` | run a shell command | yes for `rm`, `mv`, `sudo`, `curl \| sh`, `git push --force`, `terraform`, … |
| `ssh` | run a command on a remote host | always |
| `read_file` | read a text file | no |
| `write_file` | create or overwrite a file | always |
| `edit_file` | replace the first occurrence of `old` with `new` | always |
| `glob` | list files matching a pattern | no |
| `grep` | recursive search (uses ripgrep if installed) | no |
| `ls` | show pwd + directory contents | no |
| `http_get` | fetch a URL body | no |

### Permission modes

- **ask** (default): every destructive call shows the command and asks `[y]es / [n]o / [a]lways / [s]top`. `always` is remembered per command-prefix and per directory in `~/.loud/permissions.json`.
- **yolo**: never asks. Use only on a fresh machine you control.
- **safe**: refuses every destructive tool. Read-only agent.

State is kept in `~/.loud/`:

```
~/.loud/
├── auth.json           # JWT + user
├── config.json         # api_url, model, permission_mode, …
├── permissions.json    # cached "always allow" decisions
└── current_session.json
```

---

## Models

The CLI exposes three aliases that map to whatever the backend has loaded:

| Alias | Backs onto | Use for |
|---|---|---|
| `loud-go` | qwen2.5:3b | fast chat, quick refactors |
| `loud-pro` | qwen2.5:7b | reasoning, multi-file edits |
| `loud-ultra` | qwen2.5:14b | deep refactors, complex debugging |

---

## Security

- Internal IPs and infra hostnames are scrubbed from every line the CLI prints.
- All destructive operations go through the permission gate unless you explicitly switched to `yolo`.
- Auth lives in `~/.loud/auth.json` with `0600` perms on Unix.
- The CLI does not phone home — every byte goes to **your** LOUD server.

---

## Uninstall

```bash
rm -rf ~/.loud ~/.local/bin/loud   # or loud.cmd on Windows
```

(Or `brew uninstall loud` if you used Homebrew.)

---

## License

MIT
