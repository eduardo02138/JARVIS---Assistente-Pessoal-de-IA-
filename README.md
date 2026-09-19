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
             │System Tools │  │   Plugins   │  │ MCP Server  │
             │Linux/OS/HW  │  │ADK Skills  │  │External AI │
             └─────────────┘  └─────────────┘  └─────────────┘
```

### Main components

| Component | Purpose |
| --- | --- |
| `server.py` | Main FastAPI runtime, HTTP/WebSocket endpoints and Gemini Live bridge |
| `servidor_adk.py` | Native Google ADK text/voice server and session runtime |
| `agentes/` | ADK agents, routing and tool integration |
| `agentes/computer_use/` | Browser automation / Gemini Computer Use integration with Playwright |
| `policy_engine.py` | Risk classification, confirmations and temporary control authority |
| `system_tools.py` | Linux, hardware, media and local automation tools |
| `plugin_manager.py` / `plugin_sdk.py` | Plugin discovery and extension contracts |
| `adk_skill_loader.py` | ADK Skill loading and SkillToolset integration |
| `jarvis_mcp_server.py` | MCP stdio server for IDEs and external agents |
| `static/` | Holographic web HUD |
| `gemini-live-widget/` | Floating desktop widget frontend |
| `monitoring/` | Trust gates, regression tests and structured runtime auditing |

---

## Core capabilities

### Real-time voice and Gemini Live

JARVIS streams microphone audio and model audio over WebSockets, supports natural interruption (barge-in), configurable voices and live session controls. The runtime is built so model configuration can evolve without coupling the UI to one hard-coded execution path.

### Google Agent Development Kit (ADK)

The native ADK layer provides routing between low-latency and coordinated agent paths, durable session support and modular skills. Active skills can be injected through `SkillToolset`, keeping the agent context smaller than loading every capability eagerly.

### Tool calling and Linux automation

System tools expose hardware telemetry, application control, screenshots, media actions and local automation. Plugins extend the catalog with workspace, research, game companion, smart-home, streaming and other specialized capabilities.

### Policy Engine and secure execution

The model does not receive unrestricted authority over the machine. Tool requests are evaluated by a policy layer that can classify operations as read-only, local write, external write or privileged and require confirmation/leases for sensitive actions.

### Screen vision and Computer Use

A dedicated Computer Use agent can operate a Chromium browser through Playwright. Browser control is intentionally separated from the general-purpose agent/tool set so browser authority can be governed independently.

### MCP integration

`jarvis_mcp_server.py` exposes selected JARVIS capabilities over the **Model Context Protocol**, allowing compatible IDEs and AI agents to use JARVIS as a local tool server.

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
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
```

At minimum, configure your Gemini key and a local JARVIS session token:

```env
GEMINI_API_KEY="your_gemini_api_key"
JARVIS_TOKEN="replace_with_a_long_random_secret"
HOST="127.0.0.1"
PORT=8000
```

Do not commit `.env` or real API keys.

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
http://127.0.0.1:8000
```

### Optional: desktop widget

```bash
./run_app.sh
```

### Optional: Google ADK server

```bash
.venv/bin/python servidor_adk.py
```

### Optional: Computer Use

```bash
.venv/bin/playwright install-deps chromium
.venv/bin/playwright install chromium
```

---

## Plugins and ADK Skills

JARVIS uses a modular plugin architecture rather than hard-coding every integration in the central agent. Current modules include areas such as:

- Google Workspace workflows
- Deep research
- Finance/demo portfolio tools
- Motion/video AI integrations
- Game companion tools
- Smart-home workflows
- Live-stream assistance
- Social integrations
- Linux system and hardware tools

Each capability can define an ADK `SKILL.md` so the model receives focused instructions only for enabled skills.

---

## Testing and trust gates

The repository includes automated architecture, security and regression tests:

```bash
PYTHONPATH=. .venv/bin/pytest monitoring/test_trust_gates.py monitoring/test_reproduction_p0.py -v
.venv/bin/python monitoring/test_suite.py --p0
.venv/bin/python monitoring/test_adk.py
```

GitHub Actions runs the CI pipeline on repository changes.

---

## Project structure

```text
.
├── agentes/                 # Google ADK agents and Computer Use
├── assets/                  # README screenshots / project media
├── gemini-live-widget/      # Desktop widget frontend
├── monitoring/              # Trust gates, regressions and audit tooling
├── plugins/                 # Modular tools and ADK skills
├── static/                  # Main holographic web HUD
├── static_adk/              # Unified ADK web client
├── app.py                   # PySide6 desktop app
├── server.py                # Main runtime
├── servidor_adk.py          # Native ADK runtime
├── policy_engine.py         # Tool governance and authorization
├── system_tools.py          # Linux/system tools
├── plugin_manager.py        # Plugin discovery
├── plugin_sdk.py            # Plugin contracts
├── adk_skill_loader.py      # ADK skill loader
└── jarvis_mcp_server.py     # MCP server
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
- 🛣️ [`ROADMAP.md`](ROADMAP.md) — planned work
- 🤝 [`CONTRIBUTING.md`](CONTRIBUTING.md) — contribution guide
- 🔐 [`SECURITY.md`](SECURITY.md) — security reporting and trust model notes

---

## License

Released under the [MIT License](LICENSE).

If JARVIS is useful to you, consider **starring the repository** — it helps other developers discover the project.
