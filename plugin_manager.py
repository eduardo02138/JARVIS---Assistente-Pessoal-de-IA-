"""
Gerenciador de Plug-ins e Loja de Extensões do J.A.R.V.I.S.
Controla o ciclo de vida dos plug-ins, ativação/desativação dinâmica
e exportação de ferramentas para o Gemini Live e Antigravity.
"""

import os
import sys
import importlib
import logging
from typing import Optional

from plugin_sdk import JarvisPlugin, PluginMeta, ToolSpec

logger = logging.getLogger("JARVIS_PLUGIN_MANAGER")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGINS_DIR = os.path.join(BASE_DIR, "plugins")

# Catálogo oficial da Loja de Plug-ins do Ecossistema JARVIS / N.E.K.O.
STORE_CATALOG = [
    {
        "id": "game_companion",
        "name": "Companhia em Jogos Online",
        "version": "1.0.0",
        "category": "gaming",
        "icon": "🎮",
        "description": "Assistência tática em tempo real para jogos (Marvel Rivals, GTA, RPGs), timers e estratégias.",
        "author": "Stark Gaming Division",
        "installed": True,
        "enabled": True
    },
    {
        "id": "smart_home",
        "name": "Casa Inteligente & IoT",
        "version": "1.0.0",
        "category": "smart_home",
        "icon": "🏠",
        "description": "Controle de iluminação inteligente, climatização residencial e cenas de ambiente ('Foco', 'Cinema').",
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
    },
    {
        "id": "crypto_ticker",
        "name": "Monitor de Criptoativos & Mercado",
        "version": "1.0.0",
        "category": "general",
        "icon": "📈",
        "description": "Telemetria de cotações em tempo real de Bitcoin, Ethereum, Solana e índices globais.",
        "author": "Stark Finance",
        "installed": False,
        "enabled": False
    }
]

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
            "smart_home": "plugins.smart_home.plugin",
            "live_stream": "plugins.live_stream.plugin",
            "social_feed": "plugins.social_feed.plugin",
        }

        for plugin_id, mod_path in known_modules.items():
            try:
                mod = importlib.import_module(mod_path)
                # Encontra a subclasse JarvisPlugin
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if isinstance(attr, type) and issubclass(attr, JarvisPlugin) and attr is not JarvisPlugin:
                        instance = attr()
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

        # Atualiza o catálogo da loja
        for item in STORE_CATALOG:
            if item["id"] == plugin_id:
                item["enabled"] = new_state
                break

        # Sincroniza dinamicamente com o system_tools
        self.sync_with_system_tools()

        return {
            "sucesso": True,
            "plugin_id": plugin_id,
            "enabled": new_state,
            "mensagem": msg
        }

    def install_plugin(self, plugin_id: str) -> dict:
        """Simula a instalação de um plug-in da loja."""
        for item in STORE_CATALOG:
            if item["id"] == plugin_id:
                item["installed"] = True
                item["enabled"] = True
                # Se já estiver instanciado na memória, ativa
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

    def sync_with_system_tools(self):
        """Injeta as ferramentas ativas dos plug-ins diretamente no TOOL_REGISTRY e GEMINI_FUNCTION_DECLARATIONS."""
        try:
            import system_tools
            active_tools = self.get_active_tools()
            
            # Registra handlers no TOOL_REGISTRY
            for t in active_tools:
                system_tools.TOOL_REGISTRY[t.name] = t.handler

            # Registra schemas em GEMINI_FUNCTION_DECLARATIONS se não existirem
            existing_names = {d["name"] for d in system_tools.GEMINI_FUNCTION_DECLARATIONS}
            for t in active_tools:
                if t.name not in existing_names:
                    system_tools.GEMINI_FUNCTION_DECLARATIONS.append({
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters
                    })
            logger.info(f"Sincronização concluída: {len(active_tools)} ferramentas de plug-ins registradas.")
        except Exception as e:
            logger.error(f"Erro ao sincronizar ferramentas de plug-ins: {e}")

# Instância única global do gerenciador
plugin_manager = PluginManager()
plugin_manager.sync_with_system_tools()
