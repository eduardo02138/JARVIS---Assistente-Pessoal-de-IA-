"""Segurança de execução: processos filhos, leitura de páginas e resultados de ferramentas.

Cobre três camadas inspiradas no endurecimento do OpenClaw:
  - processos abertos pelo JARVIS não herdam JARVIS_TOKEN nem as chaves do Gemini;
  - read_web_page não alcança loopback, rede local nem metadados de nuvem;
  - resultados de ferramentas chegam ao modelo serializáveis e com tamanho limitado.
"""

import json
import os
import stat
import sys
import textwrap

import pytest

import gemini_bridge
import processos
import system_tools


@pytest.fixture
def segredos(tmp_path, monkeypatch):
    """Ambiente com os segredos do JARVIS, um .env de projeto e variáveis da sessão do usuário."""
    arquivo_env = tmp_path / ".env"
    arquivo_env.write_text(
        "DISCORD_BOT_TOKEN=token-do-plugin\n"
        "SESSION_DB_URL=postgresql://jarvis:senha@localhost/jarvis\n"
        "JARVIS_HOST=127.0.0.1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(processos, "ARQUIVO_ENV", str(arquivo_env))
    valores = {
        "JARVIS_TOKEN": "segredo-jarvis",
        "JARVIS_SECRET_TOKEN": "segredo-jarvis-2",
        "GEMINI_API_KEY": "chave-gemini",
        "GEMINI_API_KEYS": "chave-a,chave-b",
        "GOOGLE_API_KEY": "chave-google",
        "OMNIROUTE_API_KEY": "chave-omniroute",
        "DISCORD_BOT_TOKEN": "token-do-plugin",
        "SESSION_DB_URL": "postgresql://jarvis:senha@localhost/jarvis",
        "JARVIS_HOST": "127.0.0.1",
        "GH_TOKEN": "token-do-proprio-usuario",
        "DISPLAY": ":0",
    }
    for nome, valor in valores.items():
        monkeypatch.setenv(nome, valor)
    monkeypatch.delenv("JARVIS_ENV_REPASSAR", raising=False)
    return valores


SEGREDOS_DO_JARVIS = (
    "JARVIS_TOKEN", "JARVIS_SECRET_TOKEN", "GEMINI_API_KEY", "GEMINI_API_KEYS",
    "GOOGLE_API_KEY", "OMNIROUTE_API_KEY", "DISCORD_BOT_TOKEN", "SESSION_DB_URL",
)


def test_ambiente_sem_segredos_remove_credenciais_do_jarvis(segredos):
    env = processos.ambiente_sem_segredos()
    for nome in SEGREDOS_DO_JARVIS:
        assert nome not in env, f"{nome} vazaria para os processos filhos"
    assert env["DISPLAY"] == ":0" and "PATH" in env, "a sessão gráfica continua disponível"
    assert env["JARVIS_HOST"] == "127.0.0.1", "configuração sem cara de segredo não é removida"
    assert env["GH_TOKEN"] == "token-do-proprio-usuario", "token exportado pelo usuário não pertence ao JARVIS"


def test_repassar_libera_variavel_mas_nunca_o_token_da_api_local(segredos, monkeypatch):
    monkeypatch.setenv("JARVIS_ENV_REPASSAR", "GEMINI_API_KEY, JARVIS_TOKEN")
    env = processos.ambiente_sem_segredos()
    assert env["GEMINI_API_KEY"] == "chave-gemini"
    assert "JARVIS_TOKEN" not in env, "o token da API local nunca é repassado"


def test_processo_real_nao_enxerga_os_segredos(segredos, tmp_path):
    saida = tmp_path / "env.json"
    codigo = f"import json, os; json.dump(dict(os.environ), open({str(saida)!r}, 'w'))"
    processos.abrir_desanexado([sys.executable, "-c", codigo]).wait(timeout=10)
    env_filho = json.loads(saida.read_text(encoding="utf-8"))
    for nome in SEGREDOS_DO_JARVIS:
        assert nome not in env_filho
    assert env_filho["DISPLAY"] == ":0"


def test_ferramentas_de_abertura_usam_o_ambiente_sem_segredos(segredos, monkeypatch):
    chamadas = []

    class PopenFalso:
        def __init__(self, argv, **kwargs):
            chamadas.append((argv, kwargs))

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(processos.subprocess, "Popen", PopenFalso)
    assert system_tools.search_web("previsão do tempo")["sucesso"] is True
    assert system_tools.open_website("example.com")["sucesso"] is True
    assert len(chamadas) == 2
    for argv, kwargs in chamadas:
        assert argv[0] == "xdg-open"
        assert "JARVIS_TOKEN" not in kwargs["env"] and "GEMINI_API_KEY" not in kwargs["env"]
        assert kwargs["start_new_session"] is True and kwargs["stdin"] is not None


def test_agente_antigravity_nao_recebe_o_token(segredos, tmp_path, monkeypatch):
    agy = tmp_path / "agy"
    agy.write_text(textwrap.dedent("""\
        #!/bin/sh
        echo "token=[$JARVIS_TOKEN] gemini=[$GEMINI_API_KEY] display=[$DISPLAY]"
    """), encoding="utf-8")
    agy.chmod(agy.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(system_tools, "AGY_BIN", str(agy))
    monkeypatch.setattr(system_tools, "WORKSPACE_DIR", str(tmp_path))
    for nome in ("AUDIT_JSONL", "COMMANDS_LOG", "LATEST_RESPONSE_MD"):
        monkeypatch.setattr(gemini_bridge, nome, str(tmp_path / nome.lower()))

    res = system_tools.antigravity_run_prompt("liste os arquivos", continue_session=False)
    assert res["sucesso"] is True
    assert "token=[]" in res["resposta_completa"] and "gemini=[]" in res["resposta_completa"]
    assert "display=[:0]" in res["resposta_completa"]

    monkeypatch.setenv("JARVIS_ENV_REPASSAR", "GEMINI_API_KEY")
    res = system_tools.antigravity_run_prompt("liste os arquivos", continue_session=False)
    assert "gemini=[chave-gemini]" in res["resposta_completa"], "CLI que usa chave própria pode recebê-la por opção"
    assert "token=[]" in res["resposta_completa"]


def test_nenhum_lancamento_direto_de_subprocesso_nas_ferramentas():
    """Aplicativos, navegador e IDE só abrem pelo helper que remove os segredos."""
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for relativo in ("system_tools.py", "gemini_bridge.py", "plugins/game_companion/plugin.py"):
        with open(os.path.join(raiz, relativo), encoding="utf-8") as arquivo:
            fonte = arquivo.read()
        assert "subprocess.Popen(" not in fonte, f"{relativo} abre processo herdando os segredos"


# ----------------- read_web_page: proteção contra SSRF -----------------

import gzip
import http.server
import threading

import rede_segura


@pytest.mark.parametrize("endereco", [
    "http://127.0.0.1:8000/api/status",
    "localhost:8000",
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5/",
    "http://192.168.0.1/admin",
    "http://100.64.0.1/",
    "http://0.0.0.0:20128/v1/models",
    "http://2130706433/",
    "http://[fe80::1]/",
    "http://224.0.0.1/",
])
def test_read_web_page_recusa_destinos_internos(endereco, monkeypatch):
    monkeypatch.delenv("JARVIS_WEB_PERMITIR_REDE_LOCAL", raising=False)
    res = system_tools.read_web_page(endereco)
    assert res["sucesso"] is False and res.get("bloqueado") is True, res


def test_rede_local_so_com_opt_in_e_nunca_loopback_ou_metadados(monkeypatch):
    monkeypatch.delenv("JARVIS_WEB_PERMITIR_REDE_LOCAL", raising=False)
    assert rede_segura.motivo_de_bloqueio_do_ip("192.168.0.10")
    assert rede_segura.motivo_de_bloqueio_do_ip("8.8.8.8") is None
    monkeypatch.setenv("JARVIS_WEB_PERMITIR_REDE_LOCAL", "1")
    assert rede_segura.motivo_de_bloqueio_do_ip("192.168.0.10") is None
    assert rede_segura.motivo_de_bloqueio_do_ip("127.0.0.1")
    assert rede_segura.motivo_de_bloqueio_do_ip("169.254.169.254")
    assert rede_segura.motivo_de_bloqueio_da_url("file:///etc/passwd")


BOMBA_GZIP = gzip.compress(b"\0" * (40 * 1024 * 1024))


class _Pagina(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _responder(self, corpo, tipo="text/html; charset=utf-8", extras=()):
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        for nome, valor in extras:
            self.send_header(nome, valor)
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        if self.path == "/artigo":
            self._responder("<html><head><title>Notícia</title></head><body><p>Texto público.</p>"
                            "<script>segredo()</script></body></html>".encode("utf-8"))
        elif self.path == "/redireciona":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()
        elif self.path == "/bomba":
            self._responder(BOMBA_GZIP, extras=[("Content-Encoding", "gzip")])
        elif self.path == "/enorme":
            self._responder(b"<p>" + b"a" * (5 * 1024 * 1024) + b"</p>")
        elif self.path == "/documento.pdf":
            self._responder(b"%PDF-1.7 ...", tipo="application/pdf")
        else:
            self.send_response(404)
            self.end_headers()


class _ServidorSilencioso(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass  # o leitor para no teto de bytes e fecha a conexão: reset esperado


@pytest.fixture
def servidor_web(monkeypatch):
    """Servidor HTTP local, alcançado sem proxy."""
    for var in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")
    servidor = _ServidorSilencioso(("127.0.0.1", 0), _Pagina)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{servidor.server_address[1]}"
    servidor.shutdown()
    servidor.server_close()


@pytest.fixture
def loopback_liberado_para_o_teste(monkeypatch):
    """Trata 127.0.0.1 como público para exercitar o caminho feliz; o resto segue bloqueado."""
    original = rede_segura.motivo_de_bloqueio_do_ip
    monkeypatch.setattr(rede_segura, "motivo_de_bloqueio_do_ip",
                        lambda endereco: None if endereco == "127.0.0.1" else original(endereco))


def test_read_web_page_le_pagina_publica(servidor_web, loopback_liberado_para_o_teste):
    res = system_tools.read_web_page(f"{servidor_web}/artigo")
    assert res["sucesso"] is True, res
    assert res["titulo"] == "Notícia" and "Texto público." in res["conteudo"]
    assert "segredo" not in res["conteudo"]


def test_redirecionamento_para_metadados_e_recusado(servidor_web, loopback_liberado_para_o_teste):
    res = system_tools.read_web_page(f"{servidor_web}/redireciona")
    assert res["sucesso"] is False and res["bloqueado"] is True
    assert "redirecionamento" in res["mensagem"]


def test_dns_rebinding_e_barrado_na_conexao(servidor_web, monkeypatch):
    # A checagem de DNS foi enganada ("público"), mas a conexão real caiu no loopback
    monkeypatch.setattr(rede_segura, "motivo_de_bloqueio_do_host", lambda host, porta=None: None)
    res = system_tools.read_web_page(f"{servidor_web}/artigo")
    assert res["sucesso"] is False and res["bloqueado"] is True
    assert "loopback" in res["mensagem"]


def test_download_e_gzip_tem_teto(servidor_web, loopback_liberado_para_o_teste):
    bomba = rede_segura.ler_url_publica(f"{servidor_web}/bomba")
    assert bomba.truncada is True and len(bomba.corpo) <= rede_segura.LIMITE_DE_BYTES
    enorme = rede_segura.ler_url_publica(f"{servidor_web}/enorme")
    assert enorme.truncada is True and len(enorme.corpo) == rede_segura.LIMITE_DE_BYTES
    assert system_tools.read_web_page(f"{servidor_web}/enorme")["sucesso"] is True


def test_conteudo_nao_textual_e_recusado(servidor_web, loopback_liberado_para_o_teste):
    res = system_tools.read_web_page(f"{servidor_web}/documento.pdf")
    assert res["sucesso"] is False and "application/pdf" in res["mensagem"]


# ----------------- Resultados de ferramentas: serializáveis e limitados -----------------

import asyncio
import dataclasses
import datetime
import enum
import pathlib

from google.adk.agents import Agent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types

import resultados_de_ferramentas as rf
from agentes.assistente import CALLBACKS_POS_FERRAMENTA, criar_agente_rapido, limitar_resultado_da_ferramenta


class _Cor(enum.Enum):
    AZUL = "azul"


@dataclasses.dataclass
class _Ponto:
    x: int
    y: int


def test_resultado_pequeno_volta_intacto():
    original = {"sucesso": True, "mensagem": "Volume em 40%.", "nivel": 40}
    assert rf.limitar_resultado(original) == original


def test_tipos_fora_do_json_viram_valores_serializaveis():
    res = rf.limitar_resultado({
        "tags": {"b", "a"}, "bruto": b"\x00\x01", "quando": datetime.datetime(2026, 9, 24, 10, 30),
        "caminho": pathlib.Path("/tmp/x"), "nan": float("nan"), "cor": _Cor.AZUL, "ponto": _Ponto(1, 2),
    })
    json.dumps(res, allow_nan=False)  # não pode lançar
    assert res["tags"] == ["a", "b"] and res["quando"] == "2026-09-24T10:30:00"
    assert res["cor"] == "azul" and res["ponto"] == {"x": 1, "y": 2} and "bytes" in res["bruto"]


@pytest.mark.parametrize("gigante", [
    {"sucesso": True, "mensagem": "Página lida.", "conteudo": "palavra " * 200_000},
    {"sucesso": True, "jogos": [{"nome": f"Jogo {i}", "comando": "steam -applaunch 1"} for i in range(50_000)]},
    {f"chave_{i}": i for i in range(20_000)},
    ["item"] * 100_000,
    "texto solto " * 100_000,
])
def test_resultado_grande_cabe_no_limite(gigante):
    res = rf.limitar_resultado(gigante, limite=2000)
    assert rf.tamanho_do_resultado(res) <= 2000
    assert res["resultado_truncado"] is True and res["tamanho_original"] > 2000
    if isinstance(gigante, dict):
        for chave in ("sucesso", "mensagem"):
            if chave in gigante:
                assert res[chave] == gigante[chave], "chaves essenciais chegam intactas"


def test_callback_adk_preserva_midia_e_so_resume_json_grande(monkeypatch):
    class Ferramenta:
        name = "ferramenta_qualquer"

    imagem = {"imagem": types.Part.from_bytes(data=b"x" * 100_000, mime_type="image/png")}
    assert limitar_resultado_da_ferramenta(Ferramenta(), {}, None, imagem) is None, "mídia segue para o ADK extrair"
    assert limitar_resultado_da_ferramenta(Ferramenta(), {}, None, {"sucesso": True}) is None
    monkeypatch.setenv("JARVIS_LIMITE_RESULTADO_FERRAMENTA", "3000")
    resumido = limitar_resultado_da_ferramenta(Ferramenta(), {}, None, {"texto": "a" * 50_000})
    assert resumido["resultado_truncado"] is True and rf.tamanho_do_resultado(resumido) <= 3000


def test_agentes_encadeiam_lease_e_limite_nessa_ordem():
    agente = criar_agente_rapido()
    nomes = [c.__name__ for c in agente.canonical_after_tool_callbacks]
    assert nomes == ["registrar_autoridade_dos_modos", "limitar_resultado_da_ferramenta"]


class _ModeloRoteirizado(BaseLlm):
    """Chama a ferramenta uma vez e depois devolve, como texto, a resposta que recebeu dela."""

    model: str = "modelo-roteirizado"

    async def generate_content_async(self, llm_request, stream=False):
        respostas = [parte.function_response for conteudo in llm_request.contents
                     for parte in (conteudo.parts or []) if parte.function_response]
        if not respostas:
            chamada = types.FunctionCall(name="ler_documento_enorme", args={})
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=chamada)]))
            return
        recebido = json.dumps(respostas[-1].response, ensure_ascii=False)
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=recebido)]))


def ler_documento_enorme() -> dict:
    """Devolve um documento gigante."""
    return {"sucesso": True, "mensagem": "Documento lido.", "texto": "linha de log\n" * 20_000}


def test_modelo_do_adk_recebe_o_resultado_ja_limitado(monkeypatch):
    monkeypatch.setenv("JARVIS_LIMITE_RESULTADO_FERRAMENTA", "4000")
    agente = Agent(name="teste_limite", model=_ModeloRoteirizado(), tools=[ler_documento_enorme],
                   after_tool_callback=CALLBACKS_POS_FERRAMENTA)
    runner = InMemoryRunner(agent=agente, app_name="teste_limite")

    async def conversar():
        sessao = await runner.session_service.create_session(app_name="teste_limite", user_id="u")
        textos = []
        async for evento in runner.run_async(
            user_id="u", session_id=sessao.id,
            new_message=types.Content(role="user", parts=[types.Part(text="leia o documento")]),
        ):
            if evento.is_final_response() and evento.content and evento.content.parts:
                textos += [p.text for p in evento.content.parts if p.text]
        return textos

    recebido = json.loads(asyncio.run(conversar())[-1])
    assert recebido["resultado_truncado"] is True and recebido["mensagem"] == "Documento lido."
    assert rf.tamanho_do_resultado(recebido) <= 4000


def test_live_nativo_limita_antes_de_registrar_e_enviar():
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(raiz, "servidor", "live_nativo.py"), encoding="utf-8") as arquivo:
        fonte = arquivo.read()
    limite = fonte.index("res = limitar_resultado(res)")
    assert limite < fonte.index('record_event("tool_result"')
    assert limite < fonte.index('gemini_bridge.log_audit_event("JARVIS", f"tool_result:{func_name}"')
    assert limite < fonte.index('response={"result": res}')
