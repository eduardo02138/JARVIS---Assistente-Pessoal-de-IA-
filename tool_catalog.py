"""
Catálogo Unificado de Ferramentas do J.A.R.V.I.S. (ToolDefinition & ToolCatalog)
Autoridade única de registro de ferramentas, schemas, políticas de risco e adapters.

Elimina duplicações semânticas entre APIs (Gemini Native, Google ADK, FastMCP)
e centraliza contratos de execução e governança de segurança.
"""

import functools
import inspect
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from policy_engine import RiskLevel, policy_engine

logger = logging.getLogger("jarvis.tool_catalog")


@dataclass
class ToolDefinition:
    """
    Definição canônica de uma ferramenta no ecossistema J.A.R.V.I.S.
    
    Attributes:
        name: Nome canônico da ferramenta (ex: 'system_get_status').
        handler: Função Python executável correspondente.
        description: Descrição em linguagem natural para LLMs e documentação.
        parameters: Schema JSON / OpenAPI dos parâmetros da ferramenta.
        risk_level: Nível de risco no PolicyEngine (READ, LOW_WRITE, EXTERNAL_WRITE, PRIVILEGED).
        aliases: Nomes alternativos ou legados suportados (ex: ['get_system_status', 'status_do_sistema']).
        tags: Categorias da ferramenta (ex: ['system', 'telemetry']).
    """
    name: str
    handler: Callable[..., Any]
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.READ
    aliases: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    def to_gemini_declaration(self, as_name: Optional[str] = None) -> Dict[str, Any]:
        """Gera schema compatível com Gemini Native Function Calling."""
        params = self.parameters if self.parameters else {"type": "OBJECT", "properties": {}}
        return {
            "name": as_name or self.name,
            "description": self.description,
            "parameters": params,
        }

    def to_adk_tool(self, as_name: Optional[str] = None) -> Any:
        """Gera instância de FunctionTool para o Google ADK mantendo assinatura e docstring."""
        from google.adk.tools import FunctionTool

        target_name = as_name or self.name
        sig = inspect.signature(self.handler)
        doc = self.description or getattr(self.handler, "__doc__", "") or f"Ferramenta {target_name}."

        @functools.wraps(self.handler)
        def _adk_wrapper(*args, **kwargs):
            return self.handler(*args, **kwargs)

        _adk_wrapper.__name__ = target_name
        _adk_wrapper.__qualname__ = target_name
        _adk_wrapper.__doc__ = doc
        _adk_wrapper.__signature__ = sig
        return FunctionTool(_adk_wrapper)

    def to_mcp_wrapper(self, as_name: Optional[str] = None, prefix: str = "jarvis_") -> tuple[str, Callable[..., Any]]:
        """Gera tupla (nome_mcp, wrapper_fn) compatível com FastMCP / MCP Server."""
        nome_mcp = f"{prefix}{as_name or self.name}"
        sig = inspect.signature(self.handler)

        def _mcp_wrapper(**kwargs):
            try:
                resultado = self.handler(**kwargs)
                return json.dumps(resultado, indent=2, ensure_ascii=False, default=str)
            except Exception as e:
                return json.dumps({
                    "sucesso": False,
                    "ferramenta": as_name or self.name,
                    "erro": str(e),
                }, indent=2, ensure_ascii=False)

        _mcp_wrapper.__name__ = nome_mcp
        _mcp_wrapper.__qualname__ = nome_mcp
        _mcp_wrapper.__doc__ = self.description
        _mcp_wrapper.__signature__ = sig
        return nome_mcp, _mcp_wrapper


class ToolCatalog:
    """
    Catálogo unificado de ferramentas do J.A.R.V.I.S.
    Gerencia registro, resolução de aliases, adapters e sincronização de governança.
    """
    _instance: Optional["ToolCatalog"] = None

    def __init__(self):
        self._canonical: Dict[str, ToolDefinition] = {}
        self._alias_map: Dict[str, str] = {}

    @classmethod
    def get_instance(cls) -> "ToolCatalog":
        """Retorna o singleton do ToolCatalog."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register(self, tool: ToolDefinition) -> ToolDefinition:
        """
        Registra uma ferramenta canônica e seus aliases, sincronizando
        automaticamente com o PolicyEngine.
        """
        self._canonical[tool.name] = tool
        self._alias_map[tool.name] = tool.name
        for alias in tool.aliases:
            self._alias_map[alias] = tool.name

        # Sincroniza nível de risco com o PolicyEngine
        try:
            rl = tool.risk_level if isinstance(tool.risk_level, RiskLevel) else RiskLevel(str(tool.risk_level))
            policy_engine.register_tool_policy(tool.name, rl)
            for alias in tool.aliases:
                policy_engine.register_tool_policy(alias, rl)
        except Exception as e:
            logger.debug(f"Falha ao registrar política para '{tool.name}': {e}")

        return tool

    def register_tool_spec(self, spec: Any) -> ToolDefinition:
        """Registra uma ToolSpec de plug-in no catálogo."""
        rl = getattr(spec, "risk_level", None)
        risk_enum = RiskLevel(rl) if rl else RiskLevel.READ
        tool = ToolDefinition(
            name=spec.name,
            handler=spec.handler,
            description=spec.description,
            parameters=spec.parameters,
            risk_level=risk_enum,
            tags=["plugin"]
        )
        return self.register(tool)

    def unregister(self, name_or_alias: str) -> Optional[ToolDefinition]:
        """Remove uma ferramenta do catálogo e limpa seus aliases mapeados."""
        canonical_name = self._alias_map.get(name_or_alias, name_or_alias)
        tool = self._canonical.pop(canonical_name, None)
        if tool:
            self._alias_map.pop(tool.name, None)
            for alias in tool.aliases:
                self._alias_map.pop(alias, None)
        return tool

    def get(self, name_or_alias: str) -> Optional[ToolDefinition]:
        """Busca a definição canônica a partir de um nome ou alias."""
        canonical_name = self._alias_map.get(name_or_alias, name_or_alias)
        return self._canonical.get(canonical_name)

    def resolve_canonical_name(self, name_or_alias: str) -> str:
        """Retorna o nome canônico para um dado nome ou alias."""
        return self._alias_map.get(name_or_alias, name_or_alias)

    def execute(self, name_or_alias: str, *args, **kwargs) -> Any:
        """Executa a ferramenta referenciada por nome canônico ou alias."""
        tool = self.get(name_or_alias)
        if not tool:
            raise KeyError(f"Ferramenta '{name_or_alias}' não encontrada no catálogo.")
        return tool.handler(*args, **kwargs)

    def get_canonical_tools(self) -> List[ToolDefinition]:
        """Retorna a lista de todas as ferramentas canônicas registradas."""
        return list(self._canonical.values())

    def get_all_names(self, include_aliases: bool = True) -> List[str]:
        """Retorna todos os nomes de ferramentas (canônicos e aliases se especificado)."""
        if include_aliases:
            return list(self._alias_map.keys())
        return list(self._canonical.keys())

    def get_gemini_declarations(self, include_aliases: bool = False) -> List[Dict[str, Any]]:
        """Gera schemas Gemini Function Declarations para as ferramentas registradas."""
        decls = [t.to_gemini_declaration() for t in self._canonical.values()]
        if include_aliases:
            for alias, c_name in self._alias_map.items():
                if alias != c_name:
                    tool = self._canonical[c_name]
                    decls.append(tool.to_gemini_declaration(as_name=alias))
        return decls

    def get_adk_tools(self, include_aliases: bool = False) -> List[Any]:
        """Gera lista de FunctionTools para o Google ADK."""
        tools = [t.to_adk_tool() for t in self._canonical.values()]
        if include_aliases:
            for alias, c_name in self._alias_map.items():
                if alias != c_name:
                    tool = self._canonical[c_name]
                    tools.append(tool.to_adk_tool(as_name=alias))
        return tools

    def sync_with_policy_engine(self):
        """Assegura que todas as ferramentas e aliases estejam presentes no PolicyEngine."""
        for name, tool in self._canonical.items():
            rl = tool.risk_level if isinstance(tool.risk_level, RiskLevel) else RiskLevel(str(tool.risk_level))
            policy_engine.register_tool_policy(name, rl)
            for alias in tool.aliases:
                policy_engine.register_tool_policy(alias, rl)


# Singleton global
tool_catalog = ToolCatalog.get_instance()
