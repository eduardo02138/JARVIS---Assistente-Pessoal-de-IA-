"""Identidade da invocação ADK usada pela governança das ferramentas."""

from typing import Any, Tuple


def identidade_da_sessao(tool_context: Any, padrao: str = "local") -> Tuple[str, str]:
    """(sessão, usuário) reais de quem pediu a ferramenta.

    O ToolContext do ADK não tem atributo session_id: a sessão fica em
    tool_context.session.id. Ler "session_id" direto caía sempre no padrão,
    e as confirmações do usuário, feitas pela sessão real, nunca encontravam
    a pendência criada pelo guarda.
    """
    sessao = (
        getattr(getattr(tool_context, "session", None), "id", None)
        or getattr(tool_context, "session_id", None)
        or padrao
    )
    usuario = getattr(tool_context, "user_id", None) or getattr(tool_context, "usuario", None) or sessao
    return sessao, usuario
