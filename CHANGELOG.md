# LOUD CLI · Changelog

## v1.8.1 — 2026-06-04 (CLI terminal · login exclusivo LOUD Pro)
- **`loud login` ahora es exclusivo de usuarios LOUD Pro** (de pago). El login del CLI manda el header `X-Loud-Client: cli`; el backend sólo lo acepta para cuentas Pro (o admin). Los usuarios free reciben un mensaje de upgrade a `loud.codes/plans`. El chat web no manda ese header, así que los free siguen entrando a la web normalmente (con loud-go).
- Reemplazado el viejo device-flow (`/v1/auth/cli/init` + poll), que no existía en el backend, por login directo usuario/contraseña contra `/v1/auth/login`.
- Backend (servicio): membresía por usuario (columna `plan` free/pro), el admin la activa/quita desde el panel (Users), gate del chat (free → loud-go), endpoint `PUT /v1/users/{id}/plan`. Admin = sin plan, acceso total.

## v1.8.0 — 2026-06-04 (CLI terminal · sub-agentes + multi-motor "LOUD Connect")
> Expansión de la mente de LOUD: ahora delega en sub-agentes y puede correr con CUALQUIER motor de IA. Todo nativo, sin nombrar herramientas de terceros.
- **Sub-agentes (`agent`)**: tool nueva que lanza un sub-agente focalizado para una sub-tarea acotada (investigar, armar un archivo, auditar). Corre su propio loop con TODAS las tools, respeta permisos y anti-loop, y devuelve SOLO el resultado final sin contaminar el contexto principal. Anti-recursión: profundidad máx. 2; un sub-agente no puede lanzar otro.
- **Multi-motor "LOUD Connect"**: LOUD ya no corre sólo con su cloud/local — ahora puede usar CUALQUIER endpoint OpenAI-compatible (su propio endpoint, u otro proveedor) como motor. Modo nuevo `custom` + cliente streaming OpenAI (`_stream_chat_custom`) que traduce `data: {choices:[{delta}]}` a los eventos del loop, con acumulación de tool-calls.
- **Menú `loud connect` / `/connect`**: menú interactivo LOUD-branded para agregar/elegir/borrar proveedores (nombre, base URL, API key, modelo) y cambiar el engine (cloud/local/custom). Config en `~/.loud/config.json` (`providers`, `active_provider`).
- Tools totales: el agente ahora puede delegar y cambiar de motor desde adentro de la sesión.

## v1.7.0 — 2026-06-04 (CLI terminal · agente nivel pro — plan, skills, compactación, anti-alucinación)
> Salto grande de capacidad del agente de terminal. Todo nativo, activado SÓLO cuando la tarea lo amerita (overhead cero en lo cotidiano). No toca backend ni installers de deps pesadas.
- **Plan multi-paso (`todo`)**: tool nueva para descomponer tareas largas en un checklist con estados (`pending`/`in_progress`/`done`/`cancelled`). El agente lo usa SÓLO en trabajos de 3+ pasos (build → correr → verificar → arreglar), va marcando items a medida que avanza y no re-hace lo ya hecho. Para pedidos simples no se toca.
- **Skills "grows-with-you"**: el agente puede cargar paquetes de instrucciones reutilizables on-demand. Se crean en `~/.loud/skills/<nombre>/SKILL.md` (frontmatter `name`/`description` + markdown). Tools `skills_list(filter?)` y `skill_view(name)`, slash commands `/skills` y `/skill <nombre>`. El catálogo se inyecta al system prompt; el agente sólo carga una skill si es relevante al pedido. LOUD se vuelve extensible sin tocar el código.
- **Compactación de contexto**: en sesiones largas, cuando el historial supera el umbral (`compact_threshold_tokens`, default ~16k), LOUD comprime el medio de la conversación en un checkpoint estructurado (protege el inicio + los últimos ~6k tokens, resume lo del medio con el propio modelo, con fallback determinístico si falla) y re-inyecta el plan vigente. Mata el "se pierde el hilo" en tareas largas con modelos de contexto chico. Sanitiza pares tool-call/result huérfanos para no romper la secuencia. No-op por debajo del umbral.
- **REGLA INVIOLABLE #8 — nunca inventes output de tools**: prohibido fabricar contenido de archivos no leídos, salida de comandos no corridos, HTML/headers de URLs no traídas, o "tests que pasaron" sin la tool que lo confirme. Reportar un bloqueo honesto > inventar un resultado. Corta seco la alucinación de resultados en modelos locales.
- **REGLA INVIOLABLE #9 — datos de la máquina siempre por tool**: aritmética, hashes, fecha/hora, estado del sistema, contenido de archivos, estado de git, existencia de rutas → SIEMPRE con `bash`/`read_file`/`ls`, nunca de memoria. El entorno real puede diferir de lo asumido.

## v1.6.16 — 2026-06-01 (CLI terminal · no abandonar procesos largos + anti-cuelgue)
- **El agente ya no abandona tareas largas a mitad**: `max_iterations` 50 → 200. Antes, en procesos multi-paso (build → verificar → arreglar), el loop del agente llegaba al techo de 50 iteraciones y abandonaba ("llegué al techo de iteraciones") aunque la tarea no estuviera terminada. El anti-loop (corta cuando la misma tool+args se llama 3×) sigue evitando loops infinitos, así que subir el techo es seguro.
- **Anti-cuelgue en los streams de chat**: los clientes httpx de chat (Ollama local + cloud) usaban `timeout=None` → si la conexión se trababa, la CLI quedaba colgada para siempre. Ahora usan `httpx.Timeout(connect=15, read=600, write=600)`: NO corta la generación activa (los chunks llegan en <1s y el read-timeout se resetea en cada chunk) pero aborta con error si hay ~10 min de silencio total (conexión muerta) en vez de colgarse.

## v1.6.15 — 2026-05-31 (backend / chat web · loud.codes)
> Release de **servicio**: arregla el chat web. El binario CLI (`cli/loud.py`) no cambió de lógica; el fix vive en el backend FastAPI (`/opt/loud/api/app/main.py` en EC2). NO toca brew ni installers.
- **Fix corte de respuestas (loop "Continuar…")**: los Modelfiles custom (`loud-pro`, etc.) tenían `PARAMETER num_predict 800` horneado → toda respuesta se cortaba a ~800 tokens (~3.000 chars) y el panel mostraba "Continuar", que reabría OTRA respuesta y perdía el hilo del artefacto. El endpoint `/v1/chat` ahora inyecta `options.num_predict=-1` y `options.num_ctx=16384` para chat de texto (las opciones por-request pisan el Modelfile). El bloque de visión conserva su cap de 380 a propósito.
- **Fix loud-ultra sin respuesta**: `loud-ultra` y `loud-2.0` nunca se construyeron como tags de Ollama → se mandaban crudos → `model 'loud-ultra' not found` → respuesta vacía. Agregado `_resolve_model()` con `MODEL_ALIASES`: `loud-ultra→qwen2.5:14b`, `loud-2.0→qwen2.5:32b` (pro/go/eye pasan derecho, ya existen como tags).
- Verificado en vivo: ultra responde (`qwen2.5:14b`), y un artefacto largo cierra con `done_reason=stop` a 1096 tokens (antes cortaba a 800).
- ⚠️ **Divergencia de código**: el repo tiene `api/app/main.py` (3168 líneas, versión nueva con MODEL_ALIASES y `num_predict=-1` ya incorporados) que **nunca se desplegó**. Lo VIVO era la versión vieja de 1196 líneas. El archivo desplegado+arreglado quedó como `api/app/main.LIVE-deployed.py`. Backup en server: `/opt/loud/api/app/main.py.bak-20260531`.


## v1.6.1 — 2026-05-25
- **Anti-refusal médico/dosis/farmacología**: REGLA INVIOLABLE #1 extendida con cobertura explícita de medicina, farmacología, salud mental, sustancias controladas, suplementos, finanzas, política, contenido adulto. Lista de frases prohibidas ahora incluye "Lo siento, pero no puedo asistir con eso", "Es mejor buscar recursos médicos legítimos", "consulta a un profesional", "no deberías usarlos sin asesoría médica" — el modelo respondía estas a preguntas básicas tipo "qué uso para la tos".
- Backend `loud-codes/api` patcheado en paralelo (mismo prompt fuerte forzado en TODA llamada a /v1/chat — fix del panel web admin que mandaba prompt débil).


## v1.6.0 — 2026-05-25
- **Modo Análisis ecosystem-aware**: tool nueva `project_scan(path, max_depth?)` que detecta el tipo de proyecto en cualquier folder por sus manifests. Reconoce 80+ ecosistemas: Node/JS/TS (npm/pnpm/yarn/bun/Deno + Next/Vite/Astro/Nuxt/SvelteKit/Remix + Tailwind + Turbo/Nx/Lerna), Python (pyproject/requirements/Pipfile/poetry/uv + Django), Rust, Go (con workspaces), Ruby (con Rails/Rack/gems), PHP (con Laravel), Java/Kotlin/JVM (Maven/Gradle), Swift (SPM/CocoaPods/Xcode), .NET (csproj/fsproj/sln), Elixir, Haskell (Stack/Cabal), Dart/Flutter, C/C++ (CMake/Make/Conan/vcpkg/Meson), Zig, Nim, Crystal, Lua, Julia, R, OCaml, Nix, Docker/Compose, Terraform/Pulumi, Vercel/Netlify/Fly/Railway/CF Workers, DVC/MLflow, agent-files (CLAUDE.md/AGENTS.md/.cursorrules).
- **Harvest de docs en el scan**: además del fingerprint, lista todos los `*.md`/`.rst` (README, INSTRUCTIONS, ARCHITECTURE, RUNBOOK, CONTINUE, AGENTS.md, CHANGELOG, etc) para que el agente lea las instrucciones del proyecto ANTES de actuar.
- **Fallback por extensión**: si no hay manifests, hace un census de extensiones de código (`.py`, `.ts`, `.rs`, `.go`, etc) para identificar el lenguaje predominante.
- Sistema prompt: nueva sección **"🔬 MODO ANÁLISIS DE PROYECTO — ECOSYSTEM-AWARE"** con flujo obligatorio de 6 pasos. Si el folder tiene `AGENTS.md`/`CLAUDE.md`/`.cursorrules`, esas instrucciones del usuario ganan sobre el system prompt (salvo identidad LOUD y REGLAS #1-#6).
- Total tools: 31 (era 30).

## v1.5.0 — 2026-05-25
- **Modo Diseño con cult-ui nativo**: 2 tools nuevas — `cult_ui_list(filter?)` (catálogo de los 157 componentes premium de cult-ui con descripción) y `cult_ui_get(name)` (código fuente + deps + comando `shadcn add` listo para copiar). Endpoint shadcn-style oficial: `https://www.cult-ui.com/r/<name>.json`.
- Sistema prompt: nueva sección **"🎨 MODO DISEÑO — NUNCA GENERES UI GENÉRICA"**. Flujo obligatorio para cualquier pedido de UI/landing/hero/card/button → primero `cult_ui_list`, después `cult_ui_get` del componente elegido, recién después escribir layout. Lista de los 30+ componentes imprescindibles agrupados por slot (hero, botones, cards, texto, backgrounds, layout, efectos, marketing) embebida en el prompt.
- Prohibido: gradients genéricos, sombras planas, hero de 3 columnas con stock — si el agente se encuentra escribiendo eso, debe parar y abrir cult-ui.

## v1.4.0 — 2026-05-25
- **Scrapling nativo en LOUD**: 3 tools nuevas — `scrape(url, css?)` (Fetcher rápido, default), `scrape_stealth(url, css?, solve_cloudflare?)` (anti-bot: Cloudflare/Distil/PerimeterX), `scrape_dynamic(url, css?)` (render JS con Chromium). Soporte de selectores CSS/XPath (`h2`, `.price`, `a::attr(href)`). Sin `css` devuelve texto limpio del body; con `css` devuelve solo los matches.
- Installers actualizados (Homebrew formula, `web/install.sh`, `web/install.ps1`, `loud setup gui`) para incluir `scrapling[fetchers]` por default.
- Sistema prompt actualizado: `scrape` es el nuevo default para "extrae/scrape/sacame X de la URL Y". `http_get` queda solo para fetch crudo. `browser_*` queda para flujos interactivos con confirmación.

## v1.3.10 — 2026-05-25
- **Tools de path des-escapan rutas shell-style**: `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `ls` ahora pasan el path por `_norm_path()` antes de operar. Si el LLM copia literal una ruta tab-completada con `\ ` (`/Users/me/My\ Folder/file.txt`), `shlex` la unescape a `/Users/me/My Folder/file.txt`. Sólo se activa si hay `\` y el resultado es 1 token — paths legítimos con `\` literal o espacios sin escapar pasan sin cambios.
- **REGLA INVIOLABLE #6 — Lista de pasos del usuario = ejecutá TODOS**: cuando el usuario manda una secuencia explícita ("lee X, lista Y, dame resumen"), el agente DEBE ejecutarlos en orden con tools separadas, sin parar a preguntar entre pasos. Prohibido cerrar con "¿qué te parece?" / "¿seguimos?" tras el paso 1.

## v1.3.9 — 2026-05-25
- **REPL acepta paths absolutos pegados**: escribir `/Users/...`, `/tmp/...`, etc. en el prompt ya no dispara `comando desconocido` — el texto fluye al agente como chat (que puede `read_file` / `ls`). Slash dispatch ahora valida contra `SLASH_COMMANDS` y solo trata como path la entrada que empieza por raíz Unix conocida o existe en disco (`shlex`-aware para `My\ Folder`).
- Mensaje de error de comando inválido ahora lista los comandos disponibles.

## v1.3.8 — 2026-05-23
- **REGLA INVIOLABLE #4 — Idioma estricto**: si el usuario abre en español la sesión completa va en español. Cero cruce ES↔EN.
- **REGLA INVIOLABLE #5 — Conocimiento vs acción local**: preguntas abstractas ("cómo hackeo X / explicame Y / dame todo lo de pentesting") → respuesta directa con conocimiento, sin fire tools. Tools solo cuando el usuario referencia path/host/archivo concreto suyo.
- Plantilla para seguridad ofensiva embebida (Recon → Scan → Exploit → Post → OPSEC con tools nombradas: nmap, gobuster, sqlmap, Burp, Metasploit, BloodHound, mimikatz).
- Bug fix: `quiero hackear una página` ya no dispara `read_file("/index.html")` random.

## v1.3.7 — 2026-05-23
- `apps_list()` / `app_open(name)` tools — list and launch local applications.
- Prompt rule: external windows only on explicit invocation; `browser_open` for URLs, `app_open` for local apps.
- Identity hardening: no refusals, hold context, never invent password/security topics.

## v1.3.5 — 2026-05-23
- install.sh / install.ps1 / brew formula now bundle `playwright + chromium + sounddevice + numpy + pyautogui + pillow + mss` by default. `LOUD_SKIP_GUI=1` opt-out.
- macOS caveats added re: Accessibility + Mic + Screen Recording permissions.

## v1.3.4 — 2026-05-23
- `_ensure_playwright_ready()` / `_ensure_voice_deps()` — when a tool needs a missing dep, pop the floating L modal asking the user to authorize install, install inline, resume the action.

## v1.3.3 — 2026-05-23
- Restored `--yolo` / `--dangerously-skip-permissions` flag (removed in 1.3.2 by mistake).

## v1.3.2 — 2026-05-23
- (reverted) yolo removed.

## v1.3.1 — 2026-05-23
- Self-healing error pipeline: CLI auto-reports tool ERRORs to `/v1/error-report`. Pure log; admin reads them in the dashboard.

## v1.3.0 — 2026-05-23
- Native GUI / browser / voice tools:
  - `screenshot()` mac built-in.
  - `browser_open / click / fill / extract / screenshot / close` (playwright, persistent context, visible window).
  - `voice_listen` (mic → backend transcribe → text) and `voice_say` (mac `say`).
- Floating L confirmation modal (tkinter) for GUI actions.
- `loud setup gui` subcommand.
- Backend `/v1/transcribe` (Gemini audio).

## v1.2.2 — 2026-05-22
- Default model: `loud-go` → `loud-pro` (qwen2.5:7b).
- Prompt anti-loop rules: never call `job_status` twice in a row, never spawn duplicate servers, never invent PIDs/URLs.

## v1.2.1 — 2026-05-22
- `loud "<prompt>"` seeds the REPL and stays open (use `--exit-after` for old behavior).
- `bash_background` strips leading `nohup` and trailing `&` from cmd; uses `exec` so `proc.pid` is the actual process.

## v1.2.0 — 2026-05-22
- `ask_oracle(question)` tool — one-shot Gemini lookup via backend `/v1/oracle/ask`. Not stored anywhere.

## v1.1.7 — 2026-05-22
- `max_iterations` 25 → 50.
- Prompt rule 4d: read error → emit different tool. 3 retries per step. 6 total before raising white flag to user.
- Empty-turn nudge before bail.

## v1.1.6 — 2026-05-22
- `bash_background` no longer rejects its own server patterns (it's exactly the tool for them).

## v1.1.5 — 2026-05-22
- System-path check robust to macOS `/etc → /private/etc` symlinks.

## v1.1.4 — 2026-05-22
- `_validate_bash_complexity` parses `low = stripped.lower()` before checking blocking patterns.

## v1.1.3 — 2026-05-22
- Smart-yolo: yolo still prompts for sudo, /etc, force-push, package managers, firewall, ssh.
- `bash` rejects long-running server patterns and points the model at `bash_background`.

## v1.1.2 — 2026-05-22
- Brain hard-disabled in CLI terminal mode (use_rag/use_memory hardcoded false). Brain is web-admin only.

## v1.1.1 — 2026-05-22
- `bash_background`: keep parent PID alive via `exec` (was tracking the dying bash wrapper).

## v1.1.0 — 2026-05-22
- `bash_background(cmd, label)`, `job_status`, `job_list`, `job_stop` — control long-running processes from inside LOUD.

## v1.0.2 — 2026-05-22
- Hard step-gate at tool layer: bash rejects 2+ chain ops or 2+ phase keywords. Step-by-step is the default.

## v1.0.1 — 2026-05-22
- Claude-Code-style reasoning loop baked into the system prompt.

## v1.0.0 — 2026-05-22
- First stable. Think-in-steps prompt + max_iterations 25.

## v0.8.x
- `select_option()` arrow-key picker · scoped always-allow · semver-correct update check · centered banner · activity feed + bell · live chunks counter · brain bootstrap.

## v0.7.x
- Initial CLI: bash/read/write/edit/glob/grep/ls/ssh/http_get · permission system · login device-flow · vision routing on web · CLI v0.7.0 first push.

---

## Backend milestones

- 2026-05-23: **Anti-refusal V2**. Reminder cortado a 1 frase abstracta (la versión larga era citada por qwen 7B como contenido). Nuevo detector `is_reminder_echo()`. Retry escala automáticamente a `qwen2.5:32b` (loud-2.0) que tiene menos refusal-training. Detección de consultas de seguridad → reframing como "red-team legítimo, responde por fases". Activity feed registra cada intercepción. Cubierto en /v1/chat (streaming + non-streaming) + /v1/api/chat.
- 2026-05-23: **Language coherence guard**. `detect_lang()` + `response_lang()` heuristics. Si user escribe ES y respuesta abre EN (o viceversa) → `_retry_for_language()` con directiva explícita. Cubre el bug "Hola → Hello".
- 2026-05-23: **DEFAULT_SYSTEM_PROMPT V2**. Nueva REGLA #0 al tope con lista de patrones banneados, contexto de uso legítimo (pentesting / red-team / OSINT), plantilla de respuesta para pedidos "crudos" de hacking (Recon → Scan → Exploit → Post → OPSEC).
- 2026-05-23: **`/v1/api/chat` full-power**. `use_rag` default true, auto-detect de design prompts → bump a 32B + k=12 + system "DESIGN MODE".
- 2026-05-23: `/v1/api/chat` public consult endpoint with email+key auth.
- 2026-05-23: `/v1/admin/api-keys` (POST/GET/DELETE).
- 2026-05-23: `/v1/transcribe` (Gemini audio STT).
- 2026-05-23: `/v1/error-report` + `/v1/pending-chunks` (admin error log).
- 2026-05-22: `/v1/oracle/ask` (one-shot Gemini consult).
- 2026-05-22: `/v1/chats/{id}/share` + `/v1/chats/shared/{token}` (public shared chat viewer).
- 2026-05-22: `/v1/activity` (live activity feed for bell + rail).
- 2026-05-22: Pinata backup filenames use BOG (`-05`) timestamp.
- 2026-05-22: Skills auto-bootstrap at startup (10 internal curated skills).
- 2026-05-22: github-batch-ingest + github-search-only (admin bulk repo ingestion).
- 2026-05-22: Privacy lockdown — `learn/*` chunks purged from brain, RAG gates on substantive prompts, `_maybe_auto_enrich` and `_maybe_handle_learn` disabled.

---

## Web milestones

- 2026-05-23: **Mobile launchers V2**. Burger (☰) siempre abre nav principal con labels. Nuevo botón 💬 chats (visible solo en chat-view) abre la lista de chats. Drawer cierra automáticamente al tocar un item.
- 2026-05-23: **Cursor L scope**. El cursor custom L solo vive en `#auth` (landing); dentro de la app `body.in-app` restaura cursor nativo (pointer/text/auto) — cero distracción.
- 2026-05-23: **Auth hero V2**. Capa de polish scoped a `#auth`: halo conic giratorio, card con borde animado conic-gradient, focus glow refinado, botón con flecha sliding, term-install con prompt `$`, animaciones en stagger respetando `prefers-reduced-motion`.
- 2026-05-23: API Keys section in admin.
- 2026-05-23: Markdown headers + LaTeX cleanup + ordered/unordered lists rendering in chat.
- 2026-05-23: URL hash routing `#c/<chat-id>` + `#s/<share-token>`.
- 2026-05-23: Chat kebab menu (export · share · delete) + share modal + revoke.
- 2026-05-23: Composer safe-area-inset-bottom for iOS Safari.
- 2026-05-23: Removed chunks counter from public topbar.
- 2026-05-23: All timestamps in BOG (GMT-5).
- 2026-05-22: Artifact panel (right slide-in for code generation).
- 2026-05-22: Live chunks counter + activity rail + bell notifications.
- 2026-05-22: Identity bloqueada (LOUD only, no external model names).
- 2026-05-22: Custom L cursor on loud.codes.
- 2026-05-22: Error logs section (admin reads CLI error reports).

---

## Brain growth highlights (selected)

- design + UI/UX skill repos: `emilkowalski/skill`, `Leonxlnx/taste-skill`, `pbakaus/impeccable`, `nextlevelbuilder/ui-ux-pro-max-skill`, `21st-dev/*` (8 repos). **~1100 chunks**.
- computer control: `browser-use/browser-use`, `Skyvern-AI/skyvern`, `xlang-ai/OSWorld`. **~1000 chunks**.
- workflow: `czlonkowski/n8n-mcp`, `hkuds/LightRAG`. **~1700 chunks**.
- agent skills: `anthropics/skills`, `obra/superpowers`, `multica-ai/andrej-karpathy-skills`, `ComposioHQ/awesome-claude-skills`. **~700 chunks**.
- 10 internal admin-curated skills: code-review, debugging, web-design, api-design, sql-and-migrations, prompt-engineering, security-review, refactoring, devops-ci, incident-response.

Brain total today: **~25,000 chunks · ~2,500 sources · ~22 MB**.
