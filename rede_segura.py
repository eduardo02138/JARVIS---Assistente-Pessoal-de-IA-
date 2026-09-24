"""Leitura de páginas pelas ferramentas sem alcançar a rede interna (proteção contra SSRF).

O modelo escolhe as URLs que read_web_page lê, e uma página maliciosa pode pedir que
ele leia outra. Sem filtro, isso alcançava a API local do JARVIS, o OmniRoute, o
painel do roteador ou o serviço de metadados da nuvem (169.254.169.254).

- O host é resolvido antes da conexão, e cada redirecionamento é validado de novo.
- Em conexão direta, o endereço realmente conectado também é conferido: um DNS que
  responde "público" na checagem e "interno" na conexão (DNS rebinding) é recusado.
  Via proxy, quem resolve e conecta é o proxy, e vale a política dele.
- Loopback, link-local (metadados de nuvem), multicast e faixas reservadas são sempre
  recusados. A rede local (10/8, 172.16/12, 192.168/16, 100.64/10, fc00::/7) só com
  JARVIS_WEB_PERMITIR_REDE_LOCAL=1.
- O corpo lido e o conteúdo descompactado têm teto de tamanho (sem estouro de memória
  com arquivos enormes ou bombas de gzip).
"""

import http.client
import ipaddress
import os
import socket
import urllib.parse
import urllib.request
import zlib
from email.message import Message
from typing import Mapping, NamedTuple, Optional

LIMITE_DE_BYTES = 2 * 1024 * 1024


class DestinoBloqueado(Exception):
    """A URL (ou um redirecionamento dela) aponta para a rede interna."""

    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


class PaginaLida(NamedTuple):
    corpo: bytes
    cabecalhos: Message
    url_final: str
    truncada: bool


def rede_local_permitida() -> bool:
    return os.environ.get("JARVIS_WEB_PERMITIR_REDE_LOCAL", "").strip().lower() in ("1", "true", "sim", "yes")


def motivo_de_bloqueio_do_ip(endereco: str) -> Optional[str]:
    """Por que um endereço não pode ser lido pelas ferramentas (None se for público)."""
    try:
        ip = ipaddress.ip_address(endereco.split("%", 1)[0])
    except ValueError:
        return f"endereço inválido ({endereco})"
    mapeado = getattr(ip, "ipv4_mapped", None)  # ::ffff:127.0.0.1 é o próprio loopback
    if mapeado is not None:
        ip = mapeado
    if ip.is_loopback or ip.is_unspecified:
        return "loopback (serviços desta máquina)"
    if ip.is_link_local:
        return "link-local (inclui o serviço de metadados da nuvem)"
    if ip.is_multicast or ip.is_reserved:
        return "endereço não roteável"
    if not ip.is_global and not rede_local_permitida():
        return "rede local (defina JARVIS_WEB_PERMITIR_REDE_LOCAL=1 para permitir)"
    return None


def motivo_de_bloqueio_do_host(host: str, porta: Optional[int] = None) -> Optional[str]:
    """Resolve o host e recusa se qualquer endereço dele for interno.

    Host que não resolve aqui não é recusado: sem proxy a conexão falha sozinha, e
    com proxy é ele quem resolve (e aplica a própria política).
    """
    if not host:
        return "URL sem host"
    try:
        infos = socket.getaddrinfo(host, porta or 443, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        return None
    for info in infos:
        motivo = motivo_de_bloqueio_do_ip(info[4][0])
        if motivo:
            return f"{host} aponta para {motivo}"
    return None


def motivo_de_bloqueio_da_url(url: str) -> Optional[str]:
    try:
        partes = urllib.parse.urlsplit(url)
        porta = partes.port
    except ValueError:
        return "URL inválida"
    if partes.scheme not in ("http", "https"):
        return f"esquema não permitido ({partes.scheme or 'nenhum'})"
    return motivo_de_bloqueio_do_host(partes.hostname or "", porta)


def _conferir_endereco_conectado(conexao: http.client.HTTPConnection) -> None:
    try:
        endereco = conexao.sock.getpeername()[0]
    except (OSError, AttributeError):
        return
    motivo = motivo_de_bloqueio_do_ip(endereco)
    if motivo:
        conexao.close()
        raise DestinoBloqueado(f"{conexao.host} conectou em {motivo}")


class _ConexaoHTTPConferida(http.client.HTTPConnection):
    def connect(self):
        super().connect()
        _conferir_endereco_conectado(self)


class _ConexaoHTTPSConferida(http.client.HTTPSConnection):
    def connect(self):
        super().connect()
        _conferir_endereco_conectado(self)


class _HandlerHTTP(urllib.request.HTTPHandler):
    def http_open(self, req):
        # Via proxy HTTP o par da conexão é o proxy, não o destino
        classe = http.client.HTTPConnection if req.has_proxy() else _ConexaoHTTPConferida
        return self.do_open(classe, req)


class _HandlerHTTPS(urllib.request.HTTPSHandler):
    def https_open(self, req):
        classe = http.client.HTTPSConnection if getattr(req, "_tunnel_host", None) else _ConexaoHTTPSConferida
        return self.do_open(classe, req, context=self._context)


class _RedirecionamentoConferido(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        motivo = motivo_de_bloqueio_da_url(urllib.parse.urljoin(req.full_url, newurl))
        if motivo:
            fp.close()
            raise DestinoBloqueado(f"redirecionamento recusado: {motivo}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _descompactar_gzip(corpo: bytes, limite: int) -> tuple:
    descompactador = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        dados = descompactador.decompress(corpo, limite + 1)
    except zlib.error:
        return corpo, False
    return dados[:limite], len(dados) > limite


def ler_url_publica(url: str, timeout: float = 8.0, limite_bytes: int = LIMITE_DE_BYTES,
                    cabecalhos: Optional[Mapping[str, str]] = None) -> PaginaLida:
    """GET de uma URL pública, com redirecionamentos conferidos e corpo limitado."""
    motivo = motivo_de_bloqueio_da_url(url)
    if motivo:
        raise DestinoBloqueado(motivo)
    abridor = urllib.request.build_opener(_HandlerHTTP(), _HandlerHTTPS(), _RedirecionamentoConferido())
    requisicao = urllib.request.Request(url, headers=dict(cabecalhos or {}))
    with abridor.open(requisicao, timeout=timeout) as resposta:
        corpo = resposta.read(limite_bytes + 1)
        truncada = len(corpo) > limite_bytes
        corpo = corpo[:limite_bytes]
        info = resposta.headers
        url_final = resposta.geturl()
    if (info.get("Content-Encoding") or "").lower() == "gzip" or corpo[:2] == b"\x1f\x8b":
        corpo, estourou = _descompactar_gzip(corpo, limite_bytes)
        truncada = truncada or estourou
    return PaginaLida(corpo, info, url_final, truncada)
