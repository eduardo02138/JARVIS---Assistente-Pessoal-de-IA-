"""
Gerenciador Central de Plug-ins do J.A.R.V.I.S.
Carrega, ativa, desativa e monitora extensões modulares dinamicamente.
"""

import os
import sys
import importlib
import logging
from typing import Optional
from plugin_sdk import JarvisPlugin, ToolSpec

logger = logging.getLogger("jarvis.plugins")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGINS_DIR = os.path.join(BASE_DIR, "plugins")

# Itens remotos da loja não instalados localmente
REMOTE_STORE_ITEMS = [
    {
        "id": "obs_studio",
        "name": "Controle de Cenas OBS Studio",
        "version": "1.0.0",
        "category": "streaming",
        "icon": "📹",
        "description": "Troca de cenas, fontes de áudio e gravação de gameplays diretamente por comandos de voz.",
        "author": "Comunidade Open Source",
        "installed": False,
        "enabled": False
    }
]

# Catálogo dinâmico da Loja de Habilidades alimentado por PluginMeta (Autoridade Única)
STORE_CATALOG: list[dict] = []

# Plug-ins que ainda respondem com dados simulados. Ficam desligados por padrão para não
# poluir a conversa com notificações, cotações e e-mails inventados; ative com
# JARVIS_ATIVAR_MOCKS=1 quando quiser demonstrá-los.
PLUGINS_SIMULADOS = {
    "smart_home", "social_feed", "live_stream",
    "google_workspace", "google_finance", "ginjutsu_studio", "deep_research",
}

MOCKS_ATIVOS = os.environ.get("JARVIS_ATIVAR_MOCKS", "").strip().lower() in ("1", "true", "sim", "yes")


class PluginManager:
    def __init__(self):
        self._plugins: dict[str, JarvisPlugin] = {}
        self._load_all()

    def _sync_store_catalog(self):
        """Sincroniza STORE_CATALOG a partir dos metadados reais (PluginMeta) dos plug-ins."""
        STORE_CATALOG.clear()
        # 1. Plug-ins descobertos e instalados localmente
        for plugin in self._plugins.values():
            STORE_CATALOG.append(plugin.meta.to_store_dict())

        # 2. Itens remotos/não-instalados da loja
        for remote in REMOTE_STORE_ITEMS:
            if not any(item["id"] == remote["id"] for item in STORE_CATALOG):
                STORE_CATALOG.append(dict(remote))

    def _discover_plugin_modules(self) -> list[tuple[str, str]]:
        """Varre dinamicamente o diretório plugins/ em busca de subclasses em plugins/*/plugin.py."""
        modulos = []
        if not os.path.exists(PLUGINS_DIR):
            return modulos

        for pasta in sorted(os.listdir(PLUGINS_DIR)):
            caminho_plugin = os.path.join(PLUGINS_DIR, pasta, "plugin.py")
            if os.path.isfile(caminho_plugin):
                modulos.append((pasta, f"plugins.{pasta}.plugin"))
        return modulos

    def _load_all(self):
        """Descobre e carrega automaticamente todas as subclasses de JarvisPlugin em plugins/."""
        if not os.path.exists(PLUGINS_DIR):
            os.makedirs(PLUGINS_DIR, exist_ok=True)

        if BASE_DIR not in sys.path:
            sys.path.insert(0, BASE_DIR)

        descobertos = self._discover_plugin_modules()

        for pasta, mod_path in descobertos:
            try:
                mod = importlib.import_module(mod_path)
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if isinstance(attr, type) and issubclass(attr, JarvisPlugin) and attr is not JarvisPlugin:
                        instance = attr()
                        if instance.meta.id in PLUGINS_SIMULADOS and not MOCKS_ATIVOS:
                            instance.meta.enabled = False
                        instance.on_load()
                        self._plugins[instance.meta.id] = instance
                        logger.info(f"Plug-in '{instance.meta.name}' ({instance.meta.id}) carregado com sucesso via descoberta dinâmica.")
                        break
            except Exception as e:
                logger.error(f"Falha ao carregar o plug-in da pasta '{pasta}': {e}")

        self._sync_store_catalog()

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

        for item in STORE_CATALOG:
            if item["id"] == plugin_id:
                item["enabled"] = new_state
                break

        self.sync_with_system_tools()

        return {
            "sucesso": True,
            "plugin_id": plugin_id,
            "enabled": new_state,
            "mensagem": msg
        }

    def install_plugin(self, plugin_id: str) -> dict:
        """Instala ou ativa um plug-in da loja."""
        for item in STORE_CATALOG:
            if item["id"] == plugin_id:
                item["installed"] = True
                item["enabled"] = True
                if plugin_id in self._plugins:
                    self._plugins[plugin_id].meta.enabled = True
                    self._plugins[plugin_id].on_load()
                self.sync_with_system_tools()
                return {
                    "sucesso": True,
                    "plugin_id": plugin_id,
                    "mensagem": f"Plug-in '{item['name']}' instalado e ativado no ecossistema JARVIS, senhor."
                }
        return {"sucesso": False, "mensagem": f"Plug-in '{plugin_id}' não encontrado na loja."}

    def get_all_plugins_info(self) -> list[dict]:
        """Retorna uma lista com informações e status de todos os plug-ins."""
        result = []
        for item in STORE_CATALOG:
            p = self._plugins.get(item["id"])
            info = dict(item)
            if p:
                info["enabled"] = p.meta.enabled
                info["installed"] = True
                info["tools_count"] = len(p.get_tools())
                info["tools"] = [t.name for t in p.get_tools()]
            else:
                info["tools_count"] = 0
                info["tools"] = []
            result.append(info)
        return result

    def get_store_catalog(self) -> list[dict]:
        return STORE_CATALOG

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
