# Backlog Versionado — Paridade Funcional Microsoft Copilot no J.A.R.V.I.S. (Linux)

- **Repositório**: `eduardo02138/JARVIS---Assistente-Pessoal-de-IA-`
- **Branch de Execução**: `feat/copilot-capabilities-20261024`
- **Baseline SHA**: `81afe3b6aa565145fbe67cca61fc0dbae2804eac`
- **Documento de Referência**: [`microsoft_copilot_arquitetura_funcoes_2026-10-24.md`](../microsoft_copilot_arquitetura_funcoes_2026-10-24.md)
- **Orquestrador Técnico**: Farol (com especialistas Keystone, Tecelão, Lupa, Sentinel e Harbor)

---

## 1. Inventário de Baseline (F0)

### 1.1 Superfícies de Execução e Endpoints Atuais

| Superfície / Módulo | Caminho | Classificação Baseline | Observações |
| :--- | :--- | :--- | :--- |
| FastAPI App & Bootstrap | `server.py`, `servidor/__init__.py` | `REAL` | Modularizado em `rotas_sistema`, `rotas_agente`, `live_nativo`, `live_adk`. |
| Segurança & Sessão | `servidor/seguranca.py` | `PARTIAL` | Emite tokens e `SESSOES_EMITIDAS`, porém `sessao_aceita()` não valida emissão/expiração estrita por padrão. |
| Policy Engine | `policy_engine.py` | `PARTIAL` | Classifica risco (`READ`, `LOW_WRITE`, `EXTERNAL_WRITE`, `PRIVILEGED`, `RESTRICTED`), mas não possui `AuthorityKernel` com capabilities one-shot, lock de concorrência ou journal causal. |
| AuthorityKernel | `authority_kernel.py` | `ABSENT` | P0 da Fase F1 — ponto único obrigatório de autorização e execução de efeitos colaterais. |
| Orquestrador ADK | `agentes/assistente.py`, `agentes/roteador.py`, `servidor/runtime_adk.py` | `PARTIAL` | Possui subagentes e `guarda_de_ferramentas`, mas sem separação formal `Planner -> Router -> Executor -> Verifier` com estado durável e checkpoints (F2). |
| Memória e Histórico | `agentes/memoria.py`, `preferences_manager.py` | `PARTIAL` | Persistência JSON local e busca por substring; faltam isolamento completo de histórico conversacional, retenção configurável, exclusão granular/total e links estáticos de compartilhamento (F3). |
| Pesquisa Web & Deep Research | `system_tools.py`, `plugins/deep_research/plugin.py` | `MOCK` / `PARTIAL` | `read_web_page` é real (SSRF-safe), porém `deep_research` é simulado (`simulated: true`, `time.sleep(8)`). Será substituído por pipeline real de pesquisa multi-etapas com citações verificáveis (F3). |
| Documentos, RAG e Pages | `docs/`, `agentes/` | `ABSENT` | Faltam upload multi-formato validado (até 20 arquivos / 50 MB), índice RAG local com ACL por usuário e artefatos persistentes editáveis estilo Copilot Pages (F4). |
| Voz, Visão e Integração Linux | `servidor/live_nativo.py`, `servidor/live_adk.py`, `gemini-live-widget/widget.js`, `transcricao.py` | `PARTIAL` | Funcional, mas o VAD do widget (`vadThreshold=0.007`, `maxHoldover=0.8s`) gera micro-cortes em ruído ambiente; faltam separação formal Ditado vs Read Aloud vs Voz Live e contexto visual consentido separado de mutação (F5). |
| Browser Autônomo & Journeys | `agentes/computer_use/playwright_computer.py` | `PARTIAL` | Controle Chromium via Playwright funcional; faltam políticas de domínio, pausa humana em formulários/pagamentos/mensagens, defesa de prompt injection web e Journeys opt-in com retenção de 14 dias (F6). |
| Conectores & OAuth PKCE | `plugins/google_workspace/plugin.py`, `mcp_client_manager.py` | `MOCK` / `PARTIAL` | Cliente MCP é real; `google_workspace` é simulado. Falta framework OAuth 2.0 + PKCE com cofre protegido de tokens e conectores reais/honestos para Gmail, Drive, Calendar, Outlook, OneDrive e Contacts (F7). |
| Cowork & Tarefas Agendadas | `agentes/` | `ABSENT` | Falta fila durável (`QUEUED`, `RUNNING`, `WAITING_APPROVAL`, `SUCCEEDED`, `FAILED`, `CANCELLED`, `BLOCKED`), checkpoints, retries limitados e scheduler local (F8). |
| Geração e Edição de Imagens | `plugins/ginjutsu_studio/` | `MOCK` | Plug-in atual é simulado. Falta pipeline com adaptador configurável de provedor, edição iterativa, galeria local e reporte honesto quando sem credencial/quota (F9). |
| Compras & Saúde | `plugins/google_finance/` | `ABSENT` / `MOCK` | Faltam comparador estruturado de compras sem compra automática e módulo informativo isolado de saúde com consentimento explícito (F10). |
| UX Unificada & Homologação | `index.html`, `app.js`, `gemini-live-widget/` | `PARTIAL` | HUD e Widget existem; faltam endpoints/controles unificados para modos, histórico, Pages, tarefas, aprovações, conectores e suíte E2E completa (F11). |

### 1.2 Classificação dos Plug-ins Existentes

| Plug-in | `simulated` no Manifest | Classificação Real | Destino no Plano |
| :--- | :---: | :---: | :--- |
| `game_companion` | `false` | `REAL` | Mantido e integrado ao `AuthorityKernel`. |
| `deep_research` | `true` | `MOCK` | Promovido a `REAL` na Fase F3 com pesquisa web verificável, decomposição de sub-perguntas e citações auditáveis. |
| `google_workspace` | `true` | `MOCK` | Integrado ao subsistema de Conectores OAuth PKCE na Fase F7 (opera real quando credenciado ou `BLOCKED`/`UNAVAILABLE` honesto quando sem credenciais OAuth). |
| `google_finance` | `true` | `MOCK` | Mantido desativado por padrão em produção (`JARVIS_ATIVAR_MOCKS=0`). |
| `smart_home` | `true` | `MOCK` | Mantido desativado por padrão em produção (`JARVIS_ATIVAR_MOCKS=0`). |
| `social_feed` | `true` | `MOCK` | Mantido desativado por padrão em produção (`JARVIS_ATIVAR_MOCKS=0`). |
| `live_stream` | `true` | `MOCK` | Mantido desativado por padrão em produção (`JARVIS_ATIVAR_MOCKS=0`). |
| `ginjutsu_studio` | `true` | `MOCK` | Mantido desativado por padrão; novo módulo dedicado de imagens (`F9`) com adaptador real/configurável. |

---

## 2. Backlog Rastreável por Fase (F0 a F11)

| ID | Fase | Prioridade | Título da Tarefa | Critério de Aceite / Gate | Status |
| :--- | :---: | :---: | :--- | :--- | :---: |
| `T-F0.1` | F0 | P0 | Congelar baseline, validar testes P0/ADK/pytest e publicar inventário e backlog | Suíte baseline 100% verde + documentos versionados na branch | `DONE` |
| `T-F1.1` | F1 | P0 | Implementar `AuthorityKernel` (`authority_kernel.py`) com registro fechado de executores, capabilities one-shot, lock de concorrência, detecção de ambiguidade e journal causal | Zero bypass conhecido; suíte adversarial `monitoring/test_authority_kernel.py` 100% verde | `DONE` |
| `T-F1.2` | F1 | P0 | Integrar `AuthorityKernel` em `servidor/seguranca.py`, `policy_engine.py`, `agentes/assistente.py`, `servidor/live_nativo.py`, `servidor/rotas_agente.py`, `jarvis_mcp_server.py` e `system_tools.py` | Todas as chamadas de ferramentas e efeitos colaterais passam pelo `AuthorityKernel` | `DONE` |
| `T-F2.1` | F2 | P0 | Implementar orquestração estruturada (`orquestrador.py`) separando `Planner`, `Router`, `Executor` e `Verifier` com contratos Pydantic, orçamento e recuperação de falhas | Tarefa longa pode reiniciar de checkpoint sem duplicar efeitos colaterais; `monitoring/test_orquestrador.py` verde | `DONE` |
| `T-F3.1` | F3 | P1 | Implementar modos selecionáveis (`rapido`, `profundo`, `estudo`, `pesquisa`, `smart`), histórico isolado por usuário (listar, reabrir, excluir item/tudo, compartilhar snapshot, retenção) e Deep Research real com citações | Zero vazamento entre usuários; citações verificáveis; `monitoring/test_chat_memoria_pesquisa.py` verde | `DONE` |
| `T-F4.1` | F4 | P1 | Implementar ingestão de documentos (PDF, DOCX, XLSX, PPTX, PNG/JPEG, CSV, JSON, MD, TXT; limite 20 arq / 50 MB / magic bytes), índice RAG local com ACL por usuário e Copilot Pages versionadas com exportação | Exclusão de arquivo remove chunks do índice imediatamente; zero vazamento cross-user; `monitoring/test_documentos_rag_pages.py` verde | `DONE` |
| `T-F5.1` | F5 | P1 | Calibrar VAD cliente/servidor contra rajadas fantasmas de `audio_stream_end`, separar Ditado / Read Aloud / Voz Live e implementar contexto visual consentido separado de automação | Zero chamadas de clique/teclado em perguntas puramente visuais; VAD estável; `monitoring/test_voz_visao.py` verde | `IN_PROGRESS` |
| `T-F6.1` | F6 | P1 | Endurecer Browser Autônomo (políticas de domínio, separação observar vs agir, pausa humana em pagamentos/formulários/mensagens, sanitização de prompt injection) e Journeys opt-in (14 dias) | Nenhuma ação sensível sem aprovação humana; `monitoring/test_browser_journeys.py` verde | `READY` |
| `T-F7.1` | F7 | P1 | Implementar OAuth 2.0 + PKCE, cofre protegido de tokens e conectores (Gmail, Drive, Calendar, Outlook, OneDrive, Contacts, Graph opcional) com escopos mínimos e revogação | Escopo mínimo, revogação funcional e status honesto (`BLOCKED`/`UNAVAILABLE` quando sem credenciais externas); `monitoring/test_conectores_oauth.py` verde | `READY` |
| `T-F8.1` | F8 | P1 | Implementar motor Cowork e tarefas agendadas (fila durável, estados formais, checkpoints, retries limitados, idempotência e notificações) | Tarefa agendada/longa persiste, pausa em aprovação e recupera de falha; `monitoring/test_cowork_scheduler.py` verde | `READY` |
| `T-F9.1` | F9 | P2 | Implementar pipeline de Geração e Edição Iterativa de Imagens com adaptadores configuráveis, metadados de proveniência, galeria por usuário e controle de custo | Edição iterativa preserva histórico; ausência de provedor reportada com transparência; `monitoring/test_imagens.py` verde | `READY` |
| `T-F10.1` | F10 | P2 | Implementar assistente de Compras (comparação estruturada com preço/moeda/data/fonte, zero compra sem confirmação) e módulo isolado opt-in de Saúde | Zero compra automática; dados de saúde isolados da memória comum; `monitoring/test_compras_saude.py` verde | `READY` |
| `T-F11.1` | F11 | P0 | Integrar rotas unificadas de UX, atualizar HUD/Widget, atualizar `.github/workflows/ci.yml`, executar regressão completa, Gitleaks e consolidar relatório final | Todos os testes e gates CI 100% verdes e rastreáveis | `READY` |
