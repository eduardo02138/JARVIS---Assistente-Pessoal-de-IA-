# 🛰️ Sistema de Monitoramento, Logs e Depuração (J.A.R.V.I.S. / Gemini Live)

Esta pasta contém a suíte completa de telemetria, diagnóstico, testes automatizados e observabilidade em tempo real do assistente.

---

## 📁 Estrutura de Arquivos

| Arquivo / Pasta | Descrição |
| :--- | :--- |
| **`dashboard.html`** | Painel Web de Depuração em tempo real (acessível em `http://localhost:8000/debug`). |
| **`monitor.py`** | Monitor interativo de terminal com visualização de métricas e eventos ao vivo. |
| **`test_suite.py`** | Script de diagnóstico e auto-teste automatizado ponta a ponta. |
| **`logger.py`** | Gerenciador estruturado de eventos, gravação JSONL, log de erros e telemetria. |
| **`logs/`** | Diretório onde os arquivos de log são persistidos. |
| ├── `assistant.log` | Log textual cronológico formatado de interações e ferramentas. |
| ├── `events.jsonl` | Stream JSONL estruturado de todos os eventos para análise e ingestão. |
| ├── `errors.log` | Log dedicado para registrar falhas, avisos de conexão e exceções. |
| └── `server.log` | Log de saída padrão e erros do processo do servidor Uvicorn / FastAPI. |

---

## 🚀 Como Utilizar

### 1. Painel Web de Depuração
Abra seu navegador no endereço:
```
http://localhost:8000/debug
```
**Recursos disponíveis no painel:**
- **Métricas de Áudio:** Pacotes de microfone (16kHz) enviados e áudio recebido (24kHz).
- **Diálogo ao Vivo:** Transcrição em tempo real da fala do usuário e respostas do JARVIS.
- **Injeção de Prompts:** Envie comandos de texto diretamente para o assistente conectado sem precisar falar.
- **Checagem do Pool:** Verifique a latência e a cota das 7 contas Gemini do OmniRoute.
- **Console Filtrável:** Filtre logs por FALA, TOOLS, ÁUDIO, ERROS ou TODOS.
- **Download de Logs:** Baixe o arquivo consolidado de logs com um clique.

---

### 2. Monitor Interativo no Terminal
Para acompanhar a telemetria ao vivo diretamente no seu terminal:
```bash
./monitoring/monitor.py
# ou
python3 monitoring/monitor.py
```

---

### 3. Diagnóstico da máquina e da configuração
Para ver o que falta nesta máquina para cada função, com a correção de cada problema:
```bash
.venv/bin/python diagnostico.py            # relatório legível (saída 1 se houver erro)
.venv/bin/python diagnostico.py --json     # relatório em JSON
```
Com o servidor rodando, `GET /api/diagnostico` (com o token) devolve o mesmo relatório.

---

### 4. Executar Teste de Diagnóstico Automatizado
Para testar todas as camadas (chaves, tools de sistema, handshake Live API, desktop app):
```bash
./monitoring/test_suite.py
# ou
python3 monitoring/test_suite.py
```

---

## 📡 Endpoints da API de Telemetria

- `GET /api/debug/telemetry` - Retorna o resumo consolidado de métricas (uptime, pacotes, tools, failovers).
- `GET /api/debug/events?limit=100` - Retorna os últimos eventos estruturados.
- `GET /api/debug/test-accounts` - Testa e retorna o status e latência de cada chave do pool.
- `POST /api/debug/inject-prompt` - Injeta um comando de teste na sessão ativa.
- `GET /api/debug/download-log` - Faz o download do arquivo `assistant.log`.
- `POST /api/debug/clear-logs` - Reseta a memória de eventos e limpa os arquivos de log.
