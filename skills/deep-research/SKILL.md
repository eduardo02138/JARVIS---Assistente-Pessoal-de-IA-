---
name: deep-research
description: Pesquisa aprofundada e elaboração assíncrona de dossiês analíticos em segundo plano sem bloquear a conversação do usuário.
---

# Deep Research Skill

Esta habilidade coordena pesquisas abrangentes e investigações temáticas assíncronas através de agentes em background.

## Capacidades Principais
1. **Disparo de Investigação (`deep_research_start`):** Inicia a varredura e compilação de informações detalhadas sobre temas complexos em segundo plano (`EXTERNAL_WRITE`).
2. **Consulta de Relatório (`deep_research_get_report`):** Recupera o status percentual ou o dossiê final completo de uma pesquisa previamente iniciada (`READ`).
3. **Listagem de Pesquisas (`deep_research_list`):** Exibe o histórico de todas as investigações ativas ou arquivadas (`READ`).

## Diretrizes de Uso
- O agente nunca deve bloquear o canal de voz enquanto a pesquisa estiver em andamento. Deve confirmar o início do trabalho e o ID da pesquisa ao usuário.
- Ao concluir a compilação, o sistema gera notificação de evento na telemetria e no HUD.
