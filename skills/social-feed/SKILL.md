---
name: social-feed
description: Monitoramento de notificações em redes sociais (Discord, Telegram, X) e publicação ou rascunho de atualizações rápidas de status.
---

# Social Feed Skill

Esta habilidade conecta o assistente às redes de comunicação e mídia social do usuário.

## Capacidades Principais
1. **Verificação de Notificações (`social_feed_check_notifications`):** Consulta menções importantes, mensagens diretas e alertas urgentes (`READ`).
2. **Publicação de Atualizações (`social_feed_post_update`):** Rascunha e programa mensagens curtas para transmissão em canais públicos (`EXTERNAL_WRITE`).

## Diretrizes de Uso e Governança
- Qualquer publicação pública (`social_feed_post_update`) requer confirmação explícita do usuário antes da postagem no feed (`requires_confirmation=True`).
