<div align="center">

```
  ██╗      ██████╗ ██╗   ██╗██████╗
  ██║     ██╔═══██╗██║   ██║██╔══██╗
  ██║     ██║   ██║██║   ██║██║  ██║
  ██║     ██║   ██║██║   ██║██║  ██║
  ███████╗╚██████╔╝╚██████╔╝██████╔╝
  ╚══════╝ ╚═════╝  ╚═════╝ ╚═════╝
```

**IA terminal-first para devs.**
Web dev · scraping · ops. Tus tools, tu máquina, tu control.

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)
[![loud.codes](https://img.shields.io/badge/web-loud.codes-ff6b35.svg)](https://loud.codes)

[Instalación](#instalación) · [Quickstart](#quickstart) · [Comandos](#comandos) · [Docs](https://loud.codes) · [🇬🇧 English](README.md)

</div>

---

## ¿Qué es LOUD?

LOUD es un agente AI privado que corres desde tu terminal. Se conecta a un backend
de inferencia hosted (corre un LLM open-source grande) y ejecuta tools
**localmente en tu máquina** — entonces puede leer tus archivos, correr tus
scripts, hacer SSH a tus servidores y escribir código. Misma UX que las
herramientas AI que usas, pero tú controlas el modelo y los datos.

Viene especializada en:

- 🌐 **Desarrollo web** — Python/FastAPI/Django, Node/TS, PHP/Laravel, frontend
- 🕷 **Scraping & automatización** — Playwright, BeautifulSoup, crawling con sesión
- ⚙️ **DevOps** — Terraform, AWS, DigitalOcean, nginx, Cloudflare, IPFS

## Instalación

### macOS / Linux (Homebrew)

```bash
brew tap loud-codes/cli
brew install loud
```

### macOS / Linux (curl una línea)

```bash
curl -fsSL https://loud.codes/install.sh | bash
```

### Windows (PowerShell)

```powershell
iwr -useb https://loud.codes/install.ps1 | iex
```

### Pip (cualquier OS con Python 3.10+)

```bash
pip install loud-cli
```

## Quickstart

```bash
loud login                          # email + password (admin te invita)
loud "resume qué hay en este repo"
loud "arregla el type error en src/auth.ts"
loud "ssh prod y dime el uso de disco"
loud                                # REPL interactiva
```

## Comandos

### Autenticación

| Comando | Qué hace |
|---|---|
| `loud login` | Login (email + password) |
| `loud logout` | Borrar token local |
| `loud whoami` | Ver usuario actual |

### Chat

| Comando | Qué hace |
|---|---|
| `loud "<pregunta>"` | One-shot |
| `loud` | REPL interactiva |
| `loud --reset` | Borrar historial |
| `loud --model NAME` | Cambiar modelo |
| `loud --api-url URL` | Apuntar a otro backend |

### Dentro de la REPL

| Slash | Qué hace |
|---|---|
| `/help` | Ver comandos |
| `/reset` | Borrar historial |
| `/model X` | Cambiar modelo |
| `/tools` | Listar tools |
| `/save FILE` | Exportar conversación |
| `/exit` | Salir |

### Gestión de usuarios (admin)

| Comando | Qué hace |
|---|---|
| `loud users list` | Listar usuarios |
| `loud users create EMAIL PW [ROLE] [NAME]` | Crear usuario |
| `loud users delete ID` | Borrar |
| `loud users password ID NEW` | Resetear password |

## Tools disponibles para LOUD

Cuando le pides algo, encadena estas tools automáticamente:

- `bash` — comandos shell locales (`powershell` en Windows)
- `ssh` — conectar a cualquier host de `~/.ssh/config`
- `read_file` / `write_file` — leer/escribir archivos locales
- `grep` — búsqueda recursiva (usa ripgrep si está)
- `http_get` — descargar cualquier URL
- `pwd_ls` — orientarse en el directorio actual

## Contexto del proyecto

Pon un `LOUD.md` en la raíz de tu repo (como `CLAUDE.md` o `CURSOR.md`)
y LOUD lo lee en cada sesión. Ejemplo:

```markdown
# LOUD.md

Esto es un Next.js 15 desplegado en Vercel. La API está en api.example.com.
Usa pnpm. Los tests corren con `pnpm test`. No toques prisma/migrations/* directo.
```

También puedes meter archivos personales en `~/.loud/context/*.md` que aplican
a todos tus proyectos.

## Privacidad

- Tus prompts y archivos solo se mandan al backend que configures.
- El backend default (`api.loud.codes`) corre el modelo en infra dedicada —
  sin APIs LLM de terceros en el medio.
- Sin telemetría. Solo se guarda tu historial local (`~/.loud/`).

## Configuración

LOUD guarda en `~/.loud/`:

```
~/.loud/
├── config.json              # api_url, model, num_ctx
├── auth.json                # JWT token (chmod 600)
├── current_session.json     # historial local
└── context/                 # tu contexto privado (leído por LOUD)
```

## Licencia

[MIT](LICENSE) © [loud.codes](https://loud.codes)
