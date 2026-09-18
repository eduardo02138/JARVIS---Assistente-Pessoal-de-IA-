---
name: google-finance
description: Análise de mercado financeiro, cotações de ativos e consolidação de carteira de investimentos com sugestões de alocação de risco.
---

# Google Finance Skill

Esta habilidade fornece inteligência financeira e acompanhamento de carteira de investimentos multiativos (ações globais, B3, índices e criptoativos).

## Capacidades Principais
1. **Consulta de Cotações (`finance_get_quote`):** Obtém preço, variação diária e indicadores básicos de um ticker ou ativo (`READ`).
2. **Visão Consolidada do Portfólio (`finance_get_portfolio`):** Calcula saldo total, rentabilidade global acumulada e lucro/prejuízo de todas as posições (`READ`).
3. **Registro de Posição (`finance_add_asset`):** Adiciona ou atualiza um ativo na carteira informando quantidade e preço médio (`LOW_WRITE`).
4. **Diagnóstico de Diversificação (`finance_get_insights`):** Identifica concentração de risco e setores sub-representados (`READ`).

## Diretrizes de Uso
- Sempre explicite ao usuário que análises automatizadas não constituem consultoria financeira regulamentada.
- Caso os valores sejam simulados, deixe isso transparente para o usuário.
