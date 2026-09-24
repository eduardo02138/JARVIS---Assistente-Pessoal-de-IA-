"""
Gerenciador Central de Plug-ins do J.A.R.V.I.S.
Carrega, ativa, desativa e monitora extensões modulares dinamicamente.

Descoberta por manifesto: cada plugins/<id>/plugin.json declara id, nome, versão,
categoria, ícone, autor, descrição, o módulo de entrada ("entry") e se o plug-in só
simula dados ("simulated"). Os manifestos são lidos sem importar código, e adicionar
um plug-in é criar a pasta com manifesto e módulo, sem editar este arquivo.
plugins/catalogo_loja.json lista os itens da loja que ainda não têm código aqui.
"""

import importlib
import json
import logging
import os
import sys
from typing import Optional

from plugin_sdk import NOME_DO_MANIFESTO, JarvisPlugin, ManifestoInvalido, ToolSpec, ler_manifesto

logger = logging.getLogger("jarvis.plugins")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGINS_DIR = os.path.join(BASE_DIR, "plugins")
CATALOGO_DA_LOJA = os.path.join(PLUGINS_DIR, "catalogo_loja.json")

# Campos de cada item da loja entregues ao HUD
CAMPOS_DO_CATALOGO = ("id", "name", "version", "category", "icon", "description", "author")

MOCKS_ATIVOS = os.environ.get("JARVIS_ATIVAR_MOCKS", "").strip().lower() in ("1", "true", "sim", "yes")


def descobrir_manifestos(pasta: str = PLUGINS_DIR) -> tuple[list[dict], dict[str, str]]:
    """Manifestos válidos de plugins/*/plugin.json e os erros de cada pasta, sem importar código."""
    manifestos: list[dict] = []
    erros: dict[str, str] = {}
    if not os.path.isdir(pasta):
        return manifestos, erros
    for nome in sorted(os.listdir(pasta)):
        dir_plugin = os.path.join(pasta, nome)
        if not os.path.isfile(os.path.join(dir_plugin, NOME_DO_MANIFESTO)):
            continue
        try:
            manifestos.append(ler_manifesto(dir_plugin))
        except ManifestoInvalido as erro:
            erros[nome] = str(erro)
    return manifestos, erros


def ler_catalogo_da_loja(caminho: str = CATALOGO_DA_LOJA) -> list[dict]:
    """Itens da loja sem código no projeto (aparecem para instalar, mas não instalam)."""
    try:
        with open(caminho, encoding="utf-8") as arquivo:
            itens = json.load(arquivo)
    except (OSError, ValueError):
        return []
    return [item for item in itens if isinstance(item, dict) and item.get("id")] if isinstance(itens, list) else []


# Plug-ins que ainda respondem com dados simulados ("simulated": true no manifesto).
# Ficam desligados por padrão para não poluir a conversa com notificações, cotações e
# e-mails inventados; ative com JARVIS_ATIVAR_MOCKS=1 quando quiser demonstrá-los.
PLUGINS_SIMULADOS = frozenset(m["id"] for m in descobrir_manifestos()[0] if m.get("simulated"))


def _classe_do_plugin(modulo) -> Optional[type]:
    """A subclasse de JarvisPlugin definida no próprio módulo de entrada."""
    for atributo in vars(modulo).values():
        if (isinstance(atributo, type) and issubclass(atributo, JarvisPlugin)
                and atributo is not JarvisPlugin and atributo.__module__ == modulo.__name__):
            return atributo
    return None


class PluginManager:
    def __init__(self, pasta: str = PLUGINS_DIR):
        self._pasta = pasta
        self._plugins: dict[str, JarvisPlugin] = {}
        self._manifestos: dict[str, dict] = {}
        self._erros: dict[str, str] = {}
        self._load_all()

    def _load_all(self):
        """Importa o módulo de entrada de cada manifesto válido e instancia o plug-in."""
        if BASE_DIR not in sys.path:
            sys.path.insert(0, BASE_DIR)

        manifestos, self._erros = descobrir_manifestos(self._pasta)
        pacote = os.path.basename(os.path.abspath(self._pasta))
        for manifesto in manifestos:
            plugin_id = manifesto["id"]
            self._manifestos[plugin_id] = manifesto
            try:
                modulo = importlib.import_module(f"{pacote}.{plugin_id}.{manifesto['entry']}")
                classe = _classe_do_plugin(modulo)
                if classe is None:
                    raise ImportError(f"nenhuma classe JarvisPlugin em {manifesto['entry']}.py")
                instance = classe()
                if instance.meta.id != plugin_id:
                    raise ValueError(f"a classe declara id '{instance.meta.id}', o manifesto '{plugin_id}'")
                if manifesto.get("simulated") and not MOCKS_ATIVOS:
                    instance.meta.enabled = False
                instance.on_load()
                self._plugins[plugin_id] = instance
                logger.info(f"Plug-in '{instance.meta.name}' ({plugin_id}) carregado com sucesso.")
            except Exception as e:
                self._erros[plugin_id] = str(e)
                logger.error(f"Falha ao carregar o plug-in '{plugin_id}': {e}")

    def erros_de_carga(self) -> dict[str, str]:
        """Plug-ins com manifesto inválido ou que falharam ao carregar, com o motivo."""
        return dict(self._erros)

    def get_active_tools(self) -> list[ToolSpec]:
        """Retorna todas as ferramentas de plug-ins atualmente ativos/habilitados."""
        tools = []
        for plugin in self._plugins.values():
            if plugin.meta.enabled:
                tools.extend(plugin.get_tools())
        return tools

    def toggle_plugin(self, plugin_id: str, enabled: Optional[bool] = None) -> dict:
        """Ativa ou desativa um plug-in dinamicamente."""
        plugin = self._plugins.get(plugin_id)
        if not plugin:
            return {"sucesso": False, "mensagem": f"Plug-in '{plugin_id}' não encontrado."}

        if enabled is None:
            new_state = not plugin.meta.enabled
        else:
            new_state = bool(enabled)

        plugin.meta.enabled = new_state
        if new_state:
            plugin.on_load()
            msg = f"Plug-in '{plugin.meta.name}' ativado com sucesso, senhor."
        else:
            plugin.on_unload()
            msg = f"Plug-in '{plugin.meta.name}' desativado temporariamente, senhor."

        self.sync_with_system_tools()

        return {
            "sucesso": True,
            "plugin_id": plugin_id,
            "enabled": new_state,
            "mensagem": msg
        }

    def install_plugin(self, plugin_id: str) -> dict:
        """Ativa um plug-in da loja que tem código no projeto; os demais recusam com o motivo."""
        plugin = self._plugins.get(plugin_id)
        if plugin is not None:
            plugin.meta.enabled = True
            plugin.on_load()
            self.sync_with_system_tools()
            return {
                "sucesso": True,
                "plugin_id": plugin_id,
                "mensagem": f"Plug-in '{plugin.meta.name}' instalado e ativado no ecossistema JARVIS, senhor."
            }
        if plugin_id in self._erros:
            return {"sucesso": False, "plugin_id": plugin_id,
                    "mensagem": f"O plug-in '{plugin_id}' não pôde ser carregado: {self._erros[plugin_id]}"}
        item = next((i for i in ler_catalogo_da_loja() if i["id"] == plugin_id), None)
        if item is not None:
            return {"sucesso": False, "plugin_id": plugin_id,
                    "mensagem": f"O plug-in '{item.get('name', plugin_id)}' ainda não está disponível nesta instalação, senhor: ele consta só no catálogo da loja."}
        return {"sucesso": False, "mensagem": f"Plug-in '{plugin_id}' não encontrado na loja."}

    def get_store_catalog(self) -> list[dict]:
        """Loja: plug-ins com manifesto (reais primeiro) e os itens só de catálogo."""
        itens = []
        for plugin_id, manifesto in self._manifestos.items():
            plugin = self._plugins.get(plugin_id)
            item = {campo: manifesto.get(campo) for campo in CAMPOS_DO_CATALOGO}
            item["installed"] = plugin is not None
            item["enabled"] = bool(plugin and plugin.meta.enabled)
            item["simulated"] = bool(manifesto.get("simulated"))
            itens.append(item)
        itens.sort(key=lambda item: (item["simulated"], item["name"] or ""))
        for dados in ler_catalogo_da_loja():
            if dados["id"] in self._manifestos:
                continue
            item = {campo: dados.get(campo) for campo in CAMPOS_DO_CATALOGO}
            item.update(installed=False, enabled=False, simulated=False)
            itens.append(item)
        return itens

    def get_all_plugins_info(self) -> list[dict]:
        """Retorna uma lista com informações e status de todos os plug-ins."""
        result = []
        for item in self.get_store_catalog():
            plugin = self._plugins.get(item["id"])
            ferramentas = [t.name for t in plugin.get_tools()] if plugin else []
            info = dict(item)
            info["tools_count"] = len(ferramentas)
            info["tools"] = ferramentas
            result.append(info)
        return result

    def rebuild_registry(self):
        """
        Reconstrói o registro do sistema garantindo que apenas ferramentas
        de plug-ins ativamente habilitados permaneçam acessíveis ao modelo.
        """
        try:
            import system_tools
            from policy_engine import policy_engine, RiskLevel
            active_tools = self.get_active_tools()
            system_tools.rebuild_registry(active_tools)

            for tool in active_tools:
                declarado = getattr(tool, "risk_level", None)
                if declarado:
                    try:
                        policy_engine.register_tool_policy(tool.name, RiskLevel(declarado))
                    except ValueError:
                        logger.error(f"Nível de risco inválido em '{tool.name}': {declarado}")
                elif policy_engine.get_risk_level(tool.name) is None:
                    logger.warning(
                        f"Ferramenta de plug-in sem política de risco: '{tool.name}'. "
                        "Ela será bloqueada pelo Policy Engine até declarar risk_level."
                    )

            logger.info(f"Reconstrução de plug-ins concluída: {len(active_tools)} ferramentas ativas no sistema.")
        except Exception as e:
            logger.error(f"Erro ao reconstruir ferramentas de plug-ins: {e}")

    def sync_with_system_tools(self):
        """Alias para rebuild_registry mantendo compatibilidade retroativa."""
        self.rebuild_registry()

# Instância única global do gerenciador
plugin_manager = PluginManager()
plugin_manager.sync_with_system_tools()
