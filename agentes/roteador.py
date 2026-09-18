"""Roteador interno: decide qual caminho atende o pedido de texto.

Regras primeiro, porque uma chamada extra de LLM só para classificar acrescenta uma
rodada de latência ao turno. O objetivo é escapar do coordenador quando o pedido é
trivial, não classificar linguagem com precisão.

Na voz não há roteamento: a sessão Live é uma conexão bidirecional presa a um agente,
e trocar de agente no meio significaria derrubá-la. Lá o coordenador já carrega as
ferramentas rápidas.
"""

import re

CAMINHO_RAPIDO = "rapido"
CAMINHO_COMPLEXO = "complexo"

# Pedidos diretos de uma ação só
PADROES_RAPIDOS = re.compile(
    r"\b(que horas|horas s[ãa]o|data de hoje|dia de hoje|"
    r"cpu|mem[óo]ria|ram|status do sistema|uso do sistema|"
    r"abrir?|abre|acessar?|pesquisar?|pesquisa|procurar?|buscar?)\b",
    re.IGNORECASE,
)

# Sinais de tarefa com várias etapas ou análise
PADROES_COMPLEXOS = re.compile(
    r"\b(depois|em seguida|ent[ãa]o|primeiro|por fim|"
    r"analis[ae]|compare?|resum[ao]|explique|planeje|organize|"
    r"e tamb[ée]m|v[áa]rios|todas as|passo a passo)\b",
    re.IGNORECASE,
)

LIMITE_DE_PALAVRAS = 14


def escolher_caminho(texto: str) -> tuple[str, str]:
    """Devolve (caminho, motivo) para o pedido em texto."""
    pedido = texto.strip()
    palavras = len(pedido.split())

    if PADROES_COMPLEXOS.search(pedido):
        return CAMINHO_COMPLEXO, "pedido com várias etapas ou análise"
    if palavras > LIMITE_DE_PALAVRAS:
        return CAMINHO_COMPLEXO, f"pedido longo ({palavras} palavras)"
    if PADROES_RAPIDOS.search(pedido):
        return CAMINHO_RAPIDO, "ação direta reconhecida"
    if palavras <= 6:
        return CAMINHO_RAPIDO, "pedido curto"
    return CAMINHO_COMPLEXO, "sem padrão claro: usa o coordenador por segurança"
