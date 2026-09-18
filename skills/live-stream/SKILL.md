---
name: live-stream
description: Assistência para transmissões ao vivo com monitoramento de status da stream, síntese concisa do chat do público e disparo de alertas.
---

# Live Stream Skill

Esta habilidade auxilia streamers e criadores de conteúdo durante transmissões ao vivo no Twitch, YouTube e Kick.

## Capacidades Principais
1. **Controle de Transmissão (`live_stream_toggle_status`):** Inicia ou pausa a telemetria e o monitoramento da live (`LOW_WRITE`).
2. **Síntese de Chat (`live_stream_read_chat_summary`):** Resume os tópicos mais comentados pelo público nos últimos minutos para que o streamer possa interagir sem perder o foco na tela (`READ`).
3. **Alertas de Transmissão (`live_stream_send_alert`):** Emite avisos visuais ou sonoros no HUD sobre eventos de doações, novos seguidores ou raids (`EXTERNAL_WRITE`).

## Diretrizes de Uso
- A síntese de chat deve ser ágil (1 frase), destacando perguntas chave ou reações dominantes do público.
