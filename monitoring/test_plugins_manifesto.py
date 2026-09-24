"""Plug-ins descobertos por manifesto (plugins/<id>/plugin.json).

O manifesto é a fonte única dos metadados: o gerenciador o lê sem importar código e
o plug-in monta o próprio PluginMeta a partir dele. Criar um plug-in não exige
editar o plugin_manager.py, e itens só de catálogo não fingem que instalaram.
"""

import json
import os
import sys
import textwrap

import pytest
from fastapi.testclient import TestClient

import plugin_manager as pm
from plugin_sdk import ManifestoInvalido, ler_manifesto

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_todo_plugin_do_projeto_tem_manifesto_valido_e_carrega():
    pastas = sorted(
        nome for nome in os.listdir(pm.PLUGINS_DIR)
        if os.path.isfile(os.path.join(pm.PLUGINS_DIR, nome, "plugin.py"))
    )
    gerenciador = pm.PluginManager()
    assert gerenciador.erros_de_carga() == {}
    assert sorted(gerenciador._plugins) == pastas
    for plugin_id, plugin in gerenciador._plugins.items():
        manifesto = ler_manifesto(os.path.join(pm.PLUGINS_DIR, plugin_id))
        assert (plugin.meta.name, plugin.meta.version, plugin.meta.icon) == (
            manifesto["name"], manifesto["version"], manifesto["icon"]), "metadados vêm só do manifesto"


def test_gerenciador_nao_tem_lista_fixa_de_plugins():
    with open(os.path.join(RAIZ, "plugin_manager.py"), encoding="utf-8") as arquivo:
        fonte = arquivo.read()
    for resquicio in ("known_modules", "STORE_CATALOG", '"plugins.game_companion.plugin"'):
        assert resquicio not in fonte


def _criar_plugin(pasta, plugin_id, manifesto=None, codigo=None):
    dir_plugin = pasta / plugin_id
    dir_plugin.mkdir(parents=True)
    dados = manifesto if manifesto is not None else {
        "id": plugin_id, "name": "Plug-in de Teste", "version": "0.1.0", "entry": "plugin",
        "category": "general", "icon": "🧪", "description": "Criado pelo teste.",
    }
    (dir_plugin / "plugin.json").write_text(json.dumps(dados), encoding="utf-8")
    (dir_plugin / "plugin.py").write_text(codigo or textwrap.dedent(f"""\
        from plugin_sdk import JarvisPlugin, PluginMeta

        class PluginDeTeste(JarvisPlugin):
            def __init__(self):
                super().__init__(PluginMeta.do_manifesto(__file__))
                self.register_tool("ferramenta_{plugin_id}", "Teste.", {{"type": "object", "properties": {{}}}},
                                   lambda: {{"sucesso": True}}, risk_level="READ")
    """), encoding="utf-8")
    return dir_plugin


@pytest.fixture
def pasta_de_plugins(tmp_path, monkeypatch):
    pasta = tmp_path / "plugins_de_teste"
    pasta.mkdir()
    monkeypatch.syspath_prepend(str(tmp_path))
    yield pasta
    for nome in [m for m in sys.modules if m.startswith("plugins_de_teste")]:
        sys.modules.pop(nome, None)


def test_plugin_novo_so_precisa_de_pasta_com_manifesto(pasta_de_plugins):
    _criar_plugin(pasta_de_plugins, "clima_local")
    gerenciador = pm.PluginManager(pasta=str(pasta_de_plugins))
    assert gerenciador.erros_de_carga() == {}
    assert gerenciador._plugins["clima_local"].meta.name == "Plug-in de Teste"
    assert [t.name for t in gerenciador.get_active_tools()] == ["ferramenta_clima_local"]


@pytest.mark.parametrize("manifesto, trecho_do_erro", [
    ({"id": "outro_nome", "name": "X", "version": "1.0.0", "entry": "plugin"}, "difere da pasta"),
    ({"id": "incompleto", "name": "X"}, "campos obrigatórios ausentes"),
    ({"id": "fuga", "name": "X", "version": "1.0.0", "entry": "..os"}, "entry"),
])
def test_manifesto_invalido_e_recusado_sem_importar_codigo(pasta_de_plugins, manifesto, trecho_do_erro):
    pasta_id = {"outro_nome": "pasta_real"}.get(manifesto["id"], manifesto["id"])
    codigo = "raise RuntimeError('código de manifesto inválido não pode ser importado')\n"
    _criar_plugin(pasta_de_plugins, pasta_id, manifesto=manifesto, codigo=codigo)
    gerenciador = pm.PluginManager(pasta=str(pasta_de_plugins))
    assert gerenciador._plugins == {}
    assert trecho_do_erro in gerenciador.erros_de_carga()[pasta_id]


def test_ler_manifesto_exige_objeto_json(tmp_path):
    (tmp_path / "plugin.json").write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ManifestoInvalido):
        ler_manifesto(str(tmp_path))


def test_plugins_simulados_vem_do_manifesto_e_ficam_desligados():
    simulados = {
        nome for nome in os.listdir(pm.PLUGINS_DIR)
        if os.path.isfile(os.path.join(pm.PLUGINS_DIR, nome, "plugin.json"))
        and ler_manifesto(os.path.join(pm.PLUGINS_DIR, nome)).get("simulated")
    }
    assert pm.PLUGINS_SIMULADOS == simulados and "game_companion" not in simulados
    if not pm.MOCKS_ATIVOS:
        gerenciador = pm.PluginManager()
        assert all(not gerenciador._plugins[pid].meta.enabled for pid in simulados)


def test_item_so_de_catalogo_nao_finge_que_instalou():
    import server
    catalogo = {item["id"]: item for item in pm.plugin_manager.get_store_catalog()}
    assert catalogo["obs_studio"]["installed"] is False
    assert pm.plugin_manager.get_store_catalog()[0]["id"] == "game_companion", "plug-ins reais primeiro"

    cliente = TestClient(server.app)
    resposta = cliente.post("/api/plugins/install", json={"plugin_id": "obs_studio"},
                            headers={"X-Jarvis-Token": server.JARVIS_SECRET_TOKEN})
    assert resposta.status_code == 200 and resposta.json()["sucesso"] is False
    assert "catálogo" in resposta.json()["mensagem"]
    assert {item["id"]: item for item in pm.plugin_manager.get_store_catalog()}["obs_studio"]["installed"] is False
