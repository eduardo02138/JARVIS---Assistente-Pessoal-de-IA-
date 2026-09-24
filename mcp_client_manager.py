"""Gerenciador de Clientes MCP (Model Context Protocol) para o Google ADK no J.A.R.V.I.S.

Permite que os agentes ADK do JARVIS se conectem como clientes a servidores MCP externos
(locais via Stdio ou remotos via SSE / Streamable HTTP), conforme documentado em:
https://adk.dev/tools-custom/mcp-tools/

Destaques da implementação:
- Descoberta declarativa via mcp_servers.json ou variável de ambiente MCP_SERVERS_CONFIG.
- Instanciação de McpToolset com StdioConnectionParams, SseConnectionParams ou StreamableHTTPConnectionParams.
- Suporte a tool_filter e tool_name_prefix por servidor.
- Integração nativa com o PolicyEngine (registro de níveis de risco por ferramenta ou padrão),
  sem nunca sobrescrever a política de uma ferramenta do próprio JARVIS.
- Servidores stdio recebem só o ambiente mínimo (PATH, HOME...) mais o bloco "env" declarado:
  as chaves do .env do JARVIS não vazam para processos de terceiros ("inherit_env": true herda
  o restante da sessão do usuário, ainda sem JARVIS_TOKEN e as chaves de provedor).
- Verificação de conexão no boot: lista as ferramentas de cada servidor e aplica default_risk_level.
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
from mcp.client.stdio import get_default_environment
from policy_engine import RiskLevel, policy_engine
from processos import ambiente_sem_segredos

# Nomes cujas políticas vieram de configurações MCP (as demais são do JARVIS e não mudam)
_POLITICAS_DE_ORIGEM_MCP: set = set()


def _expandir(valor: Any) -> str:
    """${VAR} do ambiente e ~ da pasta do usuário, para a configuração servir em qualquer máquina."""
    return os.path.expanduser(os.path.expandvars(str(valor)))


class McpClientManager:
    """Gerencia a instanciação, configuração e encerramento de conexões com servidores MCP."""

    def __init__(self):
        self._toolsets: Dict[str, McpToolset] = {}
        self._server_configs: Dict[str, Dict[str, Any]] = {}
        self._diagnostico: Dict[str, Dict[str, Any]] = {}
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
            cmd_expandido = _expandir(comando)
            args = [_expandir(a) for a in cfg.get("args", [])]
            raw_env = cfg.get("env", {})
            # Ambiente mínimo (PATH, HOME, SHELL...) como no SDK do MCP: GEMINI_API_KEY,
            # JARVIS_TOKEN e demais segredos só chegam ao servidor se declarados em "env".
            # "inherit_env" herda a sessão do usuário, ainda sem as credenciais do JARVIS.
            env_vars = ambiente_sem_segredos() if cfg.get("inherit_env") else get_default_environment()
            for k, v in raw_env.items():
                env_vars[k] = os.path.expandvars(str(v))

            server_params = StdioServerParameters(
                command=cmd_expandido,
                args=args,
                env=env_vars,
                cwd=_expandir(cfg["cwd"]) if cfg.get("cwd") else None,
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

    @staticmethod
    def _nome_exposto(cfg: Dict[str, Any], nome_ferramenta: str) -> str:
        """Nome que o agente vê: o ADK prefixa com '<tool_name_prefix>_'."""
        prefixo = cfg.get("tool_name_prefix")
        return f"{prefixo}_{nome_ferramenta}" if prefixo else nome_ferramenta

    @staticmethod
    def _registrar_politica(servidor: str, nome: str, risco: RiskLevel) -> bool:
        """Registra a política de uma ferramenta MCP sem tocar nas ferramentas do JARVIS.

        Um mcp_servers.json não pode rebaixar, por exemplo, antigravity_run_prompt para READ.
        """
        atual = policy_engine.get_risk_level(nome)
        if atual is not None and nome not in _POLITICAS_DE_ORIGEM_MCP:
            logger.warning(
                "Servidor MCP '%s': '%s' tem o nome de uma ferramenta do JARVIS; a política do JARVIS (%s) prevalece.",
                servidor, nome, atual.value,
            )
            return False
        policy_engine.register_tool_policy(nome, risco)
        _POLITICAS_DE_ORIGEM_MCP.add(nome)
        return True

    def _registrar_politicas_de_risco(self, nome: str, cfg: Dict[str, Any]):
        """Registra níveis de risco das ferramentas do MCP no PolicyEngine."""
        politicas = cfg.get("policies", {}) or {}

        # Políticas explícitas por ferramenta
        for tool_name, risk_str in politicas.items():
            try:
                risk_enum = RiskLevel(str(risk_str).upper())
            except ValueError:
                logger.warning("Nível de risco inválido para ferramenta MCP '%s': %s", tool_name, risk_str)
                continue
            self._registrar_politica(nome, self._nome_exposto(cfg, tool_name), risk_enum)

        # default_risk_level para as ferramentas do tool_filter; as demais recebem o padrão
        # quando a verificação de conexão descobre seus nomes (descobrir_ferramentas)
        default_risk_str = cfg.get("default_risk_level")
        tool_filter = cfg.get("tool_filter", [])
        if default_risk_str and isinstance(tool_filter, list):
            try:
                default_risk = RiskLevel(default_risk_str.upper())
            except ValueError:
                logger.warning("default_risk_level inválido no servidor MCP '%s': %s", nome, default_risk_str)
                return
            for tool_name in tool_filter:
                if tool_name not in politicas:
                    self._registrar_politica(nome, self._nome_exposto(cfg, tool_name), default_risk)

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

    async def descobrir_ferramentas(self, timeout_s: float = 30.0) -> Dict[str, Dict[str, Any]]:
        """Conecta a cada servidor ativo, lista suas ferramentas e aplica o default_risk_level.

        Usa um toolset temporário, aberto e fechado nesta mesma tarefa, para não prender a
        sessão MCP dos agentes à tarefa de inicialização. Ferramentas sem política continuam
        bloqueadas (fail-closed) e aparecem em "sem_politica" no status.
        """
        for nome, cfg in list(self._server_configs.items()):
            if nome.strip().lower() == "jarvis" or cfg.get("disabled", False):
                continue
            toolset = None
            try:
                toolset = self._criar_toolset_individual(nome, cfg)
                if toolset is None:
                    continue
                ferramentas = await asyncio.wait_for(toolset.get_tools(), timeout=timeout_s)
                nomes = [self._nome_exposto(cfg, t.name) for t in ferramentas]
                padrao = cfg.get("default_risk_level")
                if padrao:
                    try:
                        risco = RiskLevel(str(padrao).upper())
                        for n in nomes:
                            if policy_engine.get_risk_level(n) is None:
                                self._registrar_politica(nome, n, risco)
                    except ValueError:
                        logger.warning("default_risk_level inválido no servidor MCP '%s': %s", nome, padrao)
                sem_politica = [n for n in nomes if policy_engine.get_risk_level(n) is None]
                self._diagnostico[nome] = {"conectado": True, "ferramentas": nomes, "sem_politica": sem_politica, "erro": None}
                if sem_politica:
                    logger.warning(
                        "Servidor MCP '%s': ferramentas sem política ficam bloqueadas: %s. Defina 'policies' ou 'default_risk_level'.",
                        nome, ", ".join(sem_politica),
                    )
                logger.info("Servidor MCP '%s' conectado com %d ferramenta(s).", nome, len(nomes))
            except Exception as e:
                erro = str(e) or type(e).__name__
                self._diagnostico[nome] = {"conectado": False, "ferramentas": [], "sem_politica": [], "erro": erro[:200]}
                logger.warning("Servidor MCP '%s' não respondeu: %s", nome, erro)
            finally:
                if toolset is not None:
                    try:
                        await toolset.close()
                    except Exception:
                        pass
        return dict(self._diagnostico)

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

    def status(self) -> List[Dict[str, Any]]:
        """Retorna o status de todos os servidores MCP configurados."""
        res = []
        for nome, cfg in self._server_configs.items():
            if nome.lower() == "jarvis":
                continue
            tipo = "remote" if (cfg.get("url") or cfg.get("serverUrl")) else "stdio"
            ativo = nome in self._toolsets and not cfg.get("disabled", False)
            diagnostico = self._diagnostico.get(nome, {})
            res.append({
                "nome": nome,
                "tipo": tipo,
                "ativo": ativo,
                # None = ainda não verificado; False traz o motivo em "erro"
                "conectado": diagnostico.get("conectado"),
                "ferramentas": diagnostico.get("ferramentas", []),
                "sem_politica": diagnostico.get("sem_politica", []),
                "erro": diagnostico.get("erro"),
                "detalhes": cfg.get("command") or cfg.get("url") or cfg.get("serverUrl"),
                "tool_filter": cfg.get("tool_filter", []),
            })
        return res


# Singleton oficial do gerenciador de clientes MCP
mcp_client_manager = McpClientManager()
