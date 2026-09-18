"""
Motor Central de Confirmação do J.A.R.V.I.S. (jarvis.core.confirmation)
Autoridade única de heurística de intenção verbal e processamento de aprovações one-shot.
"""

import logging
from typing import Any, Dict, Optional, Tuple

from policy_engine import policy_engine

logger = logging.getLogger("jarvis.confirmation")

PALAVRAS_CONFIRMACAO = {
    "sim", "confirmar", "confirmado", "autorizar", "autorizado",
    "pode", "ok", "prosseguir", "positivo", "permitir",
    "executa", "pode fazer", "com certeza", "manda ver"
}

PALAVRAS_NEGACAO = {
    "nao", "não", "negar", "negado", "cancelar", "cancela",
    "recusar", "recuso"
}


def parse_verbal_intent(texto: str) -> Tuple[bool, bool]:
    """
    Analisa a presença de palavras de confirmação e negação no texto do usuário.
    Retorna (is_confirmado, is_negado).
    """
    if not texto or not isinstance(texto, str):
        return False, False

    texto_limpo = "".join(c for c in texto.lower() if c.isalnum() or c.isspace()).strip()
    tokens = set(texto_limpo.split())

    is_negado = bool(tokens & PALAVRAS_NEGACAO)
    is_confirmado = (texto_limpo in PALAVRAS_CONFIRMACAO) or (bool(tokens & PALAVRAS_CONFIRMACAO) and not is_negado)
    return is_confirmado, is_negado


def check_and_approve_verbal(
    texto: str,
    session_id: str,
    user_id: str = "local"
) -> Tuple[bool, Optional[Any], str]:
    """
    Verifica se a fala do usuário é uma confirmação verbal de ação pendente.
    Se confirmada, aprova a última ação pendente no PolicyEngine e reformula
    o prompt para execução imediata pelo modelo.
    """
    is_confirmado, _ = parse_verbal_intent(texto)
    if is_confirmado:
        pending = policy_engine.approve_latest_pending(session_id=session_id, user_id=user_id)
        if pending:
            logger.info("Usuário confirmou verbalmente a ação pendente: %s (%s)", pending.action_id, pending.tool_name)
            texto_ajustado = f"O usuário confirmou expressamente a execução da ação '{pending.tool_name}'. Execute-a agora."
            return True, pending, texto_ajustado
    return False, None, texto


def handle_confirmar_acao_payload(payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """
    Processa a requisição do endpoint /api/confirmar_acao com isolamento estrito de sessão.
    Retorna (status_code, response_dict).
    """
    action_id = payload.get("id_confirmacao") or payload.get("action_id") or payload.get("id")
    aprovado = payload.get("aprovado", True)
    session_id = payload.get("sessao") or payload.get("session_id")
    user_id = payload.get("usuario") or payload.get("user_id") or "local"

    if not session_id:
        return 400, {"status": "erro", "mensagem": "Parâmetro 'sessao' é obrigatório para confirmar ações."}

    if not action_id:
        pending = policy_engine.approve_latest_pending(session_id=session_id, user_id=user_id)
        if pending:
            return 200, {"status": "ok", "action_id": pending.action_id, "tool_name": pending.tool_name}
        return 404, {"status": "erro", "mensagem": "Nenhuma ação pendente encontrada para esta sessão/usuário"}

    if aprovado:
        sucesso = policy_engine.approve_action(action_id, session_id=session_id, user_id=user_id)
        if sucesso:
            return 200, {"status": "ok", "action_id": action_id}
        return 400, {"status": "erro", "mensagem": "Ação não encontrada, expirada ou sessão/usuário divergente"}
    else:
        policy_engine.reject_action(action_id, session_id=session_id, user_id=user_id)
        return 200, {"status": "rejeitado", "action_id": action_id}
