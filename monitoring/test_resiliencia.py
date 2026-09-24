"""Resiliência do backend: classificação de falhas do provedor e fila por sessão no chat.

As falhas do Gemini são construídas com as mesmas classes do google-genai que o ADK
propaga, e o /api/chat roda de verdade (FastAPI + sessões ADK em memória); só o
runner é substituído por um roteiro que falha como o provedor falharia.
"""

import asyncio
import urllib.error
from unittest.mock import AsyncMock

import httpx
import pytest
from google.genai import errors as genai_errors
from google.genai import types

import server
from agentes.assistente import MODELO_TEXTO_RESERVA
from servidor.falhas import MotivoDeFalha, classificar_falha
from servidor.seguranca import JARVIS_SECRET_TOKEN

CABECALHOS = {"X-Jarvis-Token": JARVIS_SECRET_TOKEN}


def _erro_google(codigo, status, mensagem):
    classe = genai_errors.ServerError if codigo >= 500 else genai_errors.ClientError
    return classe(codigo, {"error": {"code": codigo, "status": status, "message": mensagem}})


@pytest.mark.parametrize("erro, motivo", [
    (_erro_google(429, "RESOURCE_EXHAUSTED", "Resource has been exhausted (e.g. check quota)."), MotivoDeFalha.LIMITE_DE_USO),
    (_erro_google(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key."), MotivoDeFalha.AUTENTICACAO),
    (_erro_google(403, "PERMISSION_DENIED", "Method doesn't allow unregistered callers."), MotivoDeFalha.AUTENTICACAO),
    (_erro_google(404, "NOT_FOUND", "models/gemini-9 is not found for API version v1beta."), MotivoDeFalha.MODELO_INDISPONIVEL),
    (_erro_google(503, "UNAVAILABLE", "The model is overloaded. Please try again later."), MotivoDeFalha.INSTABILIDADE),
    (_erro_google(1011, None, "Internal error encountered."), MotivoDeFalha.INSTABILIDADE),
    (_erro_google(400, "INVALID_ARGUMENT",
                  "The input token count (2000000) exceeds the maximum number of tokens allowed (1048576)."),
     MotivoDeFalha.CONTEXTO_EXCEDIDO),
    (_erro_google(400, "INVALID_ARGUMENT", "Invalid JSON payload received."), MotivoDeFalha.REQUISICAO_INVALIDA),
    (asyncio.TimeoutError(), MotivoDeFalha.TEMPO_ESGOTADO),
    (ConnectionRefusedError(111, "Connection refused"), MotivoDeFalha.REDE),
    (urllib.error.HTTPError("http://omniroute", 503, "Service Unavailable", {}, None), MotivoDeFalha.INSTABILIDADE),
    (ExceptionGroup("taskgroup", [_erro_google(429, "RESOURCE_EXHAUSTED", "quota")]), MotivoDeFalha.LIMITE_DE_USO),
    (ValueError("estado interno inesperado"), MotivoDeFalha.DESCONHECIDO),
])
def test_classifica_falhas_reais_do_provedor(erro, motivo):
    assert classificar_falha(erro).motivo is motivo


def test_cada_motivo_tem_a_reacao_certa():
    cota = classificar_falha(_erro_google(429, "RESOURCE_EXHAUSTED", "quota"))
    assert cota.girar_chave and not cota.trocar_modelo and cota.usar_segundo_provedor
    sobrecarga = classificar_falha(_erro_google(503, "UNAVAILABLE", "overloaded"))
    assert not sobrecarga.girar_chave and sobrecarga.trocar_modelo and sobrecarga.usar_segundo_provedor
    invalida = classificar_falha(_erro_google(400, "INVALID_ARGUMENT", "Invalid JSON payload received."))
    assert invalida.definitiva and not invalida.girar_chave and not invalida.usar_segundo_provedor


# ----------------- /api/chat com o provedor falhando -----------------

class _RunnerRoteirizado:
    """Runner falso: levanta a falha da vez ou responde como o agente."""

    def __init__(self, modelo, falhas, chamadas):
        self.agent = type("Agente", (), {"model": modelo})()
        self._falhas = falhas
        self._chamadas = chamadas

    async def run_async(self, **kwargs):
        self._chamadas.append(self.agent.model)
        if self._falhas:
            raise self._falhas.pop(0)
        yield type("Evento", (), {
            "content": types.Content(role="model", parts=[types.Part(text="Pronto, senhor.")]),
            "is_final_response": lambda self: True,
        })()


@pytest.fixture
def chat(monkeypatch):
    """Cliente do /api/chat com runner, rotação de chave e OmniRoute controlados pelo teste."""
    estado = {"falhas": [], "chamadas": [], "giros": 0, "chaves_extras": 0}

    def obter_runner(tipo, modelo=None):
        return _RunnerRoteirizado(modelo or "gemini-flash-latest", estado["falhas"], estado["chamadas"])

    def girar():
        estado["giros"] += 1
        if estado["chaves_extras"] > 0:
            estado["chaves_extras"] -= 1
            return True
        return False

    omniroute = AsyncMock(return_value="Resposta do OmniRoute")
    monkeypatch.setattr("servidor.rotas_agente.obter_runner_adk", obter_runner)
    monkeypatch.setattr("servidor.rotas_agente.girar_chave_adk", girar)
    monkeypatch.setattr("servidor.rotas_agente.chamar_omniroute_chat", omniroute)
    monkeypatch.setattr("servidor.rotas_agente.agendar_tarefa_do_servidor", lambda coro: coro.close())
    estado["omniroute"] = omniroute

    def enviar(texto="qual o status do sistema?", sessao="sessao-resiliencia"):
        return server_client().post("/api/chat", json={"texto": texto, "sessao": sessao}, headers=CABECALHOS)

    estado["enviar"] = enviar
    return estado


def server_client():
    from fastapi.testclient import TestClient
    return TestClient(server.app)


def test_modelo_sobrecarregado_usa_o_modelo_reserva_sem_queimar_chaves(chat):
    chat["falhas"].append(_erro_google(503, "UNAVAILABLE", "The model is overloaded."))
    resposta = chat["enviar"]()
    assert resposta.status_code == 200 and resposta.json()["provedor"] == "google_studio"
    assert chat["chamadas"][-1] == MODELO_TEXTO_RESERVA
    assert chat["giros"] == 0, "503 é do modelo: girar a chave não resolve"


def test_cota_gira_a_chave_e_tenta_de_novo(chat):
    chat["chaves_extras"] = 1
    chat["falhas"].append(_erro_google(429, "RESOURCE_EXHAUSTED", "quota exceeded"))
    resposta = chat["enviar"]()
    assert resposta.status_code == 200 and resposta.json()["provedor"] == "google_studio"
    assert chat["giros"] == 1 and len(chat["chamadas"]) == 2


def test_chave_invalida_sem_outra_no_pool_aciona_o_segundo_provedor(chat):
    chat["falhas"].append(_erro_google(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key."))
    resposta = chat["enviar"]()
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["provedor"] == "omniroute" and "autenticacao" in corpo["motivo_do_roteamento"]
    assert chat["omniroute"].await_count == 1


def test_requisicao_invalida_falha_na_hora_sem_trocar_nada(chat):
    chat["falhas"].append(_erro_google(400, "INVALID_ARGUMENT", "Invalid JSON payload received."))
    resposta = chat["enviar"]()
    assert resposta.status_code == 500
    assert resposta.json()["motivo_da_falha"] == "requisicao_invalida"
    assert chat["giros"] == 0 and chat["omniroute"].await_count == 0 and len(chat["chamadas"]) == 1


# ----------------- Fila por sessão -----------------

def test_turnos_da_mesma_sessao_rodam_em_fila_e_sessoes_diferentes_em_paralelo(monkeypatch):
    ativos = {"agora": 0, "maximo": 0}

    class RunnerLento:
        agent = type("Agente", (), {"model": "gemini-flash-latest"})()

        async def run_async(self, **kwargs):
            ativos["agora"] += 1
            ativos["maximo"] = max(ativos["maximo"], ativos["agora"])
            await asyncio.sleep(0.2)
            ativos["agora"] -= 1
            yield type("Evento", (), {
                "content": types.Content(role="model", parts=[types.Part(text="ok")]),
                "is_final_response": lambda self: True,
            })()

    monkeypatch.setattr("servidor.rotas_agente.obter_runner_adk", lambda tipo, modelo=None: RunnerLento())
    monkeypatch.setattr("servidor.rotas_agente.agendar_tarefa_do_servidor", lambda coro: coro.close())

    async def disparar(sessoes):
        transporte = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transporte, base_url="http://testserver") as cliente:
            respostas = await asyncio.gather(*[
                cliente.post("/api/chat", json={"texto": "oi", "sessao": s}, headers=CABECALHOS) for s in sessoes
            ])
        assert all(r.status_code == 200 for r in respostas)

    asyncio.run(disparar(["sessao-fila", "sessao-fila", "sessao-fila"]))
    assert ativos["maximo"] == 1, "dois turnos da mesma sessão intercalaram eventos"

    ativos["maximo"] = 0
    asyncio.run(disparar(["sessao-a", "sessao-b"]))
    assert ativos["maximo"] == 2, "sessões diferentes não podem esperar umas pelas outras"


# ----------------- /ws/live: laço de contas do pool -----------------

import time
from types import SimpleNamespace

from fastapi.testclient import TestClient


class _ConexaoRecusada:
    def __init__(self, erro):
        self._erro = erro

    async def __aenter__(self):
        raise self._erro

    async def __aexit__(self, *exc):
        return False


def _conectar_live_com_falha(monkeypatch, erro):
    """Abre /ws/live com o pool de 3 chaves recusando a conexão; devolve tentativas, mensagens e tempo."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEYS", "chave-a,chave-b,chave-c")
    tentativas = []

    def cliente_falso(*args, **kwargs):
        tentativas.append(kwargs.get("api_key"))
        conectar = lambda model=None, config=None: _ConexaoRecusada(erro)  # noqa: E731
        return SimpleNamespace(_api_client=SimpleNamespace(_websocket_ssl_ctx={}),
                               aio=SimpleNamespace(live=SimpleNamespace(connect=conectar)))

    monkeypatch.setattr(server.genai, "Client", cliente_falso)
    mensagens = []
    inicio = time.monotonic()
    with TestClient(server.app) as cliente:
        with cliente.websocket_connect("/ws/live") as ws:
            ws.send_json({"type": "init", "token": JARVIS_SECRET_TOKEN, "voice": "Charon"})
            while not mensagens or mensagens[-1].get("type") != "error":
                mensagens.append(ws.receive_json())
    return tentativas, mensagens, time.monotonic() - inicio


def test_live_nativo_nao_insiste_quando_a_conexao_e_invalida(monkeypatch):
    erro = _erro_google(400, "INVALID_ARGUMENT", "Invalid value at 'setup.generation_config'.")
    tentativas, mensagens, _ = _conectar_live_com_falha(monkeypatch, erro)
    assert len(tentativas) == 1, "as outras contas recusariam a mesma configuração"
    assert "recusada" in mensagens[-1]["message"]


def test_live_nativo_gira_as_contas_sem_espera_quando_a_cota_acaba(monkeypatch):
    erro = _erro_google(429, "RESOURCE_EXHAUSTED", "Resource has been exhausted (e.g. check quota).")
    tentativas, mensagens, duracao = _conectar_live_com_falha(monkeypatch, erro)
    assert tentativas == ["chave-a", "chave-b", "chave-c"]
    assert duracao < 3, f"cota é da conta: a próxima entra sem backoff ({duracao:.1f}s)"
    assert "Todas as contas do pool falharam" in mensagens[-1]["message"]
