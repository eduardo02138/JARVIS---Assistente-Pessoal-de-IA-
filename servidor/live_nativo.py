"""Sessão Gemini Live nativa (/ws/live) e injeção de prompts do painel de depuração.

Caminho completo do Extended Thinking: thinking_config + ferramentas NON_BLOCKING
executadas em background, com confirmação do usuário pelo Policy Engine.
"""

import asyncio
import base64
import inspect
import json
import os
import secrets
import threading
import time
from typing import Dict, Optional, Set

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from google import genai
from google.adk.events import Event
from google.genai import types

import gemini_bridge
import system_tools
import transcricao
from live_protocolo import (
    PALAVRAS_SIM,
    EncerramentoLimpoDaSessao,
    desativar_ping_timeout,
    eh_recusa_pura,
    palavra_confirma,
    palavra_recusa,
)
from monitoring.logger import logger, record_event
from policy_engine import policy_engine
from provider_router import GoogleStudioProvider, provider_router
from resultados_de_ferramentas import limitar_resultado
from servidor.comum import IDIOMA, VOZ, env_flag, modelo_live_padrao
from servidor.falhas import classificar_falha
from servidor.instrucoes import JARVIS_SYSTEM_INSTRUCTION
from servidor.runtime_adk import memory_service_adk
from servidor.seguranca import (
    JARVIS_SECRET_TOKEN,
    liberar_controle_da_sessao,
    registrar_rejeicao_de_token,
    verify_jarvis_token,
)

router = APIRouter()

# Tempo máximo de espera pela confirmação do usuário em ferramentas de risco
CONFIRMATION_TIMEOUT_S = int(os.environ.get("JARVIS_CONFIRMATION_TIMEOUT", "30"))

# Silêncio do assistente (segundos) a partir do qual o microfone volta a ser encaminhado
MIC_GRACE_S = float(os.environ.get("JARVIS_MIC_GRACE", "0.3"))

# Ferramentas mutantes rápidas (GUI/estado) rodam direto no event loop: sem
# thread zumbi pós-timeout e sem corrida com o worker de leases do PolicyEngine.
_TOOLS_MUTANTES = {"set_control_mode", "set_ide_mode", "toggle_telemetry_overlay"}

# Teto de threads simultâneas para ferramentas síncronas (asyncio.to_thread):
# limita exaustão do pool do loop e zumbis acumulados após timeouts de wait_for.
_SEM_TOOL_THREADS = threading.Semaphore(3)

# Fila global para injeção de comandos vindos da tela de depuração
active_session_queue: asyncio.Queue = asyncio.Queue()


@router.post("/api/inject_prompt")
@router.post("/api/inject-prompt")
@router.post("/api/debug/inject-prompt")
async def inject_prompt(payload: dict, _=Depends(verify_jarvis_token)):
    prompt = payload.get("prompt", "").strip()
    if not prompt:
        return JSONResponse({"status": "error", "message": "Prompt vazio"}, status_code=400)
    await active_session_queue.put(prompt)
    record_event("user_text", {"text": prompt, "source": "debug_injector"})
    return {"status": "ok", "message": f"Prompt injetado com sucesso: '{prompt}'"}


def build_gemini_tools(behavior_nao_bloqueante: bool = False):
    """Monta as ferramentas para a sessão Live a partir do registro central.

    `behavior_nao_bloqueante=True` (obrigatório para `gemini-3.8-live-extended-thinking`)
    marca toda FunctionDeclaration com behavior="NON_BLOCKING": a chamada vira
    assíncrona e o modelo segue falando enquanto a ferramenta executa.
    """
    declaracoes = []
    for decl in system_tools.GEMINI_FUNCTION_DECLARATIONS:
        kwargs = {}
        if behavior_nao_bloqueante:
            kwargs["behavior"] = "NON_BLOCKING"
        declaracoes.append(
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
                ),
                **kwargs,
            )
        )
    return [types.Tool(function_declarations=declaracoes)]


def montar_thinking_config(model_name: str) -> dict:
    """Raciocínio da LiveConnectConfig por modelo.

    - `gemini-3.8-live`: docs instruem omitir thinking_config (raciocínio
      intercalado nativo por padrão).
    - `gemini-3.8-live-extended-thinking`: raciocínio em segundo plano,
      nível configurável via LIVE_THINKING_LEVEL (low|medium|high).
    - Modelos legados (2.5): budget zerado para resposta imediata.
    """
    if "extended-thinking" in model_name:
        nivel = os.environ.get("LIVE_THINKING_LEVEL", "low").strip().lower()
        if nivel not in ("low", "medium", "high"):
            nivel = "low"
        return {"thinking_config": types.ThinkingConfig(thinking_level=nivel)}
    if "3.8" not in model_name:
        return {"thinking_config": types.ThinkingConfig(thinking_budget=0)}
    return {}


# Controle de Conexão Live Única (Single Active Live Session)
# Impede que duas interfaces, abas ou janelas fiquem com a sessão Live aberta concorrentemente
active_live_socket: Optional[WebSocket] = None


@router.websocket("/ws/live")
async def websocket_live_endpoint(websocket: WebSocket):
    global active_live_socket
    await websocket.accept()

    ws_send_lock = asyncio.Lock()
    _conn_background_tasks: Set[asyncio.Task] = set()

    def _schedule_conn_task(coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        _conn_background_tasks.add(task)
        task.add_done_callback(_conn_background_tasks.discard)
        return task

    # Memória de longo prazo da sessão nativa: eventos de conversa (texto do usuário
    # e respostas do modelo) são acumulados e persistidos no jarvis.memory service.
    # O caminho ADK usa add_session_to_memory; o nativo não cria eventos ADK, então
    # este coletor alimenta o mesmo serviço via add_events_to_memory.
    eventos_memoria_nativa: list = []
    model_textos_do_turno: list = []

    async def _flush_memoria_nativa():
        """Persiste os eventos acumulados da conversa nativa na memória de longo prazo."""
        if not eventos_memoria_nativa:
            return
        lote = list(eventos_memoria_nativa)
        eventos_memoria_nativa.clear()
        try:
            await memory_service_adk.add_events_to_memory(
                app_name="assistente",
                user_id=usuario_id,
                events=lote,
                session_id=sessao_id,
            )
            logger.info(
                "Conversa salva na memória de longo prazo (%d eventos, sessão %s).",
                len(lote),
                sessao_id,
            )
        except Exception as exc:
            logger.warning("Falha ao salvar conversa nativa na memória de longo prazo: %s", exc)

    async def safe_send_json(payload: dict):
        async with ws_send_lock:
            await websocket.send_json(payload)

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

    # O slot de sessão única é liberado em qualquer saída desta conexão; uma conexão
    # já substituída por outra não mexe no slot da nova.
    try:
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
            registrar_rejeicao_de_token(
                websocket, rota="/ws/live", fonte="ws_live", origem=websocket.query_params.get("origem", "")
            )
            await safe_send_json({"type": "error", "message": "Não autorizado: JARVIS_TOKEN inválido ou ausente."})
            await websocket.close(code=1008, reason="Unauthorized")
            return

        voice_name = init_data.get("voice") or VOZ
        # O modelo pedido pelo cliente é respeitado; o .env define o padrão.
        # O bloqueio anterior forçava o downgrade de qualquer modelo 3.8 para o 2.5.
        req_model = (init_data.get("model") or "").strip()
        model_name = req_model or modelo_live_padrao()
        is_extended = "extended-thinking" in model_name
        # LIVE_AUTO_LANG: detecta e alterna idioma sozinho durante a conversa.
        # Sem a flag, o idioma fixo (JARVIS_LANGUAGE) evita troca por ruído.
        auto_lang = env_flag("LIVE_AUTO_LANG")
        req_provider = (init_data.get("provider") or "").strip() or provider_router.active_provider
        allow_barge_in = bool(init_data.get("barge_in", False)) or env_flag("JARVIS_BARGE_IN")
        activity_handling = (
            types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS
            if allow_barge_in
            else types.ActivityHandling.NO_INTERRUPTION
        )

        speech_lang_kwargs = {} if auto_lang else {"language_code": IDIOMA}
        live_connect_kwargs = {
            "response_modalities": [types.Modality.AUDIO],
            "speech_config": types.SpeechConfig(
                **speech_lang_kwargs,
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name)
                )
            ),
            "system_instruction": types.Content(
                parts=[types.Part(text=JARVIS_SYSTEM_INSTRUCTION)]
            ),
            "tools": build_gemini_tools(behavior_nao_bloqueante=is_extended),
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
        live_connect_kwargs.update(montar_thinking_config(model_name))

        config = types.LiveConnectConfig(**live_connect_kwargs)

        # Isolamento de Segredos: Pool de contas lidas estritamente do backend (.env)
        key_pool = sorted(GoogleStudioProvider.get_keys(), key=lambda k: 0 if k.startswith("AIzaSy") else 1)
        if not key_pool:
            err_msg = "Nenhuma chave no pool. Configure GEMINI_API_KEYS ou GEMINI_API_KEY no .env do servidor."
            record_event("error", {"message": err_msg})
            await safe_send_json({"type": "error", "message": err_msg})
            await websocket.close()
            return

        last_err = None
        consecutive_failures = 0
        for idx, try_key in enumerate(key_pool):
            sessao_estabelecida = False
            try:
                logger.info(f"Tentando conectar com conta {idx+1}/{len(key_pool)} do pool (modelo: {model_name})...")
                active_client = genai.Client(api_key=try_key)

                # Desativa timeout de ping que derrubava conexões após ~45s de silêncio
                desativar_ping_timeout(active_client)

                async with active_client.aio.live.connect(model=model_name, config=config) as session:
                    sessao_estabelecida = True
                    record_event("client_connected", {
                        "account_index": idx + 1,
                        "total_accounts": len(key_pool),
                        "model": model_name,
                        "voice": voice_name
                    })
                    consecutive_failures = 0
                    # Identidade desta sessão: a lease de controle físico pertence a ela
                    sessao_id = secrets.token_urlsafe(12)
                    # A sessão Live nativa é a única identidade: dona E usuária das leases do Modo IDE
                    usuario_id = sessao_id
                    live_info = provider_router.live_provider()
                    provedor_efetivo = live_info["provider"]
                    await safe_send_json({
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
                    await safe_send_json({
                        "type": "ide_mode",
                        # Como no Modo Controle: só vale com a lease desta sessão
                        "active": system_tools.get_ide_mode() and policy_engine.is_ide_lease_active(sessao_id, usuario_id)
                    })
                    # Uma nova sessão não herda o Modo Controle: a autoridade é de quem tem a lease
                    await safe_send_json({
                        "type": "control_mode",
                        "active": system_tools.get_control_mode() and policy_engine.is_control_lease_active(sessao_id),
                        "lease": policy_engine.control_lease_status()
                    })
                    await safe_send_json({
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
                        "ultimo_resultado_ferramenta": None,
                        "turno_texto_ativo": False,
                    }
                    # Confirmações pendentes de ferramentas de risco: call_id -> Future(bool)
                    pending_confirmations: Dict[str, asyncio.Future] = {}

                    # Rastreio de estado do Extended Thinking (gemini-3.8-live-extended-thinking).
                    # O modelo roda o raciocínio em background e sinaliza a sessão via
                    # interaction_status (IN_PROGRESS/IDLE). turno_concluido evita dupla
                    # finalização quando turn_complete e IDLE chegam juntos.
                    interaction_state = {"status": None}
                    turno_concluido = {"done": False}
                    ferramentas_em_voo = {"n": 0}

                    # Transcritor dedicado em tempo real (gemini-3.5-transcribe-live)
                    ultima_transcricao_usuario = {"texto": "", "tempo": 0.0}

                    async def on_transcricao_interim(texto: str):
                        if not texto:
                            return
                        try:
                            await safe_send_json({
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
                            await safe_send_json({
                                "type": "user_transcription",
                                "text": texto
                            })
                            # Aprovação por comando de voz no WebSocket nativo (parser único de live_protocolo)
                            if palavra_confirma(texto):
                                for cid, fut in list(pending_confirmations.items()):
                                    if not fut.done():
                                        fut.set_result(True)
                                        logger.info("✅ [POLICY CONFIRMED BY VOICE]: '%s'", texto)
                                        record_event("user_confirmed_via_voice", {"text": texto})
                                        await safe_send_json({
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
                        await safe_send_json({
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
                                        _schedule_conn_task(transcritor_dedicado.send_audio_stream_end())
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
                                        _schedule_conn_task(transcritor_dedicado.send_audio(pcm_data))

                                    # Repassa para a sessão do agente com controle refinado
                                    agora = time.time()
                                    esperando_resposta = assistant_state["busy"] and (agora - assistant_state["ultimo_envio_usuario"] < 10.0)
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
                                    _schedule_conn_task(transcritor_dedicado.send_audio_stream_end())
                                await session.send_realtime_input(audio_stream_end=True)

                            elif msg_type == "text":
                                user_text = msg.get("text", "").strip()
                                if user_text:
                                    logger.info(f"Comando de texto do usuário: {user_text}")
                                    # Se houver autorização pendente, palavras de confirmação/rejeição resolvem imediatamente
                                    if pending_confirmations:
                                        txt_lower = user_text.lower().strip()
                                        if palavra_confirma(user_text):
                                            for cid, fut in list(pending_confirmations.items()):
                                                if not fut.done():
                                                    fut.set_result(True)
                                            logger.info(f"✅ [POLICY CONFIRMED BY TEXT]: '{user_text}'")
                                            record_event("user_confirmed_via_text", {"text": user_text})
                                            if txt_lower in PALAVRAS_SIM:
                                                continue
                                        elif palavra_recusa(user_text):
                                            for cid, fut in list(pending_confirmations.items()):
                                                if not fut.done():
                                                    fut.set_result(False)
                                            logger.info(f"❌ [POLICY DENIED BY TEXT]: '{user_text}'")
                                            record_event("user_denied_via_text", {"text": user_text})
                                            if eh_recusa_pura(user_text):
                                                continue
                                    record_event("user_text", {"text": user_text})
                                    gemini_bridge.log_audit_event("USER", "chat_input", user_text)
                                    eventos_memoria_nativa.append(
                                        Event(
                                            author="user",
                                            content=types.Content(
                                                parts=[types.Part(text=user_text)]
                                            ),
                                            session_id=sessao_id,
                                        )
                                    )
                                    assistant_state["busy"] = True
                                    assistant_state["turno_texto_ativo"] = True
                                    turno_concluido["done"] = False
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
                                await safe_send_json({"type": "system_status", "data": status})

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
                            assistant_state["turno_texto_ativo"] = True
                            turno_concluido["done"] = False
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
                        # Lock do envio de tool response: a sessão Live não aceita chamadas
                        # concorrentes de send_tool_response quando ferramentas rodam em background.
                        tool_resp_lock = asyncio.Lock()

                        async def executar_ferramenta(func_name: str, call_id: str, args: dict):
                            """Pipeline de Function Calling em modo assíncrono (ferramentas NON_BLOCKING).

                            Roda em background (asyncio.create_task): o receive loop continua
                            consumindo áudio/raciocínio enquanto a ferramenta trabalha. No
                            gemini-3.8-live default (flip async), as tools chegam em lote; o
                            lock serializa o send_tool_response para respeitar a sessão.
                            """
                            ferramentas_em_voo["n"] += 1
                            try:
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
                                        timeout_ferramenta = 90.0 if func_name in ("antigravity_run_prompt", "deep_research_start") else 30.0
                                        try:
                                            if inspect.iscoroutinefunction(executor):
                                                res = await asyncio.wait_for(executor(**args), timeout=timeout_ferramenta)
                                            elif func_name in _TOOLS_MUTANTES:
                                                res = executor(**args)
                                            else:
                                                with _SEM_TOOL_THREADS:
                                                    res = await asyncio.wait_for(asyncio.to_thread(executor, **args), timeout=timeout_ferramenta)
                                        except asyncio.TimeoutError:
                                            res = {"sucesso": False, "erro": f"Timeout ({int(timeout_ferramenta)}s) na execução da ferramenta {func_name}."}
                                        except Exception as exc:
                                            res = {"sucesso": False, "erro": str(exc)}
                                    else:
                                        res = {"sucesso": False, "erro": f"Ferramenta {func_name} desconhecida."}

                                # Serializável e dentro do limite antes de chegar ao modelo, ao HUD e à auditoria
                                res = limitar_resultado(res)
                                assistant_state["ultima_ferramenta"] = func_name
                                assistant_state["ultimo_resultado_ferramenta"] = res
                                record_event("tool_result", {"name": func_name, "result": res})
                                gemini_bridge.log_audit_event("JARVIS", f"tool_result:{func_name}", res, {"args": args})
                                await safe_send_json({"type": "tool_result", "name": func_name, "result": res})

                                if func_name == "toggle_telemetry_overlay":
                                    await safe_send_json({
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
                                    await safe_send_json({
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
                                    await safe_send_json({
                                        "type": "ide_mode",
                                        "active": res.get("ide_mode", False),
                                        "lease": lease,
                                        "data": res
                                    })

                                async with tool_resp_lock:
                                    await session.send_tool_response(function_responses=[
                                        types.FunctionResponse(name=func_name, id=call_id, response={"result": res})
                                    ])
                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:
                                logger.exception("Falha no pipeline assíncrono da ferramenta %s: %s", func_name, exc)
                                try:
                                    async with tool_resp_lock:
                                        await session.send_tool_response(function_responses=[
                                            types.FunctionResponse(name=func_name, id=call_id, response={"result": {"sucesso": False, "erro": str(exc)}})
                                        ])
                                except Exception:
                                    pass
                            finally:
                                ferramentas_em_voo["n"] -= 1

                        async def finalizar_turno():
                            """Limpa o estado de turno após conclusão (turn_complete ou IDLE)."""
                            if turno_concluido["done"]:
                                return
                            turno_concluido["done"] = True
                            assistant_state["texto_recebido_no_turno"] = 0
                            # Resiliência de voz: ferramenta concluída mas o modelo fechou o turno em silêncio
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
                                model_textos_do_turno.append(msg_fala)
                                await safe_send_json({"type": "fallback_text", "text": msg_fala})

                            assistant_state["busy"] = False
                            assistant_state["turno_texto_ativo"] = False
                            assistant_state["ultima_ferramenta"] = None
                            assistant_state["ultimo_resultado_ferramenta"] = None
                            record_event("turn_complete")
                            await safe_send_json({"type": "turn_complete"})

                            # Persiste a conversa deste turno na memória de longo prazo
                            if model_textos_do_turno:
                                eventos_memoria_nativa.append(
                                    Event(
                                        author="model",
                                        content=types.Content(
                                            parts=[types.Part(text="\n".join(model_textos_do_turno))]
                                        ),
                                        session_id=sessao_id,
                                    )
                                )
                                model_textos_do_turno.clear()
                            await _flush_memoria_nativa()

                        while True:
                            try:
                                async for response in session.receive():
                                    # Estado de iteração do Extended Thinking (IN_PROGRESS/IDLE).
                                    # Chega como campo do topo em LiveServerMessage.
                                    status_interacao = getattr(response, "interaction_status", None)
                                    if status_interacao and status_interacao != interaction_state["status"]:
                                        interaction_state["status"] = status_interacao
                                        record_event("interaction_status", {"status": status_interacao})
                                        await safe_send_json({"type": "interaction_status", "status": status_interacao})
                                        if status_interacao == "IN_PROGRESS":
                                            assistant_state["busy"] = True
                                            turno_concluido["done"] = False
                                            model_textos_do_turno.clear()
                                        elif status_interacao == "IDLE" and is_extended:
                                            await finalizar_turno()

                                    server_content = getattr(response, "server_content", None)
                                    if server_content is not None:
                                        if server_content.interrupted:
                                            assistant_state["busy"] = False
                                            assistant_state["turno_texto_ativo"] = False
                                            record_event("interrupted")
                                            await safe_send_json({"type": "interrupted"})
                                            continue

                                        model_turn = server_content.model_turn
                                        if model_turn is not None:
                                            for part in model_turn.parts:
                                                if part.inline_data and part.inline_data.data:
                                                    record_event("model_audio_chunk", {"bytes": len(part.inline_data.data)})
                                                    assistant_state["ultimo_audio"] = time.time()
                                                    assistant_state["audio_recebido_no_turno"] += len(part.inline_data.data)
                                                    audio_b64 = base64.b64encode(part.inline_data.data).decode("utf-8")
                                                    await safe_send_json({
                                                        "type": "audio",
                                                        "data": audio_b64
                                                    })

                                        # Transcrição contínua da resposta falada pelo Gemini em tempo real
                                        if server_content.output_transcription and server_content.output_transcription.text:
                                            transcribed = server_content.output_transcription.text
                                            record_event("model_text", {"text": transcribed})
                                            assistant_state["texto_recebido_no_turno"] += len(transcribed)
                                            model_textos_do_turno.append(transcribed)
                                            await safe_send_json({
                                                "type": "text",
                                                "text": transcribed
                                            })
                                        elif model_turn is not None:
                                            # Fallback: se não houver output_transcription, envia texto textual do model_turn
                                            for part in model_turn.parts:
                                                if part.text:
                                                    is_thought = getattr(part, "thought", False) or False
                                                    record_event("model_text", {"text": part.text, "thought": is_thought})
                                                    if is_thought:
                                                        # Raciocínio explícito do Extended Thinking: exibido no HUD como blur/collapse
                                                        await safe_send_json({"type": "thought", "text": part.text})
                                                    else:
                                                        assistant_state["texto_recebido_no_turno"] += len(part.text)
                                                        model_textos_do_turno.append(part.text)
                                                        await safe_send_json({
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
                                            # No gemini-3.8-live o turn_complete encerra o turno; no
                                            # extended-thinking o fim real chega com status IDLE
                                            # (turn_complete pode vir ainda com ferramentas em voo).
                                            if not is_extended or interaction_state["status"] == "IDLE":
                                                await finalizar_turno()

                                    # Tratamento de Function Calling (Ferramentas do SO)
                                    tool_call = getattr(response, "tool_call", None)
                                    if tool_call is not None:
                                        assistant_state["busy"] = True
                                        turno_concluido["done"] = False
                                        for call in tool_call.function_calls:
                                            func_name = call.name
                                            call_id = call.id
                                            args = call.args or {}

                                            record_event("tool_call", {"name": func_name, "args": args})
                                            await safe_send_json({
                                                "type": "tool_call",
                                                "name": func_name,
                                                "args": args
                                            })

                                            # Execução assíncrona: não bloqueia o recebimento de
                                            # raciocínio e áudio do Gemini enquanto a ferramenta roda.
                                            # A referência fica retida (contra GC) e é cancelada no fim da conexão.
                                            tarefa = asyncio.create_task(executar_ferramenta(func_name, call_id, args))
                                            _conn_background_tasks.add(tarefa)
                                            tarefa.add_done_callback(_conn_background_tasks.discard)

                            except Exception as gemini_err:
                                codigo_fechamento = getattr(gemini_err, "code", None)
                                if codigo_fechamento == 1000:
                                    logger.info("Conexão do Gemini Live encerrada de forma limpa pelo servidor (close %s).", codigo_fechamento)
                                    raise EncerramentoLimpoDaSessao() from gemini_err
                                err_str = str(gemini_err)
                                codigo_transiente = (
                                    codigo_fechamento in (1001, 1011)
                                    or "1011" in err_str
                                    or "1001" in err_str
                                    or "Internal error encountered" in err_str
                                )
                                if codigo_transiente:
                                    logger.warning("Desconexão transitória da Google Gemini Live API (%s). Acionando failover de conta...", gemini_err)
                                else:
                                    logger.exception("Erro no loop contínuo do Gemini Live: %s", gemini_err)
                                raise

                    # Worker 4: encerra os Modos Controle e IDE quando a lease de autoridade expira
                    async def control_lease_worker():
                        while True:
                            await asyncio.sleep(5)
                            ide = policy_engine.ide_lease_status()
                            if ide.get("owner") == sessao_id and not ide.get("ativa") and system_tools.get_ide_mode():
                                system_tools.set_ide_mode(False)
                                lease_ide = policy_engine.revoke_ide_lease(session_id=sessao_id)
                                record_event("ide_lease_expired", lease_ide)
                                logger.info("Lease do Modo IDE expirada por inatividade: Modo IDE desativado.")
                                await safe_send_json({
                                    "type": "ide_mode",
                                    "active": False,
                                    "lease": lease_ide,
                                    "data": {"sucesso": True, "mensagem": "Modo IDE encerrado por inatividade, senhor. Diga 'ativar modo IDE' para retomar."}
                                })
                            dono_desta_sessao = policy_engine.control_lease_status().get("owner") == sessao_id
                            if (dono_desta_sessao and system_tools.get_control_mode()
                                    and not policy_engine.is_control_lease_active(sessao_id)):
                                system_tools.set_control_mode(False)
                                lease = policy_engine.revoke_control_lease(session_id=sessao_id)
                                record_event("control_lease_expired", lease)
                                logger.info("Lease de controle expirada: Modo Controle desativado automaticamente.")
                                await safe_send_json({
                                    "type": "control_mode",
                                    "active": False,
                                    "lease": lease,
                                    "data": {"sucesso": True, "mensagem": "Autoridade de controle expirada, senhor. Modo Controle desativado."}
                                })

                    # Worker 5: Watchdog para recuperação automática se a Live API silenciar sem resposta
                    async def turn_watchdog_worker():
                        while True:
                            await asyncio.sleep(5)
                            if assistant_state.get("busy"):
                                agora = time.time()
                                ultimo_envio = assistant_state.get("ultimo_envio_usuario", 0.0)
                                ultimo_aud = assistant_state.get("ultimo_audio", 0.0)
                                decorrido = agora - max(ultimo_envio, ultimo_aud)
                                if decorrido > 20.0:
                                    logger.warning("Watchdog: turno inerte por mais de 20s sem resposta. Destravando sessão.")
                                    assistant_state["busy"] = False
                                    assistant_state["turno_texto_ativo"] = False
                                    turno_concluido["done"] = True
                                    await safe_send_json({"type": "turn_complete"})

                    # TaskGroup garante o cancelamento dos demais workers quando um deles termina
                    # ou falha: sem isso, o injection_worker antigo continuaria consumindo a fila global.
                    try:
                        async with asyncio.TaskGroup() as tg:
                            tg.create_task(ws_client_worker())
                            tg.create_task(injection_worker())
                            tg.create_task(from_gemini_worker())
                            tg.create_task(control_lease_worker())
                            tg.create_task(turn_watchdog_worker())
                    except* WebSocketDisconnect:
                        logger.info("Cliente Web HUD desconectado.")
                    except* EncerramentoLimpoDaSessao:
                        logger.info("Sessão do Gemini Live encerrada de forma limpa pela API (close 1000/1001).")
                    finally:
                        try:
                            if model_textos_do_turno:
                                eventos_memoria_nativa.append(
                                    Event(
                                        author="model",
                                        content=types.Content(
                                            parts=[types.Part(text="\n".join(model_textos_do_turno))]
                                        ),
                                        session_id=sessao_id,
                                    )
                                )
                                model_textos_do_turno.clear()
                            await _flush_memoria_nativa()
                        except Exception:
                            pass
                        await transcritor_dedicado.close()
                        for t in list(_conn_background_tasks):
                            if not t.done():
                                t.cancel()
                        liberar_controle_da_sessao(sessao_id)
                    record_event("client_disconnected")
                    return

            except WebSocketDisconnect:
                logger.info("Cliente Web HUD desconectado.")
                record_event("client_disconnected")
                return
            except Exception as e:
                last_err = e
                falha = classificar_falha(e)
                consecutive_failures += 1
                next_idx = (idx + 1) % len(key_pool)
                motivo_falha = str(e)
                if hasattr(e, "exceptions") and e.exceptions:
                    sub_err = e.exceptions[0]
                    motivo_falha = f"{type(sub_err).__name__}: {sub_err}"
                record_event("account_failover", {
                    "from_index": idx + 1,
                    "to_index": next_idx + 1,
                    "reason": motivo_falha,
                    "motivo": falha.motivo.value,
                })
                if falha.definitiva and not sessao_estabelecida:
                    # Configuração recusada ao conectar: as outras contas falhariam igual.
                    # Já no meio da conversa, reconectar abre uma sessão nova e limpa (segue abaixo).
                    logger.warning(f"Conta {idx+1} recusou a conexão ({falha.motivo.value}: {motivo_falha}). Outra conta não resolve.")
                    break
                # Cota e chave recusada são da conta: a próxima segue sem espera
                espera = 0 if falha.girar_chave else min(1 << (consecutive_failures - 1), 30)
                logger.warning(f"Conta {idx+1} falhou ({falha.motivo.value}: {motivo_falha}). Tentando próxima do pool em {espera}s...")
                try:
                    await safe_send_json({"type": "warn", "message": f"Conta {idx+1} falhou, rotacionando para próxima..."})
                except Exception:
                    pass
                if espera:
                    await asyncio.sleep(espera)
                continue

        falha_final = classificar_falha(last_err) if last_err is not None else None
        if falha_final is not None and falha_final.definitiva and not sessao_estabelecida:
            err_final = f"Sessão Live recusada pelo provedor: {falha_final.mensagem()}"
        else:
            err_final = f"Todas as contas do pool falharam: {last_err}"
        record_event("error", {"message": err_final})
        try:
            await safe_send_json({"type": "error", "message": err_final})
            await websocket.close()
        except Exception:
            pass
    finally:
        if active_live_socket is websocket:
            active_live_socket = None
