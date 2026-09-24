# JARVIS — Assistente Pessoal de IA com Gemini Live + Google ADK

<p align="center">
  <strong>Assistente pessoal multimodal em tempo real com voz, visão de tela, automação segura do Linux, tool calling e orquestração de agentes.</strong>
</p>

<p align="center">
  <a href="README.md">English</a> · <a href="README.pt-BR.md">Português (Brasil)</a>
</p>

<p align="center">
  <img src="assets/jarvis-hud-interface.png" alt="HUD web holográfico do JARVIS" width="49%">
  <img src="assets/gemini-live-widget.png" alt="Widget desktop Gemini Live do JARVIS" width="49%">
</p>

## O que é o JARVIS?

O **JARVIS** é um assistente pessoal de IA open source para Linux construído com **Gemini Live**, **Google Agent Development Kit (ADK)**, **FastAPI**, **WebSockets**, **PySide6**, plugins modulares e um **Policy Engine** para controlar ações sensíveis.

Ele foi criado para ir além de um chatbot. O sistema pode manter conversação de voz full-duplex, usar contexto visual da tela, rotear tarefas entre agentes especializados, chamar ferramentas locais, automatizar ações no computador e expor capacidades para outras IAs por meio de **MCP (Model Context Protocol)**.

O projeto é especialmente relevante para quem pesquisa ou desenvolve **voice agents**, **assistentes pessoais de IA**, **IA multimodal**, **Gemini Live API**, **Google ADK**, **AI agents**, **tool calling**, **Computer Use**, **MCP** e **automação Linux**.

> **Status:** desenvolvimento ativo. A arquitetura está sendo endurecida com testes de segurança, regressão e contratos de runtime.

---

## Destaques

- 🎙️ Conversação por voz em tempo real com áudio bidirecional e barge-in.
- 👁️ Visão de tela e workflows de navegador/Computer Use.
- 🧠 Orquestração de agentes com Google ADK.
- 🛠️ Tool calling para sistema operacional, hardware e plugins.
- 🔐 Policy Engine com classificação de risco, confirmação e leases temporárias.
- 🖥️ HUD web holográfico e widget desktop flutuante em PySide6.
- 🧩 Arquitetura de plugins e Skills ADK.
- 🔌 MCP nos dois sentidos: servidor para IDEs/agentes externos e cliente para servidores MCP externos.
- 🐧 Projeto Linux-first com suporte a Wayland/X11.

---

## Exemplos de uso

```text
“Jarvis, quais processos estão usando mais CPU?”
“Jarvis, tire uma captura de tela e me diga o que está aparecendo.”
“Jarvis, use o navegador para verificar esta página.”
“Jarvis, procure os e-mails recentes relacionados ao projeto.”
“Jarvis, abra o aplicativo e aumente o volume em 15%.”
```

As ferramentas disponíveis dependem dos plugins habilitados. Ações sensíveis são avaliadas pelo Policy Engine e podem exigir confirmação explícita.

---

## Arquitetura resumida

```text
Usuário / Voz
      │
      ├── HUD Web
      └── Widget PySide6
              │
          FastAPI
              │
      ┌───────┼────────┐
      │       │        │
 Gemini Live  ADK  Computer Use
      │       │        │
      └───────┼────────┘
              │
        Policy Engine
              │
      ┌───────┼──────────────┐
      │       │              │
 System Tools Plugins/Skills MCP (servidor + cliente)
```

Um único processo FastAPI (`server.py`) hospeda tudo: a bridge nativa do Gemini Live, o runtime Google ADK (texto e voz), os clientes web e o painel de depuração. Não existe mais um servidor ADK separado.

### Principais componentes

| Componente | Função |
| --- | --- |
| `server.py` | Runtime FastAPI unificado: Gemini Live nativo (`/ws/live`), voz ADK (`/ws/live_adk`), chat ADK (`/api/chat`), confirmações e plugins |
| `live_protocolo.py` | Protocolo Live compartilhado: parser único de confirmação/recusa, ajuste de ping e encerramento limpo |
| `provider_router.py` | Pool de chaves Google AI Studio (primário) e failover OmniRoute (secundário, só texto) |
| `transcricao.py` | Configuração de transcrição Live e transcritor dedicado opcional |
| `agentes/` | Agentes ADK (rápido, coordenador, voz), roteador, adaptadores de ferramentas e memória persistente |
| `agentes/computer_use/` | Controle de navegador via Playwright / Computer Use |
| `policy_engine.py` | Governança, risco, confirmações e leases de controle/IDE/computador |
| `system_tools.py` | Ferramentas do Linux, hardware e automação local + declarações de função do Gemini |
| `controller_engine.py` | Mouse/teclado virtual via `evdev`/`uinput` (degrada sem quebrar se indisponível) |
| `preferences_manager.py` | Preferências persistentes (apps padrão, plataforma de música, launchers) |
| `plugin_manager.py` / `plugin_sdk.py` | Descoberta, ciclo de vida e contratos de plugins |
| `plugins/` | Código de runtime dos plugins (`plugins/<id>/plugin.py`) |
| `skills/` | Skills ADK (`skills/<nome-da-skill>/SKILL.md` + `assets/`), fonte única das instruções |
| `adk_skill_loader.py` | Carregamento de Skills ADK e SkillToolset |
| `jarvis_mcp_server.py` | Servidor MCP para agentes e IDEs externas |
| `mcp_client_manager.py` | Cliente MCP: conecta servidores MCP externos aos agentes ADK com políticas de risco |
| `gemini_bridge.py` / `gemini/` | Ponte por arquivos e log de auditoria com a IDE Antigravity |
| `static/` | HUD web holográfico (em `/`) |
| `static_adk/` | Cliente de voz ADK (em `/static_adk/`) |
| `gemini-live-widget/` | Frontend do widget desktop (em `/widget/`, embrulhado pelo `app.py`) |
| `monitoring/` | Logger estruturado, painel de depuração (`/debug`), Trust Gates e regressões |

### Endpoints principais

| Endpoint | Função |
| --- | --- |
| `GET /` | HUD web |
| `GET /widget/` | Interface do widget desktop |
| `GET /static_adk/` | Cliente de voz ADK |
| `GET /debug` | Painel de depuração em tempo real |
| `WS /ws/live` | Sessão Gemini Live nativa (Extended Thinking completo + ferramentas NON_BLOCKING) |
| `WS /ws/live_adk` | Sessão Live do Google ADK (agentes, memória, skills, toolsets MCP) |
| `POST /api/chat` | Turno de texto roteado entre agente rápido, coordenador e Computer Use |
| `GET /api/health` | Modelos, pool de chaves, provedor ativo e status MCP |

Rotas de mutação e os dois WebSockets exigem `JARVIS_TOKEN`; clientes locais obtêm o token em `GET /api/auth/session` (somente loopback).

### Modelos e provedores

Os modelos padrão ficam no `.env`: `gemini-3.8-live` para voz, `gemini-3.8-live-extended-thinking` para raciocínio em segundo plano (`LIVE_THINKING_LEVEL`), `gemini-2.5-flash-native-audio-latest` como reserva de voz e `gemini-flash-latest` para texto.

`GEMINI_API_KEYS` aceita um pool de chaves separadas por vírgula: a sessão Live rotaciona para a próxima chave em caso de cota ou erro transitório. No chat de texto, um proxy OmniRoute local opcional (`OMNIROUTE_URL`) atua como segundo provedor, acionado automaticamente quando o Google AI Studio está indisponível ou diretamente quando selecionado no HUD. A voz Live sempre roda no Google.

---

## Instalação rápida

### Requisitos

- Linux
- Python 3.11+
- Chave da API Gemini
- Microfone para modo de voz
- Chromium/Playwright apenas para Computer Use

### 1. Clone o repositório

```bash
git clone https://github.com/eduardo02138/JARVIS---Assistente-Pessoal-de-IA-.git
cd JARVIS---Assistente-Pessoal-de-IA-
```

### 2. Crie o ambiente Python

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

### 3. Configure o ambiente

```bash
cp .env.example .env
```

Configure pelo menos:

```env
GEMINI_API_KEY="sua_chave_gemini"
# Pool opcional para rotação automática: GEMINI_API_KEYS="chave1,chave2"
JARVIS_TOKEN="um_segredo_local_longo_e_aleatorio"
JARVIS_HOST="127.0.0.1"
PORT=8000
```

O `.env.example` documenta todas as demais opções (modelos, voz, idioma, leases, transcrição, OmniRoute, MCP, memória). Nunca publique `.env` ou chaves reais de API.

### 4. Execute

```bash
./run_jarvis.sh
```

ou:

```bash
.venv/bin/python server.py
```

Abra:

```text
http://127.0.0.1:8000          # HUD web
http://127.0.0.1:8000/debug    # painel de depuração
```

O runtime Google ADK faz parte do mesmo servidor: o cliente de voz ADK fica em `http://127.0.0.1:8000/static_adk/` e os turnos de texto ADK passam por `POST /api/chat`.

### Widget desktop

```bash
./run_app.sh
```

O `app.py` exibe o `/widget/` numa janela PySide6 sem bordas e inicia o backend se ele não estiver rodando.

### Computer Use

```bash
.venv/bin/playwright install-deps chromium
.venv/bin/playwright install chromium
```

---

## Segurança e governança

O projeto evita entregar autoridade irrestrita ao modelo. As ferramentas passam por uma camada de políticas que diferencia consultas somente leitura de ações que alteram o sistema, serviços externos ou recursos privilegiados.

Entre os mecanismos existentes estão:

- servidor local restrito ao loopback;
- token de sessão;
- classificação de risco de ferramentas;
- confirmação explícita para operações sensíveis;
- leases temporárias para controle físico/computador;
- logs estruturados e testes de regressão.

Veja também [`SECURITY.md`](SECURITY.md).

---

## Plugins e Skills ADK

O código de cada plugin fica em `plugins/<id>/plugin.py`; a Skill ADK correspondente (`SKILL.md` e `assets/` opcionais) fica em `skills/<nome-da-skill>/`. Assim o modelo recebe instruções focadas só das skills ativas.

| Plugin | Skill | Situação |
| --- | --- | --- |
| `game_companion` | `game-companion` | Real (jogos locais, launchers, timers táticos) — ativo por padrão |
| `google_workspace` | `google-workspace` | Simulado (dados de demonstração) |
| `deep_research` | `deep-research` | Simulado (dados de demonstração) |
| `google_finance` | `google-finance` | Simulado (carteira e cotações de demonstração) |
| `ginjutsu_studio` | `ginjutsu-studio` | Simulado (demo de IA de vídeo/movimento) |
| `smart_home` | `smart-home` | Simulado (dispositivos de demonstração) |
| `live_stream` | `live-stream` | Simulado (chat/alertas de demonstração) |
| `social_feed` | `social-feed` | Simulado (notificações de demonstração) |

Os plugins simulados ficam **desligados por padrão** para o assistente nunca relatar dados inventados como reais. Use `JARVIS_ATIVAR_MOCKS=1` para ativá-los em demonstrações. As ferramentas de sistema e hardware Linux são nativas (`system_tools.py`) e sempre disponíveis.

### Cliente MCP

Copie `mcp_servers.example.json` para `mcp_servers.json` (ou aponte `MCP_SERVERS_CONFIG` para um arquivo) para conectar servidores MCP externos (stdio, SSE ou HTTP) aos agentes ADK. Cada servidor declara `tool_filter` e níveis de risco por ferramenta, então as ferramentas externas também passam pelo Policy Engine.

---

## Testes

As mesmas suítes executadas pelo CI:

```bash
export GEMINI_API_KEY="ci-dummy-key-test" JARVIS_TOKEN="ci-secret-token-test-123"
PYTHONPATH=. .venv/bin/pytest monitoring/test_trust_gates.py monitoring/test_mcp_client.py \
    monitoring/test_live_protocolo.py monitoring/test_reproduction_p0.py -v
.venv/bin/python monitoring/test_suite.py --p0   # gates de segurança e arquitetura (P0)
.venv/bin/python monitoring/test_adk.py          # cenários Google ADK
```

Não é preciso chave real: as suítes rodam offline com credenciais fictícias. O GitHub Actions também faz checagem de sintaxe, smoke test de imports e Gitleaks em todo push e pull request para `main`.

---

## Estrutura do projeto

```text
.
├── agentes/                 # Agentes ADK, roteador, adaptadores, memória e Computer Use
├── assets/                  # Screenshots e mídia do projeto
├── gemini/                  # Ponte por arquivos com a IDE Antigravity
├── gemini-live-widget/      # Widget desktop
├── monitoring/              # Logger, painel de depuração, Trust Gates e regressões
├── plugins/                 # Código dos plugins (plugins/<id>/plugin.py)
├── skills/                  # Skills ADK (skills/<nome>/SKILL.md + assets/)
├── static/                  # HUD web principal
├── static_adk/              # Cliente de voz ADK
├── app.py                   # Aplicativo PySide6 (embrulha o widget)
├── server.py                # Runtime unificado (Gemini Live + ADK + API)
├── live_protocolo.py        # Protocolo Live compartilhado
├── provider_router.py       # Pool de chaves + failover OmniRoute
├── transcricao.py           # Configuração de transcrição Live
├── policy_engine.py         # Governança de ferramentas
├── system_tools.py          # Ferramentas do sistema + declarações de função
├── controller_engine.py     # Mouse/teclado virtual (evdev/uinput)
├── preferences_manager.py   # Preferências persistentes
├── plugin_manager.py        # Descoberta e ciclo de vida de plugins
├── plugin_sdk.py            # Contratos de plugins
├── adk_skill_loader.py      # Loader de Skills ADK
├── gemini_bridge.py         # Ponte Antigravity e log de auditoria
├── jarvis_mcp_server.py     # Servidor MCP
├── mcp_client_manager.py    # Cliente MCP (servidores externos → agentes ADK)
├── mcp_servers.example.json # Modelo de configuração do cliente MCP
├── run_jarvis.sh            # Inicia o servidor
└── run_app.sh               # Inicia o widget desktop
```

---

## Roadmap e contribuição

Veja [`ROADMAP.md`](ROADMAP.md) para as próximas etapas e [`CONTRIBUTING.md`](CONTRIBUTING.md) para contribuir com código, plugins, documentação, testes ou interface.

Se o projeto for útil para você, considere deixar uma ⭐ no repositório — isso ajuda outros desenvolvedores a encontrar o JARVIS.

## Licença

MIT — veja [`LICENSE`](LICENSE).
