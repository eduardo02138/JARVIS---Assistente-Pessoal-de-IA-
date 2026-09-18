"""Servidor FastAPI do assistente: um cliente, uma sessão, dois caminhos internos.

- POST /api/chat  : turno de texto (Runner.run_async). O roteador escolhe o caminho.
- WS   /ws/live   : voz bidirecional em tempo real (Runner.run_live + LiveRequestQueue)
- GET  /          : cliente próprio (static/index.html)

Observação sobre a Live API: quem seleciona a conexão bidirecional é o próprio
run_live(). O RunConfig só descreve modalidades, voz e transcrição — em Python o
StreamingMode não participa dessa escolha.

Configuração da sessão Live via variáveis de ambiente (todas opcionais, default off):
  LIVE_PROATIVITY=1        áudio proativo (modelo decide quando falar) — específico do modelo
  LIVE_AFFECTIVE_DIALOG=1  adaptação emocional ao tom do usuário — específico do modelo
  LIVE_EXPLICIT_VAD=1      emite eventos de voz explícitos (evento.voice_activity)
  LIVE_SAVE_BLOB=1         grava o áudio da sessão (depuração/auditoria; ~1.92 MB/min)
  LIVE_VAD_DISABLED=1      desliga VAD automático (clientes push-to-talk/VAD próprio)
  LIVE_METADADOS='{"k":"v"}'  metadados anexados a cada evento da invocação
"""

import asyncio
import base64
import json
import logging
import os
import sys
import time

RAIZ = os.path.dirname(os.path.abspath(__file__))
# Auto-injeção do .venv local para execução transparente via python3 ou fish shell
for venv_site in [
    os.path.join(RAIZ, ".venv", "lib", f"python{sys.version_info.major}.{sys.version_info.minor}", "site-packages"),
    os.path.join(RAIZ, ".venv", "lib", "site-packages")
]:
    if os.path.isdir(venv_site) and venv_site not in sys.path:
        sys.path.insert(0, venv_site)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(RAIZ, ".env"), override=True)
except ImportError:
    pass

import secrets
import transcricao
import urllib.request
from typing import Optional
from fastapi import (
    FastAPI,
    Query,
    WebSocket,
    WebSocketDisconnect,
    Depends,
    Header,
    HTTPException,
    Request,
)  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from jarvis.core.auth import (
    JARVIS_SECRET_TOKEN,
    verify_jarvis_token,
    verify_jarvis_token_ws,
    is_valid_token,
)
from jarvis.core.confirmation import check_and_approve_verbal, handle_confirmar_acao_payload
from jarvis.core.key_pool import get_gemini_keys
from google.adk.agents import LiveRequestQueue  # noqa: E402
from google.adk.agents.run_config import RunConfig  # noqa: E402
from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import BaseSessionService, InMemorySessionService  # noqa: E402
from google.adk.apps.app import App, EventsCompactionConfig  # noqa: E402
from google.adk.agents.context_cache_config import ContextCacheConfig  # noqa: E402
from agentes.memoria import JarvisMemoryService  # noqa: E402
from google.genai import types  # noqa: E402
from google.genai.errors import ServerError, ClientError
from policy_engine import policy_engine  # noqa: E402

from agentes.assistente import (  # noqa: E402
    criar_agente_coordenador,
    criar_agente_de_voz,
    criar_agente_rapido,
)
from agentes.roteador import CAMINHO_RAPIDO, escolher_caminho  # noqa: E402
from agentes.computer_use.agente import (
    MODELO_COMPUTER,
    criar_agente_computer_use,
)  # noqa: E402

CAMINHO_COMPUTADOR = "computador"
CAMINHO_VOZ = "voz"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("assistente")

APP_NOME = "assistente_adk"
VOZ = os.environ.get("VOICE_NAME", "Charon")
IDIOMA = os.environ.get("LANGUAGE_CODE", "pt-BR")

# Famílias distintas: run_live() exige modelo Live; run_async() usa modelo de texto.
MODELO_LIVE = os.environ.get("LIVE_MODEL_PRIMARY", "gemini-3.8-live")
MODELO_LIVE_RESERVA = os.environ.get("LIVE_MODEL_FALLBACK", "gemini-2.5-flash-native-audio-latest")
MODELO_TEXTO = os.environ.get("TEXT_MODEL", "gemini-flash-latest")
MODELO_TEXTO_RESERVA = os.environ.get("TEXT_MODEL_FALLBACK", "gemini-2.5-flash")

# Pool de chaves: o nível gratuito estoura cota (429) com facilidade, então o servidor
# gira para a próxima chave em vez de devolver erro ao usuário.
CHAVES = get_gemini_keys()
_indice_chave = 0
if CHAVES:
    os.environ["GOOGLE_API_KEY"] = CHAVES[0]
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "0")


def cota_ou_indisponivel(erro: Exception) -> bool:
    texto = str(erro)
    return any(marca in texto for marca in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE"))


def girar_chave() -> bool:
    """Passa para a próxima chave do pool e força a recriação dos agentes."""
    global _indice_chave
    if len(CHAVES) < 2:
        return False
    _indice_chave = (_indice_chave + 1) % len(CHAVES)
    os.environ["GOOGLE_API_KEY"] = CHAVES[_indice_chave]
    _runners.clear()  # o cliente do Gemini é criado com a chave do ambiente
    logger.warning("Cota atingida: girando para a chave %d de %d.", _indice_chave + 1, len(CHAVES))
    return True

app = FastAPI(title="Assistente de Voz em Tempo Real (ADK + Gemini Live)")


def criar_servico_de_sessao() -> BaseSessionService:
    """SQLite por padrão: o estado 'user:' precisa sobreviver a um restart.

    InMemorySessionService compartilha o estado do usuário entre sessões apenas
    enquanto o processo vive; ao reiniciar, tudo se perde.
    """
    url = os.environ.get("SESSION_DB_URL", f"sqlite+aiosqlite:///{os.path.join(RAIZ, 'sessoes.db')}")
    if url.lower() in ("memoria", "memory", "none", ""):
        logger.warning("Sessões em memória: preferências não sobrevivem a um restart.")
        return InMemorySessionService()
    try:
        from google.adk.sessions import DatabaseSessionService

        logger.info("Sessões persistentes em %s", url)
        return DatabaseSessionService(db_url=url)
    except Exception as erro:  # sqlalchemy ausente, banco inacessível, etc.
        logger.warning("Sem sessão persistente (%s). Usando memória.", erro)
        return InMemorySessionService()


sessoes = criar_servico_de_sessao()
memory_service_adk = JarvisMemoryService()

# Um Runner por caminho interno; o usuário enxerga um assistente só.
_runners: dict[str, Runner] = {}


def obter_runner(caminho: str) -> Runner:
    """caminho: 'rapido', 'complexo' (texto), 'computador' (Computer Use) ou 'voz'."""
    if caminho not in _runners:
        if caminho == CAMINHO_VOZ:
            agente = criar_agente_de_voz(MODELO_LIVE)
        elif caminho == CAMINHO_COMPUTADOR:
            agente = criar_agente_computer_use(MODELO_COMPUTER)
        elif caminho == CAMINHO_RAPIDO:
            agente = criar_agente_rapido(MODELO_TEXTO)
        else:
            agente = criar_agente_coordenador(MODELO_TEXTO)

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
            name=APP_NOME,
            root_agent=agente,
            events_compaction_config=compaction_config,
            context_cache_config=cache_config,
        )
        _runners[caminho] = Runner(
            app=app_obj,
            session_service=sessoes,
            memory_service=memory_service_adk,
        )
    return _runners[caminho]


def trocar_modelo(runner: Runner, modelo: str) -> None:
    """Troca o modelo do agente e dos sub-agentes (plano B de indisponibilidade)."""
    runner.agent.model = modelo
    for ferramenta in getattr(runner.agent, "tools", []):
        subagente = getattr(ferramenta, "agent", None)
        if subagente is not None:
            subagente.model = modelo


async def garantir_sessao(usuario: str, sessao: str) -> None:
    """Mesma sessão para todos os caminhos: a conversa não se parte ao trocar de agente."""
    existente = await sessoes.get_session(
        app_name=APP_NOME, user_id=usuario, session_id=sessao
    )
    if existente is None:
        await sessoes.create_session(
            app_name=APP_NOME, user_id=usuario, session_id=sessao
        )


app.mount("/static", StaticFiles(directory=os.path.join(RAIZ, "static_adk")), name="static")


@app.get("/")
async def pagina_inicial():
    return FileResponse(os.path.join(RAIZ, "static_adk", "index.html"))


@app.get("/api/auth/session")
async def obter_token_sessao(request: Request):
    """Permite apenas ao cliente local no loopback obter o token da sessão ativa."""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(status_code=403, detail="Acesso restrito ao localhost.")
    return {"token": JARVIS_SECRET_TOKEN}

@app.get("/api/health")
async def saude():
    return {
        "status": "online",
        "modelo_live": MODELO_LIVE,
        "modelo_live_reserva": MODELO_LIVE_RESERVA,
        "modelo_texto": MODELO_TEXTO,
        "modelo_computador": MODELO_COMPUTER,
        "chave_configurada": bool(os.environ.get("GOOGLE_API_KEY")),
        "chaves_no_pool": len(CHAVES),
        "sessoes": type(sessoes).__name__,
        "voz": VOZ,
        "modo_computador": policy_engine.computer_lease_status(),
    }


# ----------------------------- MODO TEXTO -----------------------------

@app.post("/api/computer/mode")
async def alternar_modo_computador(payload: dict, _=Depends(verify_jarvis_token)):
    """Ativa/desativa a lease do Modo Computador (navegador via Computer Use).

    Ativar: concede autoridade temporária de operação do navegador à sessão.
    Desativar: revoga a lease e fecha o Chromium compartilhado do runner.
    """
    ativo = bool(payload.get("ativo"))
    sessao = payload.get("sessao") or "sessao-principal"
    usuario = payload.get("usuario") or "local"
    if not ativo:
        lease = policy_engine.revoke_computer_lease(session_id=sessao)
        runner = _runners.get(CAMINHO_COMPUTADOR)
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

    # Ativação exige confirmação prévia registrada (reutiliza o fluxo de pendências).
    args_modo = {"enabled": True, "descricao": "Ativação do Modo Computador (navegação em Chromium)"}
    pendentes = policy_engine.list_pending_actions(session_id=sessao, user_id=usuario)
    modo_pendente = next(
        (p for p in pendentes if p.tool_name == "set_computer_mode"), None
    )
    if modo_pendente is not None and modo_pendente.status == "pending":
        return {
            "status": "aguardando_confirmacao",
            "id_confirmacao": modo_pendente.action_id,
            "mensagem": f"Confirme a ativação do Modo Computador (id {modo_pendente.action_id[:8]}).",
        }

    if policy_engine.consume_authorization(
        tool_name="set_computer_mode",
        args=args_modo,
        session_id=sessao,
        user_id=usuario,
    ):
        lease = policy_engine.grant_computer_lease(owner=sessao)
        logger.info(
            "Modo Computador concedido à sessão '%s' por %ss.",
            sessao,
            lease["segundos_restantes"],
        )
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
        "mensagem": f"Confirme a ativação do Modo Computador (id {modo_pendente.action_id[:8]}).",
    }


@app.get("/api/acoes_pendentes")
async def listar_pendentes(
    sessao: Optional[str] = Query(None),
    usuario: Optional[str] = Query(None),
    _=Depends(verify_jarvis_token)
):
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
                "expira_em": max(0, int(a.expires_at - now_m))
            }
            for a in pendentes
        ]
    }


@app.post("/api/confirmar_acao")
async def confirmar_acao(payload: dict, _=Depends(verify_jarvis_token)):
    status_code, resp = handle_confirmar_acao_payload(payload)
    if status_code != 200:
        if status_code == 400 and "obrigatório" in resp.get("mensagem", ""):
            raise HTTPException(status_code=400, detail=resp.get("mensagem", ""))
        return resp
    return resp


async def chamar_omniroute_chat(texto: str) -> str:
    """Wrapper fino canônico: implementação mora em OmniRouteProvider.chat()."""
    from provider_router import OmniRouteProvider
    return await OmniRouteProvider.chat(texto)


@app.post("/api/chat")
async def chat(payload: dict, _=Depends(verify_jarvis_token)):
    """Um turno de texto. O roteador escolhe o caminho; 'caminho' no payload força."""
    texto = (payload.get("texto") or "").strip()
    if not texto:
        return {"status": "erro", "mensagem": "Campo 'texto' é obrigatório."}

    usuario = payload.get("usuario", "local")
    sessao = payload.get("sessao", "sessao-principal")
    forcado = payload.get("caminho")
    texto_min = texto.lower()
    marcas_navegador = (
        "modo computador",
        "use o navegador",
        "controle o navegador",
        "controlar o navegador",
        "navegação automática",
    )
    if forcado in ("rapido", "complexo", CAMINHO_COMPUTADOR):
        caminho, motivo = forcado, "escolha explícita no payload"
    elif any(marca in texto_min for marca in marcas_navegador):
        caminho, motivo = CAMINHO_COMPUTADOR, "solicitação de operação do navegador (Computer Use)"
    else:
        caminho, motivo = escolher_caminho(texto)

    # Verifica palavras de confirmacao emitidas pelo usuario (jarvis.core.confirmation)
    confirmado, pending, texto = check_and_approve_verbal(texto, session_id=sessao, user_id=usuario)
    if confirmado and pending:
        caminho, motivo = "complexo", "execucao de acao autorizada pelo usuario"


    runner = obter_runner(caminho)
    await garantir_sessao(usuario, sessao)

    async def um_turno() -> tuple[str, list[str]]:
        resposta, ferramentas = "", []
        async for evento in runner.run_async(
            user_id=usuario,
            session_id=sessao,
            new_message=types.Content(role="user", parts=[types.Part(text=texto)]),
        ):
            if not evento.content or not evento.content.parts:
                continue
            for parte in evento.content.parts:
                if parte.function_call:
                    ferramentas.append(parte.function_call.name)
                if parte.text and evento.is_final_response():
                    resposta += parte.text
        return resposta.strip(), ferramentas

    resposta = None
    ferramentas = []
    ultimo_erro = None
    for tentativa in range(len(CHAVES) + 1):
        try:
            resposta, ferramentas = await um_turno()
            break
        except Exception as erro:
            if not cota_ou_indisponivel(erro):
                raise
            ultimo_erro = erro
            if girar_chave():
                runner = obter_runner(caminho)  # recriado com a chave nova
                continue
            if caminho == CAMINHO_COMPUTADOR:
                # O modelo de texto reserva não entende a config computer_use:
                # trocar quebraria o toolset. Devolve indisponibilidade explícita.
                logger.warning("Modelo de Computer Use indisponível (%s).", erro)
                ultimo_erro = erro
                break
            # Sem outra chave: espera e tenta o modelo de texto reserva
            logger.warning("Modelo de texto indisponível (%s). Tentando o reserva.", erro)
            await asyncio.sleep(2)
            trocar_modelo(runner, MODELO_TEXTO_RESERVA)
            try:
                resposta, ferramentas = await um_turno()
                break
            except Exception as erro_final:
                ultimo_erro = erro_final
                break

    if resposta is None:
        # Failover automático para o OmniRoute (segundo provedor)
        logger.info("Chaves Google AI Studio esgotadas no pool. Acionando OmniRoute (:20128) como segundo provedor...")
        try:
            resp_texto = await chamar_omniroute_chat(texto)
            return {
                "status": "ok",
                "caminho": caminho,
                "motivo_do_roteamento": "Failover: Google AI Studio sem cota -> OmniRoute acionado como 2º provedor",
                "provedor": "omniroute",
                "modelo": "omniroute/gemini-2.5-flash",
                "resposta": resp_texto,
                "ferramentas": [],
            }
        except Exception as omni_err:
            logger.warning("Falha também no segundo provedor OmniRoute: %s", omni_err)
            return {
                "status": "erro",
                "caminho": caminho,
                "mensagem": f"Google AI Studio e segundo provedor (OmniRoute) indisponíveis: {ultimo_erro}",
            }

    # Ingestão assíncrona da sessão na memória de longo prazo (background task)
    async def _salvar_memoria_bg():
        try:
            sess_obj = await sessoes.get_session(app_name=APP_NOME, user_id=usuario, session_id=sessao)
            if sess_obj:
                await memory_service_adk.add_session_to_memory(sess_obj)
        except Exception as e:
            logger.warning("Falha ao salvar sessão na memória: %s", e)

    asyncio.create_task(_salvar_memoria_bg())

    return {
        "status": "ok",
        "caminho": caminho,
        "motivo_do_roteamento": motivo,
        "modelo": runner.agent.model,
        "resposta": resposta,
        "ferramentas": ferramentas,
    }


# ------------------------------ MODO VOZ ------------------------------
def _env_flag(nome: str) -> bool:
    """Lê uma flag 0/1 do ambiente; tudo desligado sem a variável."""
    return os.environ.get(nome, "0").strip().lower() in ("1", "true", "yes", "on")


def _env_json(nome: str, padrao=None):
    """Lê um valor JSON do ambiente; retorna o padrão se faltar ou for inválido."""
    bruto = os.environ.get(nome)
    if not bruto or not bruto.strip():
        return padrao
    try:
        return json.loads(bruto)
    except Exception:
        logger.warning("Valor inválido em %s (JSON esperado): ignorado.", nome)
        return padrao


def montar_run_config() -> RunConfig:
    """Modalidades, voz, transcrição e tuning opcional da sessão Live.

    Não há StreamingMode aqui: em Python é o run_live() que ativa a Live API.
    Os recursos extras (proatividade, diálogo afetivo, VAD etc.) são opcionais:
    só entram na configuração quando as variáveis de ambiente os ativam.
    """
    cfg: dict = {
        "response_modalities": ["AUDIO"],
        "speech_config": types.SpeechConfig(
            language_code=IDIOMA,
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOZ)
            ),
        ),
        "input_audio_transcription": transcricao.build_input_transcription_config(),
        "output_audio_transcription": transcricao.build_output_transcription_config(),
        # Sessão de áudio termina em ~15 min sem compressão de contexto
        "context_window_compression": types.ContextWindowCompressionConfig(
            sliding_window=types.SlidingWindow()
        ),
    }

    if _env_flag("LIVE_PROATIVITY"):
        cfg["proactivity"] = types.ProactivityConfig(proactive_audio=True)
    if _env_flag("LIVE_AFFECTIVE_DIALOG"):
        cfg["enable_affective_dialog"] = True
    if _env_flag("LIVE_EXPLICIT_VAD"):
        cfg["explicit_vad_signal"] = True
    if _env_flag("LIVE_SAVE_BLOB"):
        cfg["save_live_blob"] = True
    if _env_flag("LIVE_VAD_DISABLED"):
        cfg["realtime_input_config"] = types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(disabled=True)
        )
    elif _env_flag("JARVIS_BARGE_IN") or _env_flag("LIVE_ALLOW_BARGE_IN"):
        cfg["realtime_input_config"] = types.RealtimeInputConfig(
            activity_handling=types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS
        )
    metadados = _env_json("LIVE_METADADOS")
    if metadados:
        cfg["custom_metadata"] = metadados

    return RunConfig(**cfg)


@app.websocket("/ws/live")
async def live(
    websocket: WebSocket,
    usuario: str = Query("local"),
    sessao: str = Query("sessao-principal"),
):
    await websocket.accept()
    
    # Handshake de autenticação
    try:
        init_raw = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
        init_data = json.loads(init_raw)
    except Exception:
        await websocket.close(code=1008, reason="Init Timeout / Format Error")
        return

    if not init_data or init_data.get("token") != JARVIS_SECRET_TOKEN:
        logger.warning("Tentativa de conexão WebSocket /ws/live não autorizada: token inválido ou ausente.")
        try:
            await websocket.send_json({"tipo": "erro", "mensagem": "Não autorizado: JARVIS_TOKEN inválido ou ausente."})
        except Exception:
            pass
        await websocket.close(code=1008, reason="Unauthorized")
        return

    runner = obter_runner("voz")
    await garantir_sessao(usuario, sessao)

    fila = LiveRequestQueue()
    logger.info("Cliente autenticado no modo live ADK (modelo %s)", runner.agent.model)
    await websocket.send_json({"tipo": "pronto", "modelo": runner.agent.model, "voz": VOZ})

    async def do_cliente_para_o_agente():
        """Áudio e texto do navegador entram na fila do ADK."""
        while True:
            msg = json.loads(await websocket.receive_text())
            tipo = msg.get("tipo") or msg.get("type")
            if tipo == "audio":
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
            elif tipo in ("texto", "text"):
                texto = msg.get("texto") or msg.get("text")
                if texto:
                    fila.send_content(
                        types.Content(role="user", parts=[types.Part(text=texto)])
                    )
            elif tipo in ("fim_do_audio", "end_of_audio", "audio_stream_end"):
                fila.send_audio_stream_end()

    async def do_agente_para_o_cliente():
        """Eventos do ADK viram mensagens JSON para o cliente."""
        async for evento in runner.run_live(
            user_id=usuario,
            session_id=sessao,
            live_request_queue=fila,
            run_config=montar_run_config(),
        ):
            parcial = getattr(evento, "interim_input_transcription", None)
            if parcial and parcial.text:
                await websocket.send_json(
                    {"tipo": "transcricao_usuario_parcial", "texto": parcial.text}
                )
            if evento.input_transcription and evento.input_transcription.text:
                await websocket.send_json(
                    {"tipo": "transcricao_usuario", "texto": evento.input_transcription.text}
                )
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
            if _env_flag("LIVE_EXPLICIT_VAD") and evento.voice_activity:
                try:
                    va = evento.voice_activity
                    estado = getattr(va, "is_speech", None)
                    if estado is None:
                        estado = getattr(va, "voice_in", None) or getattr(va, "response_in", None)
                    await websocket.send_json(
                        {"tipo": "voz_ativa", "ativo": bool(estado), "detalhe": str(va)}
                    )
                except Exception:
                    pass
            if evento.turn_complete:
                await websocket.send_json({"tipo": "turno_concluido"})
                try:
                    sess_obj = await sessoes.get_session(app_name=APP_NOME, user_id=usuario, session_id=sessao)
                    if sess_obj:
                        asyncio.create_task(memory_service_adk.add_session_to_memory(sess_obj))
                except Exception:
                    pass

    try:
        # TaskGroup: se um lado cair, o outro é cancelado junto
        async with asyncio.TaskGroup() as tg:
            tg.create_task(do_cliente_para_o_agente())
            tg.create_task(do_agente_para_o_cliente())
    except* (WebSocketDisconnect, asyncio.CancelledError):
        logger.info("Cliente saiu do modo live")
    except* (ServerError, ClientError) as grupo:
        erro_inst = grupo.exceptions[0]
        logger.warning("Falha na sessao Live (%s). Rotacionando chave e/ou modelo.", erro_inst)
        girou = girar_chave()
        trocar_modelo(runner, MODELO_LIVE_RESERVA)
        msg_erro = "Limite ou instabilidade na Live API. Chave rotacionada no pool. Reconecte para continuar." if girou else "Modelo Live indisponivel. Reconecte para tentar o modelo reserva."
        try:
            await websocket.send_json({"tipo": "erro", "mensagem": msg_erro})
        except Exception:
            pass
    finally:
        fila.close()
        try:
            sess_obj = await sessoes.get_session(app_name=APP_NOME, user_id=usuario, session_id=sessao)
            if sess_obj:
                await memory_service_adk.add_session_to_memory(sess_obj)
        except Exception as e:
            logger.warning("Falha ao persistir sessão Live na memória: %s", e)


if __name__ == "__main__":
    import uvicorn

    porta = int(os.environ.get("ADK_PORT", "8100"))
    logger.info("Servidor em http://127.0.0.1:%s", porta)
    uvicorn.run(app, host="127.0.0.1", port=porta)
