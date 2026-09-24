"""Diagnóstico (python diagnostico.py e GET /api/diagnostico).

Cada problema vira um achado com nível e correção acionável; uma verificação que
quebra não derruba as outras, e o código de saída sinaliza erros para scripts.
"""

import json

import pytest
from fastapi.testclient import TestClient

import diagnostico


def _achado(relatorio, id_achado):
    return next(a for a in relatorio["achados"] if a["id"] == id_achado)


def test_relatorio_tem_niveis_validos_e_correcao_em_todo_problema():
    relatorio = diagnostico.executar_diagnostico()
    ids = [a["id"] for a in relatorio["achados"]]
    assert len(ids) == len(set(ids)), "cada achado tem id único"
    for achado in relatorio["achados"]:
        assert achado["nivel"] in (diagnostico.OK, diagnostico.AVISO, diagnostico.ERRO)
        if achado["nivel"] == diagnostico.ERRO:
            assert achado["correcao"], f"erro sem correção: {achado['id']}"
    assert sum(relatorio["resumo"].values()) == len(relatorio["achados"])


def test_detecta_configuracao_insegura_ou_incompleta(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    monkeypatch.delenv("JARVIS_TOKEN", raising=False)
    monkeypatch.setenv("JARVIS_HOST", "0.0.0.0")
    monkeypatch.setenv("JARVIS_WEB_PERMITIR_REDE_LOCAL", "1")
    monkeypatch.setenv("JARVIS_ENV_REPASSAR", "GEMINI_API_KEY")
    relatorio = diagnostico.executar_diagnostico()
    assert _achado(relatorio, "configuracao.chave_gemini")["nivel"] == diagnostico.ERRO
    assert _achado(relatorio, "seguranca.token")["nivel"] == diagnostico.AVISO
    assert _achado(relatorio, "seguranca.rede")["nivel"] == diagnostico.AVISO
    assert _achado(relatorio, "seguranca.leitura_web")["nivel"] == diagnostico.AVISO
    assert "GEMINI_API_KEY" in _achado(relatorio, "seguranca.processos")["detalhe"]


def test_maquina_sem_utilitarios_recebe_a_instrucao_de_instalacao(monkeypatch):
    import perfil_maquina
    monkeypatch.setattr(diagnostico.shutil, "which", lambda nome: None)
    monkeypatch.setattr("system_tools.shutil.which", lambda nome: None)
    monkeypatch.setattr(perfil_maquina, "sessao_grafica", lambda: "wayland")
    relatorio = diagnostico.executar_diagnostico()
    assert "wireplumber" in _achado(relatorio, "funcao.volume")["correcao"]
    assert "grim" in _achado(relatorio, "funcao.captura_de_tela")["correcao"], "dica certa para Wayland"
    assert "xdg-utils" in _achado(relatorio, "funcao.abrir_links")["correcao"]


def test_plugin_quebrado_vira_erro_e_verificacao_com_excecao_vira_aviso(monkeypatch):
    from plugin_manager import plugin_manager
    monkeypatch.setattr(plugin_manager, "erros_de_carga", lambda: {"clima": "manifesto ilegível"})

    def verificacao_que_quebra():
        raise RuntimeError("falha simulada")

    monkeypatch.setattr(diagnostico, "VERIFICACOES", [verificacao_que_quebra, *diagnostico.VERIFICACOES])
    relatorio = diagnostico.executar_diagnostico()
    assert _achado(relatorio, "plugins.clima")["nivel"] == diagnostico.ERRO
    assert _achado(relatorio, "diagnostico.verificacao_que_quebra")["nivel"] == diagnostico.AVISO
    assert _achado(relatorio, "configuracao.chave_gemini"), "as outras verificações continuam"


@pytest.mark.parametrize("com_chave, codigo", [(True, 0), (False, 1)])
def test_cli_sinaliza_erro_no_codigo_de_saida(monkeypatch, capsys, com_chave, codigo):
    if not com_chave:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    monkeypatch.setattr(diagnostico, "VERIFICACOES", [diagnostico._configuracao])
    assert diagnostico.main(["--json", "--sem-mcp"]) == codigo
    relatorio = json.loads(capsys.readouterr().out)
    assert relatorio["resumo"]["erro"] == (0 if com_chave else 1)


def test_endpoint_exige_token_e_devolve_o_relatorio():
    import server
    cliente = TestClient(server.app)
    assert cliente.get("/api/diagnostico").status_code == 401
    resposta = cliente.get("/api/diagnostico", headers={"X-Jarvis-Token": server.JARVIS_SECRET_TOKEN})
    assert resposta.status_code == 200
    assert {"resumo", "achados"} <= set(resposta.json())
