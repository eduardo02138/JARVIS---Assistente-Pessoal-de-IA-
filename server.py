"""
Servidor Backend do JARVIS
FastAPI + WebSockets + Google GenAI Live API + Ferramentas do SO + Sistema de Monitoramento e Depuração

Ponto de entrada: monta a aplicação a partir dos routers do pacote servidor/ e
reexporta os nomes públicos usados por scripts e testes.
"""
import os
from contextlib import asynccontextmanager

from servidor import RAIZ_PROJETO  # carrega o .env antes dos demais imports

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google import genai

import gemini_bridge
from monitoring.logger import logger
from servidor import live_adk as _live_adk
from servidor import live_nativo as _live_nativo
from servidor import rotas_agente, rotas_sistema, seguranca
from servidor.comum import IDIOMA, VOZ, agendar_tarefa_do_servidor
from servidor.live_adk import live_adk, montar_run_config
from servidor.live_nativo import build_gemini_tools, montar_thinking_config, websocket_live_endpoint
from servidor.rotas_agente import chamar_omniroute_chat
from servidor.runtime_adk import obter_runner, obter_runner_adk
from servidor.seguranca import JARVIS_SECRET_TOKEN, liberar_controle_da_sessao, verify_jarvis_token

__all__ = [
    "app",
    "genai",
    "IDIOMA",
    "VOZ",
    "JARVIS_SECRET_TOKEN",
    "build_gemini_tools",
    "chamar_omniroute_chat",
    "liberar_controle_da_sessao",
    "live_adk",
    "montar_run_config",
    "montar_thinking_config",
    "obter_runner",
    "obter_runner_adk",
    "verify_jarvis_token",
    "websocket_live_endpoint",
]


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    agendar_tarefa_do_servidor(gemini_bridge.gemini_file_watcher_task())
    try:
        from mcp_client_manager import mcp_client_manager
        toolsets = mcp_client_manager.carregar_toolsets()
        if toolsets:
            logger.info("MCP Client: %d servidor(es) MCP carregado(s) no boot.", len(toolsets))
    except Exception as e:
        logger.warning("Falha ao inicializar clientes MCP no boot: %s", e)
    yield
    try:
        from mcp_client_manager import mcp_client_manager
        await mcp_client_manager.close_all()
    except Exception as e:
        logger.warning("Erro ao encerrar conexões MCP no shutdown: %s", e)


app = FastAPI(title="JARVIS AI Assistant - Gemini Live", lifespan=lifespan)

# Servir arquivos estáticos do HUD, do Widget e do cliente ADK
STATIC_DIR = os.path.join(RAIZ_PROJETO, "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

WIDGET_DIR = os.path.join(RAIZ_PROJETO, "gemini-live-widget")
app.mount("/widget", StaticFiles(directory=WIDGET_DIR, html=True), name="gemini-live-widget")

STATIC_ADK_DIR = os.path.join(RAIZ_PROJETO, "static_adk")
app.mount("/static_adk", StaticFiles(directory=STATIC_ADK_DIR, html=True), name="static_adk")

MONITORING_DIR = os.path.join(RAIZ_PROJETO, "monitoring")

for _modulo in (seguranca, rotas_sistema, rotas_agente, _live_adk, _live_nativo):
    app.include_router(_modulo.router)


@app.get("/")
async def get_index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/debug")
async def get_debug_dashboard():
    return FileResponse(os.path.join(MONITORING_DIR, "dashboard.html"))


if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("JARVIS_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 8000))
    print("\n=======================================================")
    print(f"⚡ J.A.R.V.I.S. Online - Interface em: http://{host}:{port}")
    print(f"⚡ Central de Depuração & Logs em: http://{host}:{port}/debug")
    print(f"🔒 Rede: Vinculado a {host} (Proteção contra acesso externo)")
    print("=======================================================\n")
    uvicorn.run(app, host=host, port=port)
