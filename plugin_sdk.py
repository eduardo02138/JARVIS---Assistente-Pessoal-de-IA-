"""
SDK de Plug-ins do J.A.R.V.I.S.
Inspirado na arquitetura modular do Project N.E.K.O.
Permite a criação e expansão de ferramentas em cenários de jogos,
casa inteligente, streaming e mídias sociais.
"""

from dataclasses import dataclass
from typing import Callable, Any, Optional

@dataclass
class ToolSpec:
    """Especificação de ferramenta registrada por um plug-in."""
    name: str
    description: str
    parameters: dict
    handler: Callable[..., Any]
    # Nível de risco para o Policy Engine: READ, LOW_WRITE, EXTERNAL_WRITE ou PRIVILEGED.
    # Sem esse campo a ferramenta é bloqueada pelo Policy Engine (fail-closed).
    risk_level: Optional[str] = None

@dataclass
class PluginMeta:
    """Metadados descritivos de um plug-in do JARVIS."""
    id: str
    name: str
    version: str = "1.0.0"
    author: str = "Stark Industries"
    category: str = "general"  # gaming, smart_home, streaming, social, general
    description: str = ""
    icon: str = "🔌"
    enabled: bool = True
    installed: bool = True

    def to_store_dict(self) -> dict:
        """Converte metadados canônicos para representação no catálogo da loja."""
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "category": self.category,
            "icon": self.icon,
            "description": self.description,
            "author": self.author,
            "installed": self.installed,
            "enabled": self.enabled,
        }

class JarvisPlugin:
    """Classe base abstrata para todos os plug-ins do ecossistema JARVIS."""
    
    meta: PluginMeta
    
    def __init__(self, meta: PluginMeta):
        self.meta = meta
        self._tools: list[ToolSpec] = []

    def register_tool(self, name: str, description: str, parameters: dict, handler: Callable[..., Any],
                      risk_level: Optional[str] = None):
        """Registra uma função como ferramenta exposta à IA (evita duplicações)."""
        for idx, existing in enumerate(self._tools):
            if existing.name == name:
                self._tools[idx] = ToolSpec(
                    name=name,
                    description=description,
                    parameters=parameters,
                    handler=handler,
                    risk_level=risk_level
                )
                return
        self._tools.append(ToolSpec(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            risk_level=risk_level
        ))

    def on_load(self):
        """Chamado quando o plug-in é carregado e ativado no sistema."""
        pass

    def on_unload(self):
        """Chamado quando o plug-in é desativado."""
        pass

    def get_tools(self) -> list[ToolSpec]:
        """Retorna as ferramentas registradas pelo plug-in."""
        return self._tools
