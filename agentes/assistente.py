"""Assistente único, com dois cérebros internos e governança unificada de segurança.

Para o usuário existe um assistente só. Por dentro há dois caminhos:
- caminho rápido: ferramentas diretas no próprio agente (hora, status, abrir site,
  pesquisa). Sem delegação, sem salto extra de modelo.
- caminho complexo: sub-agentes especialistas chamados por AgentTool, com guarda de
  risco e memória de preferências.

Segurança:
O ADK conecta-se diretamente ao PolicyEngine oficial do J.A.R.V.I.S. Ações de risco
(como abrir sites ou executar comandos) exigem autorização estritamente one-shot com
TTL e verificação de integridade dos argumentos. O LLM não possui nenhuma ferramenta
de auto-autorização: a aprovação só pode vir de uma ação legítima do usuário.
"""

import os
import logging
from typing import Any, Optional

logger = logging.getLogger("JARVIS_ASSISTENTE")

from google.adk.agents import Agent
from google.adk.tools import ToolContext, load_memory
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.base_tool import BaseTool

from policy_engine import policy_engine, RiskLevel
from .ferramentas import (
    abrir_site,
    consultar_preferencias,
    hora_atual,
    lembrar_preferencia,
    pesquisar_na_web,
    status_do_sistema,
    obter_todas_ferramentas_adk,
    obter_dicionario_ferramentas_adk,
)

# Modelos: Live e texto são famílias diferentes.
MODELO_LIVE = os.environ.get("LIVE_MODEL_PRIMARY", "gemini-3.8-live")
MODELO_LIVE_RESERVA = os.environ.get("LIVE_MODEL_FALLBACK", "gemini-2.5-flash-native-audio-latest")
MODELO_TEXTO = os.environ.get("TEXT_MODEL", "gemini-flash-latest")
MODELO_TEXTO_RESERVA = os.environ.get("TEXT_MODEL_FALLBACK", "gemini-2.5-flash")

INSTRUCAO_BASE = """Você é o J.A.R.V.I.S., assistente pessoal inteligente em português do Brasil, falado e escrito.

Conversa:
- Respostas concisas, de uma ou duas frases curtas. Quem ouve não consegue reler.
- Não leia listas longas nem URLs inteiras em voz alta.
- Nunca afirme ter feito algo que a ferramenta não confirmou.

Ferramentas e Governança:
- Você possui 56 ferramentas integradas do ecossistema: telemetria de hardware (GPU NVIDIA, CPU, RAM), controle de volume, janelas, modo IDE, inicialização de jogos Steam, automação residencial, Google Workspace, Deep Research, finanças e controle de periféricos.
- Use as ferramentas imediatamente quando o usuário solicitar informações ou ações do sistema.
- Ações de risco são bloqueadas automaticamente pelo Policy Engine. Se uma ferramenta retornar status bloqueado aguardando confirmação, peça autorização ao usuário de forma clara.
- Você NUNCA pode conceder a sua própria autorização; a confirmação precisa ser emitida pelo usuário.
- Use lembrar_preferencia quando o usuário disser uma preferência duradoura.
- Use load_memory para consultar informações, fatos e conversas passadas sempre que o usuário perguntar sobre algo discutido anteriormente.
"""

INSTRUCAO_COORDENADOR = INSTRUCAO_BASE + """
Delegação e Memória:
- Use load_memory para buscar fatos, preferências e conversas anteriores quando o usuário perguntar sobre o passado.
- Use especialista_sistema para análises mais longas de hardware, GPU e preferências salvas.
- Use especialista_navegador para sequências de navegação e buscas encadeadas.
- Tarefas diretas você mesmo resolve chamando as ferramentas do sistema, sem salto extra.
"""


def guarda_de_ferramentas(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext
) -> Optional[dict]:
    """Bloqueia ferramentas que exigem confirmação explícita do usuário.

    Reutiliza o PolicyEngine oficial do J.A.R.V.I.S. A autorização é estritamente
    one-shot com TTL e hash de argumentos: uma vez executada, a permissão é revogada.
    O LLM não pode se auto-autorizar.
    """
    if isinstance(tool, AgentTool):
        return None

    session_id = getattr(tool_context, "session_id", None) or "local"
    user_id = getattr(tool_context, "user_id", None) or getattr(tool_context, "usuario", None) or "local"
    decision = policy_engine.evaluate(tool.name, args, session_id=session_id, user_id=user_id)

    if not decision.allowed:
        return {
            "status": "negado_por_politica",
            "motivo": decision.reason,
        }

    if decision.requires_confirmation:
        # Verifica se há autorização one-shot aprovada pelo usuário para esta chamada
        if policy_engine.consume_authorization(tool.name, args, session_id=session_id, user_id=user_id):
            return None  # Autorizado e consumido!

        # Bloqueado: cria solicitação pendente com TTL de 60s com isolamento de sessão e usuário
        pending = policy_engine.create_pending_action(
            tool_name=tool.name,
            args=args,
            session_id=session_id,
            user_id=user_id,
            ttl=60.0
        )
        return {
            "status": "bloqueado_aguardando_confirmacao",
            "acao": tool.name,
            "id_confirmacao": pending.action_id,
            "argumentos": args,
            "motivo": decision.reason,
            "mensagem": (
                f"A ferramenta sensível '{tool.name}' foi bloqueada pelo Policy Engine aguardando aprovação explícita do usuário. "
                "Informe ao usuário a ação e solicite que ele confirme (digitando 'sim' ou autorizando na interface). "
                f"ID da pendência: {pending.action_id}."
            ),
        }

    return None


def _instrucoes_das_skills() -> str:
    """Bloco L2 das Skills ADK ativas, injetado no prompt do agente (progressive disclosure)."""
    try:
        from adk_skill_loader import adk_skill_loader
        return adk_skill_loader.bloco_de_instrucoes(apenas_ativas=True)
    except Exception:
        return ""


def _criar_skill_toolset():
    """Monta um SkillToolset ADK com as Skills ativas (game_companion).

    Habilita as duas ferramentas do ADK (no modo com registry do Google Cloud)
    e o carregamento das instruções de skill no agente. Se o Google Cloud
    Skill Registry não estiver configurado (sem GOOGLE_CLOUD_PROJECT), o
    toolset carrega apenas as skills locais ativas, sem custo extra de
    contexto das skills inativas.
    """
    try:
        from adk_skill_loader import adk_skill_loader
        from google.adk.tools.skill_toolset import SkillToolset

        adk_skill_loader.carregar()
        ativas = [
            skill
            for name, skill in adk_skill_loader.skills_carregadas().items()
            if name in set(adk_skill_loader.ids_ativos())
        ]
        if not ativas:
            return None

        registry = None
        if os.environ.get("GOOGLE_CLOUD_PROJECT"):
            try:
                from google.adk.integrations.skill_registry import GCPSkillRegistry
                registry = GCPSkillRegistry(
                    project_id=os.environ.get("GOOGLE_CLOUD_PROJECT"),
                    location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
                )
            except Exception as e:
                logger.warning("Skill Registry GCP indisponível, usando skills locais: %s", e)

        return SkillToolset(skills=ativas, registry=registry)
    except Exception as e:
        logger.warning("SkillToolset indisponível (%s); fallback para bloco de instruções.", e)
        return None


def criar_agente_rapido(modelo: Optional[str] = None) -> Agent:
    """Caminho rápido: um agente, ferramentas diretas, nenhum salto extra."""
    todas_ferramentas = obter_todas_ferramentas_adk()
    skill_toolset = _criar_skill_toolset()
    ferramentas = list(todas_ferramentas) + ([] if skill_toolset is None else [skill_toolset])
    return Agent(
        name="assistente_rapido",
        model=modelo or MODELO_TEXTO,
        description="Responde pedidos diretos: GPU, telemetria, jogos, volume, hora, status, pesquisa e controle.",
        instruction=INSTRUCAO_BASE + ("" if skill_toolset is not None else _instrucoes_das_skills()),
        tools=[
            hora_atual,
            status_do_sistema,
            abrir_site,
            pesquisar_na_web,
            load_memory,
            *ferramentas,
        ],
        before_tool_callback=guarda_de_ferramentas,
    )


def criar_agente_coordenador(modelo: Optional[str] = None) -> Agent:
    """Caminho complexo: catálogo completo de ferramentas mais dois especialistas por AgentTool."""
    ferramentas_map = obter_dicionario_ferramentas_adk()
    todas_ferramentas = list(ferramentas_map.values())

    tools_especialista_sistema = [hora_atual, status_do_sistema, consultar_preferencias]
    for nome in ("get_gpu_status", "adjust_volume", "toggle_telemetry_overlay", "list_installed_games", "list_open_windows"):
        if nome in ferramentas_map:
            tools_especialista_sistema.append(ferramentas_map[nome])

    especialista_sistema = Agent(
        name="especialista_sistema",
        model=MODELO_TEXTO,
        description="Analisa hardware, GPU NVIDIA, uso de CPU e memória, telemetria, hora e preferências salvas.",
        instruction=(
            "Responda em uma frase curta, com o número mais relevante. "
            "Use as ferramentas em vez de estimar valores."
        ),
        tools=tools_especialista_sistema,
        before_tool_callback=guarda_de_ferramentas,
    )

    tools_especialista_navegador = [abrir_site, pesquisar_na_web]
    for nome in ("open_website", "search_web"):
        if nome in ferramentas_map:
            tools_especialista_navegador.append(ferramentas_map[nome])

    especialista_navegador = Agent(
        name="especialista_navegador",
        model=MODELO_TEXTO,
        description="Executa sequências de navegação: abrir sites e pesquisas encadeadas.",
        instruction=(
            "Abra o que foi pedido e confirme em poucas palavras. "
            "Se a ferramenta responder bloqueada, explique que falta autorização do usuário."
        ),
        tools=tools_especialista_navegador,
        before_tool_callback=guarda_de_ferramentas,
    )

    skill_toolset = _criar_skill_toolset()
    return Agent(
        name="assistente",
        model=modelo or MODELO_TEXTO,
        description="Assistente J.A.R.V.I.S. com 56 ferramentas do sistema, plug-ins e especialistas.",
        instruction=INSTRUCAO_COORDENADOR + ("" if skill_toolset is not None else _instrucoes_das_skills()),
        tools=[
            hora_atual,
            status_do_sistema,
            abrir_site,
            pesquisar_na_web,
            lembrar_preferencia,
            consultar_preferencias,
            load_memory,
            *todas_ferramentas,
            AgentTool(agent=especialista_sistema),
            AgentTool(agent=especialista_navegador),
        ] + ([] if skill_toolset is None else [skill_toolset]),
        before_tool_callback=guarda_de_ferramentas,
    )


def criar_agente_de_voz(modelo: Optional[str] = None) -> Agent:
    """Agente usado na sessão Live: um só, com os dois caminhos embutidos."""
    return criar_agente_coordenador(modelo or MODELO_LIVE)
