"""
SDK de Plug-ins do J.A.R.V.I.S.
Inspirado na arquitetura modular do Project N.E.K.O.
Permite a criação e expansão de ferramentas em cenários de jogos,
casa inteligente, streaming e mídias sociais.
"""

import json
import os
import re
from dataclasses import dataclass
from typing import Callable, Any, Optional

# Cada plug-in declara seus metadados em plugins/<id>/plugin.json (fonte única): o
# gerenciador descobre os plug-ins lendo os manifestos, sem importar código, e o
# próprio plug-in monta o PluginMeta a partir do mesmo arquivo.
NOME_DO_MANIFESTO = "plugin.json"
CAMPOS_OBRIGATORIOS = ("id", "name", "version", "entry")
_ID_VALIDO = re.compile(r"^[a-z][a-z0-9_]*$")
_MODULO_VALIDO = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ManifestoInvalido(ValueError):
    """plugin.json ausente, ilegível ou fora do formato."""


def ler_manifesto(pasta_do_plugin: str) -> dict:
    """Lê e valida plugins/<id>/plugin.json (o id precisa coincidir com a pasta)."""
    caminho = os.path.join(pasta_do_plugin, NOME_DO_MANIFESTO)
    try:
        with open(caminho, encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
    except (OSError, ValueError) as erro:
        raise ManifestoInvalido(f"{NOME_DO_MANIFESTO} ilegível: {erro}") from erro
    if not isinstance(dados, dict):
        raise ManifestoInvalido(f"{NOME_DO_MANIFESTO} precisa ser um objeto JSON.")
    faltando = [campo for campo in CAMPOS_OBRIGATORIOS if not dados.get(campo)]
    if faltando:
        raise ManifestoInvalido(f"campos obrigatórios ausentes: {', '.join(faltando)}.")
    pasta = os.path.basename(os.path.abspath(pasta_do_plugin))
    if dados["id"] != pasta:
        raise ManifestoInvalido(f"id '{dados['id']}' difere da pasta '{pasta}'.")
    if not _ID_VALIDO.match(dados["id"]):
        raise ManifestoInvalido(f"id '{dados['id']}' inválido (minúsculas, dígitos e _).")
    if not _MODULO_VALIDO.match(str(dados["entry"])):
        raise ManifestoInvalido(f"entry '{dados['entry']}' precisa ser o nome de um módulo da pasta do plug-in.")
    return dados

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
    simulated: bool = False

    @classmethod
    def do_manifesto(cls, arquivo_do_modulo: str) -> "PluginMeta":
        """Metadados do plugin.json da pasta do módulo (use com __file__)."""
        dados = ler_manifesto(os.path.dirname(os.path.abspath(arquivo_do_modulo)))
        return cls(
            id=dados["id"],
            name=dados["name"],
            version=dados["version"],
            author=dados.get("author", "Stark Industries"),
            category=dados.get("category", "general"),
            description=dados.get("description", ""),
            icon=dados.get("icon", "🔌"),
            simulated=bool(dados.get("simulated", False)),
        )

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
