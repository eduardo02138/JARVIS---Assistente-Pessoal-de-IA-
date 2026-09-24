"""Autenticação do backend: token, sessões emitidas e liberação de autoridade.

O token protege rotas de mutação e os WebSockets; a sessão emitida por
/api/auth/session é a identidade canônica das rotas de ação (nunca o
'usuario' enviado pelo cliente).
"""

import os
import secrets
import threading
import time
from typing import Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request, WebSocket

import system_tools
from monitoring.logger import logger, record_event
from policy_engine import policy_engine
from servidor.comum import eh_loopback

router = APIRouter()

JARVIS_SECRET_TOKEN = os.environ.get("JARVIS_TOKEN")
if not JARVIS_SECRET_TOKEN:
    JARVIS_SECRET_TOKEN = secrets.token_urlsafe(24)

# Sessões emitidas por /api/auth/session (identidade server-side).
SESSOES_EMITIDAS: Dict[str, float] = {}
_SESSOES_LOCK = threading.RLock()
SESSO_TTL_S = int(os.environ.get("JARVIS_SESSAO_TTL_S", "43200"))


def sessao_aceita(sessao: Optional[str]) -> bool:
    """Fail-closed: sessão explícita e qualquer exceto os nomes reservados.

    'sessao-principal' e 'default' nunca são aceitos como identidade client-claimed:
    obrigam o fluxo de confirmação a usar sessões reais (emitidas ou arbitrárias do
    frontend), impedindo que duas conexões colidam em uma sessão mágica global.
    """
    return bool(sessao) and sessao not in ("sessao-principal", "default")


def liberar_controle_da_sessao(session_id: str) -> None:
    """Encerra a autoridade física e do agente ao fim da sessão dona da lease.

    Sessões que não são donas da lease não mexem no Modo Controle ou IDE de quem é.
    """
    if policy_engine.control_lease_status().get("owner") == session_id:
        if system_tools.get_control_mode():
            system_tools.set_control_mode(False)
        lease = policy_engine.revoke_control_lease(session_id=session_id)
        record_event("control_lease_released", lease)

    if policy_engine.ide_lease_status().get("owner") == session_id:
        lease_ide = policy_engine.revoke_ide_lease(session_id=session_id)
        record_event("ide_lease_released", lease_ide)
    logger.info("Sessão encerrada: Modo Controle desativado e lease de controle revogada.")


async def verify_jarvis_token(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_jarvis_token: Optional[str] = Header(None),
    token: Optional[str] = Query(None)
):
    """Exige token de autorização para ações de mutação ou controle."""
    req_token = None
    if authorization and authorization.startswith("Bearer "):
        req_token = authorization.split("Bearer ")[1].strip()
    elif x_jarvis_token:
        req_token = x_jarvis_token.strip()
    elif token:
        req_token = token.strip()

    if req_token != JARVIS_SECRET_TOKEN:
        raise HTTPException(status_code=401, detail="Não autorizado: JARVIS_TOKEN inválido ou ausente.")
    return req_token


def registrar_rejeicao_de_token(websocket: WebSocket, rota: str, fonte: str, origem: str = "") -> None:
    """Registra um handshake WebSocket sem token válido.

    Rejeições provocadas pelos testes locais (origem=teste no loopback) viram INFO,
    para não gerar falso alarme no log de segurança.
    """
    client_host = websocket.client.host if websocket.client else ""
    origem_teste = origem == "teste" and eh_loopback(client_host)
    if origem_teste:
        logger.info("[TESTE] Rejeição de WebSocket %s sem token: comportamento esperado.", rota)
    else:
        logger.warning("Tentativa de conexão WebSocket %s não autorizada: token inválido ou ausente.", rota)
    record_event("auth_error", {
        "source": fonte,
        "reason": "invalid_or_missing_token",
        "origem": "teste" if origem_teste else "real",
    })


@router.get("/api/auth/session")
@router.get("/api/auth/token")
async def get_session_token(request: Request):
    """Permite apenas ao cliente local no loopback obter o token da sessão ativa.

    Emite também um sessao_id server-side: identidade canônica para as rotas de
    ação (nunca o 'usuario' client-claimed). O servidor ignora user_id recebido
    do cliente; user_id efetivo é sempre a sessão autorizada.
    """
    client_host = request.client.host if request.client else ""
    if not eh_loopback(client_host):
        raise HTTPException(status_code=403, detail="Acesso restrito ao localhost.")
    sessao_id = secrets.token_urlsafe(24)
    with _SESSOES_LOCK:
        SESSOES_EMITIDAS[sessao_id] = time.monotonic() + SESSO_TTL_S
    return {"status": "ok", "token": JARVIS_SECRET_TOKEN, "sessao_id": sessao_id}
