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
import asyncio
import logging
from typing import Dict, Optional

import secrets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, Header, HTTPException, Request, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from policy_engine import policy_engine
from google import genai
from google.genai import types

import system_tools
from plugin_manager import plugin_manager
import gemini_bridge
from monitoring.logger import (
    logger, record_event, get_recent_events,
    get_telemetry_summary, clear_logs, TEXT_LOG_FILE
)

app = FastAPI(title="JARVIS AI Assistant - Gemini Live")

JARVIS_SECRET_TOKEN = os.environ.get("JARVIS_TOKEN")
if not JARVIS_SECRET_TOKEN:
    JARVIS_SECRET_TOKEN = secrets.token_urlsafe(24)

# Tempo máximo de espera pela confirmação do usuário em ferramentas de risco
CONFIRMATION_TIMEOUT_S = int(os.environ.get("JARVIS_CONFIRMATION_TIMEOUT", "60"))

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

@app.get("/health")
@app.get("/api/health")
async def health_check():
    raw_keys = os.environ.get("GEMINI_API_KEYS", "")
    key_pool = [k.strip() for k in raw_keys.split(",") if k.strip()]
    has_key = bool(os.environ.get("GEMINI_API_KEY")) or bool(key_pool)
    return {
        "status": "online",
        "gemini_api_key_configured": has_key,
        "accounts_count": len(key_pool) if key_pool else (1 if has_key else 0),
        "omniroute_combo": os.environ.get("OMNIROUTE_COMBO", "jarvis"),
        "model": os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-native-audio-latest")
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
    record_event("plugin_toggle", {"plugin_id": plugin_id, "result": res})
    return res

@app.post("/api/plugins/install")
async def install_plugin_endpoint(payload: dict, _=Depends(verify_jarvis_token)):
    plugin_id = payload.get("plugin_id")
    res = plugin_manager.install_plugin(plugin_id)
    record_event("plugin_install", {"plugin_id": plugin_id, "result": res})
    return res


# ----------------- ENDPOINTS DO SISTEMA DE MONITORAMENTO E DEPURAÇÃO -----------------
@app.get("/api/debug/telemetry")
async def get_telemetry():
    return get_telemetry_summary()

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

            test_model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-native-audio-latest")
            test_config = types.LiveConnectConfig(response_modalities=[types.Modality.TEXT])
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
   - Sempre que o senhor pedir para abrir a pasta gemini, abrir a ponte de desenvolvimento ou consultar o canal de auditoria, chame 'antigravity_open_gemini_bridge'.
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
@app.websocket("/ws/live")
async def websocket_live_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("Cliente Web HUD conectado via WebSocket.")

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
        logger.warning("Tentativa de conexão WebSocket não autorizada: token inválido ou ausente.")
        record_event("auth_error", {"source": "ws_live", "reason": "invalid_or_missing_token"})
        await websocket.send_json({"type": "error", "message": "Não autorizado: JARVIS_TOKEN inválido ou ausente."})
        await websocket.close(code=1008, reason="Unauthorized")
        return

    voice_name = init_data.get("voice") or os.environ.get("JARVIS_VOICE", "Charon")
    req_model = init_data.get("model")
    if not req_model or "3.8" in req_model or "exp" in req_model:
        model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-native-audio-latest")
    else:
        model_name = req_model

    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        thinking_config=types.ThinkingConfig(thinking_budget=0),
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name)
            )
        ),
        system_instruction=types.Content(
            parts=[types.Part(text=JARVIS_SYSTEM_INSTRUCTION)]
        ),
        tools=build_gemini_tools(),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        realtime_input_config=types.RealtimeInputConfig(
            activity_handling=types.ActivityHandling.NO_INTERRUPTION
        )
    )

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
                await websocket.send_json({
                    "type": "connected",
                    "message": f"Sistemas online. Conectado ao modelo {model_name} com a voz {voice_name}.",
                    "voice": voice_name,
                    "model": model_name
                })
                await websocket.send_json({
                    "type": "ide_mode",
                    "active": system_tools.get_ide_mode()
                })
                await websocket.send_json({
                    "type": "control_mode",
                    "active": system_tools.get_control_mode()
                })
                logger.info(f"Sessão Gemini Live estabelecida com sucesso usando {model_name}!")
                assistant_state = {"busy": False}
                # Confirmações pendentes de ferramentas de risco: call_id -> Future(bool)
                pending_confirmations: Dict[str, asyncio.Future] = {}

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
                        "timeout_s": CONFIRMATION_TIMEOUT_S
                    })
                    record_event("policy_confirmation_request", {
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
                    while True:
                        msg_text = await websocket.receive_text()
                        msg = json.loads(msg_text)
                        msg_type = msg.get("type")

                        if msg_type == "audio":
                            audio_b64 = msg.get("data", "")
                            if audio_b64:
                                pcm_data = base64.b64decode(audio_b64)
                                record_event("user_audio_chunk", {"bytes": len(pcm_data)})
                                # Se o assistente estiver respondendo/falando, não repassa o microfone
                                # para evitar que o som dos alto-falantes cause falso barge-in/cancelamento
                                if not assistant_state["busy"]:
                                    await session.send_realtime_input(
                                        audio=types.Blob(
                                            data=pcm_data,
                                            mime_type="audio/pcm;rate=16000"
                                        )
                                    )

                        elif msg_type == "text":
                            user_text = msg.get("text", "").strip()
                            if user_text:
                                logger.info(f"Comando de texto do usuário: {user_text}")
                                record_event("user_text", {"text": user_text})
                                gemini_bridge.log_audit_event("USER", "chat_input", user_text)
                                assistant_state["busy"] = True
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

                        elif msg_type == "tool_confirmation":
                            # Resposta do usuário a uma ferramenta que exige autorização explícita
                            pending = pending_confirmations.pop(msg.get("id"), None)
                            if pending is not None and not pending.done():
                                pending.set_result(bool(msg.get("approved")))

                # Worker 2: Lê injeções de prompt via painel web de depuração
                async def injection_worker():
                    while True:
                        inj_text = await active_session_queue.get()
                        logger.info(f"Injetando prompt na sessão ativa: '{inj_text}'")
                        assistant_state["busy"] = True
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
                                            if part.text:
                                                is_thought = getattr(part, "thought", False) or False
                                                record_event("model_text", {"text": part.text, "thought": is_thought})
                                                clean_part = part.text.strip()
                                                if (
                                                    not is_thought
                                                    and not clean_part.startswith("<ctrl")
                                                    and not clean_part.startswith("**")
                                                    and not clean_part.startswith("I've processed")
                                                    and not clean_part.startswith("Okay, I'm")
                                                    and not clean_part.startswith("Yes, I can hear")
                                                ):
                                                    await websocket.send_json({
                                                        "type": "text",
                                                        "text": part.text
                                                    })
                                            if part.inline_data and part.inline_data.data:
                                                record_event("model_audio_chunk", {"bytes": len(part.inline_data.data)})
                                                audio_b64 = base64.b64encode(part.inline_data.data).decode("utf-8")
                                                await websocket.send_json({
                                                    "type": "audio",
                                                    "data": audio_b64
                                                })

                                    # Transcrição da resposta falada pelo Gemini em tempo real
                                    if server_content.output_transcription and server_content.output_transcription.text:
                                        transcribed = server_content.output_transcription.text
                                        record_event("model_text", {"text": transcribed})
                                        await websocket.send_json({
                                            "type": "text",
                                            "text": transcribed
                                        })

                                    # Transcrição da fala do usuário se disponível
                                    if server_content.input_transcription and server_content.input_transcription.text:
                                        user_trans = server_content.input_transcription.text
                                        await websocket.send_json({
                                            "type": "user_transcription",
                                            "text": user_trans
                                        })

                                    if server_content.turn_complete:
                                        assistant_state["busy"] = False
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
                                        decision = policy_engine.evaluate(func_name, args)
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

                                        if func_name == "set_control_mode":
                                            # A lease dá autoridade temporária ao mouse e ao teclado virtuais
                                            if res.get("sucesso") and res.get("control_mode"):
                                                lease = policy_engine.grant_control_lease(owner="hud")
                                                record_event("control_lease_granted", lease)
                                            else:
                                                lease = policy_engine.revoke_control_lease()
                                                record_event("control_lease_revoked", lease)
                                            await websocket.send_json({
                                                "type": "control_mode",
                                                "active": res.get("control_mode", False),
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
                            logger.error(f"Erro no loop contínuo do Gemini Live: {gemini_err}")
                            break

                # Executa todos os workers sem cancelamentos indesejados
                await asyncio.gather(ws_client_worker(), injection_worker(), from_gemini_worker())
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
