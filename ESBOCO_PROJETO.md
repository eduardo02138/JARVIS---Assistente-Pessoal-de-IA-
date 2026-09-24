# 📐 Esboço & Especificação Técnica do Projeto: J.A.R.V.I.S.

Este documento serve como o **mapa arquitetural e guia de desenvolvimento** para a construção e evolução do assistente de voz pessoal J.A.R.V.I.S., utilizando a **Gemini Multimodal Live API**.

---

## 1. Visão Geral do Sistema

O J.A.R.V.I.S. é um assistente pessoal por comando de voz com tempo de resposta quase instantâneo (*full-duplex*), capaz de:
- Escutar e falar simultaneamente sem truncamento de áudio.
- Ser interrompido naturalmente no meio de uma frase (*barge-in*).
- Executar ações reais no sistema operacional através de **Function Calling** nativo da Google GenAI.
- Apresentar telemetria em tempo real através de um painel holográfico inspirado no Reator Arc de Tony Stark.

---

## 2. Mapa da Estrutura de Arquivos

```
<raiz-do-projeto>/
│
├── .venv/                   # Ambiente virtual isolado (Python 3.11+)
├── requirements.txt         # Dependências de runtime (requirements-dev.txt: testes)
├── .env.example             # Modelo de variáveis de ambiente (GEMINI_API_KEY, PORT, etc.)
│
├── ESBOCO_PROJETO.md        # [Este arquivo] Especificação técnica e guia do desenvolvedor
├── README.md                # Instalação, arquitetura e testes
├── run_jarvis.sh            # Inicia o servidor com 1 clique
├── run_app.sh               # Abre o App Desktop com 1 clique
├── app.py                   # App Desktop flutuante (PySide6 / QtWebEngine) sobre /widget/
│
├── server.py                # Ponto de entrada: monta o FastAPI a partir de servidor/
├── servidor/                # Backend dividido por responsabilidade:
│   ├── seguranca.py         #   token, sessões emitidas e liberação de leases
│   ├── runtime_adk.py       #   sessões, memória, runners (padrão e reserva) e rotação de chaves do ADK
│   ├── falhas.py            #   classificação das falhas do provedor e a reação a cada uma
│   ├── rotas_sistema.py     #   saúde, diagnóstico, provedores, plug-ins, depuração e preferências
│   ├── rotas_agente.py      #   chat ADK (fila por sessão), confirmações e Modo Computador
│   ├── live_adk.py          #   WebSocket /ws/live_adk (Live pelo ADK)
│   └── live_nativo.py       #   WebSocket /ws/live (Gemini Live nativo + Function Calling)
│
├── agentes/                 # Agentes ADK, roteador, memória e Computer Use
├── system_tools.py          # Ferramentas do SO (telemetria, apps, volume, web, notas, Antigravity)
├── perfil_maquina.py        # Identifica a máquina em tempo de execução (GPU, discos, tela, apps)
├── policy_engine.py         # Classificação de risco, confirmações e leases
├── processos.py             # Abre programas sem entregar JARVIS_TOKEN e chaves do .env
├── rede_segura.py           # read_web_page só em páginas públicas (proteção contra SSRF)
├── resultados_de_ferramentas.py # Resultados de ferramentas serializáveis e com tamanho limitado
├── diagnostico.py           # Diagnóstico da máquina e da configuração (python diagnostico.py)
├── plugins/ + skills/       # Plug-ins (plugin.json + código) e Skills ADK (SKILL.md + assets)
│
├── static/                  # Frontend Web Holográfico (HUD Sci-Fi)
├── static/common/           # Módulo JS compartilhado entre HUD e widget (áudio, visão, token)
├── gemini-live-widget/      # Frontend do widget desktop
└── static_adk/              # Cliente de voz do caminho ADK
```

---

## 3. Pipeline de Áudio em Tempo Real

```
[ Usuário Fala ]
      │
      ▼ (Microfone no Navegador via Web Audio API)
[ Captura de Áudio: PCM 16-bit, 16.000 Hz, 1 Canal Mono ]
      │
      ▼ (Chunks Base64 via WebSocket /ws/live)
[ Servidor FastAPI: servidor/live_nativo.py ]
      │
      ▼ (types.Blob mime_type="audio/pcm;rate=16000")
[ Gemini Multimodal Live API (gemini-3.8-live) ]
      │
      ├───────────────────────┬───────────────────────┐
      ▼                       ▼                       ▼
[ Resposta de Áudio ]    [ Barge-in / Interrupção ]  [ Chamada de Função ]
(PCM 24kHz Base64)       (Usuário começou a falar)   (ex: get_system_status)
      │                       │                       │
      ▼                       ▼                       ▼
[ Reprodução no HUD ]    [ Esvazia buffer áudio ]    [ Executa em system_tools.py ]
(Sem engasgos/delay)     (Silencia JARVIS na hora)   [ Devolve resultado ao Gemini ]
```

---

## 4. Catálogo de Habilidades (Tools)

Cada ferramenta é registrada com um schema JSON que o Gemini reconhece para decidir quando chamá-la:

| Ferramenta | Descrição | Parâmetros |
| :--- | :--- | :--- |
| `get_system_status` | Telemetria completa de hardware (CPU, RAM, Disco, Bateria, Uptime). | *Nenhum* |
| `get_machine_profile` | Identifica a máquina: sistema, ambiente gráfico, CPU, RAM, GPUs, discos, tela, áudio e apps padrão. | *Nenhum* |
| `get_current_datetime` | Horário, data e dia da semana. | *Nenhum* |
| `open_application` | Inicia programas locais (navegador, código, terminal, calculadora). | `app_name: string` |
| `search_web` | Abre o navegador padrão em uma busca do Google. | `query: string` |
| `adjust_volume` | Altera volume do sistema operacional (aumentar, diminuir, mutar). | `action: string, percent: int` |
| `take_quick_note` | Grava uma anotação em arquivo texto local (`~/jarvis_notes.txt`). | `note_text: string` |
| `read_notes` | Recupera as anotações recentes gravadas pelo assistente. | *Nenhum* |

---

## 5. Roteiro de Expansão (Próximos Passos)

1. **Wake Word Local**:
   - Ativação por palavra-chave ("Ei Jarvis" ou "Jarvis") rodando localmente sem enviar áudio para a nuvem antes do gatilho.
2. **Visão Multimodal (Webcam / Compartilhamento de Tela)**:
   - Enviar 1 frame JPEG por segundo para o Gemini Live analisar o que você está vendo ou apontando.
3. **Automação Residencial / IoT**:
   - Integração com Home Assistant ou lâmpadas inteligentes (Philips Hue, Tuya) para controlar o quarto/escritório por voz.
4. **Comandos de Terminal Supervisionados**:
   - Capacidade do JARVIS rodar scripts ou verificar status de servidores sob confirmação do usuário.

---

## 6. Integração com OmniRoute & Pool de Contas

O projeto integra diretamente com o **OmniRoute**:
- **Combo Dedicado**: `jarvis` configurado no OmniRoute com estratégia `round-robin`.
- **Pool de Contas**: Suporte a failover automático entre as contas Gemini do pool (`GEMINI_API_KEYS`). Se uma conta atingir limite de cota (*Rate Limit 429*), o JARVIS rotaciona de forma transparente para a próxima conta disponível sem interromper a sessão.
- **Configuração Segura**: Chaves carregadas via `.env` com permissões restritas `0600`.


---

## 7. Módulo Google ADK Voice Agent (Arquitetura Unificada)

O repositório unifica a ponte nativa do JARVIS e o ecossistema oficial do **Google Agent Development Kit (ADK)**:

```text
                    CLIENTE ÚNICO
                 HTML + JS + Microfone
                         │
                         ▼
                SERVIDOR ÚNICO / ADK
                         │
                 ┌───────┴────────┐
                 │                │
                 ▼                ▼
           CAMINHO RÁPIDO   COORDENADOR AVANÇADO
          (baixa latência)      (complexo)
          tarefas simples    sub-agentes especialistas
                 │                │
                 │          ┌─────┴─────┐
                 │          ▼           ▼
                 │     especialista  especialista
                 │       sistema      navegador
                 │
                 └──────────┬───────────┘
                            ▼
                       MESMA RESPOSTA
                       TEXTO OU VOZ
```

### Decisões de Engenharia ADK
1. **Runner.run_live() vs StreamingMode**: A seleção da Live API é feita exclusivamente por `Runner.run_live()`. Em Python, o enum `StreamingMode.BIDI` não é lido no fluxo live e foi removido.
2. **Segregação de Modelos**:
   - `LIVE_MODEL_PRIMARY`: Modelos Live nativos com WebSockets bidirecionais (`gemini-3.8-live` ou `gemini-2.5-flash-native-audio-latest`).
   - `TEXT_MODEL`: Modelos textuais convencionais (`gemini-flash-latest` com fallback para `gemini-2.5-flash`).
3. **Persistência de Memória Durável**: Utiliza `DatabaseSessionService` (`sqlite+aiosqlite:///sessoes.db`) garantindo que as preferências e memórias de usuário (`user:`) sobrevivam a reinicializações de processo.
4. **Resiliência a falhas do provedor**: `servidor/falhas.py` classifica cada erro. Cota (429) e chave recusada giram o pool (`GEMINI_API_KEYS`); modelo sobrecarregado ou inexistente usa o modelo reserva num runner próprio; tempo esgotado e rede acionam o OmniRoute; requisição inválida e contexto excedido falham na hora, sem gastar as outras chaves.
5. **Fila por sessão**: dois turnos de `/api/chat` na mesma sessão nunca rodam ao mesmo tempo (sessões diferentes seguem em paralelo), para não intercalar eventos na mesma conversa do ADK.
