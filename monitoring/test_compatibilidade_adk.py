"""Compatibilidade com o Google ADK 2.x (notas de migração 1.x -> 2.0).

- A memória do JARVIS atende Context.add_memory, que o ADK 2.x expõe às ferramentas
  e aos callbacks (a classe base levantava NotImplementedError).
- Guardas estáticas das regras de migração, que falham em silêncio no runtime:
  overrides de execução do agente são ignorados (BaseAgent virou BaseNode), eventos
  não podem ser anexados à sessão na mão, BaseException não pode ser engolida
  (prende NodeInterruptedError e o cancelamento) e o Event descarta campos
  desconhecidos sem avisar.
"""

import ast
import asyncio
import os

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.memory.memory_entry import MemoryEntry
from google.adk.sessions import InMemorySessionService
from google.adk.tools.tool_context import ToolContext
from google.genai.types import Content, Part

from agentes.memoria import SESSAO_DE_MEMORIAS_EXPLICITAS, JarvisMemoryService

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _contexto_de_ferramenta(memoria: JarvisMemoryService) -> ToolContext:
    async def criar():
        servico = InMemorySessionService()
        sessao = await servico.create_session(app_name="assistente", user_id="usuario-memoria", session_id="s1")
        return ToolContext(InvocationContext(invocation_id="inv-memoria", session_service=servico,
                                             session=sessao, memory_service=memoria))
    return asyncio.run(criar())


def _memoria(texto: str, id_memoria: str = "") -> MemoryEntry:
    return MemoryEntry(content=Content(parts=[Part(text=texto)]), id=id_memoria or None,
                       timestamp="2026-09-24T10:30:00")


def _buscar(memoria: JarvisMemoryService, consulta: str) -> list[str]:
    resposta = asyncio.run(memoria.search_memory(app_name="assistente", user_id="usuario-memoria", query=consulta))
    return [p.text for m in resposta.memories for p in m.content.parts if p.text]


def test_context_add_memory_do_adk_grava_busca_e_persiste(tmp_path):
    arquivo = tmp_path / "memoria.json"
    contexto = _contexto_de_ferramenta(JarvisMemoryService(str(arquivo)))
    asyncio.run(contexto.add_memory(memories=[_memoria("O senhor prefere café sem açúcar", "pref-cafe")]))

    assert "O senhor prefere café sem açúcar" in _buscar(contexto._invocation_context.memory_service, "café")
    reaberta = JarvisMemoryService(str(arquivo))
    assert "O senhor prefere café sem açúcar" in _buscar(reaberta, "café"), "a memória explícita sobrevive ao reinício"


def test_memoria_explicita_com_mesmo_id_nao_duplica_nem_depois_de_reiniciar(tmp_path):
    arquivo = str(tmp_path / "memoria.json")
    primeira = JarvisMemoryService(arquivo)
    entrada = _memoria("Aniversário da Ana é 12 de março", "aniversario-ana")
    asyncio.run(primeira.add_memory(app_name="assistente", user_id="usuario-memoria", memories=[entrada]))
    asyncio.run(primeira.add_memory(app_name="assistente", user_id="usuario-memoria", memories=[entrada]))

    reaberta = JarvisMemoryService(arquivo)
    asyncio.run(reaberta.add_memory(app_name="assistente", user_id="usuario-memoria", memories=[entrada]))
    eventos = reaberta._session_events[("assistente", "usuario-memoria")][SESSAO_DE_MEMORIAS_EXPLICITAS]
    assert len(eventos) == 1
    assert eventos[0].timestamp == pytest.approx(__import__("datetime").datetime(2026, 9, 24, 10, 30).timestamp())


def test_memoria_vazia_e_ignorada(tmp_path):
    memoria = JarvisMemoryService(str(tmp_path / "memoria.json"))
    asyncio.run(memoria.add_memory(app_name="assistente", user_id="u", memories=[MemoryEntry(content=Content(parts=[]))]))
    assert not os.path.exists(tmp_path / "memoria.json")


# ----------------- Guardas das regras de migração do ADK 2.0 -----------------

def _arquivos_de_producao():
    ignorar = {".venv", ".git", "__pycache__", "node_modules"}
    for pasta, subpastas, arquivos in os.walk(RAIZ):
        subpastas[:] = [s for s in subpastas if s not in ignorar]
        for nome in arquivos:
            if not nome.endswith(".py"):
                continue
            caminho = os.path.join(pasta, nome)
            relativo = os.path.relpath(caminho, RAIZ)
            if relativo.startswith("monitoring" + os.sep) and (nome.startswith("test_") or nome == "conftest.py"):
                continue
            yield relativo, caminho


def _arvores():
    for relativo, caminho in _arquivos_de_producao():
        with open(caminho, encoding="utf-8") as arquivo:
            yield relativo, ast.parse(arquivo.read(), filename=relativo)


def test_nenhum_agente_sobrescreve_a_execucao():
    """No 2.0 o BaseAgent é um BaseNode: _run_async_impl/_run_live_impl são ignorados em silêncio."""
    achados = [f"{rel}:{no.lineno} {no.name}" for rel, arvore in _arvores() for no in ast.walk(arvore)
               if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef))
               and no.name in ("_run_async_impl", "_run_live_impl")]
    assert achados == [], f"use BeforeAgentCallback/AfterAgentCallback: {achados}"


def test_eventos_nao_sao_anexados_na_sessao_na_mao():
    """O runner do 2.0 controla a emissão de eventos: nada de session.events.append/enqueue_event."""
    achados = []
    for rel, arvore in _arvores():
        for no in ast.walk(arvore):
            if not isinstance(no, ast.Call) or not isinstance(no.func, ast.Attribute):
                continue
            alvo = no.func
            if alvo.attr == "enqueue_event" or (
                    alvo.attr == "append" and isinstance(alvo.value, ast.Attribute) and alvo.value.attr == "events"):
                achados.append(f"{rel}:{no.lineno}")
    assert achados == [], f"emita o evento pelo framework (yield/append_event): {achados}"


def test_nenhum_except_engole_base_exception():
    """Capturar BaseException sem relançar prende NodeInterruptedError (pausa HITL) e o cancelamento."""
    achados = []
    for rel, arvore in _arvores():
        for no in ast.walk(arvore):
            if not isinstance(no, ast.ExceptHandler):
                continue
            tipos = no.type.elts if isinstance(no.type, ast.Tuple) else [no.type]
            amplo = no.type is None or any(isinstance(t, ast.Name) and t.id == "BaseException" for t in tipos)
            relanca = any(isinstance(n, ast.Raise) and n.exc is None for n in ast.walk(no))
            if amplo and not relanca:
                achados.append(f"{rel}:{no.lineno}")
    assert achados == [], f"capture Exception (ou relance): {achados}"


def test_event_so_recebe_campos_que_existem():
    """O Event do ADK ignora campos desconhecidos: um session_id=... sumia sem erro nenhum."""
    aceitos = set(Event.model_fields)
    aceitos |= {campo.alias for campo in Event.model_fields.values() if campo.alias}
    achados = []
    for rel, arvore in _arvores():
        for no in ast.walk(arvore):
            if not isinstance(no, ast.Call):
                continue
            nome = no.func.id if isinstance(no.func, ast.Name) else getattr(no.func, "attr", None)
            if nome != "Event":
                continue
            for argumento in no.keywords:
                if argumento.arg is not None and argumento.arg not in aceitos:
                    achados.append(f"{rel}:{no.lineno} {argumento.arg}=")
    assert achados == [], f"campos que o Event descarta: {achados}"
