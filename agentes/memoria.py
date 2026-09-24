"""Serviço de Memória de Longo Prazo do J.A.R.V.I.S.

Implementa a interface BaseMemoryService do Google ADK com persistência local em JSON.
Permite ingestão contínua de sessões e busca semântica/palavras-chave entre conversas passadas.
"""

import asyncio
import datetime
import json
import logging
import os
import threading
from typing import Optional, Sequence

from google.adk.events import Event
from google.adk.memory import InMemoryMemoryService
from google.adk.memory.memory_entry import MemoryEntry
from google.genai.types import Content, Part

logger = logging.getLogger("jarvis.memoria")

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAMINHO_PADRAO_MEMORIA = os.environ.get(
    "JARVIS_MEMORY_FILE", os.path.join(RAIZ, "memoria.json")
)
MAX_EVENTOS_POR_SESSAO = int(os.environ.get("JARVIS_MEMORY_MAX_EVENTS_PER_SESSION", "100"))
# Memórias explícitas (Context.add_memory do ADK 2.x) ficam num grupo próprio de cada usuário
SESSAO_DE_MEMORIAS_EXPLICITAS = "memorias_explicitas"


class JarvisMemoryService(InMemoryMemoryService):
    """Serviço de memória de longo prazo com persistência local em arquivo JSON.

    Herda a correspondência e ranking de palavras-chave do InMemoryMemoryService,
    adicionando serialização segura em disco para sobreviver a reinicializações.
    """

    def __init__(self, caminho_arquivo: Optional[str] = None):
        super().__init__()
        self.caminho_arquivo = caminho_arquivo or CAMINHO_PADRAO_MEMORIA
        self._disk_lock = threading.Lock()
        self._carregar_do_disco()

    def _carregar_do_disco(self):
        """Restaura eventos salvos no arquivo JSON para a memória RAM."""
        if not os.path.exists(self.caminho_arquivo) or os.path.getsize(self.caminho_arquivo) == 0:
            return

        try:
            with open(self.caminho_arquivo, "r", encoding="utf-8") as f:
                dados = json.load(f)

            with self._lock:
                for key_str, sessoes in dados.items():
                    if "::" in key_str:
                        app_n, usr_id = key_str.split("::", 1)
                        user_key = (app_n, usr_id)
                    else:
                        user_key = (key_str, "local")

                    if user_key not in self._session_events:
                        self._session_events[user_key] = {}
                    for sess_id, lista_eventos in sessoes.items():
                        eventos_recuperados = []
                        for item in lista_eventos:
                            partes = [Part(text=p) for p in item.get("textos", [])]
                            content = Content(parts=partes) if partes else None
                            # O id salvo mantém a deduplicação do ADK entre reinícios
                            ev = Event(
                                author=item.get("author", "user"),
                                content=content,
                                **({"id": item["id"]} if item.get("id") else {}),
                            )
                            if "timestamp" in item:
                                ev.timestamp = item["timestamp"]
                            eventos_recuperados.append(ev)
                        self._session_events[user_key][sess_id] = eventos_recuperados

            logger.info(
                "Memória de longo prazo carregada de %s com sucesso.",
                self.caminho_arquivo,
            )
        except Exception as err:
            logger.error("Falha ao carregar memórias de %s: %s", self.caminho_arquivo, err)

    def _salvar_no_disco(self):
        """Persiste o dicionário atual de eventos no disco de forma atômica."""
        with self._disk_lock:
            try:
                snapshot = {}
                with self._lock:
                    for user_key, sessoes in self._session_events.items():
                        if isinstance(user_key, tuple):
                            key_str = f"{user_key[0]}::{user_key[1]}"
                        else:
                            key_str = str(user_key)
                        snapshot[key_str] = {}
                        for sess_id, eventos in sessoes.items():
                            serializados = []
                            for ev in eventos:
                                if not ev.content or not ev.content.parts:
                                    continue
                                textos = [
                                    p.text
                                    for p in ev.content.parts
                                    if getattr(p, "text", None)
                                ]
                                if textos:
                                    serializados.append(
                                        {
                                            "id": ev.id,
                                            "author": ev.author,
                                            "textos": textos,
                                            "timestamp": getattr(ev, "timestamp", 0.0),
                                        }
                                    )
                            if serializados:
                                snapshot[key_str][sess_id] = serializados[-MAX_EVENTOS_POR_SESSAO:]

                caminho_dir = os.path.dirname(os.path.abspath(self.caminho_arquivo))
                os.makedirs(caminho_dir, exist_ok=True)
                temp_path = f"{self.caminho_arquivo}.tmp"
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(snapshot, f, ensure_ascii=False, indent=2)
                os.replace(temp_path, self.caminho_arquivo)
            except Exception as err:
                logger.error("Erro ao persistir memórias em disco: %s", err)

    async def _salvar_no_disco_async(self) -> None:
        """Executa a persistência em disco em threadpool separada para não travar o event loop."""
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._salvar_no_disco)
        except RuntimeError:
            self._salvar_no_disco()

    async def add_session_to_memory(self, session) -> None:
        """Adiciona a sessão à memória e persiste em disco de forma assíncrona."""
        await super().add_session_to_memory(session)
        await self._salvar_no_disco_async()

    async def add_events_to_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        events: list[Event],
        session_id: Optional[str] = None,
        custom_metadata: Optional[dict] = None,
    ) -> None:
        """Adiciona eventos delta à memória e persiste em disco de forma assíncrona."""
        await super().add_events_to_memory(
            app_name=app_name,
            user_id=user_id,
            events=events,
            session_id=session_id,
            custom_metadata=custom_metadata,
        )
        await self._salvar_no_disco_async()

    async def add_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        memories: Sequence[MemoryEntry],
        custom_metadata: Optional[dict] = None,
    ) -> None:
        """Grava memórias explícitas (Context.add_memory do ADK 2.x) e persiste em disco.

        O InMemoryMemoryService do ADK não implementa add_memory: chamar a classe base
        levantava NotImplementedError. Cada MemoryEntry vira um evento no grupo de
        memórias explícitas do usuário, encontrado pelo load_memory e salvo no JSON;
        uma entrada com id repetido não é gravada duas vezes.
        """
        eventos = [evento for evento in map(_evento_da_memoria, memories) if evento is not None]
        if not eventos:
            return
        await self.add_events_to_memory(
            app_name=app_name,
            user_id=user_id,
            events=eventos,
            session_id=SESSAO_DE_MEMORIAS_EXPLICITAS,
            custom_metadata=custom_metadata,
        )


def _evento_da_memoria(entrada: MemoryEntry) -> Optional[Event]:
    """Converte uma MemoryEntry do ADK no evento que a memória do JARVIS guarda e busca."""
    if not entrada.content or not entrada.content.parts:
        return None
    evento = Event(
        author=entrada.author or "memoria",
        content=entrada.content,
        **({"id": entrada.id} if entrada.id else {}),
    )
    if entrada.timestamp:
        try:
            evento.timestamp = datetime.datetime.fromisoformat(entrada.timestamp).timestamp()
        except ValueError:
            pass
    return evento
