"""Backend do J.A.R.V.I.S., dividido por responsabilidade.

Importar o pacote carrega o .env da raiz do projeto antes de qualquer submódulo:
vários módulos (agentes, provedores e os deste pacote) leem o ambiente no import.

- comum.py          configuração derivada do ambiente e utilitários compartilhados
- seguranca.py      token, sessões emitidas, autenticação e liberação de leases
- runtime_adk.py    sessões, memória, runners e rotação de chaves do Google ADK
- instrucoes.py     instrução de sistema da sessão Gemini Live nativa
- rotas_sistema.py  saúde, provedores, plug-ins, depuração e preferências
- rotas_agente.py   chat ADK, confirmações pendentes e Modo Computador
- live_adk.py       WebSocket /ws/live_adk (sessão Live pelo Google ADK)
- live_nativo.py    WebSocket /ws/live (Gemini Live nativo) e injeção de prompts

server.py (raiz) monta a aplicação FastAPI a partir desses routers.
"""

import os

from dotenv import load_dotenv

RAIZ_PROJETO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(RAIZ_PROJETO, ".env")
load_dotenv(ENV_PATH, override=True)


def porta_do_servidor() -> int:
    """Porta HTTP configurada (PORT no .env; padrão 8000)."""
    try:
        return int(os.environ.get("PORT", "8000"))
    except ValueError:
        return 8000


def host_local_do_servidor() -> str:
    """Endereço que clientes desta máquina usam para falar com o servidor.

    Servidor ouvindo em todas as interfaces (0.0.0.0 / ::) é alcançado pelo loopback.
    """
    host = os.environ.get("JARVIS_HOST", "127.0.0.1").strip()
    return "127.0.0.1" if host in ("", "0.0.0.0", "::", "[::]") else host


def url_local_do_servidor() -> str:
    """URL base do servidor para o widget, o servidor MCP, a ponte gemini e o monitor."""
    host = host_local_do_servidor()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"  # IPv6 literal
    return f"http://{host}:{porta_do_servidor()}"
