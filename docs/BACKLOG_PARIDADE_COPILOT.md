# Backlog de Paridade Funcional — J.A.R.V.I.S. vs. Microsoft Copilot

**Repositório**: `eduardo02138/JARVIS---Assistente-Pessoal-de-IA-`  
**Branch**: `feat/copilot-capabilities-20261024`  
**Baseline de origem**: `origin/main` (`81afe3b6aa565145fbe67cca61fc0dbae2804eac`)  
**Orquestrador Técnico**: Farol (`Keystone`, `Tecelão`, `Lupa`, `Sentinel`, `Harbor`)  
**Status Global**: `DONE` (F0 a F11 concluídas e auditadas)

---

## Matriz de Fases e Status

| Fase | Prioridade | Escopo | Módulos Principais | Suíte de Testes | Status |
| :--- | :---: | :--- | :--- | :--- | :---: |
| **F0** | P0 | Baseline, Inventário e Sincronização Git | `docs/BACKLOG_PARIDADE_COPILOT.md`, `docs/RELATORIO_IMPLEMENTACAO_COPILOT.md` | Baseline P0 + ADK | `DONE` |
| **F1** | P0 | Identidade Server-Side e Autoridade Única (`AuthorityKernel`) | `authority_kernel.py`, `servidor/seguranca.py`, `policy_engine.py`, `system_tools.py` | `monitoring/test_authority_kernel.py` (6/6) | `DONE` |
| **F2** | P0 | Orquestração Durável (`Planner -> Router -> Executor -> Verifier`) | `orquestrador.py`, `servidor/rotas_agente.py` | `monitoring/test_orquestrador.py` (3/3) | `DONE` |
| **F3** | P1 | Chat Multimodelo, Modos (`rapido`, `profundo`, `pesquisa`, `estudo`), Memória e Deep Research Real | `pesquisa_e_memoria.py`, `servidor/rotas_agente.py` | `monitoring/test_chat_memoria_pesquisa.py` (3/3) | `DONE` |
| **F4** | P1 | Documentos Multi-formato, RAG com ACL por Usuário e Copilot Pages | `documentos_rag_pages.py`, `servidor/rotas_agente.py` | `monitoring/test_documentos_rag_pages.py` (3/3) | `DONE` |
| **F5** | P1 | Voz (Debounce de Turno, Ditado Isolado, Read Aloud) e Copilot Vision com Consentimento | `voz_visao_linux.py`, `servidor/live_nativo.py`, `gemini-live-widget/widget.js` | `monitoring/test_voz_visao.py` (3/3) | `DONE` |
| **F6** | P1 | Navegador Autônomo Seguro (`BrowserPolicyGuard`) e Copilot Journeys (Opt-in) | `browser_journeys.py`, `agentes/computer_use/agente.py`, `servidor/rotas_agente.py` | `monitoring/test_browser_journeys.py` (3/3) | `DONE` |
| **F7** | P1 | Conectores Pessoais e Corporativos com OAuth 2.0 + PKCE (`TokenVault` `0600`) | `conectores_oauth.py`, `servidor/rotas_agente.py` | `monitoring/test_conectores_oauth.py` (3/3) | `DONE` |
| **F8** | P1 | Copilot Cowork (Jobs Multi-Etapa Duráveis) e Tarefas Agendadas | `cowork_scheduler.py`, `servidor/rotas_agente.py` | `monitoring/test_cowork_scheduler.py` (3/3) | `DONE` |
| **F9** | P2 | Geração e Edição Iterativa de Imagens com Proveniência | `geracao_imagens.py`, `servidor/rotas_agente.py` | `monitoring/test_imagens.py` (2/2) | `DONE` |
| **F10** | P2 | Assistente de Compras Estruturado e Espaço de Saúde Opt-in Isolado | `compras_e_saude.py`, `servidor/rotas_agente.py` | `monitoring/test_compras_saude.py` (2/2) | `DONE` |
| **F11** | P0 | UX Unificada (`/api/copilot/capabilities`), CI e Homologação Final | `static/index.html`, `gemini-live-widget/index.html`, `.github/workflows/ci.yml` | Suíte completa Pytest + P0 + ADK | `DONE` |
