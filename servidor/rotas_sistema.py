"""Rotas de saúde, provedores, plug-ins, depuração e preferências."""

import asyncio
import os
import time

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, JSONResponse
from google import genai
from google.genai import types

import preferences_manager
import system_tools
from agentes.assistente import (
    MODELO_LIVE,
    MODELO_LIVE_EXTENDED,
    MODELO_LIVE_RESERVA,
    MODELO_TEXTO,
    MODELO_TEXTO_RESERVA,
)
from agentes.computer_use.agente import MODELO_COMPUTER
from live_protocolo import desativar_ping_timeout
from monitoring.logger import (
    TEXT_LOG_FILE,
    clear_logs,
    get_recent_events,
    get_telemetry_summary,
    logger,
    record_event,
)
from plugin_manager import plugin_manager
from policy_engine import policy_engine
from provider_router import GoogleStudioProvider, OmniRouteProvider, provider_router
from servidor.comum import VOZ, modelo_live_padrao
from servidor.runtime_adk import runners_adk, session_service_adk
from servidor.seguranca import verify_jarvis_token

router = APIRouter()


def _status_mcp_sanitizado() -> list:
    """Status dos servidores MCP sem expor binários, URLs ou filtros (health é público)."""
    from mcp_client_manager import mcp_client_manager
    return [
        {"nome": s["nome"], "tipo": s["tipo"], "ativo": s["ativo"], "conectado": s.get("conectado")}
        for s in mcp_client_manager.status()
    ]


@router.get("/health")
@router.get("/api/health")
async def health_check():
    key_pool = GoogleStudioProvider.get_keys()
    has_key = bool(key_pool)
    omni = OmniRouteProvider.check_status()
    return {
        "status": "online",
        "gemini_api_key_configured": has_key,
        "accounts_count": len(key_pool),
        "primary_provider": "google_studio",
        "secondary_provider": "omniroute",
        "active_provider": provider_router.active_provider,
        "omniroute_online": omni["online"],
        "omniroute_combo": omni["combo"],
        "model": modelo_live_padrao(),
        "modelo_live": MODELO_LIVE,
        "modelo_live_reserva": MODELO_LIVE_RESERVA,
        "modelo_live_extended": MODELO_LIVE_EXTENDED,
        "modelo_texto": MODELO_TEXTO,
        "modelo_texto_reserva": MODELO_TEXTO_RESERVA,
        "modelo_computador": MODELO_COMPUTER,
        "chave_configurada": has_key,
        "chaves_no_pool": len(key_pool),
        "sessoes": type(session_service_adk).__name__,
        "voz": VOZ,
        "modo_computador": policy_engine.computer_lease_status(),
        "mcp_servers": _status_mcp_sanitizado(),
    }


@router.get("/api/diagnostico")
async def diagnostico_da_maquina(_=Depends(verify_jarvis_token)):
    """Relatório do diagnóstico (o mesmo de python diagnostico.py), com as conexões MCP reais."""
    import diagnostico
    return await asyncio.to_thread(diagnostico.executar_diagnostico)


@router.get("/api/mcp/servers")
async def listar_servidores_mcp(_=Depends(verify_jarvis_token)):
    """Lista os servidores MCP externos conectados ao assistente."""
    from mcp_client_manager import mcp_client_manager
    return {
        "status": "ok",
        "servers": mcp_client_manager.status(),
    }


@router.get("/api/providers")
async def get_providers_endpoint():
    key_pool = GoogleStudioProvider.get_keys()
    has_key = bool(key_pool)
    omni = OmniRouteProvider.check_status()
    act = provider_router.active_provider

    return {
        "active": act,
        "primary": "google_studio",
        "secondary": "omniroute",
        "providers": [
            {
                "id": "google_studio",
                "name": "Google AI Studio API",
                "tier": "primary",
                "is_primary": True,
                "is_active": act == "google_studio",
                "status": "online" if has_key else "missing_keys",
                "model": modelo_live_padrao(),
                "accounts_count": len(key_pool),
                "features": ["Native Audio 24kHz", "Latência <500ms", "Live WebSockets", f"Visão & {len(system_tools.TOOL_REGISTRY)} Ferramentas"],
                "description": "Provedor primário oficial com velocidade máxima e áudio bidirecional em tempo real."
            },
            {
                "id": "omniroute",
                "name": "OmniRoute Proxy",
                "tier": "secondary",
                "is_secondary": True,
                "is_active": act == "omniroute",
                "status": "online" if omni["online"] else "offline",
                "url": omni["url"],
                "combo": omni["combo"],
                "accounts_count": omni["accounts"],
                "features": ["Failover Automático (Rate Limit 429)", "Balanceamento Round-Robin", "Porta :20128"],
                "description": "Segundo provedor local de inteligência e contingência para alta disponibilidade."
            }
        ]
    }


@router.post("/api/providers/select")
async def select_provider_endpoint(payload: dict, _=Depends(verify_jarvis_token)):
    chosen = (payload.get("provider") or "").strip().lower()
    if provider_router.set_active_provider(chosen):
        record_event("provider_changed", {"provider": provider_router.active_provider})
        logger.info(f"Provedor ativo de IA alterado para: {provider_router.active_provider}")
        return {"status": "ok", "active": provider_router.active_provider}
    return {"status": "erro", "mensagem": "Provedor inválido. Escolha 'google_studio' ou 'omniroute'."}


@router.post("/api/providers/test")
async def test_providers_endpoint():
    results = await provider_router.test_all()
    return {
        "status": "ok",
        "active": provider_router.active_provider,
        "results": results
    }


@router.get("/api/status")
async def system_status():
    return system_tools.get_system_status()

# ----------------- ENDPOINTS DO ECOSSISTEMA DE PLUG-INS (N.E.K.O SDK) -----------------
@router.get("/api/plugins")
async def get_plugins():
    return plugin_manager.get_all_plugins_info()


@router.get("/api/plugins/store")
async def get_plugins_store():
    return plugin_manager.get_store_catalog()


@router.post("/api/plugins/toggle")
async def toggle_plugin_endpoint(payload: dict, _=Depends(verify_jarvis_token)):
    plugin_id = payload.get("plugin_id")
    enabled = payload.get("enabled")
    res = plugin_manager.toggle_plugin(plugin_id, enabled)
    runners_adk.clear()
    record_event("plugin_toggle", {"plugin_id": plugin_id, "result": res})
    return res


@router.post("/api/plugins/install")
async def install_plugin_endpoint(payload: dict, _=Depends(verify_jarvis_token)):
    plugin_id = payload.get("plugin_id")
    res = plugin_manager.install_plugin(plugin_id)
    runners_adk.clear()
    record_event("plugin_install", {"plugin_id": plugin_id, "result": res})
    return res


# ----------------- ENDPOINTS DO SISTEMA DE MONITORAMENTO E DEPURAÇÃO -----------------
@router.get("/api/debug/telemetry")
async def get_telemetry():
    return get_telemetry_summary()


@router.get("/api/system/telemetry")
async def get_system_telemetry():
    return system_tools.toggle_telemetry_overlay(enabled=True)


@router.get("/api/debug/events")
async def get_events(limit: int = 100):
    return get_recent_events(limit=limit)


@router.get("/api/debug/download-log")
async def download_log():
    if os.path.exists(TEXT_LOG_FILE):
        return FileResponse(TEXT_LOG_FILE, filename="jarvis_assistant.log", media_type="text/plain")
    return JSONResponse({"status": "error", "message": "Arquivo de log não encontrado"}, status_code=404)


@router.post("/api/debug/clear-logs")
async def clear_system_logs(_=Depends(verify_jarvis_token)):
    clear_logs()
    return {"status": "ok", "message": "Logs limpos com sucesso"}


@router.get("/api/debug/test-accounts")
async def test_all_accounts():
    key_pool = GoogleStudioProvider.get_keys()

    results = []
    valid_count = 0
    total_latency = 0

    for idx, k in enumerate(key_pool):
        start_t = time.time()
        masked = k[:6] + "..." + k[-4:] if len(k) > 10 else "***"
        try:
            cl = genai.Client(api_key=k)
            # Desativa timeout de ping que derrubava conexões após ~45s de silêncio
            desativar_ping_timeout(cl)

            test_model = modelo_live_padrao()
            # Modelos native-audio só aceitam áudio; exigir TEXT gera falso "chave inválida".
            modalidades = [types.Modality.AUDIO] if "native-audio" in test_model else [types.Modality.TEXT]
            test_config = types.LiveConnectConfig(response_modalities=modalidades)
            async with cl.aio.live.connect(model=test_model, config=test_config) as s:
                await s.send_client_content(
                    turns=types.Content(role="user", parts=[types.Part(text="ping")]),
                    turn_complete=True
                )
                lat = int((time.time() - start_t) * 1000)
                valid_count += 1
                total_latency += lat
                results.append({
                    "index": idx + 1,
                    "masked_key": masked,
                    "valid": True,
                    "latency_ms": lat,
                    "error": None
                })
        except Exception as e:
            lat = int((time.time() - start_t) * 1000)
            results.append({
                "index": idx + 1,
                "masked_key": masked,
                "valid": False,
                "latency_ms": lat,
                "error": str(e)[:120]
            })

    avg_lat = int(total_latency / valid_count) if valid_count else 0
    return {
        "total_keys": len(key_pool),
        "valid_keys": valid_count,
        "avg_latency_ms": avg_lat,
        "results": results,
        "summary": f"{valid_count}/{len(key_pool)} contas operacionais"
    }


@router.get("/api/preferences")
async def get_preferences_endpoint():
    return preferences_manager.get_all_preferences()


@router.post("/api/preferences")
async def update_preferences_endpoint(payload: dict, _=Depends(verify_jarvis_token)):
    cat = payload.get("category", "default_apps")
    key = payload.get("key")
    val = payload.get("value")
    if not key:
        return JSONResponse({"status": "error", "message": "Chave obrigatória"}, status_code=400)
    ok = preferences_manager.set_preference(cat, key, val)
    return {"status": "ok" if ok else "error", "preferences": preferences_manager.get_all_preferences()}
