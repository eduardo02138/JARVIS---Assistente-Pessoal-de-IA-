"""
F5 — Voz, Visão e Integração Linux (Paridade Microsoft Copilot Voice & Vision)

Implementa:
1. VoiceTurnDebouncer: calibração de VAD, holdover e supressão de `audio_stream_end`
   fantasma/duplicado sem áudio real acumulado no turno.
2. VoiceDictationService: modo ditado (speech-to-text puro para caixa de edição),
   estritamente isolado de turnos conversacionais e de execução de ferramentas.
3. ReadAloudService: leitura em voz alta sob demanda de respostas/páginas com
   segmentação de sentenças e controle de voz, velocidade, pausa e parada.
4. ConsentedVisionService: Copilot Vision com consentimento explícito por sessão
   (janela, tela inteira, câmera ou imagem enviada), revogação imediata e garantia
   de que perguntas sobre a tela são estritamente de observação (READ_ONLY_OBSERVATION),
   bloqueando cliques, digitação ou mutações sem Modo Controle/Computer Use.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
import threading
import time
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

VisionSourceType = Literal["screen", "window", "camera", "upload"]

# Ferramentas de mutação física ou de sistema que NUNCA podem ser acionadas
# automaticamente apenas por uma consulta de observação visual (Copilot Vision).
FORBIDDEN_MUTATION_TOOLS_IN_VISION = frozenset({
    "mouse_click",
    "click_at",
    "move_mouse",
    "type_text",
    "press_key",
    "hotkey",
    "scroll",
    "drag_and_drop",
    "bash",
    "run_shell_command",
    "close_app",
    "open_app",
    "manage_process",
    "control_media",
})


class VoiceTurnDebouncer:
    """
    Evita rajadas de `audio_stream_end` sem áudio real ou duplicadas em curto intervalo.
    Resolve o problema de turnos fantasmas na Gemini Live API.
    """

    def __init__(
        self,
        min_chunks_for_turn: int = 1,
        min_bytes_for_turn: int = 320,
        debounce_window_s: float = 0.65,
    ) -> None:
        self._lock = threading.RLock()
        self.min_chunks_for_turn = max(1, int(min_chunks_for_turn))
        self.min_bytes_for_turn = max(1, int(min_bytes_for_turn))
        self.debounce_window_s = max(0.05, float(debounce_window_s))
        self.chunks_in_burst: int = 0
        self.bytes_in_burst: int = 0
        self.last_chunk_ts: float = 0.0
        self.last_stream_end_ts: float = 0.0
        self.suppressed_count: int = 0
        self.emitted_count: int = 0

    def record_audio_chunk(self, pcm_bytes: bytes, now: Optional[float] = None) -> None:
        ts = time.monotonic() if now is None else float(now)
        with self._lock:
            if not pcm_bytes:
                return
            self.chunks_in_burst += 1
            self.bytes_in_burst += len(pcm_bytes)
            self.last_chunk_ts = ts

    def should_emit_stream_end(self, now: Optional[float] = None) -> Tuple[bool, str]:
        ts = time.monotonic() if now is None else float(now)
        with self._lock:
            if (
                self.chunks_in_burst < self.min_chunks_for_turn
                or self.bytes_in_burst < self.min_bytes_for_turn
            ):
                self.suppressed_count += 1
                self.chunks_in_burst = 0
                self.bytes_in_burst = 0
                return False, "sem_audio_no_turno"

            if (
                self.last_stream_end_ts > 0.0
                and (ts - self.last_stream_end_ts) < self.debounce_window_s
            ):
                self.suppressed_count += 1
                self.chunks_in_burst = 0
                self.bytes_in_burst = 0
                return False, "debounce_rajada"

            self.chunks_in_burst = 0
            self.bytes_in_burst = 0
            self.last_stream_end_ts = ts
            self.emitted_count += 1
            return True, "ok"

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "chunks_in_burst": self.chunks_in_burst,
                "bytes_in_burst": self.bytes_in_burst,
                "emitted_count": self.emitted_count,
                "suppressed_count": self.suppressed_count,
                "min_chunks_for_turn": self.min_chunks_for_turn,
                "min_bytes_for_turn": self.min_bytes_for_turn,
                "debounce_window_s": self.debounce_window_s,
            }


class DictationSessionState(BaseModel):
    session_id: str
    active: bool = False
    buffer_text: str = ""
    segments: List[str] = Field(default_factory=list)
    language_code: str = "pt-BR"
    triggers_conversation_turn: bool = False
    executes_agent_tools: bool = False
    updated_at: float = Field(default_factory=time.time)


class VoiceDictationService:
    """
    Modo Ditado: converte voz em texto puro para inserção no editor do usuário,
    sem disparar respostas do agente nem invocar ferramentas.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: Dict[str, DictationSessionState] = {}

    def start_dictation(self, session_id: str, language_code: str = "pt-BR", clear: bool = False) -> DictationSessionState:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None or clear:
                state = DictationSessionState(
                    session_id=session_id,
                    active=True,
                    language_code=language_code,
                    triggers_conversation_turn=False,
                    executes_agent_tools=False,
                )
            else:
                state.active = True
                state.language_code = language_code
                state.updated_at = time.time()
            self._sessions[session_id] = state
            return state.model_copy(deep=True)

    def append_segment(self, session_id: str, text_segment: str) -> DictationSessionState:
        cleaned = (text_segment or "").strip()
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                state = DictationSessionState(session_id=session_id, active=True)
                self._sessions[session_id] = state
            if not state.active:
                raise PermissionError("Modo ditado não está ativo para esta sessão.")
            if cleaned:
                state.segments.append(cleaned)
                state.buffer_text = " ".join(state.segments)
                state.updated_at = time.time()
            return state.model_copy(deep=True)

    def stop_dictation(self, session_id: str) -> DictationSessionState:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                state = DictationSessionState(session_id=session_id, active=False)
                self._sessions[session_id] = state
            else:
                state.active = False
                state.updated_at = time.time()
            return state.model_copy(deep=True)

    def clear_buffer(self, session_id: str) -> DictationSessionState:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                state = DictationSessionState(session_id=session_id, active=False)
            else:
                state.segments.clear()
                state.buffer_text = ""
                state.updated_at = time.time()
            self._sessions[session_id] = state
            return state.model_copy(deep=True)

    def get_state(self, session_id: str) -> DictationSessionState:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return DictationSessionState(session_id=session_id, active=False)
            return state.model_copy(deep=True)


class ReadAloudState(BaseModel):
    session_id: str
    status: Literal["IDLE", "PLAYING", "PAUSED", "STOPPED"] = "IDLE"
    voice_name: str = "Aoede"
    rate: float = 1.0
    language_code: str = "pt-BR"
    current_segment_index: int = 0
    segments: List[str] = Field(default_factory=list)
    updated_at: float = Field(default_factory=time.time)


class ReadAloudService:
    """
    Serviço de Leitura em Voz Alta (Read Aloud) com segmentação de sentenças,
    controle de velocidade (0.5x a 2.0x), escolha de voz, pausa, retomada e parada.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._states: Dict[str, ReadAloudState] = {}

    @staticmethod
    def segment_text(text: str) -> List[str]:
        cleaned = re.sub(r"\s+", " ", (text or "").strip())
        if not cleaned:
            return []
        parts = re.split(r"(?<=[.!?…])\s+", cleaned)
        return [p.strip() for p in parts if p.strip()]

    def start_read_aloud(
        self,
        session_id: str,
        text: str,
        voice_name: str = "Aoede",
        rate: float = 1.0,
        language_code: str = "pt-BR",
    ) -> ReadAloudState:
        clamped_rate = max(0.5, min(2.0, float(rate)))
        segments = self.segment_text(text)
        with self._lock:
            state = ReadAloudState(
                session_id=session_id,
                status="PLAYING" if segments else "IDLE",
                voice_name=voice_name or "Aoede",
                rate=clamped_rate,
                language_code=language_code or "pt-BR",
                current_segment_index=0,
                segments=segments,
            )
            self._states[session_id] = state
            return state.model_copy(deep=True)

    def pause(self, session_id: str) -> ReadAloudState:
        with self._lock:
            state = self._states.get(session_id) or ReadAloudState(session_id=session_id)
            if state.status == "PLAYING":
                state.status = "PAUSED"
                state.updated_at = time.time()
            self._states[session_id] = state
            return state.model_copy(deep=True)

    def resume(self, session_id: str) -> ReadAloudState:
        with self._lock:
            state = self._states.get(session_id) or ReadAloudState(session_id=session_id)
            if state.status == "PAUSED" and state.segments:
                state.status = "PLAYING"
                state.updated_at = time.time()
            self._states[session_id] = state
            return state.model_copy(deep=True)

    def stop(self, session_id: str) -> ReadAloudState:
        with self._lock:
            state = self._states.get(session_id) or ReadAloudState(session_id=session_id)
            state.status = "STOPPED"
            state.current_segment_index = 0
            state.updated_at = time.time()
            self._states[session_id] = state
            return state.model_copy(deep=True)

    def get_state(self, session_id: str) -> ReadAloudState:
        with self._lock:
            state = self._states.get(session_id) or ReadAloudState(session_id=session_id)
            return state.model_copy(deep=True)


class VisionConsentRecord(BaseModel):
    consent_id: str = Field(default_factory=lambda: f"vcons_{secrets.token_urlsafe(8)}")
    session_id: str
    sources_allowed: List[VisionSourceType] = Field(default_factory=list)
    target_window_title: Optional[str] = None
    granted_at_wall: float = Field(default_factory=time.time)
    granted_at_mono: float = Field(default_factory=time.monotonic)
    ttl_seconds: float = 900.0
    active: bool = True
    mode: Literal["READ_ONLY_OBSERVATION"] = "READ_ONLY_OBSERVATION"


class ConsentedVisionService:
    """
    Gerencia o consentimento visual explícito por sessão (Copilot Vision) e garante
    que a análise de tela/janela/câmera/imagem seja estritamente somente-leitura,
    impedindo qualquer clique ou digitação implícita sem Modo Controle.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._consents: Dict[str, VisionConsentRecord] = {}

    def grant_consent(
        self,
        session_id: str,
        sources: List[VisionSourceType],
        ttl_seconds: float = 900.0,
        target_window_title: Optional[str] = None,
    ) -> VisionConsentRecord:
        valid_sources: List[VisionSourceType] = []
        for s in sources:
            if s in ("screen", "window", "camera", "upload") and s not in valid_sources:
                valid_sources.append(s)
        if not valid_sources:
            raise ValueError("Especifique ao menos uma fonte visual válida ('screen', 'window', 'camera', 'upload').")

        with self._lock:
            rec = VisionConsentRecord(
                session_id=session_id,
                sources_allowed=valid_sources,
                target_window_title=target_window_title,
                ttl_seconds=max(10.0, min(float(ttl_seconds), 7200.0)),
                active=True,
            )
            self._consents[session_id] = rec
            return rec.model_copy(deep=True)

    def revoke_consent(self, session_id: str) -> bool:
        with self._lock:
            rec = self._consents.get(session_id)
            if not rec:
                return False
            rec.active = False
            return True

    def check_consent(
        self,
        session_id: str,
        source: VisionSourceType,
        now_mono: Optional[float] = None,
    ) -> Tuple[bool, str, Optional[VisionConsentRecord]]:
        now = time.monotonic() if now_mono is None else float(now_mono)
        with self._lock:
            rec = self._consents.get(session_id)
            if rec is None or not rec.active:
                return False, "Consentimento visual não concedido ou já revogado para esta sessão.", None
            if (now - rec.granted_at_mono) > rec.ttl_seconds:
                rec.active = False
                return False, "Consentimento visual expirado (TTL excedido).", None
            if source not in rec.sources_allowed:
                return (
                    False,
                    f"Fonte visual '{source}' não autorizada neste consentimento ({rec.sources_allowed}).",
                    None,
                )
            return True, "ok", rec.model_copy(deep=True)

    def validate_tool_during_vision(self, tool_name: str) -> Tuple[bool, str]:
        """
        Garante que uma consulta visual nunca dispare ferramentas de clique,
        digitação ou mutação de estado sem aprovação explícita no Modo Controle.
        """
        normalized = (tool_name or "").strip().lower()
        if normalized in FORBIDDEN_MUTATION_TOOLS_IN_VISION:
            return (
                False,
                f"Bloqueado pelo Copilot Vision: a ferramenta '{tool_name}' altera o estado físico/sistema "
                "e não pode ser acionada por uma consulta puramente visual (READ_ONLY_OBSERVATION).",
            )
        return True, "ok"

    def analyze_visual_input(
        self,
        session_id: str,
        source: VisionSourceType,
        question: str,
        image_bytes: Optional[bytes] = None,
        ocr_hint_text: Optional[str] = None,
        requested_tools: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        ok, reason, rec = self.check_consent(session_id=session_id, source=source)
        if not ok or rec is None:
            raise PermissionError(reason)

        # Verifica se alguma ferramenta de mutação foi solicitada indevidamente junto à pergunta visual
        for t_name in requested_tools or []:
            allowed, block_msg = self.validate_tool_during_vision(t_name)
            if not allowed:
                raise PermissionError(block_msg)

        frame_hash = hashlib.sha256(image_bytes).hexdigest()[:16] if image_bytes else None
        byte_size = len(image_bytes) if image_bytes else 0

        extracted_context = (ocr_hint_text or "").strip()
        if not extracted_context and image_bytes:
            extracted_context = f"Frame visual ({source}, {byte_size} bytes, sha256={frame_hash}) recebido sob consentimento."

        resposta = (
            f"[Observação Visual Somente-Leitura ({source})]: "
            f"Pergunta='{question.strip()}'. "
            f"Contexto visual analisado: {extracted_context or 'Sem texto auxiliar extraído.'}"
        )
        return {
            "status": "ok",
            "consent_id": rec.consent_id,
            "source": source,
            "mode": "READ_ONLY_OBSERVATION",
            "ui_mutation_performed": False,
            "frame_sha256": frame_hash,
            "frame_bytes": byte_size,
            "question": question,
            "analysis": resposta,
        }


def detect_linux_multimedia_stack() -> Dict[str, Any]:
    """Inspeciona o ambiente multimídia Linux (PipeWire, PulseAudio, X11/Wayland, xdg-desktop-portal)."""
    session_type = os.environ.get("XDG_SESSION_TYPE", "").lower() or (
        "wayland" if os.environ.get("WAYLAND_DISPLAY") else ("x11" if os.environ.get("DISPLAY") else "headless")
    )
    return {
        "session_type": session_type,
        "pipewire_available": bool(shutil.which("pw-cli") or shutil.which("pipewire")),
        "pulseaudio_available": bool(shutil.which("pactl")),
        "xdg_portal_ hint": bool(os.environ.get("XDG_CURRENT_DESKTOP")),
        "ffmpeg_available": bool(shutil.which("ffmpeg")),
    }


voice_dictation_service = VoiceDictationService()
read_aloud_service = ReadAloudService()
consented_vision_service = ConsentedVisionService()
