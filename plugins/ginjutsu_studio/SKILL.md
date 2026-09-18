---
name: ginjutsu-studio
description: Transferência de movimento, coreografia, atuação e enquadramento de vídeos existentes para novos personagens via Higgsfield Ginjutsu.
---

# Ginjutsu Studio Skill

Esta habilidade automatiza a renderização de vídeos com IA generativa, transferindo movimentos de referências reais para avatares e modelos 3D mantendo fidelidade cinemática.

## Capacidades Principais
1. **Transferência de Movimento (`ginjutsu_create_motion_transfer`):** Submete tarefas de animação transferindo a física e atuação de um vídeo base para um novo personagem (`EXTERNAL_WRITE`).
2. **Engenharia de Prompt Mestre (`ginjutsu_generate_prompt`):** Constrói prompts estruturados otimizados para consistência de estilo e retenção de detalhes corporais (`READ`).
3. **Monitoramento de Renderização (`ginjutsu_list_jobs`):** Acompanha o status e tempo estimado de entrega de cada pipeline (`READ`).

## Diretrizes de Uso
- Ações de renderização que consomem GPUs ou créditos externos são classificadas como `EXTERNAL_WRITE` e requerem confirmação do usuário.
