# J.A.R.V.I.S. — Gemini 3.8 Live Personal AI Assistant & Linux Automation

**J.A.R.V.I.S.** é um assistente pessoal multimodal inspirado no conceito de um copiloto estilo Jarvis, construído para conversação por voz em tempo real, automação segura do Linux, visão de tela, execução de ferramentas e orquestração de agentes.

O projeto combina **Gemini 3.8 Live**, **Google Agent Development Kit (ADK)**, **FastAPI**, **WebSockets**, **Python**, **PySide6**, um ecossistema modular de **plug-ins** e um **Policy Engine fail-closed** para controlar ações sensíveis. A experiência pode ser usada pelo HUD web holográfico, pelo widget desktop flutuante ou pelos endpoints ADK de texto e voz.

### 🔎 Tecnologias e palavras-chave

`Gemini 3.8 Live` · `Gemini Live API` · `Google ADK` · `JARVIS` · `Personal AI Assistant` · `Voice Assistant` · `AI Agent` · `Multimodal AI` · `Linux Automation` · `FastAPI` · `WebSocket` · `PySide6` · `Tool Calling` · `Policy Engine` · `Screen Vision` · `Open Source AI`

> O modelo Live principal atualmente suportado pelo projeto é **`gemini-3.8-live`**. O runtime foi estruturado para permitir troca de modelos por configuração, sem afirmar suporte a modelos ainda não disponibilizados oficialmente.

---

## ⚡ Principais Capacidades

### 1. Conversação Bidirecional Full-Duplex
- **Streaming de Áudio em Tempo Real**: Latência ultrabaixa com WebSockets e Web Audio API (PCM 16kHz in / 24kHz out).
- **Interrupção Natural (*Barge-in*)**: Fale a qualquer instante e o JARVIS interrompe a fala anterior na hora para acatar a nova instrução.
- **Personalidade Stark**: Tratamento refinado ("Senhor"), raciocínio ágil e síntese vocal personalizável (*Charon*, *Puck*, *Fenrir*, *Aoede*, *Kore*).
- **Pool de Contas & Failover Resiliente**: Suporte ao OmniRoute e balanceamento de carga *round-robin* entre contas com transição transparente em caso de esgotamento de cota (*Rate Limit 429*).

### 2. Dupla Experiência de Interface
- 🛸 **HUD Web Holográfico (`/`)**: Reator Arc reativo com espectrograma em tempo real no `<canvas>`, painéis em Glassmorphism, telemetria de hardware e suporte a microfone contínuo ou *Push-to-Talk* (`<Espaço>`).
- 🪟 **App Desktop Nativo Flutuante (`app.py`)**: Janela transparente sem bordas, arraste nativo via Wayland/X11 (`startSystemMove`), botão de expansão "Ask Gemini" e atalho de ativação rápida:
  - **`Alt + Espaço`**: Alterna a visibilidade com a janela em foco. Para funcionar em qualquer aplicativo, registre um atalho global do sistema apontando para `python app.py --toggle` (veja abaixo).
  - **`Esc`**: Recolhe o widget rapidamente.
- 🛠️ **Central de Depuração & Auditoria (`/debug`)**: Monitor de eventos em tempo real, logs estruturados (`events.jsonl`) e telemetria de hardware/GPU.

### 3. Governança e Segurança Reforçada (Fase P0)
- **Isolamento de Rede**: Servidor restrito ao loopback local (`127.0.0.1`), bloqueando varreduras na rede local.
- **Autenticação por Token**: Proteção de endpoints e handshake WebSocket via `JARVIS_TOKEN` e sessão de curta duração (`/api/auth/session`).
- **Motor de Políticas (Policy Engine)**: Cada uma das 55 ferramentas possui classificação de risco estrita (*fail-closed*):
  - `READ`: Telemetria, cotações, consultas e listagens.
  - `LOW_WRITE`: Ações locais benignas (volume, timers, rascunhos, anotações).
  - `EXTERNAL_WRITE`: Interações externas que exigem confirmação explícita.
  - `PRIVILEGED`: Automações de terminal e agentes de código.
- **Lease Temporária de Controle Físico**: Mouse e teclado só são liberados sob autorização prévia por tempo limitado com revogação instantânea.

---

## 🧩 Catálogo de Plug-ins & Habilidades Ativas (55 Ferramentas)

O sistema possui uma arquitetura modular expansível gerenciada pelo `plugin_manager.py`:

| Plug-in | Ícone | Ferramentas Chave | Descrição & Funcionalidades |
| :--- | :---: | :--- | :--- |
| **Google Workspace** | 📑 | `workspace_search_emails`<br>`workspace_create_draft`<br>`workspace_append_doc`<br>`workspace_create_keep_note` | Redigir documentos no Docs, pesquisar e-mails na caixa de entrada do Gmail e capturar notas no Keep por voz. |
| **Pesquisa Profunda** | 🔬 | `deep_research_start`<br>`deep_research_get_report`<br>`deep_research_list` | Varreduras e dossiês aprofundados assíncronos em segundo plano com alerta automático por voz e no HUD ao concluir. |
| **Google Finance** | 📈 | `finance_get_quote`<br>`finance_get_portfolio`<br>`finance_add_asset`<br>`finance_get_insights` | SIMULADO: cotações de demonstração… não consulta o Google Finance real. Demonstração de carteira e alocação de ativos. |
| **Ginjutsu Motion AI**| 🎬 | `ginjutsu_create_motion_transfer`<br>`ginjutsu_generate_prompt`<br>`ginjutsu_list_jobs` | Transferência de coreografia, atuação e movimentos de vídeos para novos personagens via Higgsfield Ginjutsu. |
| **Game Companion** | 🎮 | `game_companion_list_installed_games`<br>`game_companion_launch_game`<br>`game_companion_tactical_timer`<br>`game_companion_get_strategy` | Catálogo de jogos locais (Steam, Lutris, Heroic), inicializador direto por voz, timers táticos e conselhos de partida. |
| **Casa Inteligente** | 🏠 | `smart_home_set_light`<br>`smart_home_activate_scene`<br>`smart_home_get_climate` | Automação residencial: controle de iluminação, climatização e acionamento de cenas (*Foco*, *Cinema*, *Descanso*). |
| **Live Streaming** | 📡 | `live_stream_toggle_status`<br>`live_stream_read_chat_summary`<br>`live_stream_send_alert` | Assistência para transmissões ao vivo: leitura e resumo de chat em tempo real (Twitch/YouTube) e metas. |
| **Mídias Sociais** | 💬 | `social_feed_check_notifications`<br>`social_feed_post_update` | Monitoramento inteligente de feeds e notificações urgentes (Discord, Telegram, X/Twitter). |
| **Sistema & Hardware**| ⚙️ | `get_system_status`<br>`get_gpu_status`<br>`adjust_volume`<br>`open_application`<br>`take_screenshot`<br>`antigravity_open_workspace` | Telemetria completa (NVIDIA RTX, CPU, RAM, Disco), controle de mídia, gerenciamento de janelas e integração IDE. |

---

## 🚀 Como Iniciar

### 1. Configurar Chaves de Ambiente
Crie ou edite o arquivo `.env` na raiz do projeto:
```env
GEMINI_API_KEY="sua_chave_gemini"
# Ou múltiplas chaves para pool de failover:
# GEMINI_API_KEYS="chave_1,chave_2,chave_3"
JARVIS_TOKEN="sua_chave_secreta_de_sessao"
PORT=8000
HOST="127.0.0.1"
```

### 2. Iniciar o Servidor Backend
Você pode iniciar via script rápido:
```bash
./run_jarvis.sh
```
Ou manualmente no ambiente virtual:
```bash
.venv/bin/python server.py
```

### 3. Iniciar o Aplicativo Desktop (Opcional)
Para a interface flutuante transparente com arraste nativo e atalhos:
```bash
./run_app.sh
```
*Atalho global:* o Qt só captura teclas com a janela em foco (e no Wayland nem isso é garantido), então o atalho de sistema é registrado no ambiente de trabalho e conversa com a instância em execução:

```bash
python app.py --toggle
```

No GNOME: **Configurações → Teclado → Atalhos personalizados**, crie um atalho `Alt+Space` com esse comando (use o caminho completo do projeto e do Python do `.venv`). Sem nenhuma instância aberta, o comando inicia o aplicativo.

### 4. Acessar o HUD Web
Caso prefira o navegador, acesse:
```
http://127.0.0.1:8000
```
- Clique em **"INICIAR JARVIS"** e autorize a captura de áudio.
- Para acompanhar logs, telemetria e depuração: `http://127.0.0.1:8000/debug`.

---

## 🗣️ Exemplos de Comandos por Voz

### Produtividade & Workspace
- *"Jarvis, procure no meu Gmail os e-mails recentes sobre segurança do sistema."*
- *"Jarvis, anote no Google Keep: comprar componentes novos para a bancada."*
- *"Jarvis, crie um rascunho de e-mail para a equipe apresentando o relatório semanal."*

### Finanças & Portfólio
- *"Jarvis, qual a cotação da Petrobras e do Bitcoin agora?"*
- *"Jarvis, mostre o desempenho consolidado do meu portfólio no Google Finance."*
- *"Jarvis, quais setores estão sub-representados na minha carteira de investimentos?"*

### Pesquisa Profunda & Dossiês
- *"Jarvis, inicie uma Pesquisa Profunda sobre as novas tecnologias de IA multimodal para 2026."*
- *(Você pode continuar conversando normalmente; o JARVIS avisa por áudio assim que concluir o relatório).*

### Jogos & Entretenimento
- *"Jarvis, quais jogos estão instalados na minha máquina?"*
- *"Jarvis, inicie o Marvel Rivals para mim."*
- *"Jarvis, configure um cronômetro tático de 90 segundos para o respawn do Boss."*

### Hardware & Casa Inteligente
- *"Jarvis, como estão a temperatura e o uso da minha placa de vídeo RTX?"*
- *"Jarvis, aumente o volume em 15% e ative a cena de trabalho no escritório."*

---


---

## 🎙️ Arquitetura Nativa Google ADK (Voice Agent v0.1)

Além da bridge customizada do JARVIS, o projeto conta com um módulo nativo baseado no **Google Agent Development Kit (ADK 2.9+)**:

- **Cliente Único & Roteamento Interno**: O usuário interage por texto ou voz sem precisar selecionar versão do agente. O roteador (`agentes/roteador.py`) despacha heurística e semanticamente:
  - *Caminho Rápido (`criar_agente_rapido`)*: Consultas diretas de baixa latência (hora, status, busca simples).
  - *Caminho Coordenador (`criar_agente_coordenador`)*: Tarefas complexas orquestrando especialistas (`especialista_sistema`, `especialista_navegador`) via `AgentTool`.
- **Persistência Durável com `DatabaseSessionService`**: Suporte a SQLite assíncrono (`sessoes.db`). As memórias gravadas sob a chave `user:` persistem mesmo com o reinício do servidor.
- **Modelos Dedicados & Failover**:
  - **Live (Voz Bidirecional)**: `gemini-3.8-live` ou `gemini-2.5-flash-native-audio-latest` através de `Runner.run_live()` e `LiveRequestQueue`.
  - **Texto & Sub-agentes**: `gemini-flash-latest` com failover automático em caso de 503 para `gemini-2.5-flash`.
- **Rotação Automática de Chaves**: Caso uma chave atinja a cota (HTTP 429), o sistema faz o failover transparente para a próxima chave configurada em `GEMINI_API_KEYS`.

### 🧩 Habilidades ADK (`adk_skill_loader.py`)

Cada plug-in traz uma **Skill ADK** no padrão oficial (L1 frontmatter, L2 corpo, L3 recursos):
- **L1/L2**: `plugins/<id>/SKILL.md` — frontmatter YAML validado e instruções de uso para o modelo.
- **L3**: `plugins/<id>/assets/*.json` — dados de referência (carteira padrão, estado da casa, dicas táticas) carregados com fallback embutido.
- **`ADKSkillLoader`**: usa `load_skill_from_dir` do ADK quando o diretório segue o padrão kebab-case; caso contrário faz *parser* local com os mesmos modelos (`Skill`, `Frontmatter`, `Resources`) do SDK.
- **`SkillToolset`**: as skills **ativas** são injetadas nos agentes ADK (`criar_agente_rapido`, `criar_agente_coordenador`) como `SkillToolset`, mantendo o contexto enxuto (apenas skills habilitadas). Configurando `GOOGLE_CLOUD_PROJECT`/`GOOGLE_CLOUD_LOCATION` (com ADC autenticado), o mesmo toolset passa a usar **Google Cloud Skill Registry** para descoberta e carregamento sob demanda (`search_skills`/`load_skill`). Sem a configuração GCP, tudo segue local, sem custo de API.

### 🔌 Servidor MCP (`jarvis_mcp_server.py`)

O servidor MCP (`stdio`) permite que a IDE Antigravity e agentes externos usem o JARVIS:
- Ferramentas nativas: telemetria, notificação por voz, health-check, jogos e bridge Gemini.
- **Plug-ins via MCP**: cada ferramenta de plug-in **ativo** é exposta automaticamente como `jarvis_plugin_<nome>`, com nomes, descrições e parâmetros vindos do Plugin SDK/Policy Engine.

### Como Iniciar o Servidor ADK
```bash
# Executar o servidor de voz ADK (porta 8100)
.venv/bin/python servidor_adk.py
```
Acesse em: `http://127.0.0.1:8100`

### 🖥️ Modo Computador (Gemini Computer Use)

O JARVIS inclui um agente dedicado que opera o navegador Chromium via Playwright sob o modelo
`gemini-2.5-computer-use-preview-10-2025` (`agentes/computer_use/`). É um agente **single-tool**:
não compartilha as 56 ferramentas do ecossistema e por isso não contamina os agentes normais.

Instalação do navegador (uma vez):
```bash
.venv/bin/playwright install-deps chromium
.venv/bin/playwright install chromium
```

Ativação do Modo Computador (exige confirmação do usuário, como o Modo Controle):
```bash
# 1) Pede a ativação: retorna id_confirmacao
curl -X POST localhost:8100/api/computer/mode -H "Authorization: Bearer $JARVIS_TOKEN" \
  -d '{"ativo": true, "sessao": "sessao-principal"}'

# 2) Confirma a pendência
curl -X POST localhost:8100/api/confirmar_acao -H "Authorization: Bearer $JARVIS_TOKEN" \
  -d '{"id_confirmacao": "<id>", "sessao": "sessao-principal", "aprovado": true}'

# 3) Repete a ativação: concede a lease (padrão 900s)
curl -X POST localhost:8100/api/computer/mode -H "Authorization: Bearer $JARVIS_TOKEN" \
  -d '{"ativo": true, "sessao": "sessao-principal"}'
```

Depois disso, um turno com `caminho: "computador"` (ou texto contendo "use o navegador") é atendido
pelo agente de Computer Use. Sem lease ativa, `guarda_computador` bloqueia toda tool do navegador.
Desativar fecha o Chromium compartilhado:
```bash
curl -X POST localhost:8100/api/computer/mode -H "Authorization: Bearer $JARVIS_TOKEN" \
  -d '{"ativo": false, "sessao": "sessao-principal"}'
```

Variáveis: `COMPUTER_USE_MODEL`, `COMPUTER_USE_HEADLESS`, `COMPUTER_USE_SCREEN_W/H`,
`JARVIS_COMPUTER_LEASE_TTL`.

## 🧪 Validação & Testes Automatizados

O repositório inclui uma suíte de testes de integridade arquitetural e de segurança:
```bash
# Executa a verificação completa da Fase P0
.venv/bin/python monitoring/test_suite.py --p0
```

---

## 🏛️ Estrutura de Arquivos

```
├── app.py                     # App Desktop flutuante transparente (PySide6 / QtWebEngine)
├── server.py                  # Servidor principal FastAPI + WebSocket Bridge Gemini Live
├── policy_engine.py           # Motor de Governança e Controle de Políticas de Risco
├── system_tools.py            # Habilidades centrais do SO e integração Antigravity IDE
├── plugin_sdk.py              # SDK para criação e padronização de plug-ins
├── plugin_manager.py          # Carregamento dinâmico e catálogo da loja de habilidades
├── adk_skill_loader.py        # Carregador de Skills ADK (SKILL.md L1/L2/L3 + SkillToolset)
├── jarvis_mcp_server.py       # Servidor MCP (stdio) p/ IDE e agentes externos + plug-ins
├── plugins/                   # Módulos de expansão (Workspace, Finance, Research, Jogos, etc.)
│   └── <plug-in>/SKILL.md     # Skill ADK: frontmatter L1, corpo L2, assets/ L3
├── gemini-live-widget/        # Frontend do widget flutuante e modo expandido "Ask Gemini"
├── static/                    # Frontend do HUD Holográfico Sci-Fi (Reator Arc)
├── monitoring/                # Logs estruturados (events.jsonl) e suíte de testes P0
├── servidor_adk.py            # Servidor FastAPI com Google ADK Runner e DatabaseSessionService
├── agentes/                   # Agentes ADK (assistente.py, roteador.py, ferramentas.py)
├── agentes/computer_use/      # Agente Computer Use + PlaywrightComputer (navegador Chromium)
├── static_adk/                # Cliente web unificado para o agente ADK
└── run_jarvis.sh              # Script utilitário para subida rápida do ambiente
```
