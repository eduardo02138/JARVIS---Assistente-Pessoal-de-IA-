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
SESSOES_REVOGADAS: set[str] = set()
_SESSOES_LOCK = threading.RLock()
SESSO_TTL_S = int(os.environ.get("JARVIS_SESSAO_TTL_S", "43200"))
EXIGIR_SESSAO_EMITIDA: bool = os.environ.get("JARVIS_STRICT_SESSION_ISSUANCE", "1").strip().lower() not in (
    "0", "false", "nao", "no"
)
NOMES_SESSAO_RESERVADOS = frozenset({
    "sessao-principal",
    "default",
    "anonymous",
    "anonimo",
    "global",
    "all",
    "*",
})

# Pre-emissão da sessão padrão do widget local
with _SESSOES_LOCK:
    SESSOES_EMITIDAS["sessao-widget"] = time.monotonic() + SESSO_TTL_S


def emitir_sessao(sessao_id: Optional[str] = None, ttl_s: float = SESSO_TTL_S) -> str:
    """Emite e registra uma sessão server-side com TTL monotônico em SESSOES_EMITIDAS."""
    sid = (sessao_id or secrets.token_urlsafe(24)).strip()
    if not sid or sid in NOMES_SESSAO_RESERVADOS:
        raise ValueError("Identificador de sessão inválido ou reservado.")
    with _SESSOES_LOCK:
        SESSOES_REVOGADAS.discard(sid)
        SESSOES_EMITIDAS[sid] = time.monotonic() + float(ttl_s)
    return sid


def revogar_sessao(sessao: str) -> None:
    """Revoga imediatamente uma sessão emitida e libera eventuais leases associadas."""
    if not sessao:
        return
    with _SESSOES_LOCK:
        SESSOES_REVOGADAS.add(sessao)
        SESSOES_EMITIDAS.pop(sessao, None)
    liberar_controle_da_sessao(sessao)


def expirar_sessao(sessao: str) -> None:
    """Marca uma sessão em SESSOES_EMITIDAS como expirada monotonicamente."""
    if not sessao:
        return
    with _SESSOES_LOCK:
        SESSOES_EMITIDAS[sessao] = time.monotonic() - 1.0


def validar_sessao_emitida(sessao: Optional[str]) -> bool:
    """Valida estritamente se a sessão foi emitida pelo servidor, não foi revogada e está dentro do TTL."""
    if not sessao or not isinstance(sessao, str):
        return False
    sid = sessao.strip()
    if not sid or sid in NOMES_SESSAO_RESERVADOS:
        return False
    with _SESSOES_LOCK:
        if sid in SESSOES_REVOGADAS:
            return False
        expira_em = SESSOES_EMITIDAS.get(sid)
        if expira_em is None:
            return False
        if time.monotonic() > expira_em:
            return False
        return True


def sessao_aceita(sessao: Optional[str], exigir_emissao: Optional[bool] = None) -> bool:
    """Fail-closed: valida se a sessão foi emitida pelo servidor e está dentro do TTL.

    - Rejeita sessões vazias, reservadas ('sessao-principal', 'default', 'anonymous', etc.),
      revogadas ou expiradas.
    - Exige presença prévia e válida em SESSOES_EMITIDAS (emitir_sessao / /api/auth/session).
    - Não há autocadastro durante a validação.
    """
    return validar_sessao_emitida(sessao)


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
        # Sem a lease o Modo IDE não tem autoridade: a próxima sessão não pode herdá-lo "ligado"
        if system_tools.get_ide_mode():
            system_tools.set_ide_mode(False)
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
