"""Protocolo compartilhado da sessão Gemini Live do J.A.R.V.I.S.

Centraliza lógica antes duplicada em server.py (e no servidor ADK extinto):
1. Interpretação de palavras de confirmação/recusa ditas ou digitadas pelo usuário
   (único parser: /ws/live, /ws/live_adk e /api/chat usam as mesmas regras).
2. Desativação do timeout de ping do WebSocket Live (evita queda após silêncio).
3. Encerramento limpo da sessão Live: close code 1000 é shutdown normal, não erro.

O pool de chaves Gemini vive em GoogleStudioProvider (provider_router.py).

server.py é o único importador: sem drift.
"""

import logging

try:
    from monitoring.logger import logger
except Exception:
    logger = logging.getLogger("JARVIS.Protocolo")

# Cobertura unificada dos conjuntos usados nos parsers originais de server.py
# (blocos 678-684 / 922-928 / 998-1004 usam palavras_confirmacao/negacao;
#  bloco 1412-1430 usa palavras_sim/nao — união cobre ambos).
PALAVRAS_SIM = {
    "sim", "confirmar", "confirmado", "autorizar", "autorizado", "pode",
    "ok", "prosseguir", "positivo", "permitir", "yes", "conceder",
}
FRASES_SIM = {"fazer teste"}
PALAVRAS_NAO = {
    "nao", "não", "negar", "negado", "cancelar", "cancela",
    "recusar", "recuso", "no",
}
# Verbos/frases que o modelo usa para PEDIR informação, não para autorizar.
# "pode repetir?" ou "pode continuar?" não são confirmações de ação pendente.
VERBOS_PEDIDO = {
    "repetir", "repete", "continuar", "continua", "dizer", "falar",
    "explicar", "explica", "mostrar", "mostra", "listar", "aguardar",
    "esperar", "perguntar", "pergunta", "informar", "detalhe", "detalhar",
}


def _limpar(texto: str) -> str:
    return "".join(c for c in texto.lower() if c.isalnum() or c.isspace()).strip()


def _tem_sim(texto_limpo: str, tokens: set) -> bool:
    """Palavra única de aceite OU frase multi-word embutida (ex.: 'fazer teste'))."""
    if tokens & PALAVRAS_SIM:
        return True
    return any(frase in texto_limpo for frase in FRASES_SIM)


def palavra_confirma(texto: str) -> bool:
    """True se o texto confirma expressamente uma ação pendente.

    Confirmação exige palavra de aceite sem nenhuma palavra de recusa:
    "sim, pode autorizar" confirma; "sim, cancelar" não confirma.
    Frase que o modelo usa para PEDIR informação não confirma:
    "pode repetir?" ou "pode continuar?" retornam False (verbo de pedido).
    """
    if not texto:
        return False
    texto_limpo = _limpar(texto)
    if not texto_limpo:
        return False
    tokens = set(texto_limpo.split())
    negado = bool(tokens & PALAVRAS_NAO)
    pedido = bool(tokens & VERBOS_PEDIDO)
    if pedido:
        return False
    return _tem_sim(texto_limpo, tokens) and not negado


def palavra_recusa(texto: str) -> bool:
    """True se o texto contém palavra de negação ou cancelamento."""
    if not texto:
        return False
    return bool(set(_limpar(texto).split()) & PALAVRAS_NAO)


class EncerramentoLimpoDaSessao(Exception):
    """Sessão Live fechada pela API com close code 1000 (shutdown limpo).

    Sinaliza shutdown normal: o handler externo não faz failover de conta.
    Close 1001 (going-away) NÃO é limpo: propaga para o failover rotacionar.
    """


def desativar_ping_timeout(client) -> None:
    """Desativa o timeout de ping do WebSocket Live que derrubava conexões após ~45s de silêncio."""
    if hasattr(client, "_api_client") and hasattr(client._api_client, "_websocket_ssl_ctx"):
        client._api_client._websocket_ssl_ctx["ping_interval"] = None
        client._api_client._websocket_ssl_ctx["ping_timeout"] = None