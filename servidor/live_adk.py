"""Sessão de voz Live pelo Google ADK (/ws/live_adk): agentes, memória, skills e MCP."""

import asyncio
import base64
import json
from typing import Optional, Set

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from google.adk.agents import LiveRequestQueue
from google.adk.agents.run_config import RunConfig
from google.genai import types
from google.genai.errors import ClientError, ServerError

import transcricao
from agentes.assistente import MODELO_LIVE_RESERVA
from live_protocolo import palavra_confirma
from monitoring.logger import logger, record_event
from policy_engine import policy_engine
from servidor.comum import IDIOMA, VOZ, env_flag, env_json
from servidor.runtime_adk import (
    CAMINHO_VOZ,
    girar_chave_adk,
    memory_service_adk,
    obter_runner_adk,
    session_service_adk,
    trocar_modelo,
)
from servidor.seguranca import JARVIS_SECRET_TOKEN, registrar_rejeicao_de_token, sessao_aceita

router = APIRouter()


def montar_run_config(modelo: str = "") -> RunConfig:
    """Modalidades, voz, transcrição e tuning opcional da sessão Live do ADK.

    Recursos extras (proatividade, diálogo afetivo, VAD etc.) só entram na
    configuração quando as variáveis de ambiente os ativam:
      LIVE_PROATIVITY, LIVE_AFFECTIVE_DIALOG, LIVE_EXPLICIT_VAD,
      LIVE_SAVE_BLOB, LIVE_VAD_DISABLED, LIVE_METADADOS, LIVE_AUTO_LANG.

    Limitação do caminho ADK: o RunConfig do ADK instalado não aceita
    thinking_config nem behavior nas tools. Para `gemini-3.8-live-extended-thinking`
    completo (raciocínio em 2º plano + tools NON_BLOCKING), use o caminho nativo
    /ws/live. Aqui o modelo extended segue utilizável com os defaults da API.
    """
    modelado = modelo or ""
    cfg: dict = {
        "response_modalities": ["AUDIO"],
        "speech_config": types.SpeechConfig(
            **({} if env_flag("LIVE_AUTO_LANG") else {"language_code": IDIOMA}),
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOZ)
            ),
        ),
        "input_audio_transcription": transcricao.build_input_transcription_config(),
        "output_audio_transcription": transcricao.build_output_transcription_config(),
        # Sessões longas exigem compressão de contexto (evita o corte por tamanho)
        "context_window_compression": types.ContextWindowCompressionConfig(
            sliding_window=types.SlidingWindow()
        ),
    }

    if env_flag("LIVE_PROATIVITY"):
        cfg["proactivity"] = types.ProactivityConfig(proactive_audio=True)
    if env_flag("LIVE_AFFECTIVE_DIALOG"):
        # Diálogo afetivo foi removido da API nos modelos 3.8 (config = erro).
        if "3.8" in modelado:
            logger.info("LIVE_AFFECTIVE_DIALOG ignorado: recurso removido nos modelos Gemini 3.8.")
        else:
            cfg["enable_affective_dialog"] = True
    if env_flag("LIVE_EXPLICIT_VAD"):
        cfg["explicit_vad_signal"] = True
    if env_flag("LIVE_SAVE_BLOB"):
        cfg["save_live_blob"] = True
    if env_flag("LIVE_VAD_DISABLED"):
        cfg["realtime_input_config"] = types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(disabled=True)
        )
    elif env_flag("JARVIS_BARGE_IN") or env_flag("LIVE_ALLOW_BARGE_IN"):
        cfg["realtime_input_config"] = types.RealtimeInputConfig(
            activity_handling=types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS
        )
    if "extended-thinking" in modelado:
        logger.info(
            "Modelo extended no caminho ADK: thinking_config e tools NON_BLOCKING "
            "não são suportados pelo RunConfig do ADK; usando defaults da API."
        )
    metadados = env_json("LIVE_METADADOS")
    if metadados:
        cfg["custom_metadata"] = metadados

    return RunConfig(**cfg)


@router.websocket("/ws/live_adk")
async def live_adk(
    websocket: WebSocket,
    sessao: Optional[str] = Query(None),
    origem: str = Query(""),
):
    """Sessão de voz Live bidirecional nativa do Google ADK com handshake autenticado."""
    await websocket.accept()

    ws_send_lock = asyncio.Lock()
    _conn_background_tasks: Set[asyncio.Task] = set()

    def _schedule_conn_task(coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        _conn_background_tasks.add(task)
        task.add_done_callback(_conn_background_tasks.discard)
        return task

    async def safe_send_json(payload: dict):
        async with ws_send_lock:
            await websocket.send_json(payload)

    # Handshake seguro: exige token idêntico ao /ws/live
    try:
        init_raw = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
        init_data = json.loads(init_raw)
    except Exception:
        await websocket.close(code=1008, reason="Init Timeout / Format Error")
        return

    if not init_data or init_data.get("type") != "init" or init_data.get("token") != JARVIS_SECRET_TOKEN:
        registrar_rejeicao_de_token(websocket, rota="/ws/live_adk", fonte="ws_live_adk", origem=origem)
        try:
            await safe_send_json({"tipo": "erro", "mensagem": "Não autorizado: JARVIS_TOKEN inválido ou ausente."})
        except Exception:
            pass
        await websocket.close(code=1008, reason="Unauthorized")
        return

    if not sessao_aceita(sessao):
        try:
            await safe_send_json({"tipo": "erro", "mensagem": "Sessão explícita e válida obrigatória no /ws/live_adk."})
        except Exception:
            pass
        await websocket.close(code=1008, reason="InvalidSession")
        return

    usuario = sessao
    runner = obter_runner_adk(CAMINHO_VOZ)
    try:
        await session_service_adk.create_session(
            app_name="assistente", user_id=usuario, session_id=sessao
        )
    except Exception:
        pass

    fila = LiveRequestQueue()
    logger.info("Cliente autenticado no Live ADK (sessão: %s, modelo: %s)", sessao, runner.agent.model)
    await safe_send_json({"tipo": "pronto", "modelo": runner.agent.model, "voz": VOZ})

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
                texto_msg = (msg.get("texto") or msg.get("text") or "").strip()
                if palavra_confirma(texto_msg):
                    pending = policy_engine.approve_latest_pending(session_id=sessao, user_id=usuario)
                    if pending:
                        logger.info("Ação pendente %s (%s) aprovada por DIGITAÇÃO no Live ADK!", pending.action_id, pending.tool_name)
                        await safe_send_json({
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
                    types.Content(role="user", parts=[types.Part(text=texto_msg)])
                )
            elif tipo == "confirmar_acao":
                action_id = msg.get("id_confirmacao")
                aprovado = msg.get("aprovado", True)
                if aprovado:
                    sucesso = policy_engine.approve_action(action_id, session_id=sessao, user_id=usuario)
                    if sucesso:
                        pending = policy_engine.get_pending_action(action_id)
                        tool_name = pending.tool_name if pending else "ação"
                        await safe_send_json({
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
                    await safe_send_json({"tipo": "acao_rejeitada", "action_id": action_id})

    async def do_agente_para_o_cliente():
        run_cfg = montar_run_config(modelo=getattr(runner.agent, "model", ""))
        async for evento in runner.run_live(
            user_id=usuario,
            session_id=sessao,
            live_request_queue=fila,
            run_config=run_cfg,
        ):
            status_interacao = getattr(evento, "interaction_status", None)
            if status_interacao:
                await safe_send_json({"tipo": "interaction_status", "status": status_interacao})
            parcial = getattr(evento, "interim_input_transcription", None)
            if parcial and parcial.text:
                await safe_send_json(
                    {"tipo": "transcricao_usuario_parcial", "texto": parcial.text}
                )
            if evento.input_transcription and evento.input_transcription.text:
                transcricao_usuario = evento.input_transcription.text.strip()
                await safe_send_json(
                    {"tipo": "transcricao_usuario", "texto": transcricao_usuario}
                )
                # Hook de aprovação verbal por voz na sessão Live
                if palavra_confirma(transcricao_usuario):
                    pending = policy_engine.approve_latest_pending(session_id=sessao, user_id=usuario)
                    if pending:
                        logger.info("Ação pendente %s (%s) aprovada por COMANDO DE VOZ no Live!", pending.action_id, pending.tool_name)
                        await safe_send_json({
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
                await safe_send_json(
                    {"tipo": "texto", "texto": evento.output_transcription.text}
                )
            if evento.content and evento.content.parts:
                for parte in evento.content.parts:
                    if parte.inline_data and parte.inline_data.data:
                        await safe_send_json(
                            {
                                "tipo": "audio",
                                "dados": base64.b64encode(parte.inline_data.data).decode(),
                            }
                        )
                    if parte.function_call:
                        await safe_send_json(
                            {
                                "tipo": "ferramenta",
                                "nome": parte.function_call.name,
                                "args": dict(parte.function_call.args or {}),
                            }
                        )
                    if parte.function_response:
                        await safe_send_json(
                            {
                                "tipo": "ferramenta_resultado",
                                "nome": parte.function_response.name,
                                "resultado": dict(parte.function_response.response or {}),
                            }
                        )
            if evento.interrupted:
                await safe_send_json({"tipo": "interrompido"})
            if env_flag("LIVE_EXPLICIT_VAD"):
                va = getattr(evento, "voice_activity", None)
                if va is not None:
                    try:
                        estado = getattr(va, "is_speech", None)
                        if estado is None:
                            estado = getattr(va, "voice_in", None) or getattr(va, "response_in", None)
                        await safe_send_json(
                            {"tipo": "voz_ativa", "ativo": bool(estado), "detalhe": str(va)}
                        )
                    except Exception:
                        pass
            if evento.turn_complete:
                await safe_send_json({"tipo": "turno_concluido"})
                try:
                    sess_obj = await session_service_adk.get_session(
                        app_name="assistente", user_id=usuario, session_id=sessao
                    )
                    if sess_obj:
                        _schedule_conn_task(memory_service_adk.add_session_to_memory(sess_obj))
                except Exception:
                    pass

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(do_cliente_para_o_agente())
            tg.create_task(do_agente_para_o_cliente())
    except* (WebSocketDisconnect, asyncio.CancelledError):
        logger.info("Cliente Live ADK desconectou.")
    except* (ServerError, ClientError) as grupo:
        erro_inst = grupo.exceptions[0]
        logger.warning("Falha na sessão Live ADK (%s). Rotacionando chave e/ou modelo.", erro_inst)
        girou = girar_chave_adk()
        if girou:
            # A rotação limpa o cache de runners; aplica a reserva ao runner recriado
            # para que a próxima conexão já use o modelo reserva (mensagem abaixo verdadeira).
            # Sem revert: o objec runner velho foi descartado; nao se troca modelo dele.
            novo_runner = obter_runner_adk(CAMINHO_VOZ)
            try:
                trocar_modelo(novo_runner, MODELO_LIVE_RESERVA)
            except Exception:
                pass
        msg_erro = (
            "Limite ou instabilidade na Live API. Chave rotacionada no pool. Reconecte para continuar."
            if girou
            else "Modelo Live indisponível. Reconecte para tentar o modelo reserva."
        )
        try:
            await safe_send_json({"tipo": "erro", "mensagem": msg_erro})
        except Exception:
            pass
    finally:
        fila.close()
        for t in list(_conn_background_tasks):
            if not t.done():
                t.cancel()
        try:
            sess_obj = await session_service_adk.get_session(
                app_name="assistente", user_id=usuario, session_id=sessao
            )
            if sess_obj:
                await memory_service_adk.add_session_to_memory(sess_obj)
        except Exception as e:
            logger.warning("Falha ao salvar sessão Live na memória de longo prazo: %s", e)
