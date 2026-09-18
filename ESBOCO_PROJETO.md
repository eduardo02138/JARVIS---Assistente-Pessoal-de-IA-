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
/home/edu/Documentos/assistente/
│
├── .venv/                   # Ambiente virtual isolado (Python 3.14 / uv)
├── requirements.txt         # Lista de dependências fixadas
├── .env.example             # Modelo para variáveis de ambiente (GEMINI_API_KEY, PORT, etc.)
│
├── ESBOCO_PROJETO.md        # [Este arquivo] Especificação técnica e guia do desenvolvedor
├── README.md                # Instruções rápidas de uso e comandos suportados
├── run_jarvis.sh            # Script utilitário para iniciar o assistente com 1 clique
│
├── app.py                   # [NOVO] Aplicativo Desktop Nativo Flutuante (PySide6 / QtWebEngine)
├── run_app.sh               # Script para abrir o App Desktop com 1 clique
├── server.py                # Núcleo do Backend:
│                            # - Servidor FastAPI
│                            # - WebSocket Bridge com a Gemini Live API
│                            # - Despachante e validador de Function Calling
│
├── system_tools.py          # Habilidades e Ferramentas do Sistema Operacional:
│                            # - Telemetria de Hardware (CPU, RAM, Disco, Bateria, Uptime)
│                            # - Lançador de Aplicativos (Chrome, VSCode, Terminal, etc.)
│                            # - Controle de Volume e Áudio
│                            # - Pesquisa na Web
│                            # - Bloco de Notas / Lembretes
│
└── static/                  # Frontend Web Holográfico (HUD Sci-Fi):
    ├── index.html           # Estrutura do HUD, Reator Arc e painéis
    ├── style.css            # Estilos em Glassmorphism, animações e cores Neon
    └── app.js               # Web Audio API (16kHz in / 24kHz out) + Canvas 60 FPS
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
[ Servidor FastAPI: server.py ]
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
- **Pool de Contas**: Suporte a failover automático entre as **7 contas Gemini** cadastradas. Se uma conta atingir limite de cota (*Rate Limit 429*), o JARVIS rotaciona de forma transparente para a próxima conta disponível sem interromper a sessão.
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
4. **Resiliência a Quotas (HTTP 429)**: Rotação dinâmica do pool de chaves (`GEMINI_API_KEYS`).
