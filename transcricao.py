"""Configuração canônica de transcrição Live do J.A.R.V.I.S.

Um só lugar define dica de idioma, vocabulário, modo e gerencia a sessão
dedicada de baixa latência (gemini-3.5-transcribe-live). server.py importa daqui: sem drift.

Variáveis de ambiente:
  JARVIS_TRANSCRIBE_LANGS      lista separada por vírgula, padrão "pt-BR".
                               Dica explícita dispensa detecção automática e
                               reduz latência da primeira hipótese.
  JARVIS_TRANSCRIBE_VOCAB      termos separados por vírgula (até 100 usados),
                               ex: "JARVIS,Antigravity,Steam".
  JARVIS_TRANSCRIBE_MODE       "verbatim" (padrão, mais rápido) ou "smart"
                               (legível, porém com mais latência).
  JARVIS_TRANSCRIBE_MODEL      modelo de transcrição streaming dedicado,
                               padrão "gemini-3.5-transcribe-live".
  JARVIS_DEDICATED_TRANSCRIBE  "1" para ativar transcritor dedicado paralelo (padrão).
"""

import asyncio
import logging
import os
from typing import Awaitable, Callable, Optional

try:
    from monitoring.logger import logger
except Exception:
    logger = logging.getLogger("JARVIS.Transcricao")


def get_language_codes() -> list[str]:
    bruto = os.environ.get("JARVIS_TRANSCRIBE_LANGS", "pt-BR")
    return [c.strip() for c in bruto.split(",") if c.strip()]


def get_custom_vocabulary() -> list[str]:
    bruto = os.environ.get("JARVIS_TRANSCRIBE_VOCAB", "")
    return [t.strip() for t in bruto.split(",") if t.strip()][:100]


def get_transcription_mode() -> str:
    modo = os.environ.get("JARVIS_TRANSCRIBE_MODE", "verbatim").strip().lower()
    return "smart" if modo == "smart" else "verbatim"


def get_transcribe_model() -> str:
    return os.environ.get("JARVIS_TRANSCRIBE_MODEL", "gemini-3.5-transcribe-live").strip()


def is_dedicated_transcribe_enabled() -> bool:
    val = os.environ.get("JARVIS_DEDICATED_TRANSCRIBE", "1").strip().lower()
    return val in ("1", "true", "yes", "on")


def build_input_transcription_config():
    """Monta AudioTranscriptionConfig com dica de idioma (Current SDK).

    Com LIVE_AUTO_LANG=1 a dica é omitida: o modelo detecta e alterna o
    idioma sozinho durante a conversa (a dica fixa trava a detecção).
    """
    from google.genai import types

    se_auto_lang = os.environ.get("LIVE_AUTO_LANG", "0").strip().lower() in ("1", "true", "yes", "on")
    kwargs: dict = {}
    if not se_auto_lang:
        kwargs["language_codes"] = get_language_codes()
    vocab = get_custom_vocabulary()
    if vocab:
        kwargs["custom_vocabulary"] = vocab
    if get_transcription_mode() == "smart":
        kwargs["mode"] = "SMART"
    return types.AudioTranscriptionConfig(**kwargs)


def build_output_transcription_config():
    """Transcrição da fala do modelo: sem dica de idioma, só texto."""
    from google.genai import types

    return types.AudioTranscriptionConfig()


class LiveTranscriber:
    """Sessão dedicada de transcrição de áudio em tempo real com baixa latência.

    Utiliza o modelo gemini-3.5-transcribe-live da Live API com response_modalities=["TEXT"],
    emitindo hipóteses parciais (interim_input_transcription) em frações de segundo
    enquanto o usuário fala.
    """

    def __init__(
        self,
        on_interim: Optional[Callable[[str], Awaitable[None]]] = None,
        on_final: Optional[Callable[[str], Awaitable[None]]] = None,
    ):
        self.on_interim = on_interim
        self.on_final = on_final
        self.model = get_transcribe_model()
        self._session = None
        self._session_context = None
        self._receive_task: Optional[asyncio.Task] = None
        self._running = False
        self._lock = asyncio.Lock()

    @property
    def is_active(self) -> bool:
        return self._running and self._session is not None

    async def start(self, api_key: str) -> bool:
        """Abre a conexão de transcrição dedicada de forma assíncrona."""
        if not is_dedicated_transcribe_enabled():
            logger.info("Transcritor dedicado desativado via configuração.")
            return False

        if not api_key:
            logger.warning("Nenhuma chave Gemini disponível para o transcritor dedicado.")
            return False

        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)
            config = types.LiveConnectConfig(
                response_modalities=["TEXT"],
                input_audio_transcription=build_input_transcription_config(),
            )

            self._session_context = client.aio.live.connect(model=self.model, config=config)
            self._session = await self._session_context.__aenter__()
            self._running = True
            self._receive_task = asyncio.create_task(self._receive_loop())
            logger.info("Transcritor dedicado ativo: %s com idioma %s.", self.model, get_language_codes())
            return True
        except Exception as e:
            logger.warning("Falha ao inicializar transcritor dedicado (%s). Mantendo transcrição via sessão do agente: %s", self.model, e)
            self._running = False
            self._session = None
            return False

    async def send_audio(self, pcm_data: bytes) -> bool:
        """Envia chunk de PCM 16-bit 16kHz para o modelo de transcrição."""
        if not self.is_active or not pcm_data:
            return False

        from google.genai import types

        try:
            async with self._lock:
                if self._session:
                    await self._session.send_realtime_input(
                        audio=types.Blob(data=pcm_data, mime_type="audio/pcm;rate=16000")
                    )
            return True
        except Exception as e:
            logger.debug("Erro ao enviar áudio para o transcritor dedicado: %s", e)
            return False

    async def send_audio_stream_end(self) -> bool:
        """Sinaliza término de fala para consolidação do texto."""
        if not self.is_active:
            return False

        try:
            async with self._lock:
                if self._session:
                    await self._session.send_realtime_input(audio_stream_end=True)
            return True
        except Exception as e:
            logger.debug("Erro ao enviar áudio_stream_end para o transcritor: %s", e)
            return False

    async def _receive_loop(self):
        """Consome eventos da Live API do transcritor e dispara callbacks."""
        try:
            async for response in self._session.receive():
                if not self._running:
                    break

                sc = getattr(response, "server_content", None)
                if not sc:
                    continue

                # Hipótese parcial instantânea (interim)
                parcial = getattr(sc, "interim_input_transcription", None)
                if parcial and parcial.text and self.on_interim:
                    texto = parcial.text.strip()
                    if texto:
                        try:
                            await self.on_interim(texto)
                        except Exception as cb_err:
                            logger.debug("Erro no callback on_interim: %s", cb_err)

                # Hipótese final consolidada
                final = getattr(sc, "input_transcription", None)
                if final and final.text and self.on_final:
                    texto = final.text.strip()
                    if texto:
                        try:
                            await self.on_final(texto)
                        except Exception as cb_err:
                            logger.debug("Erro no callback on_final: %s", cb_err)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("Loop do transcritor dedicado encerrado: %s", e)
        finally:
            self._running = False

    async def close(self):
        """Encerra a sessão de transcrição de forma limpa."""
        self._running = False
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except (asyncio.CancelledError, Exception):
                pass
            self._receive_task = None

        if self._session_context:
            try:
                await self._session_context.__aexit__(None, None, None)
            except Exception:
                pass
            self._session_context = None
            self._session = None
