"""Configuração derivada do ambiente e utilitários compartilhados pelo backend."""

import asyncio
import json
import os
from typing import Set

from monitoring.logger import logger

# Voz e idioma da sessão Live (valem para /ws/live e /ws/live_adk)
VOZ = os.environ.get("VOICE_NAME") or os.environ.get("JARVIS_VOICE", "Charon")
IDIOMA = os.environ.get("LANGUAGE_CODE") or os.environ.get("JARVIS_LANGUAGE", "pt-BR")

# Clientes aceitos como locais: o TestClient do Starlette se apresenta como "testclient".
HOSTS_LOOPBACK = ("127.0.0.1", "::1", "localhost", "testclient")


def eh_loopback(host: str) -> bool:
    """True se a conexão vem da própria máquina."""
    return host in HOSTS_LOOPBACK


def env_flag(nome: str) -> bool:
    """Lê uma flag 0/1 do ambiente; tudo desligado sem a variável."""
    return os.environ.get(nome, "0").strip().lower() in ("1", "true", "yes", "on")


def env_json(nome: str, padrao=None):
    """Lê um valor JSON do ambiente; retorna o padrão se faltar ou for inválido."""
    bruto = os.environ.get(nome)
    if not bruto or not bruto.strip():
        return padrao
    try:
        return json.loads(bruto)
    except Exception:
        logger.warning("Valor inválido em %s (JSON esperado): ignorado.", nome)
        return padrao


def modelo_live_padrao() -> str:
    """Modelo do caminho Live nativo (GEMINI_MODEL), lido a cada uso."""
    return os.environ.get("GEMINI_MODEL", "gemini-3.8-live")


# Retenção de tarefas de background do servidor contra Garbage Collection (Python 3.14)
_server_background_tasks: Set[asyncio.Task] = set()


def agendar_tarefa_do_servidor(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _server_background_tasks.add(task)
    task.add_done_callback(_server_background_tasks.discard)
    return task
