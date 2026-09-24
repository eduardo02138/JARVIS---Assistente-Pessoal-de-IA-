"""
Plug-in: Google Finance & Gestão de Portfólio Inteligente
Plug-in em modo demonstração: as cotações e o portfólio são simulados localmente,
analisar alocação de ativos e gerar insights financeiros por comando de voz.
"""

import json
import logging
import os
from typing import List, Dict
from plugin_sdk import JarvisPlugin, PluginMeta

logger = logging.getLogger("jarvis.plugins.google_finance")

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ASSETS_PADRAO = os.path.join(RAIZ, "skills", "google-finance", "assets", "portfolio_default.json")

# Fallback embutido caso o arquivo de camada L3 (assets/) seja removido.
PORTFOLIO_FALLBACK: List[Dict] = [
    {"ticker": "NVDA", "nome": "NVIDIA Corporation", "setor": "Tecnologia / Semicondutores", "quantidade": 25, "preco_medio": 115.50, "cotacao_atual": 138.20},
    {"ticker": "BTC", "nome": "Bitcoin", "setor": "Criptoativos", "quantidade": 0.45, "preco_medio": 58000.0, "cotacao_atual": 64200.0},
    {"ticker": "PETR4", "nome": "Petrobras PN", "setor": "Energia / Petróleo", "quantidade": 300, "preco_medio": 36.20, "cotacao_atual": 39.10},
    {"ticker": "IVVB11", "nome": "iShares S&P 500 ETF", "setor": "Índice Global", "quantidade": 50, "preco_medio": 290.0, "cotacao_atual": 325.40},
]


def _carregar_portfolio_padrao() -> List[Dict]:
    """Lê a carteira inicial da camada L3 (assets/) com fallback embutido."""
    try:
        with open(ASSETS_PADRAO, encoding="utf-8") as f:
            dados = json.load(f)
        if isinstance(dados, list) and dados:
            return dados
    except Exception as e:
        logger.warning("Carteira padrão (assets) indisponível; usando fallback embutido: %s", e)
    return PORTFOLIO_FALLBACK

class GoogleFinancePlugin(JarvisPlugin):
    def __init__(self):
        super().__init__(PluginMeta.do_manifesto(__file__))
        # Carteira padrão inicial do investidor
        self.portfolio: List[Dict] = _carregar_portfolio_padrao()

    def on_load(self):
        self.register_tool(
            name="finance_get_quote",
            description="SIMULADO: devolve uma cotação de demonstração de ação, índice ou criptomoeda (ex: 'PETR4', 'NVDA', 'BTC'). Os valores não vêm do mercado real.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "ticker": {
                        "type": "STRING",
                        "description": "Código do ativo ou ticker (ex: 'NVDA', 'PETR4', 'BTC', 'AAPL')."
                    }
                },
                "required": ["ticker"]
            },
            handler=self.get_quote,
            risk_level="READ"
        )

        self.register_tool(
            name="finance_get_portfolio",
            description="Exibe a visão consolidada de todos os investimentos do senhor no Google Finance: saldo total, lucro/prejuízo e rentabilidade.",
            parameters={
                "type": "OBJECT",
                "properties": {}
            },
            handler=self.get_portfolio,
            risk_level="READ"
        )

        self.register_tool(
            name="finance_add_asset",
            description="Adiciona ou atualiza uma posição de ativo no portfólio de investimentos do Google Finance.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "ticker": {
                        "type": "STRING",
                        "description": "Símbolo do ativo (ex: 'VALE3', 'TSLA', 'SOL')."
                    },
                    "quantity": {
                        "type": "NUMBER",
                        "description": "Quantidade de cotas ou unidades adquiridas."
                    },
                    "avg_price": {
                        "type": "NUMBER",
                        "description": "Preço médio de compra por unidade."
                    },
                    "sector": {
                        "type": "STRING",
                        "description": "Setor econômico do ativo (opcional, ex: 'Tecnologia', 'Saúde', 'Cripto')."
                    }
                },
                "required": ["ticker", "quantity", "avg_price"]
            },
            handler=self.add_asset,
            risk_level="LOW_WRITE"
        )

        self.register_tool(
            name="finance_get_insights",
            description="Gera insights automáticos sobre a carteira: setores sub-representados, diversificação de risco e alocação de ativos.",
            parameters={
                "type": "OBJECT",
                "properties": {}
            },
            handler=self.get_insights,
            risk_level="READ"
        )

    def get_quote(self, ticker: str) -> dict:
        t = ticker.upper().strip()
        # Busca no portfólio existente ou gera cotação referencial
        for item in self.portfolio:
            if item["ticker"] == t:
                var = ((item["cotacao_atual"] - item["preco_medio"]) / item["preco_medio"]) * 100
                return {
                    "sucesso": True,
                    "mock": True,
                    "executado_externamente": False,
                    "ticker": t,
                    "nome": item["nome"],
                    "preco_atual": item["cotacao_atual"],
                    "variacao_diaria": "+1.85%",
                    "variacao_posicao": f"{var:+.2f}%",
                    "mensagem": f"Cotação simulada de {t}… não é o preço real de mercado: R$ {item['cotacao_atual']:,.2f} ({var:+.2f}% na posição de demonstração)."
                }

        # Valores referenciais dinâmicos
        preco_ref = 150.00
        if "BTC" in t: preco_ref = 64200.00
        elif "ETH" in t: preco_ref = 3450.00
        elif "NVDA" in t: preco_ref = 138.20

        return {
            "sucesso": True,

            "mock": True,

            "executado_externamente": False,
            "ticker": t,
            "nome": f"Ativo de Mercado ({t})",
            "preco_atual": preco_ref,
            "variacao_diaria": "+0.95%",
            "mensagem": f"Cotação simulada de {t}… não é o preço real de mercado: R$ {preco_ref:,.2f}."
        }

    def get_portfolio(self) -> dict:
        total_investido = 0.0
        total_atual = 0.0
        detalhes = []

        for item in self.portfolio:
            custo = item["quantidade"] * item["preco_medio"]
            valor = item["quantidade"] * item["cotacao_atual"]
            total_investido += custo
            total_atual += valor
            lucro = valor - custo
            rent = (lucro / custo * 100) if custo > 0 else 0
            detalhes.append({
                "ticker": item["ticker"],
                "setor": item["setor"],
                "quantidade": item["quantidade"],
                "valor_mercado": round(valor, 2),
                "lucro_prejuizo": round(lucro, 2),
                "rentabilidade": f"{rent:+.2f}%"
            })

        lucro_total = total_atual - total_investido
        rent_total = (lucro_total / total_investido * 100) if total_investido > 0 else 0

        return {
            "sucesso": True,

            "mock": True,

            "executado_externamente": False,
            "total_investido": round(total_investido, 2),
            "patrimonio_atual": round(total_atual, 2),
            "lucro_total": round(lucro_total, 2),
            "rentabilidade_total": f"{rent_total:+.2f}%",
            "posicoes": detalhes,
            "mensagem": f"Senhor, seu portfólio consolidado no Google Finance está avaliado em R$ {total_atual:,.2f}, acumulando rentabilidade positiva de {rent_total:+.2f}% (+R$ {lucro_total:,.2f})."
        }

    def add_asset(self, ticker: str, quantity: float, avg_price: float, sector: str = "Geral") -> dict:
        t = ticker.upper().strip()
        for item in self.portfolio:
            if item["ticker"] == t:
                item["quantidade"] += float(quantity)
                item["preco_medio"] = float(avg_price)
                return {
                    "sucesso": True,
                    "mock": True,
                    "executado_externamente": False,
                    "ticker": t,
                    "mensagem": f"Posição de {t} atualizada no portfólio simulado, senhor. Ainda não há conexão com o Google Finance real."
                }

        self.portfolio.append({
            "ticker": t,
            "nome": f"{t} Asset",
            "setor": sector,
            "quantidade": float(quantity),
            "preco_medio": float(avg_price),
            "cotacao_atual": float(avg_price) * 1.02
        })
        return {
            "sucesso": True,
            "mock": True,
            "executado_externamente": False,
            "ticker": t,
            "mensagem": f"Novo ativo {t} adicionado ao seu portfólio no setor '{sector}', senhor."
        }

    def get_insights(self) -> dict:
        total_atual = sum(item["quantidade"] * item["cotacao_atual"] for item in self.portfolio)
        setores = {}
        for item in self.portfolio:
            val = item["quantidade"] * item["cotacao_atual"]
            setor = item["setor"]
            setores[setor] = setores.get(setor, 0.0) + val

        alocacao_pct = {s: round((v / total_atual) * 100, 1) for s, v in setores.items()}

        sub_representados = ["Saúde / Biotecnologia", "Utilities / Saneamento", "Renda Fixa / Tesouro Direto"]
        recomendacao = (
            f"Alocação setorial atual: {', '.join(f'{k}: {v}%' for k, v in alocacao_pct.items())}. "
            f"Setores sub-representados identificados: {', '.join(sub_representados)}. "
            "Recomenda-se aportar em Renda Fixa ou fundos defensivos para equilibrar a alta exposição a Tecnologia e Cripto."
        )

        return {
            "sucesso": True,

            "mock": True,

            "executado_externamente": False,
            "alocacao_setorial": alocacao_pct,
            "setores_sub_representados": sub_representados,
            "recomendacao_tatica": recomendacao,
            "mensagem": f"Análise de portfólio concluída, senhor: {recomendacao}"
        }
