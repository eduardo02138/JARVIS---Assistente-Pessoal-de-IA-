"""
Módulo Unificado de Autenticação do J.A.R.V.I.S. (jarvis.core.auth)
Autoridade única de emissão, validação e proteção de tokens de sessão.
"""

import os
import secrets
from typing import Optional

from fastapi import Header, HTTPException, Query, Request

# Token secreto do assistente carregado de variável de ambiente ou gerado de forma criptográfica
JARVIS_SECRET_TOKEN = os.environ.get("JARVIS_TOKEN")
if not JARVIS_SECRET_TOKEN:
    JARVIS_SECRET_TOKEN = secrets.token_urlsafe(24)


def is_valid_token(token: Optional[str]) -> bool:
    """Valida o token recebido contra o token secreto usando comparação segura contra timing attacks."""
    if not token or not isinstance(token, str):
        return False
    return secrets.compare_digest(token.strip(), JARVIS_SECRET_TOKEN.strip())


def extract_token_from_headers_or_query(
    authorization: Optional[str] = None,
    x_jarvis_token: Optional[str] = None,
    token: Optional[str] = None
) -> Optional[str]:
    """Extrai o token de autenticação a partir dos cabeçalhos padrão ou parâmetros de consulta."""
    if authorization and authorization.startswith("Bearer "):
        return authorization.split("Bearer ", 1)[1].strip()
    if x_jarvis_token:
        return x_jarvis_token.strip()
    if token:
        return token.strip()
    return None


async def verify_jarvis_token(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_jarvis_token: Optional[str] = Header(None),
    token: Optional[str] = Query(None)
) -> str:
    """
    Dependência FastAPI canônica para validação de requisições autenticadas.
    Aceita Authorization: Bearer, X-Jarvis-Token e ?token=.
    """
    req_token = extract_token_from_headers_or_query(
        authorization=authorization,
        x_jarvis_token=x_jarvis_token,
        token=token
    )

    if not is_valid_token(req_token):
        raise HTTPException(
            status_code=401,
            detail="Não autorizado: JARVIS_TOKEN inválido ou ausente."
        )
    return req_token


def verify_jarvis_token_ws(token: Optional[str]) -> bool:
    """Valida token para conexões WebSocket."""
    return is_valid_token(token)
