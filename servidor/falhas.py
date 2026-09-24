"""Classificação das falhas dos provedores de IA e a reação certa para cada uma.

Antes, o chat e as sessões Live procuravam "429"/"503" no texto do erro e reagiam
sempre igual: girar a chave do pool. Assim uma chave inválida (401/403) virava
erro 500 sem failover, um modelo sobrecarregado (503) queimava as chaves do pool
sem necessidade, e uma requisição inválida (400) trocava o modelo da sessão Live.

Cada falha agora tem um motivo e a ação que resolve:
- limite_de_uso (429, RESOURCE_EXHAUSTED, cota): girar a chave; sem outra, 2º provedor.
- autenticacao (401/403, chave inválida): girar a chave; sem outra, 2º provedor.
- modelo_indisponivel (404, modelo inexistente): modelo reserva, depois 2º provedor.
- instabilidade (500/502/503/529, sobrecarga, close 1011): modelo reserva, depois 2º provedor.
- tempo_esgotado e rede: 2º provedor.
- contexto_excedido e requisicao_invalida: nenhuma troca resolve; erro claro na hora.
"""

import asyncio
import enum
import socket
import urllib.error
from dataclasses import dataclass
from typing import Optional


class MotivoDeFalha(str, enum.Enum):
    LIMITE_DE_USO = "limite_de_uso"
    AUTENTICACAO = "autenticacao"
    MODELO_INDISPONIVEL = "modelo_indisponivel"
    INSTABILIDADE = "instabilidade"
    TEMPO_ESGOTADO = "tempo_esgotado"
    REDE = "rede"
    CONTEXTO_EXCEDIDO = "contexto_excedido"
    REQUISICAO_INVALIDA = "requisicao_invalida"
    DESCONHECIDO = "desconhecido"


_MENSAGENS = {
    MotivoDeFalha.LIMITE_DE_USO: "Limite de uso da API atingido",
    MotivoDeFalha.AUTENTICACAO: "Chave de API recusada pelo provedor",
    MotivoDeFalha.MODELO_INDISPONIVEL: "Modelo indisponível no provedor",
    MotivoDeFalha.INSTABILIDADE: "Provedor instável ou sobrecarregado",
    MotivoDeFalha.TEMPO_ESGOTADO: "O provedor demorou demais para responder",
    MotivoDeFalha.REDE: "Sem conexão com o provedor",
    MotivoDeFalha.CONTEXTO_EXCEDIDO: "A conversa ficou longa demais para o modelo; comece uma nova sessão",
    MotivoDeFalha.REQUISICAO_INVALIDA: "O provedor recusou a requisição",
    MotivoDeFalha.DESCONHECIDO: "Falha inesperada no provedor",
}


@dataclass(frozen=True)
class Falha:
    motivo: MotivoDeFalha
    codigo: Optional[int]
    detalhe: str

    @property
    def girar_chave(self) -> bool:
        """Outra chave do pool resolve (cota e autenticação são por chave)."""
        return self.motivo in (MotivoDeFalha.LIMITE_DE_USO, MotivoDeFalha.AUTENTICACAO)

    @property
    def trocar_modelo(self) -> bool:
        """O modelo reserva resolve (o problema é do modelo, não da chave)."""
        return self.motivo in (MotivoDeFalha.MODELO_INDISPONIVEL, MotivoDeFalha.INSTABILIDADE)

    @property
    def definitiva(self) -> bool:
        """Nenhuma troca de chave, modelo ou provedor resolve: repetir só gasta cota."""
        return self.motivo in (MotivoDeFalha.CONTEXTO_EXCEDIDO, MotivoDeFalha.REQUISICAO_INVALIDA)

    @property
    def usar_segundo_provedor(self) -> bool:
        """Falha do lado do provedor: o OmniRoute pode responder no lugar."""
        return not self.definitiva and self.motivo != MotivoDeFalha.DESCONHECIDO

    def mensagem(self) -> str:
        return f"{_MENSAGENS[self.motivo]} ({self.detalhe[:200]})"


_STATUS_POR_MOTIVO = {
    "RESOURCE_EXHAUSTED": MotivoDeFalha.LIMITE_DE_USO,
    "UNAUTHENTICATED": MotivoDeFalha.AUTENTICACAO,
    "PERMISSION_DENIED": MotivoDeFalha.AUTENTICACAO,
    "NOT_FOUND": MotivoDeFalha.MODELO_INDISPONIVEL,
    "UNAVAILABLE": MotivoDeFalha.INSTABILIDADE,
    "INTERNAL": MotivoDeFalha.INSTABILIDADE,
    "DEADLINE_EXCEEDED": MotivoDeFalha.TEMPO_ESGOTADO,
    "INVALID_ARGUMENT": MotivoDeFalha.REQUISICAO_INVALIDA,
    "FAILED_PRECONDITION": MotivoDeFalha.REQUISICAO_INVALIDA,
}


def _falha_raiz(exc: BaseException) -> BaseException:
    """Desembrulha ExceptionGroup (TaskGroup) até a primeira falha concreta."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def _codigo(exc: BaseException) -> Optional[int]:
    """Código HTTP (google-genai, urllib, httpx) ou de fechamento do WebSocket."""
    for candidato in (
        getattr(exc, "code", None),
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
        getattr(getattr(exc, "rcvd", None), "code", None),
    ):
        if isinstance(candidato, int) and not isinstance(candidato, bool):
            return candidato
    return None


def classificar_falha(exc: BaseException) -> Falha:
    exc = _falha_raiz(exc)
    codigo = _codigo(exc)
    status = str(getattr(exc, "status", "") or "").upper()
    texto = str(exc)
    minusculo = texto.lower()
    detalhe = f"{type(exc).__name__}: {texto}" if texto else type(exc).__name__

    def falha(motivo: MotivoDeFalha) -> Falha:
        return Falha(motivo, codigo, detalhe)

    # 1. Pistas do texto: valem para REST e para o close do WebSocket da Live API
    if "input token count" in minusculo or "context window" in minusculo or "too many tokens" in minusculo:
        return falha(MotivoDeFalha.CONTEXTO_EXCEDIDO)
    if "api key not valid" in minusculo or "api_key_invalid" in minusculo or "invalid api key" in minusculo:
        return falha(MotivoDeFalha.AUTENTICACAO)
    if "quota" in minusculo or "resource_exhausted" in minusculo or "rate limit" in minusculo:
        return falha(MotivoDeFalha.LIMITE_DE_USO)
    if "model" in minusculo and ("not found" in minusculo or "is not supported" in minusculo):
        return falha(MotivoDeFalha.MODELO_INDISPONIVEL)
    if "overloaded" in minusculo or "internal error encountered" in minusculo:
        return falha(MotivoDeFalha.INSTABILIDADE)

    # 2. Status canônico do Google (APIError.status)
    if status in _STATUS_POR_MOTIVO:
        return falha(_STATUS_POR_MOTIVO[status])

    # 3. Código HTTP ou de fechamento do WebSocket
    if codigo == 429:
        return falha(MotivoDeFalha.LIMITE_DE_USO)
    if codigo in (401, 403):
        return falha(MotivoDeFalha.AUTENTICACAO)
    if codigo == 404:
        return falha(MotivoDeFalha.MODELO_INDISPONIVEL)
    if codigo in (408, 504):
        return falha(MotivoDeFalha.TEMPO_ESGOTADO)
    if codigo in (500, 502, 503, 529, 1001, 1011, 1013):
        return falha(MotivoDeFalha.INSTABILIDADE)
    if codigo in (400, 413, 422, 1007):
        return falha(MotivoDeFalha.REQUISICAO_INVALIDA)

    # 4. Tipo da exceção (sem código HTTP: erro de transporte)
    nome = type(exc).__name__
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or "timeout" in nome.lower():
        return falha(MotivoDeFalha.TEMPO_ESGOTADO)
    if codigo is None and (
        isinstance(exc, (ConnectionError, socket.gaierror, urllib.error.URLError))
        or any(pista in nome for pista in ("Connect", "Network", "ProtocolError"))
    ):
        return falha(MotivoDeFalha.REDE)
    return falha(MotivoDeFalha.DESCONHECIDO)
