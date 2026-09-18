"""
Sistema de Monitoramento e Logs do Assistente J.A.R.V.I.S. / Gemini Live
Registra eventos estruturados, telemetria de áudio e histórico de depuração.
"""
import os
import sys
import json
import time
import logging
from collections import deque
from datetime import datetime

MONITORING_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(MONITORING_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

TEXT_LOG_FILE = os.path.join(LOGS_DIR, "assistant.log")
JSONL_LOG_FILE = os.path.join(LOGS_DIR, "events.jsonl")
ERROR_LOG_FILE = os.path.join(LOGS_DIR, "errors.log")

# Logger padrão formatado
logger = logging.getLogger("jarvis_monitor")
logger.setLevel(logging.INFO)
logger.propagate = False

if not logger.handlers:
    file_handler = logging.FileHandler(TEXT_LOG_FILE, encoding="utf-8")
    file_formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    err_handler = logging.FileHandler(ERROR_LOG_FILE, encoding="utf-8")
    err_handler.setLevel(logging.WARNING)
    err_handler.setFormatter(file_formatter)
    logger.addHandler(err_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    stream_handler.setFormatter(stream_formatter)
    logger.addHandler(stream_handler)

_RECENT_EVENTS = deque(maxlen=300)

_TELEMETRY = {
    "server_start_time": time.time(),
    "client_connections": 0,
    "active_connection": False,
    "active_model": os.environ.get("GEMINI_MODEL", "gemini-3.8-live"),
    "active_voice": "Charon",
    "user_audio_packets": 0,
    "user_audio_bytes": 0,
    "model_audio_packets": 0,
    "model_audio_bytes": 0,
    "user_messages": 0,
    "model_messages": 0,
    "tool_calls_count": 0,
    "tool_calls_history": [],
    "last_user_transcript": "",
    "last_model_transcript": "",
    "failover_count": 0,
    "error_count": 0,
    "last_error": None
}

def record_event(event_type: str, data: dict = None):
    """Registra um evento de telemetria no JSONL, atualiza métricas e emite log formatado."""
    now_dt = datetime.now()
    timestamp = now_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    entry = {
        "timestamp": timestamp,
        "type": event_type,
        "data": data or {}
    }

    _RECENT_EVENTS.append(entry)

    try:
        with open(JSONL_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass

    d = data or {}

    if event_type == "client_connected":
        _TELEMETRY["client_connections"] += 1
        _TELEMETRY["active_connection"] = True
        _TELEMETRY["active_model"] = d.get("model", _TELEMETRY["active_model"])
        _TELEMETRY["active_voice"] = d.get("voice", _TELEMETRY["active_voice"])
        logger.info(f"🟢 [CLIENTE]: Conectado via WebSocket (Voz: {_TELEMETRY['active_voice']}, Modelo: {_TELEMETRY['active_model']})")

    elif event_type == "client_disconnected":
        _TELEMETRY["active_connection"] = False
        logger.info("🔴 [CLIENTE]: Desconectado do WebSocket.")

    elif event_type == "user_audio_chunk":
        chunk_size = d.get("bytes", 0)
        _TELEMETRY["user_audio_packets"] += 1
        _TELEMETRY["user_audio_bytes"] += chunk_size

    elif event_type == "model_audio_chunk":
        chunk_size = d.get("bytes", 0)
        _TELEMETRY["model_audio_packets"] += 1
        _TELEMETRY["model_audio_bytes"] += chunk_size

    elif event_type == "user_text":
        txt = d.get("text", "")
        _TELEMETRY["user_messages"] += 1
        _TELEMETRY["last_user_transcript"] = txt
        logger.info(f"👤 [USUÁRIO]: \"{txt}\"")

    elif event_type == "model_text":
        txt = d.get("text", "")
        _TELEMETRY["model_messages"] += 1
        _TELEMETRY["last_model_transcript"] = txt
        logger.info(f"🤖 [JARVIS / GEMINI]: \"{txt}\"")

    elif event_type == "tool_call":
        _TELEMETRY["tool_calls_count"] += 1
        call_info = {
            "timestamp": timestamp,
            "name": d.get("name"),
            "args": d.get("args", {})
        }
        _TELEMETRY["tool_calls_history"].append(call_info)
        if len(_TELEMETRY["tool_calls_history"]) > 25:
            _TELEMETRY["tool_calls_history"].pop(0)
        logger.info(f"⚙️ [FERRAMENTA]: Executando {d.get('name')}({json.dumps(d.get('args', {}))})")

    elif event_type == "tool_result":
        res = d.get("result", {})
        success = res.get("sucesso", True) if isinstance(res, dict) else True
        icon = "✅" if success else "⚠️"
        logger.info(f"{icon} [RESULTADO]: {d.get('name')} -> {json.dumps(res, ensure_ascii=False)[:120]}")

    elif event_type == "interrupted":
        logger.warning("⚡ [BARGE-IN]: Usuário interrompeu a resposta em andamento do assistente.")

    elif event_type == "turn_complete":
        logger.info("🏁 [TURNO CONCLUÍDO]: Ciclo de resposta concluído.")

    elif event_type == "account_failover":
        _TELEMETRY["failover_count"] += 1
        logger.warning(f"🔄 [FAILOVER]: Conta {d.get('from_index')} falhou. Alternando para conta {d.get('to_index')}. Erro: {d.get('reason')}")

    elif event_type == "error":
        _TELEMETRY["error_count"] += 1
        _TELEMETRY["last_error"] = d.get("message")
        logger.error(f"❌ [ERRO]: {d.get('message')}")

    else:
        logger.info(f"[{event_type.upper()}]: {d}")

def get_recent_events(limit: int = 100):
    """Retorna os eventos mais recentes da memória."""
    return list(_RECENT_EVENTS)[-limit:]

def get_telemetry_summary():
    """Retorna o estado consolidado da telemetria do sistema."""
    uptime_sec = int(time.time() - _TELEMETRY["server_start_time"])
    hours, remainder = divmod(uptime_sec, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    summary = dict(_TELEMETRY)
    summary["uptime"] = uptime_str
    summary["uptime_seconds"] = uptime_sec
    summary["recent_events_count"] = len(_RECENT_EVENTS)
    return summary

def clear_logs():
    """Limpa os arquivos de log e a memória."""
    _RECENT_EVENTS.clear()
    for f in [TEXT_LOG_FILE, JSONL_LOG_FILE, ERROR_LOG_FILE]:
        try:
            with open(f, "w", encoding="utf-8") as fp:
                fp.write("")
        except Exception:
            pass
