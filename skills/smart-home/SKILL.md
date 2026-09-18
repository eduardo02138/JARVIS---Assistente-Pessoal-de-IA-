---
name: smart-home
description: Automação residencial e IoT para controle de iluminação ambiente, perfis e cenas de produtividade/descanso e telemetria climática.
---

# Smart Home Skill

Esta habilidade gerencia dispositivos inteligentes e automação residencial conectada ao ecossistema do usuário.

## Capacidades Principais
1. **Controle de Iluminação (`smart_home_set_light`):** Liga, desliga e ajusta brilho ou cor das luzes em cômodos específicos (`LOW_WRITE`).
2. **Ativação de Cenas (`smart_home_activate_scene`):** Dispara configurações conjuntas pré-definidas, como "Foco/Trabalho", "Cinema" ou "Descanso" (`LOW_WRITE`).
3. **Telemetria de Clima (`smart_home_get_climate`):** Consulta sensores de temperatura, umidade e estado do ar condicionado (`READ`).

## Diretrizes de Uso
- Responda prontamente informando o estado resultante dos dispositivos afetados.
