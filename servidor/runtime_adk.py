"""Runtime do Google ADK: sessões, memória de longo prazo, runners e rotação de chaves."""

import os
from typing import Optional

from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps.app import App, EventsCompactionConfig
from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService, InMemorySessionService

from agentes.assistente import criar_agente_coordenador, criar_agente_de_voz, criar_agente_rapido
from agentes.computer_use.agente import MODELO_COMPUTER, criar_agente_computer_use
from agentes.memoria import JarvisMemoryService
from agentes.roteador import CAMINHO_COMPLEXO
from monitoring.logger import logger
from provider_router import GoogleStudioProvider

# Caminho interno do agente de Computer Use (navegador Chromium via Playwright)
CAMINHO_COMPUTADOR = "computador"
CAMINHO_VOZ = "voz"


def _criar_session_service_adk() -> BaseSessionService:
    url_banco = os.environ.get("SESSION_DB_URL", "sqlite+aiosqlite:///sessoes.db")
    if url_banco.strip().lower() in {"", "memoria", "memory", "none"}:
        return InMemorySessionService()
    try:
        from google.adk.sessions import DatabaseSessionService
        return DatabaseSessionService(db_url=url_banco)
    except Exception as err:
        logger.warning("Falha ao inicializar DatabaseSessionService (%s); usando InMemorySessionService.", err)
        return InMemorySessionService()

session_service_adk = _criar_session_service_adk()
memory_service_adk = JarvisMemoryService()
runners_adk: dict[str, Runner] = {}

_indice_chave_adk = 0


def girar_chave_adk() -> bool:
    global _indice_chave_adk
    key_pool = GoogleStudioProvider.get_keys()
    if len(key_pool) < 2:
        return False
    _indice_chave_adk = (_indice_chave_adk + 1) % len(key_pool)
    nova_chave = key_pool[_indice_chave_adk]
    os.environ["GOOGLE_API_KEY"] = nova_chave
    os.environ["GEMINI_API_KEY"] = nova_chave
    runners_adk.clear()
    logger.warning("Cota ADK atingida: rotacionando para chave %d/%d.", _indice_chave_adk + 1, len(key_pool))
    return True


def trocar_modelo(runner: Runner, modelo: str) -> None:
    """Plano B de indisponibilidade: troca o modelo do agente e dos sub-agentes."""
    runner.agent.model = modelo
    for ferramenta in getattr(runner.agent, "tools", []):
        subagente = getattr(ferramenta, "agent", None)
        if subagente is not None:
            subagente.model = modelo


def obter_runner_adk(tipo: str, modelo: Optional[str] = None) -> Runner:
    """Runner em cache por caminho; com `modelo`, um runner separado com esse modelo (reserva).

    O runner padrão nunca é alterado por uma falha de um pedido: o modelo reserva vale
    só para quem o pediu, sem afetar as outras sessões que dividem o runner.
    """
    if tipo == CAMINHO_COMPLEXO:
        tipo = "coordenador"
    chave = f"{tipo}@{modelo}" if modelo else tipo
    if chave not in runners_adk:
        if tipo == "rapido":
            agente = criar_agente_rapido(modelo)
        elif tipo == "coordenador":
            agente = criar_agente_coordenador(modelo)
        elif tipo == CAMINHO_VOZ:
            agente = criar_agente_de_voz(modelo)
        elif tipo == CAMINHO_COMPUTADOR:
            agente = criar_agente_computer_use(modelo or MODELO_COMPUTER)
        else:
            raise ValueError(f"Tipo de runner desconhecido: {tipo}")

        compaction_config = EventsCompactionConfig(
            token_threshold=int(os.environ.get("COMPACTION_TOKEN_THRESHOLD", 4000)),
            event_retention_size=int(os.environ.get("COMPACTION_RETENTION_SIZE", 5)),
            compaction_interval=int(os.environ.get("COMPACTION_INTERVAL", 10)),
            overlap_size=int(os.environ.get("COMPACTION_OVERLAP_SIZE", 2)),
        )
        cache_config = ContextCacheConfig(
            min_tokens=int(os.environ.get("CONTEXT_CACHE_MIN_TOKENS", 2048)),
            ttl_seconds=int(os.environ.get("CONTEXT_CACHE_TTL_SECONDS", 600)),
            cache_intervals=int(os.environ.get("CONTEXT_CACHE_INTERVALS", 5)),
        )
        app_obj = App(
            name="assistente",
            root_agent=agente,
            events_compaction_config=compaction_config,
            context_cache_config=cache_config,
        )
        runners_adk[chave] = Runner(
            app=app_obj,
            session_service=session_service_adk,
            memory_service=memory_service_adk,
        )
    return runners_adk[chave]

# Alias canônico: a mesma fábrica unificada sob o nome usado pelo servidor ADK antigo.
obter_runner = obter_runner_adk
