"""
Serviço de Memória Compartilhada do J.A.R.V.I.S. (jarvis.core.memory)
Re-exporta e gerencia o serviço de memória persistente entre sessões.
"""

from agentes.memoria import (
    JarvisMemoryService,
    CAMINHO_PADRAO_MEMORIA,
    MAX_EVENTOS_POR_SESSAO,
)

__all__ = ["JarvisMemoryService", "CAMINHO_PADRAO_MEMORIA", "MAX_EVENTOS_POR_SESSAO"]
