"""
Gerenciador Central de Plug-ins do J.A.R.V.I.S.
Carrega, ativa, desativa e monitora extensões modulares dinamicamente.
"""

import os
import sys
import importlib
import logging
from typing import Optional
from plugin_sdk import JarvisPlugin, ToolSpec, PluginMeta

logger = logging.getLogger("jarvis.plugins")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGINS_DIR = os.path.join(BASE_DIR, "plugins")

# Catálogo oficial da Loja de Habilidades do JARVIS
STORE_CATALOG = [
    {
        "id": "game_companion",
        "name": "Companhia em Jogos Online",
        "version": "1.2.0",
        "category": "gaming",
        "icon": "🎮",
        "description": "Assistência tática em tempo real para jogos (Marvel Rivals, GTA, RPGs), timers táticos e inicializador de jogos instalados.",
        "author": "Stark Gaming Hub",
        "installed": True,
        "enabled": True
    },
    {
        "id": "google_workspace",
        "name": "Google Workspace (Gmail, Docs & Keep)",
        "version": "1.0.0",
        "category": "general",
        "icon": "📑",
        "description": "Comandos de voz para redigir documentos no Docs, consultar caixa de entrada no Gmail e capturar ideias no Keep.",
        "author": "Google Cloud & Stark Industries",
        "installed": True,
        "enabled": True
    },
    {
        "id": "deep_research",
        "name": "Pesquisa Profunda & Dossiês Assíncronos",
        "version": "1.0.0",
        "category": "general",
        "icon": "🔬",
        "description": "Executa investigações aprofundadas em segundo plano sem travar o chat, emitindo notificações de voz/HUD ao concluir.",
        "author": "Gemini Live Research Lab",
        "installed": True,
        "enabled": True
    },
    {
        "id": "google_finance",
        "name": "Google Finance & Portfólio de Investimentos",
        "version": "1.0.0",
        "category": "general",
        "icon": "📈",
        "description": "Cotações em tempo real (B3, S&P 500, Cripto), consolidação de portfólio, alocação de ativos e insights táticos.",
        "author": "Google Finance & Stark Holdings",
        "installed": True,
        "enabled": True
    },
    {
        "id": "ginjutsu_studio",
        "name": "Ginjutsu Motion & Video AI Studio",
        "version": "1.0.0",
        "category": "general",
        "icon": "🎬",
        "description": "Transferência de atuação, coreografia e enquadramento de vídeos existentes para novos personagens via Higgsfield Ginjutsu.",
        "author": "Higgsfield & Stark Visuals",
        "installed": True,
        "enabled": True
    },
    {
        "id": "smart_home",
        "name": "Casa Inteligente & IoT",
        "version": "1.0.0",
        "category": "smart_home",
        "icon": "🏠",
        "description": "Controle de iluminação inteligente, climatização e cenas de ambiente ('Foco/Trabalho', 'Cinema', 'Descanso').",
        "author": "Stark Home Automation",
        "installed": True,
        "enabled": True
    },
    {
        "id": "live_stream",
        "name": "Transmissão ao Vivo & Streaming",
        "version": "1.0.0",
        "category": "streaming",
        "icon": "📡",
        "description": "Integração para transmissões ao vivo: leitura e síntese de chat em tempo real e alertas de doações.",
        "author": "Stark Media Lab",
        "installed": True,
        "enabled": True
    },
    {
        "id": "social_feed",
        "name": "Mídias Sociais & Notificações",
        "version": "1.0.0",
        "category": "social",
        "icon": "💬",
        "description": "Monitoramento inteligente de feeds, menções, mensagens diretas (Discord, Telegram, X/Twitter).",
        "author": "Stark Comms",
        "installed": True,
        "enabled": True
    },
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

    def _load_all(self):
        """Carrega todos os módulos de plug-in encontrados em plugins/."""
        if not os.path.exists(PLUGINS_DIR):
            os.makedirs(PLUGINS_DIR, exist_ok=True)

        if BASE_DIR not in sys.path:
            sys.path.insert(0, BASE_DIR)

        # Plugins oficiais mapeados
        known_modules = {
            "game_companion": "plugins.game_companion.plugin",
            "google_workspace": "plugins.google_workspace.plugin",
            "deep_research": "plugins.deep_research.plugin",
            "google_finance": "plugins.google_finance.plugin",
            "ginjutsu_studio": "plugins.ginjutsu_studio.plugin",
            "smart_home": "plugins.smart_home.plugin",
            "live_stream": "plugins.live_stream.plugin",
            "social_feed": "plugins.social_feed.plugin",
        }

        for plugin_id, mod_path in known_modules.items():
            try:
                mod = importlib.import_module(mod_path)
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if isinstance(attr, type) and issubclass(attr, JarvisPlugin) and attr is not JarvisPlugin:
                        instance = attr()
                        if instance.meta.id in PLUGINS_SIMULADOS and not MOCKS_ATIVOS:
                            instance.meta.enabled = False
                            for item in STORE_CATALOG:
                                if item["id"] == instance.meta.id:
                                    item["enabled"] = False
                                    break
                        instance.on_load()
                        self._plugins[instance.meta.id] = instance
                        logger.info(f"Plug-in '{instance.meta.name}' ({instance.meta.id}) carregado com sucesso.")
                        break
            except Exception as e:
                logger.error(f"Falha ao carregar o plug-in '{plugin_id}': {e}")

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
