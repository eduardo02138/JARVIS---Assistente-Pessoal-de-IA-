"""Protocolo compartilhado da sessão Gemini Live do J.A.R.V.I.S.

Centraliza lógica duplicada entre server.py e provider_router.py:
1. Interpretação de palavras de confirmação/recusa ditas ou digitadas pelo usuário.
2. Carga do pool de chaves Gemini (delega ao GoogleStudioProvider canônico).
3. Desativação do timeout de ping do WebSocket Live (evita queda após silêncio).
4. Encerramento limpo da sessão Live: close codes 1000/1001 são shutdown, não erro.

server.py e servidor_adk.py importam daqui: sem drift.
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
    "ok", "prosseguir", "positivo", "permitir", "yes", "conceder", "fazer teste",
}
PALAVRAS_NAO = {
    "nao", "não", "negar", "negado", "cancelar", "cancela",
    "recusar", "recuso", "no",
}


def _limpar(texto: str) -> str:
    return "".join(c for c in texto.lower() if c.isalnum() or c.isspace()).strip()


def palavra_confirma(texto: str) -> bool:
    """True se o texto confirma expressamente uma ação pendente.

    Confirmação exige palavra de PALAVRAS_SIM sem nenhuma palavra de recusa:
    "sim, pode autorizar" confirma; "sim, cancelar" não confirma.
    """
    if not texto:
        return False
    texto_limpo = _limpar(texto)
    if not texto_limpo:
        return False
    tokens = set(texto_limpo.split())
    negado = bool(tokens & PALAVRAS_NAO)
    return (texto_limpo in PALAVRAS_SIM) or (bool(tokens & PALAVRAS_SIM) and not negado)


def palavra_recusa(texto: str) -> bool:
    """True se o texto contém palavra de negação ou cancelamento."""
    if not texto:
        return False
    return bool(set(_limpar(texto).split()) & PALAVRAS_NAO)


class EncerramentoLimpoDaSessao(Exception):
    """Sessão Live fechada pela API com close code limpo (1000/1001).

    Sinaliza shutdown normal: o handler externo não faz failover de conta.
    """


def desativar_ping_timeout(client) -> None:
    """Desativa o timeout de ping do WebSocket Live que derrubava conexões após ~45s de silêncio."""
    if hasattr(client, "_api_client") and hasattr(client._api_client, "_websocket_ssl_ctx"):
        client._api_client._websocket_ssl_ctx["ping_interval"] = None
        client._api_client._websocket_ssl_ctx["ping_timeout"] = None