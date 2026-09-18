"""Assistente único, com dois cérebros internos.

Para o usuário existe um assistente só. Por dentro há dois caminhos:

- caminho rápido: ferramentas diretas no próprio agente (hora, status, abrir site,
  pesquisa). Sem delegação, sem salto extra de modelo.
- caminho complexo: sub-agentes especialistas chamados por AgentTool, com guarda de
  risco e memória de preferências.

Na voz os dois vivem no mesmo agente: trocar de agente no meio de uma sessão Live
significaria derrubar a conexão bidirecional, então o coordenador carrega as
ferramentas rápidas e só delega quando a tarefa pede.
No texto, o roteador escolhe o agente básico quando o pedido é trivial, o que evita
o custo de um coordenador com muitas ferramentas no schema.
"""

import os
from typing import Any, Optional

from google.adk.agents import Agent
from google.adk.tools import ToolContext
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.base_tool import BaseTool

from .ferramentas import (
    abrir_site,
    consultar_preferencias,
    hora_atual,
    lembrar_preferencia,
    pesquisar_na_web,
    status_do_sistema,
)

# Modelos: Live e texto são famílias diferentes. Um modelo de texto comum não mantém
# a conexão bidirecional do run_live(), e um modelo Live não é usado em run_async().
MODELO_LIVE = os.environ.get("LIVE_MODEL_PRIMARY", "gemini-3.8-live")
MODELO_TEXTO = os.environ.get("TEXT_MODEL", "gemini-flash-latest")

# Ferramentas que saem da máquina ou mudam algo fora do assistente
FERRAMENTAS_DE_RISCO = {"abrir_site", "pesquisar_na_web"}

INSTRUCAO_BASE = """Você é um assistente pessoal em português do Brasil, falado e escrito.

Conversa:
- Uma ou duas frases curtas. Quem ouve não consegue reler.
- Não leia listas longas nem URLs inteiras em voz alta.
- Nunca afirme ter feito algo que a ferramenta não confirmou.

Ferramentas:
- Use as ferramentas diretas para hora, status do sistema, abrir sites e pesquisas.
- Ações de risco exigem autorização: pergunte, e com o "sim" do usuário chame
  autorizar_acao antes de repetir a ação.
- Use lembrar_preferencia quando o usuário disser uma preferência duradoura.
"""

INSTRUCAO_COORDENADOR = INSTRUCAO_BASE + """
Delegação:
- Use especialista_sistema para análises mais longas de hardware e preferências salvas.
- Use especialista_navegador para sequências de navegação com vários passos.
- Tarefas simples você mesmo resolve, sem delegar: delegar custa uma rodada a mais.
"""


def autorizar_acao(acao: str, tool_context: ToolContext) -> dict:
    """Registra que o usuário autorizou uma ação sensível.

    Args:
        acao: Nome da ferramenta autorizada, por exemplo "abrir_site".
    """
    autorizadas = set(tool_context.state.get("autorizadas", []))
    autorizadas.add(acao)
    tool_context.state["autorizadas"] = sorted(autorizadas)
    return {"status": "ok", "autorizadas": sorted(autorizadas)}


def guarda_de_ferramentas(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext
) -> Optional[dict]:
    """Bloqueia ferramentas de risco sem autorização registrada na sessão.

    Devolver um dicionário aqui substitui a execução: o ADK entrega este valor ao
    modelo como se fosse o resultado, e a ferramenta original não roda.
    """
    if tool.name not in FERRAMENTAS_DE_RISCO:
        return None
    if tool.name in set(tool_context.state.get("autorizadas", [])):
        return None
    return {
        "status": "bloqueado",
        "mensagem": (
            f"A ação '{tool.name}' precisa de autorização do usuário. "
            "Peça a confirmação e, quando ele concordar, chame autorizar_acao."
        ),
    }


def criar_agente_rapido(modelo: Optional[str] = None) -> Agent:
    """Caminho rápido: um agente, ferramentas diretas, nenhum salto extra."""
    return Agent(
        name="assistente_rapido",
        model=modelo or MODELO_TEXTO,
        description="Responde pedidos diretos: hora, status do sistema, abrir site e pesquisa.",
        instruction=INSTRUCAO_BASE,
        tools=[hora_atual, status_do_sistema, abrir_site, pesquisar_na_web, autorizar_acao],
        before_tool_callback=guarda_de_ferramentas,
    )


def criar_agente_coordenador(modelo: Optional[str] = None) -> Agent:
    """Caminho complexo: ferramentas rápidas mais dois especialistas por AgentTool."""
    especialista_sistema = Agent(
        name="especialista_sistema",
        model=MODELO_TEXTO,
        description="Analisa hardware, uso de CPU e memória, hora e preferências salvas.",
        instruction=(
            "Responda em uma frase curta, com o número mais relevante. "
            "Use as ferramentas em vez de estimar valores."
        ),
        tools=[hora_atual, status_do_sistema, consultar_preferencias],
    )

    especialista_navegador = Agent(
        name="especialista_navegador",
        model=MODELO_TEXTO,
        description="Executa sequências de navegação: abrir sites e pesquisas encadeadas.",
        instruction=(
            "Abra o que foi pedido e confirme em poucas palavras. "
            "Se a ferramenta responder 'bloqueado', explique que falta autorização."
        ),
        tools=[abrir_site, pesquisar_na_web],
        before_tool_callback=guarda_de_ferramentas,
    )

    return Agent(
        name="assistente",
        model=modelo or MODELO_TEXTO,
        description="Assistente com ferramentas diretas e especialistas para tarefas longas.",
        instruction=INSTRUCAO_COORDENADOR,
        tools=[
            hora_atual,
            status_do_sistema,
            abrir_site,
            pesquisar_na_web,
            autorizar_acao,
            lembrar_preferencia,
            AgentTool(agent=especialista_sistema),
            AgentTool(agent=especialista_navegador),
        ],
        before_tool_callback=guarda_de_ferramentas,
    )


def criar_agente_de_voz(modelo: Optional[str] = None) -> Agent:
    """Agente usado na sessão Live: um só, com os dois caminhos embutidos."""
    return criar_agente_coordenador(modelo or MODELO_LIVE)
