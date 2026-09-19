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
- 🔌 Servidor MCP para IDEs e agentes externos.
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
      ┌───────┼──────────┐
      │       │          │
 System Tools Plugins   MCP
```

### Principais componentes

| Componente | Função |
| --- | --- |
| `server.py` | Runtime FastAPI principal e bridge Gemini Live |
| `servidor_adk.py` | Runtime nativo Google ADK para texto/voz |
| `agentes/` | Agentes, roteamento e integração de ferramentas ADK |
| `agentes/computer_use/` | Controle de navegador via Playwright / Computer Use |
| `policy_engine.py` | Governança, risco, confirmações e autoridade temporária |
| `system_tools.py` | Ferramentas do Linux, hardware e automação local |
| `plugin_manager.py` | Descoberta e carregamento de plugins |
| `plugin_sdk.py` | Contratos para criação de plugins |
| `adk_skill_loader.py` | Carregamento de Skills ADK |
| `jarvis_mcp_server.py` | Servidor MCP para agentes e IDEs externas |
| `monitoring/` | Trust Gates, regressões e auditoria |

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
JARVIS_TOKEN="um_segredo_local_longo_e_aleatorio"
HOST="127.0.0.1"
PORT=8000
```

Nunca publique `.env` ou chaves reais de API.

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
http://127.0.0.1:8000
```

### Widget desktop

```bash
./run_app.sh
```

### Servidor Google ADK

```bash
.venv/bin/python servidor_adk.py
```

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

O catálogo é modular e inclui áreas como:

- Google Workspace;
- pesquisa profunda;
- ferramentas financeiras demonstrativas;
- IA para vídeo/movimento;
- game companion;
- casa inteligente;
- live streaming;
- integrações sociais;
- sistema e hardware Linux.

Cada plugin pode expor uma Skill ADK focada, evitando colocar todas as instruções de todas as ferramentas no mesmo contexto do modelo.

---

## Testes

```bash
PYTHONPATH=. .venv/bin/pytest monitoring/test_trust_gates.py monitoring/test_reproduction_p0.py -v
.venv/bin/python monitoring/test_suite.py --p0
.venv/bin/python monitoring/test_adk.py
```

O projeto também possui pipeline de CI pelo GitHub Actions.

---

## Estrutura do projeto

```text
.
├── agentes/                 # Agentes ADK e Computer Use
├── assets/                  # Screenshots e mídia do projeto
├── gemini-live-widget/      # Widget desktop
├── monitoring/              # Testes, Trust Gates e auditoria
├── plugins/                 # Plugins e Skills
├── static/                  # HUD web principal
├── static_adk/              # Cliente web ADK
├── app.py                   # Aplicativo PySide6
├── server.py                # Runtime principal
├── servidor_adk.py          # Runtime ADK
├── policy_engine.py         # Governança de ferramentas
├── system_tools.py          # Ferramentas do sistema
├── adk_skill_loader.py      # Loader de Skills ADK
└── jarvis_mcp_server.py     # Servidor MCP
```

---

## Roadmap e contribuição

Veja [`ROADMAP.md`](ROADMAP.md) para as próximas etapas e [`CONTRIBUTING.md`](CONTRIBUTING.md) para contribuir com código, plugins, documentação, testes ou interface.

Se o projeto for útil para você, considere deixar uma ⭐ no repositório — isso ajuda outros desenvolvedores a encontrar o JARVIS.

## Licença

MIT — veja [`LICENSE`](LICENSE).
