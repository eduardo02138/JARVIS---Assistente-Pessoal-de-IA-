# J.A.R.V.I.S. // Stark Industries Voice Assistant & System Automator

Assistente pessoal de voz, automação de sistema operacional e hub de inteligência em tempo real inspirado no **JARVIS (Homem de Ferro)**. Desenvolvido com a **Gemini Multimodal Live API**, backend assíncrono em **FastAPI**, ecossistema modular de **Plug-ins**, **Motor de Políticas de Segurança (Policy Engine)** e duas interfaces: um **HUD Web Holográfico** futurista e um **Aplicativo Desktop Flutuante Transparente** (PySide6 / QtWebEngine).

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
| **Google Finance** | 📈 | `finance_get_quote`<br>`finance_get_portfolio`<br>`finance_add_asset`<br>`finance_get_insights` | Cotações em tempo real (B3, S&P 500, Cripto), consolidação da carteira e diagnóstico de alocação de ativos. |
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
├── plugins/                   # Módulos de expansão (Workspace, Finance, Research, Jogos, etc.)
├── gemini-live-widget/        # Frontend do widget flutuante e modo expandido "Ask Gemini"
├── static/                    # Frontend do HUD Holográfico Sci-Fi (Reator Arc)
├── monitoring/                # Logs estruturados (events.jsonl) e suíte de testes P0
└── run_jarvis.sh              # Script utilitário para subida rápida do ambiente
```
