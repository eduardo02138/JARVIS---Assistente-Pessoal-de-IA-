"""Agente de Uso de Computador (Computer Use) para o J.A.R.V.I.S.

Agente dedicado que opera o navegador Chromium via Playwright sob o modelo
"gemini-2.5-computer-use-preview". E um agente single-tool (ComputerUseToolset),
por isso nao espalha as ferramentas do ecossistema.

Governanca: nenhuma tool do toolset executa sem o "Modo Computador" ativo.
O usuario ativa o modo pelo endpoint /api/computer/mode (ou confirmacao de voz),
o PolicyEngine concede uma lease por sessao e o guarda_computador libera o
browser enquanto a lease valer. As ferramentas do navegador nunca contaminam
os agentes normais.
"""

import logging
import os
from typing import Any, Optional

from google.adk.agents import Agent
from google.adk.tools import ToolContext
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.computer_use.computer_use_toolset import ComputerUseToolset

from browser_journeys import browser_policy_guard
from policy_engine import policy_engine
from ..contexto import identidade_da_sessao
from .playwright_computer import PlaywrightComputer

logger = logging.getLogger("jarvis.computer_use")

MODELO_COMPUTER = os.environ.get("COMPUTER_USE_MODEL", "gemini-3.6-flash")

INSTRUCAO_COMPUTADOR = """Você é o J.A.R.V.I.S. operando o navegador do computador do usuário.

Regras:
- Responda em português do Brasil, de forma curta e objetiva (1 a 2 frases).
- Use as ferramentas de navegação/teclado clique a clique, sempre conferindo a tela.
- Nunca afirme ter feito algo que a ferramenta não confirmou.
- Se uma ferramenta responder 'bloqueado', explique que o Modo Computador precisa ser ativado.
"""


def guarda_computador(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
) -> Optional[dict]:
    """Exige lease ativa do Modo Computador e aplica governança de ações sensíveis/DOM."""
    # Sem sessão identificável não há lease possível: bloqueia (fail-closed)
    session_id, _ = identidade_da_sessao(tool_context, padrao="")
    if not (session_id and policy_engine.is_computer_lease_active(session_id)):
        return {
            "status": "bloqueado",
            "motivo": (
                "Modo Computador inativo: ative-o antes de controlar o navegador "
                "(endpoint /api/computer/mode ou confirme por voz)."
            ),
            "mensagem": (
                "O controle do navegador exige o Modo Computador ativo. "
                "Ative o Modo Computador com o comando 'ativar modo computador'."
            ),
        }

    tool_name = getattr(tool, "name", getattr(tool, "__name__", "browser_action"))
    eval_res = browser_policy_guard.evaluate_browser_action(
        session_id=session_id,
        action_name=tool_name,
        args=args or {},
        from_page_dom_context=bool((args or {}).get("from_page_dom_context", False)),
        dom_snippet=(args or {}).get("dom_snippet"),
    )
    if not eval_res.allowed:
        if eval_res.requires_human_confirmation:
            return {
                "status": "aguardando_confirmacao",
                "id_confirmacao": eval_res.confirmation_id,
                "categoria": eval_res.category,
                "motivo": eval_res.reason,
                "mensagem": eval_res.reason,
            }
        return {
            "status": "bloqueado",
            "categoria": eval_res.category,
            "motivo": eval_res.reason,
            "mensagem": eval_res.reason,
        }

    return None


def criar_agente_computer_use(modelo: Optional[str] = None) -> Agent:
    """Agente single-tool: ComputerUseToolset sobre Playwright local."""
    toolset = ComputerUseToolset(computer=PlaywrightComputer())
    return Agent(
        name="computador",
        model=modelo or MODELO_COMPUTER,
        description="Opera o navegador do computador do usuário (Chromium) para concluir tarefas na web.",
        instruction=INSTRUCAO_COMPUTADOR,
        tools=[toolset],
        before_tool_callback=guarda_computador,
    )