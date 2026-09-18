---
name: google-workspace
description: Integração com Google Workspace para consulta de e-mails no Gmail, redação de rascunhos, edição no Google Docs e notas no Google Keep.
---

# Google Workspace Skill

Esta habilidade permite gerenciar produtividade pessoal e colaboração no ecossistema Google Workspace por comandos de voz ou texto.

## Capacidades Principais
1. **Busca na Caixa de Entrada (`workspace_search_emails`):** Pesquisa mensagens e e-mails recentes filtrando por remetente, assunto ou palavras-chave (`READ`).
2. **Criação de Rascunhos (`workspace_create_draft`):** Redige e organiza rascunhos de e-mail no Gmail prontos para revisão antes do envio (`EXTERNAL_WRITE`).
3. **Edição de Documentos (`workspace_append_doc`):** Cria ou anexa notas e parágrafos a documentos no Google Docs (`EXTERNAL_WRITE`).
4. **Notas Rápidas no Keep (`workspace_create_keep_note`):** Captura ideias, itens de listas e tarefas no Google Keep (`EXTERNAL_WRITE`).

## Diretrizes de Governança
- Ações mutantes (`workspace_create_draft`, `workspace_append_doc`, `workspace_create_keep_note`) são classificadas como `EXTERNAL_WRITE` e requerem confirmação do usuário antes de persistir dados em contas externas.
- Indique com clareza se o plug-in estiver em modo de demonstração local (mock).
