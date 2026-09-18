"""
Servidor Backend do JARVIS
FastAPI + WebSockets + Google GenAI Live API + Ferramentas do SO + Sistema de Monitoramento e Depuração
"""
import os
from dotenv import load_dotenv
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(ENV_PATH, override=True)
import sys
import json
import base64
import time
import socket
import asyncio
import logging
import urllib.request
import urllib.error
from typing import Dict, Optional

import secrets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, Header, HTTPException, Request, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from policy_engine import policy_engine
from google.adk.apps.app import App, EventsCompactionConfig
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService, InMemorySessionService
from google.adk.agents import LiveRequestQueue
from google.adk.agents.run_config import RunConfig
from google.genai.errors import ClientError, ServerError
from agentes.memoria import JarvisMemoryService
from agentes.assistente import (
    criar_agente_rapido,
    criar_agente_coordenador,
    criar_agente_de_voz,
    MODELO_TEXTO,
    MODELO_LIVE,
    MODELO_TEXTO_RESERVA,
    MODELO_LIVE_RESERVA,
)
from agentes.roteador import escolher_caminho, CAMINHO_RAPIDO, CAMINHO_COMPLEXO
from agentes.computer_use.agente import MODELO_COMPUTER, criar_agente_computer_use
from google import genai
from google.genai import types

import system_tools
from plugin_manager import plugin_manager
import gemini_bridge
import transcricao
from provider_router import provider_router, GoogleStudioProvider, OmniRouteProvider
from monitoring.logger import (
    logger, record_event, get_recent_events,
    get_telemetry_summary, clear_logs, TEXT_LOG_FILE
)

app = FastAPI(title="JARVIS AI Assistant - Gemini Live")

JARVIS_SECRET_TOKEN = os.environ.get("JARVIS_TOKEN")
if not JARVIS_SECRET_TOKEN:
    JARVIS_SECRET_TOKEN = secrets.token_urlsafe(24)

# Tempo máximo de espera pela confirmação do usuário em ferramentas de risco
CONFIRMATION_TIMEOUT_S = int(os.environ.get("JARVIS_CONFIRMATION_TIMEOUT", "30"))

# Silêncio do assistente (segundos) a partir do qual o microfone volta a ser encaminhado
MIC_GRACE_S = float(os.environ.get("JARVIS_MIC_GRACE", "0.3"))

# Caminho interno do agente de Computer Use (navegador Chromium via Playwright)
CAMINHO_COMPUTADOR = "computador"


def liberar_controle_da_sessao(session_id: str) -> None:
    """Encerra a autoridade física e do agente ao fim da sessão dona da lease.

    Sessões que não são donas da lease não mexem no Modo Controle ou IDE de quem é.
    """
    if policy_engine.control_lease_status().get("owner") == session_id:
        if system_tools.get_control_mode():
            system_tools.set_control_mode(False)
        lease = policy_engine.revoke_control_lease(session_id=session_id)
        record_event("control_lease_released", lease)

    if policy_engine.ide_lease_status().get("owner") == session_id:
        lease_ide = policy_engine.revoke_ide_lease(session_id=session_id)
        record_event("ide_lease_released", lease_ide)
    logger.info("Sessão encerrada: Modo Controle desativado e lease de controle revogada.")

async def verify_jarvis_token(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_jarvis_token: Optional[str] = Header(None),
    token: Optional[str] = Query(None)
):
    """Exige token de autorização para ações de mutação ou controle."""
    req_token = None
    if authorization and authorization.startswith("Bearer "):
        req_token = authorization.split("Bearer ")[1].strip()
    elif x_jarvis_token:
        req_token = x_jarvis_token.strip()
    elif token:
        req_token = token.strip()

    if req_token != JARVIS_SECRET_TOKEN:
        raise HTTPException(status_code=401, detail="Não autorizado: JARVIS_TOKEN inválido ou ausente.")
    return req_token

verify_auth_token = verify_jarvis_token

@app.get("/api/auth/session")
@app.get("/api/auth/token")
async def get_session_token(request: Request):
    """Permite apenas ao cliente local no loopback obter o token da sessão ativa."""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(status_code=403, detail="Acesso restrito ao localhost.")
    return {"status": "ok", "token": JARVIS_SECRET_TOKEN}

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(gemini_bridge.gemini_file_watcher_task())

# Servir arquivos estáticos do HUD e do Widget
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

WIDGET_DIR = os.path.join(os.path.dirname(__file__), "gemini-live-widget")
app.mount("/widget", StaticFiles(directory=WIDGET_DIR, html=True), name="gemini-live-widget")

MONITORING_DIR = os.path.join(os.path.dirname(__file__), "monitoring")

# Fila global para injeção de comandos vindos da tela de depuração
active_session_queue: asyncio.Queue = asyncio.Queue()

@app.get("/")
async def get_index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

@app.get("/debug")
async def get_debug_dashboard():
    return FileResponse(os.path.join(MONITORING_DIR, "dashboard.html"))

def check_omniroute_status() -> dict:
    """Verifica se o OmniRoute (segundo provedor) está operacional via OmniRouteProvider."""
    return OmniRouteProvider.check_status()

@app.get("/health")
@app.get("/api/health")
async def health_check():
    raw_keys = os.environ.get("GEMINI_API_KEYS", "")
    key_pool = [k.strip() for k in raw_keys.split(",") if k.strip()]
    has_key = bool(os.environ.get("GEMINI_API_KEY")) or bool(key_pool)
    omni = check_omniroute_status()
    return {
        "status": "online",
        "gemini_api_key_configured": has_key,
        "accounts_count": len(key_pool) if key_pool else (1 if has_key else 0),
        "primary_provider": "google_studio",
        "secondary_provider": "omniroute",
        "active_provider": provider_router.active_provider,
        "omniroute_online": omni["online"],
        "omniroute_combo": omni["combo"],
        "model": os.environ.get("GEMINI_MODEL", "gemini-3.8-live")
    }

@app.get("/api/providers")
async def get_providers_endpoint():
    raw_keys = os.environ.get("GEMINI_API_KEYS", "")
    key_pool = [k.strip() for k in raw_keys.split(",") if k.strip()]
    has_key = bool(os.environ.get("GEMINI_API_KEY")) or bool(key_pool)
    omni = check_omniroute_status()
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
                "model": os.environ.get("GEMINI_MODEL", "gemini-3.8-live"),
                "accounts_count": len(key_pool) if key_pool else (1 if has_key else 0),
                "features": ["Native Audio 24kHz", "Latência <500ms", "Live WebSockets", "Visão & 55 Ferramentas"],
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

@app.post("/api/providers/select")
async def select_provider_endpoint(payload: dict, _=Depends(verify_jarvis_token)):
    chosen = (payload.get("provider") or "").strip().lower()
    if provider_router.set_active_provider(chosen):
        record_event("provider_changed", {"provider": provider_router.active_provider})
        logger.info(f"Provedor ativo de IA alterado para: {provider_router.active_provider}")
        return {"status": "ok", "active": provider_router.active_provider}
    return {"status": "erro", "mensagem": "Provedor inválido. Escolha 'google_studio' ou 'omniroute'."}

@app.post("/api/providers/test")
async def test_providers_endpoint():
    results = await provider_router.test_all()
    return {
        "status": "ok",
        "active": provider_router.active_provider,
        "results": results
    }

@app.get("/api/status")
async def system_status():
    return system_tools.get_system_status()

# ----------------- ENDPOINTS DO ECOSSISTEMA DE PLUG-INS (N.E.K.O SDK) -----------------
@app.get("/api/plugins")
async def get_plugins():
    return plugin_manager.get_all_plugins_info()

@app.get("/api/plugins/store")
async def get_plugins_store():
    return plugin_manager.get_store_catalog()

@app.post("/api/plugins/toggle")
async def toggle_plugin_endpoint(payload: dict, _=Depends(verify_jarvis_token)):
    plugin_id = payload.get("plugin_id")
    enabled = payload.get("enabled")
    res = plugin_manager.toggle_plugin(plugin_id, enabled)
    try:
        runners_adk.clear()
    except NameError:
        pass
    record_event("plugin_toggle", {"plugin_id": plugin_id, "result": res})
    return res

@app.post("/api/plugins/install")
async def install_plugin_endpoint(payload: dict, _=Depends(verify_jarvis_token)):
    plugin_id = payload.get("plugin_id")
    res = plugin_manager.install_plugin(plugin_id)
    try:
        runners_adk.clear()
    except NameError:
        pass
    record_event("plugin_install", {"plugin_id": plugin_id, "result": res})
    return res


# ----------------- ENDPOINTS DO SISTEMA DE MONITORAMENTO E DEPURAÇÃO -----------------
@app.get("/api/debug/telemetry")
async def get_telemetry():
    return get_telemetry_summary()

@app.get("/api/system/telemetry")
async def get_system_telemetry():
    return system_tools.toggle_telemetry_overlay(enabled=True)

@app.get("/api/debug/events")
async def get_events(limit: int = 100):
    return get_recent_events(limit=limit)

@app.get("/api/debug/download-log")
async def download_log():
    if os.path.exists(TEXT_LOG_FILE):
        return FileResponse(TEXT_LOG_FILE, filename="jarvis_assistant.log", media_type="text/plain")
    return JSONResponse({"status": "error", "message": "Arquivo de log não encontrado"}, status_code=404)

@app.post("/api/debug/clear-logs")
async def clear_system_logs(_=Depends(verify_jarvis_token)):
    clear_logs()
    return {"status": "ok", "message": "Logs limpos com sucesso"}

@app.get("/api/debug/test-accounts")
async def test_all_accounts():
    raw_keys = os.environ.get("GEMINI_API_KEYS", "")
    key_pool = [k.strip() for k in raw_keys.split(",") if k.strip()]
    single = os.environ.get("GEMINI_API_KEY")
    if single and single not in key_pool:
        key_pool.insert(0, single)

    results = []
    valid_count = 0
    total_latency = 0

    for idx, k in enumerate(key_pool):
        start_t = time.time()
        masked = k[:6] + "..." + k[-4:] if len(k) > 10 else "***"
        try:
            cl = genai.Client(api_key=k)
            # Desativa timeout de ping
            if hasattr(cl, "_api_client") and hasattr(cl._api_client, "_websocket_ssl_ctx"):
                cl._api_client._websocket_ssl_ctx["ping_interval"] = None
                cl._api_client._websocket_ssl_ctx["ping_timeout"] = None

            test_model = os.environ.get("GEMINI_MODEL", "gemini-3.8-live")
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

@app.post("/api/inject_prompt")
@app.post("/api/inject-prompt")
@app.post("/api/debug/inject-prompt")
async def inject_prompt(payload: dict, _=Depends(verify_jarvis_token)):
    prompt = payload.get("prompt", "").strip()
    if not prompt:
        return JSONResponse({"status": "error", "message": "Prompt vazio"}, status_code=400)
    await active_session_queue.put(prompt)
    record_event("user_text", {"text": prompt, "source": "debug_injector"})
    return {"status": "ok", "message": f"Prompt injetado com sucesso: '{prompt}'"}

@app.get("/api/preferences")
async def get_preferences_endpoint():
    import preferences_manager
    return preferences_manager.get_all_preferences()

@app.post("/api/preferences")
async def update_preferences_endpoint(payload: dict, _=Depends(verify_jarvis_token)):
    import preferences_manager
    cat = payload.get("category", "default_apps")
    key = payload.get("key")
    val = payload.get("value")
    if not key:
        return JSONResponse({"status": "error", "message": "Chave obrigatória"}, status_code=400)
    ok = preferences_manager.set_preference(cat, key, val)
    return {"status": "ok" if ok else "error", "preferences": preferences_manager.get_all_preferences()}

# ----------------- PROMPT & FERRAMENTAS DO JARVIS -----------------
JARVIS_SYSTEM_INSTRUCTION = """
Você é J.A.R.V.I.S. (Just A Rather Very Intelligent System), a avançada inteligência artificial pessoal do usuário.
Diretrizes fundamentais:
1. Trate o usuário de forma cortês, respeitosa e refinada, chamando-o de "Senhor" ou "Senhora".
2. Sua comunicação de voz é sofisticada, serena, precisa e pontuada com o característico humor, perspicácia britânica clássica e inteligência sarcástica de Tony Stark. Você sabe contar piadas refinadas e anedotas inteligentes quando o senhor solicitar descontração.
3. Responda em Português do Brasil com excelente eloquência e naturalidade.
4. BAIXA LATÊNCIA E RESPOSTAS ÁGEIS: Comece a falar imediatamente. Seja extremamente direto e sucinto (1 a 2 frases curtas por resposta), sem preâmbulos desnecessários, mantendo a conversa dinâmica e rápida como uma conversa humana real. Forneça respostas mais longas somente quando o senhor solicitar expressamente uma explicação detalhada.
5. Você possui ferramentas integradas para controlar o computador do senhor:
   - Verificar telemetria de hardware (CPU, memória RAM, GPU dedicada NVIDIA RTX 5060, bateria).
   - Listar e localizar jogos e aplicativos instalados no computador e no drive gamer, identificando a distribuidora (Steam, Lutris, Epic Games, etc.) e diretórios através de 'list_installed_games'.
   - Iniciar e abrir qualquer jogo ou aplicativo diretamente através de 'open_application' (ex: 'iniciar Marvel Rivals', 'jogar GTA', 'abrir Red Dead', 'abrir Steam').
   - Pesquisar na web ('search_web') e abrir qualquer site ou link diretamente no navegador ('open_website').
   - Tocar qualquer música ou artista no YouTube/Spotify ('play_music').
   - Tirar capturas de tela e salvar com nomes personalizados na pasta de imagens ('take_screenshot').
   - Alterar o volume do sistema ('adjust_volume') e gravar/ler anotações ('take_quick_note', 'read_notes').
   - Controlar a IDE Antigravity do Senhor: abrir projetos ('antigravity_open_workspace'), abrir a pasta de auditoria gemini ('antigravity_open_gemini_bridge'), abrir arquivos em linhas específicas ('antigravity_open_file'), listar servidores MCP da IDE ('antigravity_list_mcps') e delegar tarefas complexas ao agente da IDE ('antigravity_run_prompt').
   - Consultar e salvar preferências e aplicativos padrão ('manage_user_preference', 'set_game_preference', 'open_default_app').
   Invoque as ferramentas automaticamente sempre que o pedido do senhor envolver essas ações.
6. RETORNO DE FERRAMENTAS OBRIGATÓRIO: SEMPRE que executar uma ferramenta (como list_installed_games, open_application, get_gpu_status, get_system_status, antigravity_list_mcps, antigravity_open_file, antigravity_run_prompt, set_ide_mode, manage_user_preference, etc.), você DEVE responder em áudio imediatamente em seguida ao Senhor, comunicando os dados obtidos de forma concisa e natural. Nunca fique em silêncio após executar uma ferramenta.
7. MODO IDE & INTEGRAÇÃO CONTÍNUA COM ANTIGRAVITY:
   - ATIVAÇÃO: Quando o senhor falar "iniciar modo IDE", "ativar modo IDE" ou termos equivalentes, chame IMEDIATAMENTE `set_ide_mode(enabled=True)`. Anuncie prontidão dizendo que a conexão com o agente Antigravity está ativa e que manterá o canal de programação aberto.
   - DESATIVAÇÃO: Quando o senhor falar "sair do modo IDE", "encerrar modo IDE", "desativar modo IDE", chame `set_ide_mode(enabled=False)` e confirme o retorno ao modo padrão.
   - FLUXO NO MODO IDE: Sempre que estiver no Modo IDE, qualquer instrução técnica, comando de código, dúvida do projeto, edição de arquivo ou execução de testes solicitada pelo senhor DEVE ser repassada diretamente para o agente Antigravity usando `antigravity_run_prompt(prompt=..., continue_session=True)`. Quando o agente concluir, relate o resultado ao senhor em voz alta de maneira fluida e elegante, mantendo o contexto de programação contínuo.
8. ECOSSISTEMA DE PLUG-INS EXTENSÍVEL (ESTILO N.E.K.O):
   Você possui módulos de extensão dinâmicos:
   - 🎮 Companhia em Jogos: definir o jogo ativo ('game_companion_set_active_game'), disparar timers táticos de boss/cooldown ('game_companion_tactical_timer') e fornecer conselhos estratégicos ('game_companion_get_strategy').
   - 🏠 Casa Inteligente & IoT: ligar/desligar e regular luzes ('smart_home_set_light'), ativar cenas ambientais como 'Foco', 'Cinema' ou 'Descanso' ('smart_home_activate_scene') e consultar climatização ('smart_home_get_climate').
   - 📡 Streaming & Transmissão ao Vivo: monitorar live ('live_stream_toggle_status'), sintetizar o chat recente para o streamer ('live_stream_read_chat_summary') e emitir alertas ('live_stream_send_alert').
   - 💬 Mídias Sociais: checar notificações pendentes no Discord/Telegram/X ('social_feed_check_notifications') e postar atualizações ('social_feed_post_update').
   Acione essas ferramentas prontamente quando o senhor pedir qualquer uma dessas ações.
9. PENSAMENTOS INTERNOS E IDIOMA:
   - Responda EXCLUSIVAMENTE em Português do Brasil com naturalidade e refinamento.
   - NUNCA externe pensamentos, raciocínios de planejamento ou notas em inglês para o Senhor. Fale diretamente a resposta final.
10. CANAL DE AUDITORIA & PASTA GEMINI:
   - SOMENTE chame 'antigravity_open_gemini_bridge' quando o senhor pedir explicitamente para abrir a pasta gemini, abrir a ponte de desenvolvimento ou a auditoria. NUNCA chame essa ferramenta por conta própria ou em conversas normais sobre outros assuntos.
   - A pasta 'gemini' (/home/edu/Documentos/assistente/gemini/) é o canal direto onde o senhor pode mandar mensagens diretamente por arquivo (gemini/input.txt) sem precisar falar no microfone, e onde todas as interações e respostas do Antigravity ficam auditadas em 'audit.jsonl' e 'latest_response.md'.
11. MEMÓRIA PERSISTENTE E PREFERÊNCIAS DO USUÁRIO (APLICATIVOS PADRÃO & CONFIGURAÇÕES):
   - Você possui memória persistente para lembrar preferências e configurações do Senhor ('manage_user_preference', 'set_game_preference', 'open_default_app').
   - REPRODUÇÃO DE MÚSICA & PLATAFORMA PADRÃO: Ao pedir para tocar música ('play_music'), se a ferramenta indicar que a plataforma padrão ainda não está configurada, pergunte ao Senhor com cortesia: "Senhor, qual plataforma prefere utilizar como padrão para reproduzir músicas: YouTube ou Spotify?". Quando o Senhor responder (ex: "Spotify" ou "YouTube"), salve imediatamente a escolha dele usando 'manage_user_preference(action='set', category='default_apps', key='music_platform', value=escolha)' e inicie a reprodução. Nas próximas vezes em que o senhor pedir qualquer música, toque diretamente na plataforma favorita dele sem perguntar novamente.
   - PREFERÊNCIAS DE JOGOS & LAUNCHERS: Se o Senhor indicar um launcher preferido para um jogo (ex: "Sempre abra GTA pela Epic Games" ou "Abra Red Dead pela Steam"), registre imediatamente chamando 'set_game_preference'.
   - APLICATIVOS PADRÃO (ESTILO WINDOWS): Se o Senhor pedir para definir navegadores, clientes de e-mail ou editores de texto padrão, ou abrir arquivos por tipo, utilize 'manage_user_preference' e 'open_default_app'.
12. MODO CONTROLE FÍSICO DO COMPUTADOR (MOUSE, TECLADO E JANELAS):
   - ATIVAÇÃO: Quando o senhor falar ou digitar "modo controle ativar", "ativar modo controle", "iniciar modo controle", chame IMEDIATAMENTE `set_control_mode(enabled=True)`. Ao receber o retorno, comunique em áudio/voz de forma elegante as janelas abertas encontradas, a resolução da tela e confirme que o mouse e o teclado virtual estão calibrados e sob seu comando.
   - DESATIVAÇÃO: Quando o senhor falar "modo controle desativar", "sair do modo controle", "desativar controle", chame `set_control_mode(enabled=False)` e confirme o retorno ao modo normal.
   - AÇÕES NO MODO CONTROLE:
     * Consultar janelas abertas: `list_open_windows`
     * Mover o cursor do mouse: `mouse_move(delta_x, delta_y)`
     * Clicar com o mouse: `mouse_click(button='left'|'right'|'middle', double=False)`
     * Rolar a tela: `mouse_scroll(direction='up'|'down', amount=3)`
     * Digitar texto: `keyboard_type(text=...)`
     * Atalhos de teclado: `keyboard_hotkey(keys='alt+tab'|'ctrl+c'|'ctrl+v'|'super'|'enter')`
     * Capturar a tela para ver onde clicar: `take_screenshot`
13. TELEMETRIA EM TELA & MONITORAMENTO DE HARDWARE:
   - Quando o Senhor pedir "telemetria na tela", "mostrar telemetria", "abrir telemetria", "ocultar telemetria" ou disser que a telemetria não está aparecendo na janela, chame IMEDIATAMENTE `toggle_telemetry_overlay(enabled=True/False)`.
   - Ao executar a ferramenta, confirme em voz alta os dados principais de CPU, RAM e GPU e assegure ao Senhor que o painel de telemetria em tempo real foi aberto diretamente na janela do assistente sobreposta na tela.
"""

def build_gemini_tools():
    return [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name=decl["name"],
                    description=decl["description"],
                    parameters=types.Schema(
                        type=decl["parameters"].get("type", "OBJECT"),
                        properties={
                            k: types.Schema(type=v["type"], description=v.get("description", ""))
                            for k, v in decl["parameters"].get("properties", {}).items()
                        },
                        required=decl["parameters"].get("required", [])
                    )
                )
                for decl in system_tools.GEMINI_FUNCTION_DECLARATIONS
            ]
        )
    ]

# ----------------- WEBSOCKET BRIDGE COM GEMINI LIVE -----------------

# ------------------ ADK Engine & Session Service ------------------
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
    raw_keys = os.environ.get("GEMINI_API_KEYS", "")
    key_pool = [k.strip() for k in raw_keys.split(",") if k.strip()]
    single = os.environ.get("GEMINI_API_KEY")
    if single and single not in key_pool:
        key_pool.insert(0, single)
    if len(key_pool) < 2:
        return False
    _indice_chave_adk = (_indice_chave_adk + 1) % len(key_pool)
    nova_chave = key_pool[_indice_chave_adk]
    os.environ["GOOGLE_API_KEY"] = nova_chave
    os.environ["GEMINI_API_KEY"] = nova_chave
    runners_adk.clear()
    logger.warning("Cota ADK atingida: rotacionando para chave %d/%d.", _indice_chave_adk + 1, len(key_pool))
    return True


def obter_runner_adk(tipo: str) -> Runner:
    if tipo not in runners_adk:
        if tipo == "rapido":
            agente = criar_agente_rapido()
        elif tipo == "coordenador":
            agente = criar_agente_coordenador()
        elif tipo == "voz":
            agente = criar_agente_de_voz()
        elif tipo == CAMINHO_COMPUTADOR:
            agente = criar_agente_computer_use(MODELO_COMPUTER)
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
        runners_adk[tipo] = Runner(
            app=app_obj,
            session_service=session_service_adk,
            memory_service=memory_service_adk,
        )
    return runners_adk[tipo]


@app.get("/api/acoes_pendentes")
async def listar_pendentes(
    sessao: Optional[str] = Query(None),
    usuario: Optional[str] = Query(None),
    _=Depends(verify_jarvis_token)
):
    """Lista pendências ativas filtradas com estrito isolamento por sessão/usuário."""
    if not sessao and not usuario:
        return {"pendentes": []}
    pendentes = policy_engine.list_pending_actions(session_id=sessao, user_id=usuario)
    now_m = time.monotonic()
    return {
        "pendentes": [
            {
                "id": a.action_id,
                "ferramenta": a.tool_name,
                "argumentos": a.args,
                "status": a.status,
                "session_id": a.session_id,
                "user_id": a.user_id,
                "expira_em": max(0, int(a.expires_at - now_m))
            }
            for a in pendentes
        ]
    }


@app.post("/api/confirmar_acao")
async def confirmar_acao(payload: dict, _=Depends(verify_jarvis_token)):
    action_id = payload.get("id_confirmacao") or payload.get("action_id") or payload.get("id")
    aprovado = payload.get("aprovado", True)
    session_id = payload.get("sessao") or payload.get("session_id")
    user_id = payload.get("usuario") or payload.get("user_id") or "local"

    if not session_id or session_id in ("sessao-principal", "default"):
        return JSONResponse({"status": "erro", "mensagem": "Parâmetro 'sessao' explícito e válido é obrigatório para confirmar ações."}, status_code=400)

    if not action_id:
        pending = policy_engine.approve_latest_pending(session_id=session_id, user_id=user_id)
        if pending:
            return {"status": "ok", "action_id": pending.action_id, "tool_name": pending.tool_name}
        return JSONResponse({"status": "erro", "mensagem": "Nenhuma ação pendente encontrada para esta sessão/usuário"}, status_code=404)

    if aprovado:
        sucesso = policy_engine.approve_action(action_id, session_id=session_id, user_id=user_id)
        if sucesso:
            return {"status": "ok", "action_id": action_id}
        return JSONResponse({"status": "erro", "mensagem": "Ação não encontrada, expirada ou sessão/usuário divergente"}, status_code=400)
    else:
        sucesso = policy_engine.reject_action(action_id, session_id=session_id, user_id=user_id)
        return {"status": "rejeitado", "action_id": action_id}


async def chamar_omniroute_chat(texto: str) -> str:
    """Wrapper fino canônico: implementação mora em OmniRouteProvider.chat()."""
    return await OmniRouteProvider.chat(texto)


@app.post("/api/computer/mode")
async def alternar_modo_computador(payload: dict, _=Depends(verify_jarvis_token)):
    """Ativa/desativa a lease do Modo Computador (navegador via Computer Use).

    Ativar exige confirmação prévia registrada (fluxo de pendências do PolicyEngine).
    Desativar revoga a lease e fecha o Chromium compartilhado do runner.
    """
    ativo = bool(payload.get("ativo"))
    sessao = payload.get("sessao") or "sessao-principal"
    usuario = payload.get("usuario") or "local"

    if not ativo:
        lease = policy_engine.revoke_computer_lease(session_id=sessao)
        runner = runners_adk.get(CAMINHO_COMPUTADOR)
        if runner is not None:
            for ferramenta in getattr(runner.agent, "tools", []):
                fechar = getattr(ferramenta, "close", None)
                if callable(fechar):
                    try:
                        await fechar()
                    except Exception as erro:
                        logger.warning("Falha ao fechar navegador: %s", erro)
        return {"status": "ok", "modo_computador": lease, "navegador": "fechado"}

    if policy_engine.is_computer_lease_active(sessao):
        return {"status": "ok", "modo_computador": policy_engine.computer_lease_status()}

    args_modo = {"enabled": True, "descricao": "Ativação do Modo Computador (navegação em Chromium)"}
    pendentes = policy_engine.list_pending_actions(session_id=sessao, user_id=usuario)
    modo_pendente = next((p for p in pendentes if p.tool_name == "set_computer_mode"), None)
    if modo_pendente is not None and modo_pendente.status == "pending":
        return {
            "status": "aguardando_confirmacao",
            "id_confirmacao": modo_pendente.action_id,
            "sessao": sessao,
            "mensagem": f"Confirme a ativação do Modo Computador (id {modo_pendente.action_id[:8]}).",
        }

    if policy_engine.consume_authorization(
        tool_name="set_computer_mode",
        args=args_modo,
        session_id=sessao,
        user_id=usuario,
    ):
        lease = policy_engine.grant_computer_lease(owner=sessao)
        logger.info("Modo Computador concedido à sessão '%s' por %ss.", sessao, lease["segundos_restantes"])
        return {"status": "ok", "modo_computador": lease}

    modo_pendente = policy_engine.create_pending_action(
        tool_name="set_computer_mode",
        args=args_modo,
        session_id=sessao,
        user_id=usuario,
        ttl=60.0,
    )
    return {
        "status": "aguardando_confirmacao",
        "id_confirmacao": modo_pendente.action_id,
        "sessao": sessao,
        "mensagem": f"Confirme a ativação do Modo Computador (id {modo_pendente.action_id[:8]}).",
    }


@app.post("/api/chat")
async def api_chat_adk(payload: dict, _=Depends(verify_jarvis_token)):
    """Turno textual unificado: o roteador escolhe entre o agente rápido e o coordenador."""
    texto = (payload.get("texto") or "").strip()
    if not texto:
        return JSONResponse({"status": "erro", "mensagem": "Texto vazio"}, status_code=400)

    usuario = payload.get("usuario", "local")
    sessao = payload.get("sessao", "sessao-principal")
    caminho_forcado = payload.get("caminho")
    marcas_navegador = (
        "modo computador",
        "use o navegador",
        "controle o navegador",
        "controlar o navegador",
        "navegação automática",
    )

    # Verifica palavras de confirmação verbal ou digitada do usuário
    palavras_confirmacao = {"sim", "confirmar", "confirmado", "autorizar", "autorizado", "pode", "ok", "prosseguir", "positivo", "permitir"}
    palavras_negacao = {"nao", "não", "negar", "negado", "cancelar", "cancela", "recusar", "recuso"}
    texto_limpo = "".join(c for c in texto.lower() if c.isalnum() or c.isspace()).strip()
    tokens = set(texto_limpo.split())
    is_negado = bool(tokens & palavras_negacao)
    is_confirmado = (texto_limpo in palavras_confirmacao) or (bool(tokens & palavras_confirmacao) and not is_negado)
    if is_confirmado:
        pending = policy_engine.approve_latest_pending(session_id=sessao, user_id=usuario)
        if pending:
            logger.info("Usuário confirmou verbalmente a ação pendente: %s (%s)", pending.action_id, pending.tool_name)
            texto = f"O usuário confirmou expressamente a execução da ação '{pending.tool_name}'. Execute-a agora."
            caminho_forcado = "complexo"

    texto_min = texto.lower()
    if caminho_forcado in ("rapido", "complexo", CAMINHO_COMPUTADOR):
        caminho, motivo = caminho_forcado, "escolha explícita"
    elif any(marca in texto_min for marca in marcas_navegador):
        caminho, motivo = CAMINHO_COMPUTADOR, "solicitação de operação do navegador (Computer Use)"
    else:
        caminho, motivo = escolher_caminho(texto)

    # Se o provedor ativo for OmniRoute, despacha diretamente sem invocar runner Google
    if provider_router.active_provider == "omniroute":
        logger.info("Provedor ativo é OmniRoute. Despachando chat diretamente...")
        try:
            resp_texto = await OmniRouteProvider.chat(texto)
            return {
                "status": "ok",
                "caminho": caminho,
                "motivo_do_roteamento": "Provedor ativo: OmniRoute",
                "provedor": "omniroute",
                "modelo": f"omniroute/{OmniRouteProvider.get_model()}",
                "resposta": resp_texto,
                "ferramentas": [],
            }
        except Exception as omni_err:
            logger.warning("Falha no provedor ativo OmniRoute: %s", omni_err)
            return JSONResponse({
                "status": "erro",
                "caminho": caminho,
                "mensagem": f"Provedor OmniRoute indisponível: {omni_err}"
            }, status_code=503)

    if caminho == CAMINHO_RAPIDO:
        tipo_runner = "rapido"
    elif caminho == CAMINHO_COMPUTADOR:
        tipo_runner = CAMINHO_COMPUTADOR
    else:
        tipo_runner = "coordenador"
    runner = obter_runner_adk(tipo_runner)

    try:
        await session_service_adk.create_session(
            app_name="assistente", user_id=usuario, session_id=sessao
        )
    except Exception:
        pass

    resposta = ""
    ferramentas_executadas = []
    resposta_ok = False
    ultimo_erro = None

    for tentativa in range(4):
        runner = obter_runner_adk(tipo_runner)
        try:
            async for evento in runner.run_async(
                user_id=usuario,
                session_id=sessao,
                new_message=types.Content(role="user", parts=[types.Part(text=texto)]),
            ):
                if not evento.content or not evento.content.parts:
                    continue
                for parte in evento.content.parts:
                    if parte.function_call:
                        ferramentas_executadas.append(parte.function_call.name)
                    if parte.text and evento.is_final_response():
                        resposta += parte.text
            resposta_ok = True
            break
        except Exception as err:
            err_str = str(err)
            if any(marca in err_str for marca in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE")):
                ultimo_erro = err
                if girar_chave_adk():
                    continue
                if tipo_runner == CAMINHO_COMPUTADOR:
                    # O modelo de texto reserva não entende a config computer_use.
                    logger.warning("Modelo de Computer Use indisponível (%s).", err)
                    break
                logger.warning("Modelo de texto indisponível (%s). Acionando segundo provedor.", err)
                break
            logger.warning("Falha na execução do ADK run_async (%s).", err)
            return JSONResponse({
                "status": "erro",
                "mensagem": f"Erro na execução do agente: {err}",
                "caminho": caminho
            }, status_code=500)

    if not resposta_ok:
        # Failover automático para o segundo provedor (OmniRoute)
        logger.info("Google AI Studio indisponível. Acionando OmniRoute (:20128) como segundo provedor...")
        try:
            resp_texto = await chamar_omniroute_chat(texto)
            return {
                "status": "ok",
                "caminho": caminho,
                "motivo_do_roteamento": "Failover: Google AI Studio indisponível -> OmniRoute acionado como 2º provedor",
                "provedor": "omniroute",
                "modelo": f"omniroute/{OmniRouteProvider.get_model()}",
                "resposta": resp_texto,
                "ferramentas": [],
            }
        except Exception as omni_err:
            logger.warning("Falha também no segundo provedor OmniRoute: %s", omni_err)
            return JSONResponse({
                "status": "erro",
                "caminho": caminho,
                "mensagem": f"Google AI Studio e segundo provedor (OmniRoute) indisponíveis: {ultimo_erro}"
            }, status_code=503)

    # Ingestão assíncrona da sessão na memória de longo prazo (background task)
    async def _salvar_memoria_bg():
        try:
            sess_obj = await session_service_adk.get_session(
                app_name="assistente", user_id=usuario, session_id=sessao
            )
            if sess_obj:
                await memory_service_adk.add_session_to_memory(sess_obj)
        except Exception as e:
            logger.warning("Falha ao salvar sessão na memória de longo prazo: %s", e)

    asyncio.create_task(_salvar_memoria_bg())

    return {
        "status": "ok",
        "caminho": caminho,
        "motivo_do_roteamento": motivo,
        "provedor": "google_studio",
        "modelo": runner.agent.model,
        "resposta": resposta.strip(),
        "ferramentas": ferramentas_executadas,
    }


@app.websocket("/ws/live_adk")
async def live_adk(
    websocket: WebSocket,
    usuario: str = Query("local"),
    sessao: str = Query("sessao-principal"),
    origem: str = Query(""),
):
    """Sessão de voz Live bidirecional nativa do Google ADK com handshake autenticado."""
    await websocket.accept()
    
    # Handshake seguro: exige token idêntico ao /ws/live
    try:
        init_raw = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
        init_data = json.loads(init_raw)
    except Exception:
        await websocket.close(code=1008, reason="Init Timeout / Format Error")
        return

    if not init_data or init_data.get("type") != "init" or init_data.get("token") != JARVIS_SECRET_TOKEN:
        client_host = websocket.client.host if websocket.client else ""
        is_loopback = client_host in ("127.0.0.1", "::1", "localhost", "testclient")
        origem_teste = (origem == "teste" and is_loopback)
        if origem_teste:
            logger.info("[TESTE] Rejeição de WebSocket /ws/live_adk sem token: comportamento esperado.")
        else:
            logger.warning("Tentativa de conexão WebSocket /ws/live_adk não autorizada: token inválido ou ausente.")
        record_event("auth_error", {
            "source": "ws_live_adk",
            "reason": "invalid_or_missing_token",
            "origem": origem_teste and "teste" or "real",
        })
        try:
            await websocket.send_json({"tipo": "erro", "mensagem": "Não autorizado: JARVIS_TOKEN inválido ou ausente."})
        except Exception:
            pass
        await websocket.close(code=1008, reason="Unauthorized")
        return

    runner = obter_runner_adk("voz")
    try:
        await session_service_adk.create_session(
            app_name="assistente", user_id=usuario, session_id=sessao
        )
    except Exception:
        pass

    fila = LiveRequestQueue()
    logger.info("Cliente autenticado no Live ADK (sessão: %s, modelo: %s)", sessao, runner.agent.model)
    await websocket.send_json({"tipo": "pronto", "modelo": runner.agent.model})

    async def do_cliente_para_o_agente():
        client_muted = False
        while True:
            msg = json.loads(await websocket.receive_text())
            tipo = msg.get("tipo") or msg.get("type")
            if tipo in ("microphone_state", "estado_microfone"):
                client_muted = bool(msg.get("muted", msg.get("mutado", False)))
                record_event("microphone_state_changed", {"source": "live_adk", "muted": client_muted})
                if client_muted:
                    fila.send_audio_stream_end()
                continue
            if tipo == "audio":
                if client_muted:
                    # Fail-closed: descarta áudio residual enquanto mutado
                    continue
                audio_b64 = msg.get("dados") or msg.get("data")
                if audio_b64:
                    fila.send_realtime(
                        types.Blob(
                            data=base64.b64decode(audio_b64),
                            mime_type="audio/pcm;rate=16000",
                        )
                    )
            elif tipo in ("video", "imagem", "screen_frame"):
                frame_b64 = msg.get("dados") or msg.get("data")
                if frame_b64:
                    fila.send_realtime(
                        types.Blob(
                            data=base64.b64decode(frame_b64),
                            mime_type="image/jpeg",
                        )
                    )
            elif tipo in ("fim_do_audio", "end_of_audio", "audio_stream_end"):
                fila.send_audio_stream_end()
            elif tipo in ("texto", "text"):
                texto_msg = msg.get("texto", "").strip()
                palavras_confirmacao = {"sim", "confirmar", "confirmado", "autorizar", "autorizado", "pode", "ok", "prosseguir", "positivo", "permitir"}
                palavras_negacao = {"nao", "não", "negar", "negado", "cancelar", "cancela", "recusar", "recuso"}
                texto_limpo = "".join(c for c in texto_msg.lower() if c.isalnum() or c.isspace()).strip()
                tokens = set(texto_limpo.split())
                is_negado = bool(tokens & palavras_negacao)
                is_confirmado = (texto_limpo in palavras_confirmacao) or (bool(tokens & palavras_confirmacao) and not is_negado)
                if is_confirmado:
                    pending = policy_engine.approve_latest_pending(session_id=sessao, user_id=usuario)
                    if pending:
                        logger.info("Ação pendente %s (%s) aprovada por DIGITAÇÃO no Live ADK!", pending.action_id, pending.tool_name)
                        await websocket.send_json({
                            "tipo": "acao_aprovada",
                            "origem": "texto_live",
                            "action_id": pending.action_id,
                            "tool_name": pending.tool_name,
                            "mensagem": f"Ação '{pending.tool_name}' autorizada por texto no modo Live!"
                        })
                        fila.send_content(types.Content(
                            role="user",
                            parts=[types.Part(text=f"O usuário confirmou expressamente por texto: 'sim'. Execute a ferramenta '{pending.tool_name}' agora.")]
                        ))
                        continue
                fila.send_content(
                    types.Content(role="user", parts=[types.Part(text=msg["texto"])])
                )
            elif tipo == "confirmar_acao":
                action_id = msg.get("id_confirmacao")
                aprovado = msg.get("aprovado", True)
                if aprovado:
                    sucesso = policy_engine.approve_action(action_id, session_id=sessao, user_id=usuario)
                    if sucesso:
                        pending = policy_engine.get_pending_action(action_id)
                        tool_name = pending.tool_name if pending else "ação"
                        await websocket.send_json({
                            "tipo": "acao_aprovada",
                            "origem": "botao_ui",
                            "action_id": action_id,
                            "tool_name": tool_name,
                            "mensagem": f"Ação '{tool_name}' autorizada pelo botão da interface!"
                        })
                        fila.send_content(types.Content(
                            role="user",
                            parts=[types.Part(text=f"O usuário confirmou via interface. Execute a ferramenta '{tool_name}' agora.")]
                        ))
                else:
                    policy_engine.reject_action(action_id, session_id=sessao, user_id=usuario)
                    await websocket.send_json({"tipo": "acao_rejeitada", "action_id": action_id})

    async def do_agente_para_o_cliente():
        run_cfg = RunConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Charon")
                )
            ),
            input_audio_transcription=transcricao.build_input_transcription_config(),
            output_audio_transcription=transcricao.build_output_transcription_config(),
        )
        async for evento in runner.run_live(
            user_id=usuario,
            session_id=sessao,
            live_request_queue=fila,
            run_config=run_cfg,
        ):
            parcial = getattr(evento, "interim_input_transcription", None)
            if parcial and parcial.text:
                await websocket.send_json(
                    {"tipo": "transcricao_usuario_parcial", "texto": parcial.text}
                )
            if evento.input_transcription and evento.input_transcription.text:
                transcricao_usuario = evento.input_transcription.text.strip()
                await websocket.send_json(
                    {"tipo": "transcricao_usuario", "texto": transcricao_usuario}
                )
                # Hook de aprovação verbal por voz na sessão Live
                palavras_confirmacao = {"sim", "confirmar", "confirmado", "autorizar", "autorizado", "pode", "ok", "prosseguir", "positivo", "permitir"}
                palavras_negacao = {"nao", "não", "negar", "negado", "cancelar", "cancela", "recusar", "recuso"}
                texto_limpo = "".join(c for c in transcricao_usuario.lower() if c.isalnum() or c.isspace()).strip()
                tokens = set(texto_limpo.split())
                is_negado = bool(tokens & palavras_negacao)
                is_confirmado = (texto_limpo in palavras_confirmacao) or (bool(tokens & palavras_confirmacao) and not is_negado)
                if is_confirmado:
                    pending = policy_engine.approve_latest_pending(session_id=sessao, user_id=usuario)
                    if pending:
                        logger.info("Ação pendente %s (%s) aprovada por COMANDO DE VOZ no Live!", pending.action_id, pending.tool_name)
                        await websocket.send_json({
                            "tipo": "acao_aprovada",
                            "origem": "voz",
                            "action_id": pending.action_id,
                            "tool_name": pending.tool_name,
                            "mensagem": f"Ação '{pending.tool_name}' autorizada por comando de voz!"
                        })
                        fila.send_content(types.Content(
                            role="user",
                            parts=[types.Part(text=f"O usuário confirmou expressamente por voz: 'sim'. Execute a ferramenta '{pending.tool_name}' agora.")]
                        ))
            if evento.output_transcription and evento.output_transcription.text:
                await websocket.send_json(
                    {"tipo": "texto", "texto": evento.output_transcription.text}
                )
            if evento.content and evento.content.parts:
                for parte in evento.content.parts:
                    if parte.inline_data and parte.inline_data.data:
                        await websocket.send_json(
                            {
                                "tipo": "audio",
                                "dados": base64.b64encode(parte.inline_data.data).decode(),
                            }
                        )
                    if parte.function_call:
                        await websocket.send_json(
                            {
                                "tipo": "ferramenta",
                                "nome": parte.function_call.name,
                                "args": dict(parte.function_call.args or {}),
                            }
                        )
                    if parte.function_response:
                        await websocket.send_json(
                            {
                                "tipo": "ferramenta_resultado",
                                "nome": parte.function_response.name,
                                "resultado": dict(parte.function_response.response or {}),
                            }
                        )
            if evento.interrupted:
                await websocket.send_json({"tipo": "interrompido"})
            if evento.turn_complete:
                await websocket.send_json({"tipo": "turno_concluido"})
                try:
                    sess_obj = await session_service_adk.get_session(
                        app_name="assistente", user_id=usuario, session_id=sessao
                    )
                    if sess_obj:
                        asyncio.create_task(memory_service_adk.add_session_to_memory(sess_obj))
                except Exception:
                    pass

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(do_cliente_para_o_agente())
            tg.create_task(do_agente_para_o_cliente())
    except* (WebSocketDisconnect, asyncio.CancelledError):
        logger.info("Cliente Live ADK desconectou.")
    except* (ServerError, ClientError) as grupo:
        logger.warning("Falha na sessão Live ADK (%s).", grupo.exceptions[0])
        try:
            await websocket.send_json({"tipo": "erro", "mensagem": "Instabilidade na Live API. Reconecte para tentar novamente."})
        except Exception:
            pass
    finally:
        fila.close()
        try:
            sess_obj = await session_service_adk.get_session(
                app_name="assistente", user_id=usuario, session_id=sessao
            )
            if sess_obj:
                await memory_service_adk.add_session_to_memory(sess_obj)
        except Exception as e:
            logger.warning("Falha ao salvar sessão Live na memória de longo prazo: %s", e)


# Controle de Conexão Live Única (Single Active Live Session)
# Impede que duas interfaces, abas ou janelas fiquem com a sessão Live aberta concorrentemente
active_live_socket: Optional[WebSocket] = None

@app.websocket("/ws/live")
async def websocket_live_endpoint(websocket: WebSocket):
    global active_live_socket
    await websocket.accept()

    # Encerra conexão anterior imediatamente para evitar múltiplos agentes falando juntos
    if active_live_socket is not None and active_live_socket != websocket:
        logger.info("Encerrando conexão WebSocket Live anterior para garantir instância única ativa.")
        try:
            await active_live_socket.send_json({
                "type": "superseded",
                "message": "Uma nova janela do JARVIS foi aberta. Esta conexão foi colocada em espera para evitar duplicações."
            })
            await active_live_socket.close(code=1000, reason="Superseded by new session")
        except Exception:
            pass

    active_live_socket = websocket
    logger.info("Cliente Web HUD conectado via WebSocket (Sessão Live Única).")

    # Aguarda mensagem de inicialização
    init_data = None
    try:
        raw_msg = await asyncio.wait_for(websocket.receive_text(), timeout=15.0)
        init_data = json.loads(raw_msg)
    except Exception as e:
        logger.error(f"Erro ao aguardar mensagem de init: {e}")
        record_event("error", {"message": f"Timeout aguardando handshake init do cliente: {e}"})
        await websocket.close(code=1008, reason="Init timeout")
        return

    # Autenticação de Sessão no Handshake do WebSocket
    client_token = init_data.get("token") or websocket.query_params.get("token")
    if client_token != JARVIS_SECRET_TOKEN:
        client_host = websocket.client.host if websocket.client else ""
        is_loopback = client_host in ("127.0.0.1", "::1", "localhost", "testclient")
        origem_teste = (websocket.query_params.get("origem") == "teste" and is_loopback)
        if origem_teste:
            logger.info("[TESTE] Rejeição de WebSocket /ws/live sem token: comportamento esperado.")
        else:
            logger.warning("Tentativa de conexão WebSocket não autorizada: token inválido ou ausente.")
        record_event("auth_error", {
            "source": "ws_live",
            "reason": "invalid_or_missing_token",
            "origem": origem_teste and "teste" or "real",
        })
        await websocket.send_json({"type": "error", "message": "Não autorizado: JARVIS_TOKEN inválido ou ausente."})
        await websocket.close(code=1008, reason="Unauthorized")
        return

    voice_name = init_data.get("voice") or os.environ.get("JARVIS_VOICE", "Charon")
    # O modelo pedido pelo cliente é respeitado; o .env define o padrão.
    # O bloqueio anterior forçava o downgrade de qualquer modelo 3.8 para o 2.5.
    req_model = (init_data.get("model") or "").strip()
    model_name = req_model or os.environ.get("GEMINI_MODEL", "gemini-3.8-live")
    req_provider = (init_data.get("provider") or "").strip() or provider_router.active_provider
    allow_barge_in = bool(init_data.get("barge_in", False)) or os.environ.get("JARVIS_BARGE_IN", "false").lower() in ("true", "1", "yes")
    activity_handling = (
        types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS
        if allow_barge_in
        else types.ActivityHandling.NO_INTERRUPTION
    )

    live_connect_kwargs = {
        "response_modalities": [types.Modality.AUDIO],
        "speech_config": types.SpeechConfig(
            language_code=os.environ.get("JARVIS_LANGUAGE", "pt-BR"),
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name)
            )
        ),
        "system_instruction": types.Content(
            parts=[types.Part(text=JARVIS_SYSTEM_INSTRUCTION)]
        ),
        "tools": build_gemini_tools(),
        # Dica pt-BR + interim: transcrição parcial chega enquanto fala.
        "input_audio_transcription": transcricao.build_input_transcription_config(),
        "output_audio_transcription": transcricao.build_output_transcription_config(),
        "realtime_input_config": types.RealtimeInputConfig(
            activity_handling=activity_handling
        ),
        # Sessões com vídeo duram ~2 min sem compressão; a janela deslizante evita o corte
        "context_window_compression": types.ContextWindowCompressionConfig(
            sliding_window=types.SlidingWindow()
        )
    }

    # Modelos como gemini-3.8-live instruem explicitamente a omitir thinking_config
    if "3.8" not in model_name:
        live_connect_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)

    config = types.LiveConnectConfig(**live_connect_kwargs)

    # Isolamento de Segredos: Pool de contas lidas estritamente do backend (.env)
    raw_keys = os.environ.get("GEMINI_API_KEYS", "")
    parsed_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
    single_key = os.environ.get("GEMINI_API_KEY")
    if single_key and single_key not in parsed_keys:
        parsed_keys.insert(0, single_key)
    key_pool = sorted(parsed_keys, key=lambda k: 0 if k.startswith("AIzaSy") else 1)
    if not key_pool:
        err_msg = "Nenhuma chave no pool. Configure GEMINI_API_KEYS ou GEMINI_API_KEY no .env do servidor."
        record_event("error", {"message": err_msg})
        await websocket.send_json({"type": "error", "message": err_msg})
        await websocket.close()
        return

    last_err = None
    for idx, try_key in enumerate(key_pool):
        try:
            logger.info(f"Tentando conectar com conta {idx+1}/{len(key_pool)} do pool (modelo: {model_name})...")
            active_client = genai.Client(api_key=try_key)

            # Desativa timeout de ping que derrubava conexões após ~45s de silêncio
            if hasattr(active_client, "_api_client") and hasattr(active_client._api_client, "_websocket_ssl_ctx"):
                active_client._api_client._websocket_ssl_ctx["ping_interval"] = None
                active_client._api_client._websocket_ssl_ctx["ping_timeout"] = None

            async with active_client.aio.live.connect(model=model_name, config=config) as session:
                record_event("client_connected", {
                    "account_index": idx + 1,
                    "total_accounts": len(key_pool),
                    "model": model_name,
                    "voice": voice_name
                })
                # Identidade desta sessão: a lease de controle físico pertence a ela
                sessao_id = secrets.token_urlsafe(12)
                # A sessão Live nativa é a única identidade: dona E usuária das leases do Modo IDE
                usuario_id = sessao_id
                live_info = provider_router.live_provider()
                provedor_efetivo = live_info["provider"]
                await websocket.send_json({
                    "type": "connected",
                    "message": f"Sistemas online. Conectado via {provedor_efetivo} ({model_name}) com a voz {voice_name}.",
                    "voice": voice_name,
                    "model": model_name,
                    "provider": provedor_efetivo,
                    "requested_provider": req_provider,
                    "live_supported": live_info["live_supported"],
                    "nota": live_info["nota"],
                    "primary_provider": "google_studio",
                    "secondary_provider": "omniroute"
                })
                await websocket.send_json({
                    "type": "ide_mode",
                    "active": system_tools.get_ide_mode()
                })
                # Uma nova sessão não herda o Modo Controle: a autoridade é de quem tem a lease
                await websocket.send_json({
                    "type": "control_mode",
                    "active": system_tools.get_control_mode() and policy_engine.is_control_lease_active(sessao_id),
                    "lease": policy_engine.control_lease_status()
                })
                await websocket.send_json({
                    "type": "computer_mode",
                    "active": policy_engine.is_computer_lease_active(sessao_id),
                    "lease": policy_engine.computer_lease_status()
                })
                logger.info(f"Sessão Gemini Live estabelecida com sucesso usando {model_name}!")
                assistant_state = {
                    "busy": False,
                    "ultimo_audio": 0.0,
                    "ultimo_envio_usuario": 0.0,
                    "audio_recebido_no_turno": 0,
                    "texto_recebido_no_turno": 0,
                    "ultima_ferramenta": None,
                    "ultimo_resultado_ferramenta": None
                }
                # Confirmações pendentes de ferramentas de risco: call_id -> Future(bool)
                pending_confirmations: Dict[str, asyncio.Future] = {}

                # Transcritor dedicado em tempo real (gemini-3.5-transcribe-live)
                ultima_transcricao_usuario = {"texto": "", "tempo": 0.0}

                async def on_transcricao_interim(texto: str):
                    if not texto:
                        return
                    try:
                        await websocket.send_json({
                            "type": "user_transcription_interim",
                            "text": texto
                        })
                    except Exception:
                        pass

                async def on_transcricao_final(texto: str):
                    if not texto:
                        return
                    agora = time.time()
                    if ultima_transcricao_usuario["texto"] == texto and (agora - ultima_transcricao_usuario["tempo"] < 2.5):
                        return
                    ultima_transcricao_usuario["texto"] = texto
                    ultima_transcricao_usuario["tempo"] = agora
                    try:
                        await websocket.send_json({
                            "type": "user_transcription",
                            "text": texto
                        })
                        # Aprovação por comando de voz no WebSocket nativo
                        palavras_sim = {"sim", "autorizar", "autorizado", "confirmar", "confirmado", "pode", "ok", "yes", "permitir", "conceder"}
                        palavras_nao = {"nao", "não", "negar", "negado", "cancelar", "cancela", "recusar", "recuso"}
                        trans_lower = "".join(c for c in texto.lower() if c.isalnum() or c.isspace()).strip()
                        tokens_voz = set(trans_lower.split())
                        if (trans_lower in palavras_sim or bool(tokens_voz & palavras_sim)) and not (tokens_voz & palavras_nao):
                            for cid, fut in list(pending_confirmations.items()):
                                if not fut.done():
                                    fut.set_result(True)
                                    logger.info("✅ [POLICY CONFIRMED BY VOICE]: '%s'", texto)
                                    record_event("user_confirmed_via_voice", {"text": texto})
                                    await websocket.send_json({
                                        "type": "policy_verbal_confirmation_approved",
                                        "call_id": cid,
                                        "text": texto
                                    })
                    except Exception:
                        pass

                transcritor_dedicado = transcricao.LiveTranscriber(
                    on_interim=on_transcricao_interim,
                    on_final=on_transcricao_final
                )
                await transcritor_dedicado.start(api_key=try_key)

                async def request_user_confirmation(call_id: str, func_name: str, args: dict, decision) -> bool:
                    """Pede autorização ao usuário no HUD e espera a resposta."""
                    future: asyncio.Future = asyncio.get_running_loop().create_future()
                    pending_confirmations[call_id] = future
                    await websocket.send_json({
                        "type": "tool_confirmation_request",
                        "id": call_id,
                        "name": func_name,
                        "args": args,
                        "risk_level": decision.risk_level.value,
                        "reason": decision.reason,
                        "timeout_s": CONFIRMATION_TIMEOUT_S,
                        "session_id": sessao_id,
                        "sessao": sessao_id,
                        "usuario": usuario_id
                    })
                    record_event("policy_confirmation_requested", {
                        "call_id": call_id,
                        "name": func_name,
                        "risk": decision.risk_level.value,
                        "args": args
                    })
                    try:
                        return await asyncio.wait_for(future, timeout=CONFIRMATION_TIMEOUT_S)
                    except asyncio.TimeoutError:
                        return False
                    finally:
                        pending_confirmations.pop(call_id, None)

                # Worker 1: Lê comandos e áudio do WebSocket sem interrupções
                async def ws_client_worker():
                    client_muted = False
                    while True:
                        msg_text = await websocket.receive_text()
                        msg = json.loads(msg_text)
                        msg_type = msg.get("type") or msg.get("tipo")

                        if msg_type in ("microphone_state", "estado_microfone"):
                            client_muted = bool(msg.get("muted", msg.get("mutado", False)))
                            record_event("microphone_state_changed", {"muted": client_muted})
                            if client_muted:
                                if transcritor_dedicado.is_active:
                                    asyncio.create_task(transcritor_dedicado.send_audio_stream_end())
                                await session.send_realtime_input(audio_stream_end=True)
                            continue

                        if msg_type == "audio":
                            if client_muted:
                                # Fail-closed: descarta chunks residuais que cheguem enquanto mutado
                                continue
                            audio_b64 = msg.get("data", "")
                            if audio_b64:
                                pcm_data = base64.b64decode(audio_b64)
                                record_event("user_audio_chunk", {"bytes": len(pcm_data)})

                                # Encaminha imediatamente para o transcritor de baixa latência
                                if transcritor_dedicado.is_active:
                                    asyncio.create_task(transcritor_dedicado.send_audio(pcm_data))

                                # Repassa para a sessão do agente com controle refinado
                                agora = time.time()
                                esperando_resposta = assistant_state["busy"] and (agora - assistant_state["ultimo_envio_usuario"] < 1.2)
                                falando_agora = (agora - assistant_state["ultimo_audio"] < MIC_GRACE_S)
                                if allow_barge_in or (not esperando_resposta and not falando_agora):
                                    await session.send_realtime_input(
                                        audio=types.Blob(
                                            data=pcm_data,
                                            mime_type="audio/pcm;rate=16000"
                                        )
                                    )

                        elif msg_type in ("audio_stream_end", "end_of_audio", "fim_do_audio"):
                            record_event("user_audio_stream_end")
                            if transcritor_dedicado.is_active:
                                asyncio.create_task(transcritor_dedicado.send_audio_stream_end())
                            await session.send_realtime_input(audio_stream_end=True)

                        elif msg_type == "text":
                            user_text = msg.get("text", "").strip()
                            if user_text:
                                logger.info(f"Comando de texto do usuário: {user_text}")
                                # Se houver autorização pendente, palavras de confirmação/rejeição resolvem imediatamente
                                if pending_confirmations:
                                    txt_lower = user_text.lower().strip()
                                    palavras_sim = {"sim", "autorizar", "autorizado", "confirmar", "confirmado", "pode", "ok", "yes", "permitir", "conceder", "fazer teste"}
                                    palavras_nao = {"nao", "não", "negar", "negado", "cancelar", "cancela", "recusar", "no"}
                                    
                                    if any(txt_lower == p or txt_lower.startswith(p + " ") or txt_lower.endswith(" " + p) for p in palavras_sim):
                                        for cid, fut in list(pending_confirmations.items()):
                                            if not fut.done():
                                                fut.set_result(True)
                                        logger.info(f"✅ [POLICY CONFIRMED BY TEXT]: '{user_text}'")
                                        record_event("user_confirmed_via_text", {"text": user_text})
                                        if txt_lower in palavras_sim:
                                            continue
                                    elif any(txt_lower == p or txt_lower.startswith(p + " ") for p in palavras_nao):
                                        for cid, fut in list(pending_confirmations.items()):
                                            if not fut.done():
                                                fut.set_result(False)
                                        logger.info(f"❌ [POLICY DENIED BY TEXT]: '{user_text}'")
                                        record_event("user_denied_via_text", {"text": user_text})
                                        if txt_lower in palavras_nao:
                                            continue
                                record_event("user_text", {"text": user_text})
                                gemini_bridge.log_audit_event("USER", "chat_input", user_text)
                                assistant_state["busy"] = True
                                assistant_state["ultimo_envio_usuario"] = time.time()
                                assistant_state["audio_recebido_no_turno"] = 0
                                assistant_state["texto_recebido_no_turno"] = 0
                                assistant_state["ultima_ferramenta"] = None
                                assistant_state["ultimo_resultado_ferramenta"] = None
                                await session.send_client_content(
                                    turns=types.Content(
                                        role="user",
                                        parts=[types.Part(text=user_text)]
                                    ),
                                    turn_complete=True
                                )

                        elif msg_type == "get_status":
                            status = system_tools.get_system_status()
                            await websocket.send_json({"type": "system_status", "data": status})

                        elif msg_type in ("video", "screen_frame", "imagem"):
                            # Compartilhamento de tela do cliente para visão multimodal da Live API
                            frame_b64 = msg.get("data", "") or msg.get("dados", "")
                            if frame_b64:
                                frame = base64.b64decode(frame_b64)
                                record_event("screen_frame", {"bytes": len(frame)})
                                await session.send_realtime_input(
                                    video=types.Blob(data=frame, mime_type="image/jpeg")
                                )

                        elif msg_type in ("tool_confirmation", "confirmar_acao"):
                            # Resposta do usuário a uma ferramenta que exige autorização explícita
                            act_id = msg.get("id") or msg.get("id_confirmacao")
                            approved = bool(msg.get("approved") if "approved" in msg else msg.get("aprovado", True))
                            pending = pending_confirmations.pop(act_id, None)
                            if pending is not None and not pending.done():
                                pending.set_result(approved)
                            if approved:
                                policy_engine.approve_action(act_id, session_id=sessao_id, user_id=usuario_id)
                            else:
                                policy_engine.reject_action(act_id, session_id=sessao_id, user_id=usuario_id)

                # Worker 2: Lê injeções de prompt via painel web de depuração
                async def injection_worker():
                    while True:
                        inj_text = await active_session_queue.get()
                        logger.info(f"Injetando prompt na sessão ativa: '{inj_text}'")
                        assistant_state["busy"] = True
                        assistant_state["ultimo_envio_usuario"] = time.time()
                        assistant_state["audio_recebido_no_turno"] = 0
                        assistant_state["texto_recebido_no_turno"] = 0
                        assistant_state["ultima_ferramenta"] = None
                        assistant_state["ultimo_resultado_ferramenta"] = None
                        await session.send_client_content(
                            turns=types.Content(
                                role="user",
                                parts=[types.Part(text=inj_text)]
                            ),
                            turn_complete=True
                        )

                # Worker 3: Lê respostas da Gemini Live API continuamente para todos os turnos
                async def from_gemini_worker():
                    while True:
                        try:
                            async for response in session.receive():
                                server_content = response.server_content
                                if server_content is not None:
                                    if server_content.interrupted:
                                        assistant_state["busy"] = False
                                        record_event("interrupted")
                                        await websocket.send_json({"type": "interrupted"})
                                        continue

                                    model_turn = server_content.model_turn
                                    if model_turn is not None:
                                        for part in model_turn.parts:
                                            if part.inline_data and part.inline_data.data:
                                                record_event("model_audio_chunk", {"bytes": len(part.inline_data.data)})
                                                assistant_state["ultimo_audio"] = time.time()
                                                assistant_state["audio_recebido_no_turno"] += len(part.inline_data.data)
                                                audio_b64 = base64.b64encode(part.inline_data.data).decode("utf-8")
                                                await websocket.send_json({
                                                    "type": "audio",
                                                    "data": audio_b64
                                                })

                                    # Transcrição contínua da resposta falada pelo Gemini em tempo real
                                    if server_content.output_transcription and server_content.output_transcription.text:
                                        transcribed = server_content.output_transcription.text
                                        record_event("model_text", {"text": transcribed})
                                        assistant_state["texto_recebido_no_turno"] += len(transcribed)
                                        await websocket.send_json({
                                            "type": "text",
                                            "text": transcribed
                                        })
                                    elif model_turn is not None:
                                        # Fallback: se não houver output_transcription, envia texto textual do model_turn
                                        for part in model_turn.parts:
                                            if part.text:
                                                is_thought = getattr(part, "thought", False) or False
                                                record_event("model_text", {"text": part.text, "thought": is_thought})
                                                if not is_thought:
                                                    assistant_state["texto_recebido_no_turno"] += len(part.text)
                                                    await websocket.send_json({
                                                        "type": "text",
                                                        "text": part.text
                                                    })

                                    # Parcial em tempo real: se o transcritor dedicado não estiver ativo, usa nativo
                                    parcial = getattr(server_content, "interim_input_transcription", None)
                                    if parcial and parcial.text and not transcritor_dedicado.is_active:
                                        await on_transcricao_interim(parcial.text)

                                    # Transcrição da fala do usuário se disponível
                                    if server_content.input_transcription and server_content.input_transcription.text:
                                        user_trans = server_content.input_transcription.text
                                        await on_transcricao_final(user_trans)

                                    if server_content.turn_complete:
                                        assistant_state["texto_recebido_no_turno"] = 0
                                        # Resiliência de voz: se uma ferramenta foi concluída mas o modelo fechou o turno em silêncio
                                        if (assistant_state.get("ultima_ferramenta") 
                                                and assistant_state.get("audio_recebido_no_turno", 0) == 0 
                                                and assistant_state.get("texto_recebido_no_turno", 0) == 0):
                                            res_ferramenta = assistant_state.get("ultimo_resultado_ferramenta") or {}
                                            msg_fala = res_ferramenta.get("mensagem")
                                            if not msg_fala:
                                                if isinstance(res_ferramenta, dict):
                                                    itens = [f"{k}: {v}" for k, v in res_ferramenta.items() if k != "sucesso"]
                                                    msg_fala = f"Resultado de {assistant_state['ultima_ferramenta']}: {', '.join(itens)}"
                                                else:
                                                    msg_fala = str(res_ferramenta)
                                            logger.info("Modelo encerrou em silêncio após ferramenta. Enviando resposta de contingência: %s", msg_fala)
                                            record_event("model_text", {"text": msg_fala, "source": "tool_fallback"})
                                            await websocket.send_json({
                                                "type": "fallback_text",
                                                "text": msg_fala
                                            })

                                        assistant_state["busy"] = False
                                        assistant_state["ultima_ferramenta"] = None
                                        assistant_state["ultimo_resultado_ferramenta"] = None
                                        record_event("turn_complete")
                                        await websocket.send_json({"type": "turn_complete"})

                                # Tratamento de Function Calling (Ferramentas do SO)
                                tool_call = response.tool_call
                                if tool_call is not None:
                                    assistant_state["busy"] = True
                                    function_responses = []
                                    for call in tool_call.function_calls:
                                        func_name = call.name
                                        call_id = call.id
                                        args = call.args or {}

                                        record_event("tool_call", {"name": func_name, "args": args})
                                        await websocket.send_json({
                                            "type": "tool_call",
                                            "name": func_name,
                                            "args": args
                                        })

                                        # Avaliação de autorização pelo Policy Engine
                                        decision = policy_engine.evaluate(func_name, args, session_id=sessao_id, user_id=usuario_id)
                                        approved = True
                                        if decision.allowed and decision.requires_confirmation:
                                            approved = await request_user_confirmation(call_id, func_name, args, decision)

                                        if not decision.allowed:
                                            res = {"sucesso": False, "erro": f"Execução bloqueada pelo Policy Engine: {decision.reason}"}
                                            record_event("policy_blocked", {"name": func_name, "decision": decision.reason, "risk": decision.risk_level.value})
                                        elif not approved:
                                            res = {"sucesso": False, "erro": "Execução negada: o usuário não confirmou esta ação."}
                                            record_event("policy_denied_by_user", {"name": func_name, "risk": decision.risk_level.value, "args": args})
                                        else:
                                            executor = system_tools.TOOL_REGISTRY.get(func_name)
                                            if executor:
                                                try:
                                                    res = executor(**args)
                                                except Exception as exc:
                                                    res = {"sucesso": False, "erro": str(exc)}
                                            else:
                                                res = {"sucesso": False, "erro": f"Ferramenta {func_name} desconhecida."}

                                        assistant_state["ultima_ferramenta"] = func_name
                                        assistant_state["ultimo_resultado_ferramenta"] = res
                                        record_event("tool_result", {"name": func_name, "result": res})
                                        gemini_bridge.log_audit_event("JARVIS", f"tool_result:{func_name}", res, {"args": args})
                                        await websocket.send_json({
                                            "type": "tool_result",
                                            "name": func_name,
                                            "result": res
                                        })

                                        if func_name == "set_ide_mode":
                                            await websocket.send_json({
                                                "type": "ide_mode",
                                                "active": res.get("ide_mode", False)
                                            })

                                        if func_name == "toggle_telemetry_overlay":
                                            await websocket.send_json({
                                                "type": "toggle_telemetry",
                                                "active": res.get("active", True),
                                                "telemetry": res.get("telemetry", {})
                                            })

                                        if func_name == "set_control_mode":
                                            # A lease dá autoridade temporária ao mouse e ao teclado virtuais
                                            if res.get("sucesso") and res.get("control_mode"):
                                                lease = policy_engine.grant_control_lease(owner=sessao_id)
                                                record_event("control_lease_granted", lease)
                                            else:
                                                lease = policy_engine.revoke_control_lease(session_id=sessao_id)
                                                record_event("control_lease_revoked", lease)
                                            await websocket.send_json({
                                                "type": "control_mode",
                                                "active": res.get("control_mode", False),
                                                "lease": lease,
                                                "data": res
                                            })

                                        if func_name == "set_ide_mode":
                                            # A lease dá autoridade temporária ao agente Antigravity
                                            if res.get("sucesso") and res.get("ide_mode"):
                                                lease = policy_engine.grant_ide_lease(owner=sessao_id, user_id=usuario_id)
                                                record_event("ide_lease_granted", lease)
                                            else:
                                                lease = policy_engine.revoke_ide_lease(session_id=sessao_id, user_id=usuario_id)
                                                record_event("ide_lease_revoked", lease)
                                            await websocket.send_json({
                                                "type": "ide_mode",
                                                "active": res.get("ide_mode", False),
                                                "lease": lease,
                                                "data": res
                                            })

                                        function_responses.append(
                                            types.FunctionResponse(
                                                name=func_name,
                                                id=call_id,
                                                response={"result": res}
                                            )
                                        )

                                    if function_responses:
                                        await session.send_tool_response(function_responses=function_responses)

                        except Exception as gemini_err:
                            # Propaga: o TaskGroup cancela os demais workers e o handler externo
                            # faz o failover de conta. Encerrar em silêncio deixava a sessão zumbi.
                            logger.error(f"Erro no loop contínuo do Gemini Live: {gemini_err}")
                            raise

                # Worker 4: Encerra o Modo Controle assim que a lease de autoridade expira
                async def control_lease_worker():
                    while True:
                        await asyncio.sleep(5)
                        dono_desta_sessao = policy_engine.control_lease_status().get("owner") == sessao_id
                        if (dono_desta_sessao and system_tools.get_control_mode()
                                and not policy_engine.is_control_lease_active(sessao_id)):
                            system_tools.set_control_mode(False)
                            lease = policy_engine.revoke_control_lease(session_id=sessao_id)
                            record_event("control_lease_expired", lease)
                            logger.info("Lease de controle expirada: Modo Controle desativado automaticamente.")
                            await websocket.send_json({
                                "type": "control_mode",
                                "active": False,
                                "lease": lease,
                                "data": {"sucesso": True, "mensagem": "Autoridade de controle expirada, senhor. Modo Controle desativado."}
                            })

                # TaskGroup garante o cancelamento dos demais workers quando um deles termina
                # ou falha: sem isso, o injection_worker antigo continuaria consumindo a fila global.
                try:
                    async with asyncio.TaskGroup() as tg:
                        tg.create_task(ws_client_worker())
                        tg.create_task(injection_worker())
                        tg.create_task(from_gemini_worker())
                        tg.create_task(control_lease_worker())
                except* WebSocketDisconnect:
                    logger.info("Cliente Web HUD desconectado.")
                finally:
                    await transcritor_dedicado.close()
                    liberar_controle_da_sessao(sessao_id)
                record_event("client_disconnected")
                return

        except WebSocketDisconnect:
            logger.info("Cliente Web HUD desconectado.")
            record_event("client_disconnected")
            return
        except Exception as e:
            last_err = e
            next_idx = (idx + 1) % len(key_pool)
            record_event("account_failover", {
                "from_index": idx + 1,
                "to_index": next_idx + 1,
                "reason": str(e)
            })
            logger.warning(f"Conta {idx+1} falhou ({e}). Tentando próxima do pool...")
            try:
                await websocket.send_json({"type": "warn", "message": f"Conta {idx+1} falhou, rotacionando para próxima..."})
            except Exception:
                pass
            continue

    err_final = f"Todas as contas do pool falharam: {last_err}"
    record_event("error", {"message": err_final})
    try:
        await websocket.send_json({"type": "error", "message": err_final})
        await websocket.close()
    except Exception:
        pass
    finally:
        if active_live_socket == websocket:
            active_live_socket = None

if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("JARVIS_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 8000))
    print(f"\n=======================================================")
    print(f"⚡ J.A.R.V.I.S. Online - Interface em: http://{host}:{port}")
    print(f"⚡ Central de Depuração & Logs em: http://{host}:{port}/debug")
    print(f"🔒 Rede: Vinculado a {host} (Proteção contra acesso externo)")
    print(f"=======================================================\n")
    uvicorn.run(app, host=host, port=port)
