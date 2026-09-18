"""Ferramentas compartilhadas pelos agentes.

O ADK lê a assinatura e a docstring de cada função para montar o schema enviado ao
modelo, então os tipos e a descrição aqui são parte da interface com o Gemini.
"""

import datetime
import shutil
import subprocess
import urllib.parse

import psutil
from google.adk.tools import ToolContext


def hora_atual() -> dict:
    """Retorna a data e a hora atuais do computador do usuário."""
    agora = datetime.datetime.now()
    return {
        "status": "ok",
        "data": agora.strftime("%d/%m/%Y"),
        "hora": agora.strftime("%H:%M"),
        "dia_da_semana": agora.strftime("%A"),
    }


def status_do_sistema() -> dict:
    """Informa uso de CPU, memória RAM e tempo ligado da máquina."""
    memoria = psutil.virtual_memory()
    inicializacao = datetime.datetime.fromtimestamp(psutil.boot_time())
    ligado_ha = datetime.datetime.now() - inicializacao
    return {
        "status": "ok",
        "cpu_percentual": psutil.cpu_percent(interval=0.3),
        "cpu_nucleos": psutil.cpu_count(logical=True),
        "ram_percentual": memoria.percent,
        "ram_usada_gb": round(memoria.used / 1024**3, 1),
        "ram_total_gb": round(memoria.total / 1024**3, 1),
        "ligado_ha_horas": round(ligado_ha.total_seconds() / 3600, 1),
    }


def abrir_site(url: str) -> dict:
    """Abre um endereço da web no navegador padrão do usuário.

    Args:
        url: Endereço completo ou domínio, por exemplo "youtube.com".
    """
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    if not shutil.which("xdg-open"):
        return {"status": "erro", "mensagem": "xdg-open não está disponível neste sistema."}
    subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return {"status": "ok", "url": url, "mensagem": f"Abri {url} no navegador."}


def pesquisar_na_web(consulta: str) -> dict:
    """Abre uma busca no navegador do usuário.

    Args:
        consulta: O que pesquisar, em linguagem natural.
    """
    return abrir_site(f"https://www.google.com/search?q={urllib.parse.quote(consulta)}")


def lembrar_preferencia(chave: str, valor: str, tool_context: ToolContext) -> dict:
    """Guarda uma preferência do usuário na sessão (ex.: navegador favorito).

    Args:
        chave: Nome curto da preferência, como "navegador" ou "cidade".
        valor: Valor a guardar.
    """
    # O prefixo "user:" faz o ADK manter o valor entre sessões do mesmo usuário
    tool_context.state[f"user:{chave}"] = valor
    return {"status": "ok", "chave": chave, "valor": valor}


def consultar_preferencias(tool_context: ToolContext) -> dict:
    """Lista as preferências já guardadas sobre o usuário."""
    guardadas = {
        chave.removeprefix("user:"): valor
        for chave, valor in tool_context.state.to_dict().items()
        if chave.startswith("user:")
    }
    return {"status": "ok", "preferencias": guardadas, "total": len(guardadas)}


import functools
import inspect
from typing import Callable, Any
from google.adk.tools import FunctionTool, BaseTool


def obter_todas_ferramentas_adk() -> list[BaseTool]:
    """Retorna todas as 56 ferramentas do ecossistema JARVIS convertidas para o Google ADK.

    Garante que cada ferramenta preserve seu nome oficial, descrição completa
    e assinatura de parâmetros tipada, integrando-se perfeitamente ao Policy Engine.
    """
    import system_tools
    from plugin_manager import plugin_manager

    desc_map = {
        d["name"]: d.get("description", "")
        for d in getattr(system_tools, "GEMINI_FUNCTION_DECLARATIONS", [])
    }

    all_specs: dict[str, tuple[Callable[..., Any], str]] = {}

    # Coleta todas as ferramentas de plug-ins ativos cadastrados (jogos, workspace, pesquisa, finanças, etc.)
    for t in plugin_manager.get_active_tools():
        all_specs[t.name] = (t.handler, t.description)

    # Coleta todas as ferramentas base do sistema (hardware, controle, áudio, janelas, antigravity)
    for name, fn in system_tools.BASE_TOOL_REGISTRY.items():
        if name not in all_specs:
            doc = desc_map.get(name, getattr(fn, "__doc__", "") or f"Ferramenta de sistema {name}.")
            all_specs[name] = (fn, doc)

    adk_tools: list[BaseTool] = []

    def _criar_wrapper(handler_fn: Callable[..., Any], tool_name: str, docstring: str, signature: inspect.Signature):
        @functools.wraps(handler_fn)
        def _tool_wrapper(*args, **kwargs):
            return handler_fn(*args, **kwargs)

        _tool_wrapper.__name__ = tool_name
        _tool_wrapper.__doc__ = docstring
        _tool_wrapper.__signature__ = signature
        return _tool_wrapper

    for name, (handler, description) in all_specs.items():
        sig = inspect.signature(handler)
        doc = description or getattr(handler, "__doc__", "") or f"Ferramenta {name}."
        wrapped = _criar_wrapper(handler, name, doc, sig)
        tool_obj = FunctionTool(wrapped)
        adk_tools.append(tool_obj)

    return adk_tools


def obter_dicionario_ferramentas_adk() -> dict[str, BaseTool]:
    """Retorna um dicionário mapeando nome -> BaseTool para rápida indexação."""
    return {getattr(t, "name", ""): t for t in obter_todas_ferramentas_adk()}
