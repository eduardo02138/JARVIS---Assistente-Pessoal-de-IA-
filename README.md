# JARVIS AI Assistant — Gemini Live + Google ADK for Linux

<p align="center">
  <strong>A multimodal, real-time personal AI assistant with voice, screen vision, secure tool calling, Linux automation and agent orchestration.</strong>
</p>

<p align="center">
  <a href="README.md">English</a> · <a href="README.pt-BR.md">Português (Brasil)</a>
</p>

<p align="center">
  <a href="https://github.com/eduardo02138/JARVIS---Assistente-Pessoal-de-IA-/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/eduardo02138/JARVIS---Assistente-Pessoal-de-IA-/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11%2B-blue">
  <img alt="Platform" src="https://img.shields.io/badge/platform-Linux-informational">
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-green"></a>
</p>

<p align="center">
  <img src="assets/jarvis-hud-interface.png" alt="JARVIS holographic web HUD with real-time AI assistant controls" width="49%">
  <img src="assets/gemini-live-widget.png" alt="JARVIS Gemini Live desktop widget for Linux" width="49%">
</p>

## What is JARVIS?

**JARVIS** is an open-source personal AI assistant for Linux built around **Gemini Live**, the **Google Agent Development Kit (ADK)**, **FastAPI**, **WebSockets**, **PySide6** and a modular tool/plugin architecture.

It is designed for more than chat. JARVIS can maintain a **full-duplex voice conversation**, inspect screen context, route requests between specialized AI agents, call local tools, automate desktop actions and expose capabilities to external agents through **MCP** — while keeping sensitive actions behind an explicit **Policy Engine** and temporary control leases.

The project targets developers interested in **voice agents**, **multimodal AI**, **AI desktop assistants**, **agentic tool calling**, **Linux automation**, **Google ADK**, **Gemini Live API**, **Computer Use** and **Model Context Protocol (MCP)** integrations.

> **Project status:** active development. Interfaces and runtime contracts may evolve while the architecture is being hardened.

---

## Why this project is different

- 🎙️ **Real-time voice agent** — bidirectional audio streaming, barge-in and continuous conversation.
- 👁️ **Multimodal screen context** — screen vision and browser/Computer Use workflows.
- 🧠 **Google ADK agent orchestration** — fast path, coordinator path and specialist agents.
- 🛠️ **Secure tool calling** — local system tools and plugin actions are classified by risk.
- 🔐 **Fail-closed policy layer** — sensitive operations require authorization/confirmation instead of trusting the model directly.
- 🖥️ **Two user experiences** — holographic web HUD and native floating PySide6 desktop widget.
- 🧩 **Plugin + Skill architecture** — reusable capabilities can be exposed to Gemini/ADK without putting everything in one prompt.
- 🔌 **MCP server** — IDEs and external agents can discover and invoke JARVIS capabilities.
- 🐧 **Linux-first automation** — hardware telemetry, applications, media, browser control and desktop workflows.

---

## Quick look

### Holographic web HUD

`assets/jarvis-hud-interface.png` shows the browser experience served by FastAPI: voice controls, system telemetry, live assistant state and debugging information in a sci-fi HUD.

### Desktop Gemini Live widget

`assets/gemini-live-widget.png` shows the floating PySide6 widget designed for a lightweight “ask JARVIS” workflow on Linux/Wayland/X11.

---

## Example interactions

```text
“Jarvis, what is using the most CPU right now?”
“Jarvis, open my browser and inspect the current page.”
“Jarvis, search my workspace for the latest security email.”
“Jarvis, start a tactical timer for 90 seconds.”
“Jarvis, take a screenshot and tell me what is on screen.”
```

Tool availability depends on the enabled plugins and local configuration. Operations that can change external or privileged state are subject to the Policy Engine.

---

## Architecture

```text
                         ┌─────────────────────────┐
                         │       User / Voice      │
                         └────────────┬────────────┘
                                      │
                  ┌───────────────────┴───────────────────┐
                  │                                       │
          ┌───────▼────────┐                      ┌───────▼────────┐
          │   Web HUD      │                      │ PySide6 Widget │
          │ WebSocket/HTTP │                      │ Desktop Client │
          └───────┬────────┘                      └───────┬────────┘
                  └───────────────────┬───────────────────┘
                                      │
                             ┌────────▼─────────┐
                             │ FastAPI Runtime  │
                             │ server.py / ADK  │
                             └────────┬─────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    │                 │                 │
            ┌───────▼───────┐ ┌──────▼──────┐ ┌──────▼────────┐
            │ Gemini Live   │ │ Google ADK  │ │ Computer Use  │
            │ Voice/Media   │ │ Agents      │ │ Playwright    │
            └───────┬───────┘ └──────┬──────┘ └──────┬────────┘
                    │                 │                 │
                    └─────────────────┼─────────────────┘
                                      │
                             ┌────────▼─────────┐
                             │  Policy Engine   │
                             │ risk + leases +  │
                             │ confirmation     │
                             └────────┬─────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    │                 │                 │
             ┌──────▼──────┐  ┌──────▼──────┐  ┌──────▼──────┐
             │System Tools │  │   Plugins   │  │     MCP     │
             │Linux/OS/HW  │  │ + ADK Skills│  │server+client│
             └─────────────┘  └─────────────┘  └─────────────┘
```

A single FastAPI process (`server.py`) hosts everything: the native Gemini Live bridge, the Google ADK runtime (text and voice), the web clients and the debug dashboard. There is no separate ADK server anymore.

### Main components

| Component | Purpose |
| --- | --- |
| `server.py` | Entry point: builds the FastAPI app from the `servidor/` routers, mounts the web clients and re-exports the public names used by scripts and tests |
| `servidor/seguranca.py` | Session token, issued sessions (`/api/auth/session`), token checks and lease release |
| `servidor/runtime_adk.py` | ADK session/memory services, runner factory and key rotation |
| `servidor/rotas_sistema.py` | Health, providers, plugins, debug and preferences endpoints |
| `servidor/rotas_agente.py` | ADK text chat (`/api/chat`), pending confirmations and Computer Mode |
| `servidor/live_nativo.py` | Native Gemini Live WebSocket (`/ws/live`) with background tool execution |
| `servidor/live_adk.py` | ADK Live WebSocket (`/ws/live_adk`) |
| `live_protocolo.py` | Shared Live protocol helpers: the single confirm/deny parser, ping-timeout fix and clean-shutdown signal |
| `provider_router.py` | Provider selection: Google AI Studio key pool (primary) and OmniRoute failover (secondary, text only) |
| `transcricao.py` | Live input/output transcription config and the optional dedicated real-time transcriber |
| `agentes/` | ADK agents (fast path, coordinator, voice), rule-based router, tool adapters and persistent memory service |
| `agentes/computer_use/` | Browser automation / Gemini Computer Use integration with Playwright |
| `policy_engine.py` | Risk classification, confirmations and temporary control/IDE/computer leases |
| `system_tools.py` | Linux, hardware, media and local automation tools plus the Gemini function declarations |
| `controller_engine.py` | Virtual mouse/keyboard via `evdev`/`uinput` (degrades gracefully when unavailable) |
| `preferences_manager.py` | Persistent user preferences (default apps, music platform, game launchers) |
| `plugin_manager.py` / `plugin_sdk.py` | Plugin discovery, lifecycle and extension contracts |
| `plugins/` | Plugin runtime code (`plugins/<id>/plugin.py`) |
| `skills/` | ADK Skills (`skills/<skill-name>/SKILL.md` + `assets/`), the single source for skill instructions |
| `adk_skill_loader.py` | ADK Skill loading and SkillToolset integration |
| `jarvis_mcp_server.py` | MCP stdio server for IDEs and external agents |
| `mcp_client_manager.py` | MCP client: connects external MCP servers to the ADK agents as `McpToolset`s, with risk policies |
| `gemini_bridge.py` / `gemini/` | File-based bridge and audit log between JARVIS and the Antigravity IDE |
| `static/` | Holographic web HUD (served at `/`) |
| `static/common/` | JS shared by the HUD and the widget (session token, PCM audio, playback interruption, screen vision) |
| `static_adk/` | Lightweight ADK voice client (served at `/static_adk/`) |
| `gemini-live-widget/` | Floating desktop widget frontend (served at `/widget/`, wrapped by `app.py`) |
| `monitoring/` | Structured logging, debug dashboard (`/debug`), trust gates and regression tests |

### Runtime endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /` | Web HUD |
| `GET /widget/` | Desktop widget UI |
| `GET /static_adk/` | ADK voice client |
| `GET /debug` | Real-time debug dashboard |
| `WS /ws/live` | Native Gemini Live session (full Extended Thinking + NON_BLOCKING tools) |
| `WS /ws/live_adk` | Google ADK Live session (agents, memory, skills, MCP toolsets) |
| `POST /api/chat` | Text turn routed between the fast agent, the coordinator and Computer Use |
| `GET /api/health` | Models, key pool, active provider and MCP status |

Mutation endpoints and both WebSockets require `JARVIS_TOKEN`; local clients obtain it from `GET /api/auth/session` (loopback only).

---

## Core capabilities

### Real-time voice and Gemini Live

JARVIS streams microphone audio and model audio over WebSockets, supports natural interruption (barge-in), configurable voices and live session controls. The runtime is built so model configuration can evolve without coupling the UI to one hard-coded execution path.

Default models are configured in `.env`: `gemini-3.8-live` for voice, `gemini-3.8-live-extended-thinking` for background reasoning (`LIVE_THINKING_LEVEL`), `gemini-2.5-flash-native-audio-latest` as the voice fallback and `gemini-flash-latest` for text. The native `/ws/live` path supports the full Extended Thinking contract (thinking config + NON_BLOCKING tools); the ADK path uses the API defaults for that model.

### Providers and failover

`GEMINI_API_KEYS` accepts a comma-separated key pool: Live sessions rotate to the next key on quota or transient errors. For text chat, an optional local OmniRoute proxy (`OMNIROUTE_URL`) acts as the secondary provider and is used automatically when Google AI Studio is unavailable, or directly when selected in the HUD. Live voice always runs on Google.

### Google Agent Development Kit (ADK)

The native ADK layer provides routing between low-latency and coordinated agent paths, durable session support and modular skills. Active skills can be injected through `SkillToolset`, keeping the agent context smaller than loading every capability eagerly.

### Tool calling and Linux automation

System tools expose hardware telemetry, application control, screenshots, media actions and local automation. Plugins extend the catalog with workspace, research, game companion, smart-home, streaming and other specialized capabilities.

### Policy Engine and secure execution

The model does not receive unrestricted authority over the machine. Tool requests are evaluated by a policy layer that can classify operations as read-only, local write, external write or privileged and require confirmation/leases for sensitive actions.

### Screen vision and Computer Use

A dedicated Computer Use agent can operate a Chromium browser through Playwright. Browser control is intentionally separated from the general-purpose agent/tool set so browser authority can be governed independently.

### MCP integration

JARVIS speaks MCP in both directions:

- **Server** — `jarvis_mcp_server.py` exposes selected JARVIS capabilities over the **Model Context Protocol**, allowing compatible IDEs and AI agents to use JARVIS as a local tool server.
- **Client** — `mcp_client_manager.py` attaches external MCP servers (stdio, SSE or streamable HTTP) to the ADK agents. Copy `mcp_servers.example.json` to `mcp_servers.json` (or point `MCP_SERVERS_CONFIG` to a file); each server declares a `tool_filter` and per-tool risk levels, so external tools still go through the Policy Engine.

---

## Quick start

### Requirements

- Linux
- Python **3.11+**
- A Gemini API key
- Microphone for voice mode
- Chromium/Playwright only if using Computer Use

### 1. Clone

```bash
git clone https://github.com/eduardo02138/JARVIS---Assistente-Pessoal-de-IA-.git
cd JARVIS---Assistente-Pessoal-de-IA-
```

### 2. Create the environment

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt       # runtime
pip install -r requirements-dev.txt   # runtime + test tools (pytest)
```

### 3. Configure environment variables

```bash
cp .env.example .env
```

At minimum, configure your Gemini key and a local JARVIS session token:

```env
GEMINI_API_KEY="your_gemini_api_key"
# Optional key pool for automatic rotation: GEMINI_API_KEYS="key1,key2"
JARVIS_TOKEN="replace_with_a_long_random_secret"
JARVIS_HOST="127.0.0.1"
PORT=8000
```

`.env.example` documents every other setting (models, voice, language, leases, transcription, OmniRoute, MCP, memory). Do not commit `.env` or real API keys.

### 4. Start JARVIS

```bash
./run_jarvis.sh
```

Or:

```bash
.venv/bin/python server.py
```

Then open:

```text
http://127.0.0.1:8000          # web HUD
http://127.0.0.1:8000/debug    # debug dashboard
```

The Google ADK runtime is part of the same server: the ADK voice client is at `http://127.0.0.1:8000/static_adk/`, and ADK text turns go through `POST /api/chat`.

### Optional: desktop widget

```bash
./run_app.sh
```

`app.py` wraps `/widget/` in a frameless PySide6 window and starts the backend if it is not running.

### Optional: Computer Use

```bash
.venv/bin/playwright install-deps chromium
.venv/bin/playwright install chromium
```

---

## Plugins and ADK Skills

JARVIS uses a modular plugin architecture rather than hard-coding every integration in the central agent. Plugin code lives in `plugins/<id>/plugin.py`; the matching ADK Skill (`SKILL.md` plus optional `assets/`) lives in `skills/<skill-name>/`, so the model receives focused instructions only for enabled skills.

| Plugin | Skill | Status |
| --- | --- | --- |
| `game_companion` | `game-companion` | Real (local games, launchers, tactical timers) — enabled by default |
| `google_workspace` | `google-workspace` | Simulated (demo data) |
| `deep_research` | `deep-research` | Simulated (demo data) |
| `google_finance` | `google-finance` | Simulated (demo portfolio and quotes) |
| `ginjutsu_studio` | `ginjutsu-studio` | Simulated (motion/video AI demo) |
| `smart_home` | `smart-home` | Simulated (demo devices) |
| `live_stream` | `live-stream` | Simulated (demo chat/alerts) |
| `social_feed` | `social-feed` | Simulated (demo notifications) |

Simulated plugins are **disabled by default** so the assistant never reports invented data as real. Set `JARVIS_ATIVAR_MOCKS=1` to enable them for demos. Linux system and hardware tools are built in (`system_tools.py`) and always available.

---

## Testing and trust gates

The repository includes automated architecture, security and regression tests. These are the same suites the CI runs (install `requirements-dev.txt` first):

```bash
export GEMINI_API_KEY="ci-dummy-key-test" JARVIS_TOKEN="ci-secret-token-test-123"
PYTHONPATH=. .venv/bin/pytest monitoring/test_trust_gates.py monitoring/test_mcp_client.py \
    monitoring/test_live_protocolo.py monitoring/test_reproduction_p0.py -v
.venv/bin/python monitoring/test_suite.py --p0   # security & architecture gates (P0)
.venv/bin/python monitoring/test_adk.py          # Google ADK scenarios
```

No real API key is needed: the suites run offline with dummy credentials. GitHub Actions also runs a syntax check, an import smoke test and Gitleaks on every push and pull request to `main`.

---

## Project structure

```text
.
├── agentes/                 # Google ADK agents, router, tool adapters, memory, Computer Use
├── assets/                  # README screenshots / project media
├── gemini/                  # File bridge with the Antigravity IDE (examples tracked, runtime files ignored)
├── gemini-live-widget/      # Desktop widget frontend
├── monitoring/              # Logger, debug dashboard, trust gates and regression tests
├── plugins/                 # Plugin runtime code (plugins/<id>/plugin.py)
├── skills/                  # ADK Skills (skills/<skill-name>/SKILL.md + assets/)
├── servidor/                # Backend modules (security, ADK runtime, routes, Live WebSockets)
├── static/                  # Main holographic web HUD (+ static/common/ shared JS)
├── static_adk/              # ADK voice client
├── app.py                   # PySide6 desktop app (wraps the widget)
├── server.py                # Entry point: FastAPI app built from servidor/
├── live_protocolo.py        # Shared Live protocol helpers
├── provider_router.py       # Google AI Studio key pool + OmniRoute failover
├── transcricao.py           # Live transcription configuration
├── policy_engine.py         # Tool governance and authorization
├── system_tools.py          # Linux/system tools + function declarations
├── controller_engine.py     # Virtual mouse/keyboard (evdev/uinput)
├── preferences_manager.py   # Persistent user preferences
├── plugin_manager.py        # Plugin discovery and lifecycle
├── plugin_sdk.py            # Plugin contracts
├── adk_skill_loader.py      # ADK skill loader
├── gemini_bridge.py         # Antigravity file bridge and audit log
├── jarvis_mcp_server.py     # MCP server
├── mcp_client_manager.py    # MCP client (external servers → ADK agents)
├── mcp_servers.example.json # MCP client configuration template
├── requirements.txt         # Runtime dependencies
├── requirements-dev.txt     # Test dependencies (pytest)
├── run_jarvis.sh            # Starts the server
└── run_app.sh               # Starts the desktop widget
```

---

## Roadmap

See [`ROADMAP.md`](ROADMAP.md) for the public roadmap. Near-term priorities include runtime hardening, stronger integration tests, cleaner provider routing, broader Linux compatibility and easier installation.

---

## Contributing

Contributions are welcome. If you want to improve the voice runtime, ADK agents, Linux automation, plugins, security tests, documentation or UI, read [`CONTRIBUTING.md`](CONTRIBUTING.md) before opening a pull request.

For security-sensitive findings, see [`SECURITY.md`](SECURITY.md).

---

## Documentation

- 🇧🇷 [`README.pt-BR.md`](README.pt-BR.md) — Portuguese overview and setup
- 🧭 [`ESBOCO_PROJETO.md`](ESBOCO_PROJETO.md) — project design notes
- 🩺 [`monitoring/README.md`](monitoring/README.md) — logging, debug dashboard and diagnostics
- 🌉 [`gemini/README.md`](gemini/README.md) — Antigravity file bridge
- 🛣️ [`ROADMAP.md`](ROADMAP.md) — planned work
- 🤝 [`CONTRIBUTING.md`](CONTRIBUTING.md) — contribution guide
- 🔐 [`SECURITY.md`](SECURITY.md) — security reporting and trust model notes

---

## License

Released under the [MIT License](LICENSE).

If JARVIS is useful to you, consider **starring the repository** — it helps other developers discover the project.
