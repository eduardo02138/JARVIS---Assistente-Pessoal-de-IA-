"""Gerenciador de Clientes MCP (Model Context Protocol) para o Google ADK no J.A.R.V.I.S.

Permite que os agentes ADK do JARVIS se conectem como clientes a servidores MCP externos
(locais via Stdio ou remotos via SSE / Streamable HTTP), conforme documentado em:
https://adk.dev/tools-custom/mcp-tools/

Destaques da implementação:
- Descoberta declarativa via mcp_servers.json ou variável de ambiente MCP_SERVERS_CONFIG.
- Instanciação de McpToolset com StdioConnectionParams, SseConnectionParams ou StreamableHTTPConnectionParams.
- Suporte a tool_filter e tool_name_prefix por servidor.
- Integração nativa com o PolicyEngine (registro de níveis de risco por ferramenta ou padrão).
- Gestão de ciclo de vida assíncrona com close_all() integrado ao lifespan do FastAPI.
- Isolamento de recursão (ignora conexões com o próprio servidor 'jarvis').
"""

import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

logger = logging.getLogger("mcp_client_manager")

RAIZ = os.path.dirname(os.path.abspath(__file__))

# Auto-injeção do .venv local se necessário
for venv_site in [
    os.path.join(RAIZ, ".venv", "lib", f"python{sys.version_info.major}.{sys.version_info.minor}", "site-packages"),
    os.path.join(RAIZ, ".venv", "lib", "site-packages"),
]:
    if os.path.isdir(venv_site) and venv_site not in sys.path:
        sys.path.insert(0, venv_site)

from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import (
    SseConnectionParams,
    StdioConnectionParams,
    StreamableHTTPConnectionParams,
)
from mcp import StdioServerParameters
from policy_engine import RiskLevel, policy_engine


class McpClientManager:
    """Gerencia a instanciação, configuração e encerramento de conexões com servidores MCP."""

    def __init__(self):
        self._toolsets: Dict[str, McpToolset] = {}
        self._server_configs: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    def carregar_configuracao(self) -> Dict[str, Dict[str, Any]]:
        """Carrega definições de servidores MCP a partir de arquivos JSON ou ambiente."""
        caminho_config = os.environ.get("MCP_SERVERS_CONFIG")
        caminhos_busca = []

        if caminho_config:
            caminhos_busca.append(caminho_config)

        caminhos_busca.append(os.path.join(RAIZ, "mcp_servers.json"))
        caminhos_busca.append(os.path.join(RAIZ, ".mcp_servers.json"))

        # Suporte opcional ao mcp_config.json global do Antigravity
        if os.environ.get("LOAD_GLOBAL_MCP_CONFIG", "").lower() in ("1", "true", "yes"):
            global_path = os.path.expanduser("~/.gemini/config/mcp_config.json")
            caminhos_busca.append(global_path)

        for caminho in caminhos_busca:
            if os.path.isfile(caminho):
                try:
                    with open(caminho, "r", encoding="utf-8") as f:
                        dados = json.load(f)
                    servers = dados.get("mcpServers", dados)
                    if isinstance(servers, dict):
                        logger.info("Configurações MCP carregadas de '%s' (%d servidores encontrados).", caminho, len(servers))
                        return servers
                except Exception as e:
                    logger.warning("Falha ao ler configuração MCP em '%s': %s", caminho, e)

        return {}

    def _criar_toolset_individual(self, nome: str, cfg: Dict[str, Any]) -> Optional[McpToolset]:
        """Cria uma instância de McpToolset para uma configuração específica de servidor."""
        # Evita recursão infinita caso o servidor JARVIS esteja listado na configuração
        if nome.strip().lower() == "jarvis":
            logger.debug("Servidor MCP '%s' ignorado no cliente JARVIS para evitar recursão.", nome)
            return None

        if cfg.get("disabled", False):
            logger.debug("Servidor MCP '%s' desabilitado por configuração.", nome)
            return None

        tool_filter = cfg.get("tool_filter")
        tool_name_prefix = cfg.get("tool_name_prefix")

        # Conexão remota (SSE ou Streamable HTTP)
        url = cfg.get("url") or cfg.get("serverUrl")
        if url:
            url_expandida = os.path.expandvars(url)
            raw_headers = cfg.get("headers", {})
            headers = {k: os.path.expandvars(str(v)) for k, v in raw_headers.items()}
            transport = cfg.get("transport", "").lower()

            if transport == "streamable_http" or url_expandida.endswith("/mcp"):
                conn_params = StreamableHTTPConnectionParams(
                    url=url_expandida,
                    headers=headers or None,
                    timeout=float(cfg.get("timeout", 30.0)),
                )
            else:
                conn_params = SseConnectionParams(
                    url=url_expandida,
                    headers=headers or None,
                    timeout=float(cfg.get("timeout", 30.0)),
                )

            toolset = McpToolset(
                connection_params=conn_params,
                tool_filter=tool_filter,
                tool_name_prefix=tool_name_prefix,
            )
            return toolset

        # Conexão local por processo via Stdio
        comando = cfg.get("command")
        if comando:
            cmd_expandido = os.path.expandvars(comando)
            args = [os.path.expandvars(str(a)) for a in cfg.get("args", [])]
            raw_env = cfg.get("env", {})
            env_vars = os.environ.copy()
            for k, v in raw_env.items():
                env_vars[k] = os.path.expandvars(str(v))

            server_params = StdioServerParameters(
                command=cmd_expandido,
                args=args,
                env=env_vars,
            )
            conn_params = StdioConnectionParams(
                server_params=server_params,
                timeout=float(cfg.get("timeout", 30.0)),
            )

            toolset = McpToolset(
                connection_params=conn_params,
                tool_filter=tool_filter,
                tool_name_prefix=tool_name_prefix,
            )
            return toolset

        logger.warning("Configuração de servidor MCP '%s' inválida: deve conter 'command' ou 'url'.", nome)
        return None

    def _registrar_politicas_de_risco(self, nome: str, cfg: Dict[str, Any]):
        """Registra níveis de risco das ferramentas do MCP no PolicyEngine."""
        politicas = cfg.get("policies", {})
        default_risk_str = cfg.get("default_risk_level")

        # Registra políticas explícitas
        for tool_name, risk_str in politicas.items():
            try:
                risk_enum = RiskLevel(risk_str.upper())
                policy_engine.register_tool_policy(tool_name, risk_enum)
                logger.debug("Política MCP registrada: %s -> %s", tool_name, risk_enum.value)
            except Exception as e:
                logger.warning("Nível de risco inválido para ferramenta MCP '%s': %s", tool_name, e)

        # Se houver filtro e default_risk_level, registra para as ferramentas filtradas
        tool_filter = cfg.get("tool_filter", [])
        if default_risk_str and isinstance(tool_filter, list):
            try:
                default_risk = RiskLevel(default_risk_str.upper())
                for tool_name in tool_filter:
                    if tool_name not in politicas:
                        policy_engine.register_tool_policy(tool_name, default_risk)
            except Exception as e:
                logger.warning("default_risk_level inválido no servidor MCP '%s': %s", nome, e)

    def carregar_toolsets(self, forcar_recarga: bool = False) -> List[McpToolset]:
        """Carrega e inicializa os toolsets para todos os servidores MCP configurados."""
        if self._loaded and not forcar_recarga:
            return list(self._toolsets.values())

        configs = self.carregar_configuracao()
        self._server_configs = configs

        novos_toolsets: Dict[str, McpToolset] = {}
        for nome, cfg in configs.items():
            try:
                toolset = self._criar_toolset_individual(nome, cfg)
                if toolset:
                    novos_toolsets[nome] = toolset
                    self._registrar_politicas_de_risco(nome, cfg)
                    logger.info("Toolset MCP '%s' inicializado com sucesso.", nome)
            except Exception as e:
                logger.error("Falha ao inicializar servidor MCP '%s': %s", nome, e)

        self._toolsets = novos_toolsets
        self._loaded = True
        return list(self._toolsets.values())

    def get_all_toolsets(self) -> List[McpToolset]:
        """Retorna os McpToolsets já carregados ou carrega se ainda não feito."""
        if not self._loaded:
            return self.carregar_toolsets()
        return list(self._toolsets.values())

    def get_toolset(self, nome: str) -> Optional[McpToolset]:
        """Retorna o toolset de um servidor específico pelo nome."""
        return self._toolsets.get(nome)

    def registrar_servidor_dinamico(self, nome: str, cfg: Dict[str, Any]) -> Optional[McpToolset]:
        """Permite registrar e conectar um servidor MCP dinamicamente em tempo de execução."""
        toolset = self._criar_toolset_individual(nome, cfg)
        if toolset:
            self._toolsets[nome] = toolset
            self._server_configs[nome] = cfg
            self._registrar_politicas_de_risco(nome, cfg)
            logger.info("Servidor MCP dinâmico '%s' registrado e ativo.", nome)
        return toolset

    async def close_all(self):
        """Fecha todas as conexões ativas com servidores MCP (cleanup ordenado)."""
        logger.info("Encerrando conexões com %d servidor(es) MCP...", len(self._toolsets))
        for nome, toolset in list(self._toolsets.items()):
            try:
                await toolset.close()
                logger.info("Servidor MCP '%s' desconectado.", nome)
            except Exception as e:
                logger.warning("Erro ao fechar servidor MCP '%s': %s", nome, e)
        self._toolsets.clear()
        self._loaded = False

    def close_all_sync(self):
        """Auxiliar síncrono para encerramento de conexões quando fora de loop assíncrono."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(self.close_all())
            else:
                loop.run_until_complete(self.close_all())
        except Exception:
            asyncio.run(self.close_all())

    def status(self) -> List[Dict[str, Any]]:
        """Retorna o status de todos os servidores MCP configurados."""
        res = []
        for nome, cfg in self._server_configs.items():
            if nome.lower() == "jarvis":
                continue
            tipo = "remote" if (cfg.get("url") or cfg.get("serverUrl")) else "stdio"
            ativo = nome in self._toolsets and not cfg.get("disabled", False)
            res.append({
                "nome": nome,
                "tipo": tipo,
                "ativo": ativo,
                "detalhes": cfg.get("command") or cfg.get("url") or cfg.get("serverUrl"),
                "tool_filter": cfg.get("tool_filter", []),
            })
        return res


# Singleton oficial do gerenciador de clientes MCP
mcp_client_manager = McpClientManager()
