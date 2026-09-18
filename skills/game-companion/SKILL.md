---
name: game-companion
description: Assistência tática em tempo real para jogos online, gerenciamento de cronômetros de objetivos/bosses e inicialização de jogos locais.
---

# Game Companion Skill

Esta habilidade capacita o agente a atuar como copiloto tático e assistente de jogos (e-sports, RPGs e shooters no PC).

## Capacidades Principais
1. **Definição de Jogo Ativo (`game_companion_set_active_game`):** Sintoniza o contexto operacional para o jogo informado pelo usuário (ex: Marvel Rivals, Counter-Strike 2, GTA V).
2. **Cronômetro Tático (`game_companion_tactical_timer`):** Inicia contagens regressivas precisas para respawn de chefes, recarga de ultimates inimigos ou rotação de zonas seguras, gerando alertas no HUD quando o tempo expirar.
3. **Conselho Estratégico (`game_companion_get_strategy`):** Fornece dicas táticas imediatas de contra-ataque, posicionamento e controle de grupo contra personagens ou chefes específicos.
4. **Localização e Lançamento de Jogos (`game_companion_list_installed_games`, `game_companion_launch_game`):** Descobre jogos instalados no ecossistema local (Steam, Lutris, Epic Games, Proton) e inicializa executáveis diretamente.

## Diretrizes de Uso
- Sempre confirme a inicialização de timers com a duração e o nome do objetivo em formato legível (ex: "1m 30s").
- Ao responder com conselhos táticos, seja sucinto e direto, sem preâmbulos longos para não atrapalhar a atenção do jogador durante a partida.
- Nível de risco das ações de lançamento: `LOW_WRITE`. Consultas estratégicas: `READ`.
