"""Servidor FastAPI do assistente: um cliente, uma sessão, dois caminhos internos.

- POST /api/chat  : turno de texto (Runner.run_async). O roteador escolhe o caminho.
- WS   /ws/live   : voz bidirecional em tempo real (Runner.run_live + LiveRequestQueue)
- GET  /          : cliente próprio (static/index.html)

Observação sobre a Live API: quem seleciona a conexão bidirecional é o próprio
run_live(). O RunConfig só descreve modalidades, voz e transcrição — em Python o
StreamingMode não participa dessa escolha.
"""

import asyncio
import base64
import json
import logging
import os

from dotenv import load_dotenv

RAIZ = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(RAIZ, ".env"), override=True)

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from google.adk.agents import LiveRequestQueue  # noqa: E402
from google.adk.agents.run_config import RunConfig  # noqa: E402
from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import BaseSessionService, InMemorySessionService  # noqa: E402
from google.genai import types  # noqa: E402
from google.genai.errors import ServerError, ClientError
from policy_engine import policy_engine  # noqa: E402

from agentes.assistente import (  # noqa: E402
    criar_agente_coordenador,
    criar_agente_de_voz,
    criar_agente_rapido,
)
from agentes.roteador import CAMINHO_RAPIDO, escolher_caminho  # noqa: E402

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
def _carregar_chaves() -> list[str]:
    brutas = [os.environ.get("GOOGLE_API_KEY", "")]
    brutas += os.environ.get("GEMINI_API_KEYS", "").split(",")
    brutas.append(os.environ.get("GEMINI_API_KEY", ""))
    vistas, chaves = set(), []
    for chave in (c.strip() for c in brutas):
        if chave and chave not in vistas:
            vistas.add(chave)
            chaves.append(chave)
    return chaves


CHAVES = _carregar_chaves()
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

# Um Runner por caminho interno; o usuário enxerga um assistente só.
_runners: dict[str, Runner] = {}


def obter_runner(caminho: str) -> Runner:
    """caminho: 'rapido', 'complexo' (texto) ou 'voz' (sessão Live)."""
    if caminho not in _runners:
        if caminho == "voz":
            agente = criar_agente_de_voz(MODELO_LIVE)
        elif caminho == CAMINHO_RAPIDO:
            agente = criar_agente_rapido(MODELO_TEXTO)
        else:
            agente = criar_agente_coordenador(MODELO_TEXTO)
        _runners[caminho] = Runner(
            app_name=APP_NOME, agent=agente, session_service=sessoes
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


@app.get("/api/health")
async def saude():
    return {
        "status": "online",
        "modelo_live": MODELO_LIVE,
        "modelo_live_reserva": MODELO_LIVE_RESERVA,
        "modelo_texto": MODELO_TEXTO,
        "chave_configurada": bool(os.environ.get("GOOGLE_API_KEY")),
        "chaves_no_pool": len(CHAVES),
        "sessoes": type(sessoes).__name__,
        "voz": VOZ,
    }


# ----------------------------- MODO TEXTO -----------------------------

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
    action_id = payload.get("id_confirmacao")
    aprovado = payload.get("aprovado", True)
    session_id = payload.get("sessao", "sessao-principal")

    if not action_id:
        pending = policy_engine.approve_latest_pending(session_id=session_id)
        if pending:
            return {"status": "ok", "action_id": pending.action_id, "tool_name": pending.tool_name}
        return {"status": "erro", "mensagem": "Nenhuma acao pendente encontrada para confirmacao"}

    if aprovado:
        sucesso = policy_engine.approve_action(action_id, session_id=session_id)
        if sucesso:
            return {"status": "ok", "action_id": action_id}
        return {"status": "erro", "mensagem": "Acao nao encontrada ou expirada"}
    else:
        policy_engine.reject_action(action_id)
        return {"status": "rejeitado", "action_id": action_id}


@app.post("/api/chat")
async def chat(payload: dict, _=Depends(verify_jarvis_token)):
    """Um turno de texto. O roteador escolhe o caminho; 'caminho' no payload força."""
    texto = (payload.get("texto") or "").strip()
    if not texto:
        return {"status": "erro", "mensagem": "Campo 'texto' é obrigatório."}

    usuario = payload.get("usuario", "local")
    sessao = payload.get("sessao", "sessao-principal")
    forcado = payload.get("caminho")
    if forcado:
        caminho, motivo = forcado, "escolha manual"
    else:
        caminho, motivo = escolher_caminho(texto)
    # Verifica palavras de confirmacao emitidas pelo usuario
    palavras_confirmacao = {"sim", "confirmar", "confirmado", "autorizar", "autorizado", "pode", "ok", "prosseguir", "positivo"}
    texto_limpo = "".join(c for c in texto.lower() if c.isalnum() or c.isspace()).strip()
    if texto_limpo in palavras_confirmacao:
        pending = policy_engine.approve_latest_pending(session_id=sessao)
        if pending:
            logger.info("Usuario aprovou acao pendente %s (%s)", pending.action_id, pending.tool_name)
            texto = f"O usuario confirmou expressamente a acao. Execute a ferramenta {pending.tool_name} agora."
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
            # Sem outra chave: espera e tenta o modelo de texto reserva
            logger.warning("Modelo de texto indisponível (%s). Tentando o reserva.", erro)
            await asyncio.sleep(2)
            trocar_modelo(runner, MODELO_TEXTO_RESERVA)
            try:
                resposta, ferramentas = await um_turno()
                break
            except Exception as erro_final:
                return {
                    "status": "erro",
                    "caminho": caminho,
                    "mensagem": f"Modelos de texto indisponíveis no momento: {erro_final}",
                }
    else:
        return {
            "status": "erro",
            "caminho": caminho,
            "mensagem": f"Todas as chaves do pool estão sem cota: {ultimo_erro}",
        }

    return {
        "status": "ok",
        "caminho": caminho,
        "motivo_do_roteamento": motivo,
        "modelo": runner.agent.model,
        "resposta": resposta,
        "ferramentas": ferramentas,
    }


# ------------------------------ MODO VOZ ------------------------------
def montar_run_config() -> RunConfig:
    """Modalidades, voz e transcrição da sessão Live.

    Não há StreamingMode aqui: em Python é o run_live() que ativa a Live API.
    """
    return RunConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            language_code=IDIOMA,
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOZ)
            ),
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        # Sessão de áudio termina em ~15 min sem compressão de contexto
        context_window_compression=types.ContextWindowCompressionConfig(
            sliding_window=types.SlidingWindow()
        ),
    )


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
            tipo = msg.get("tipo")
            if tipo == "audio":
                fila.send_realtime(
                    types.Blob(
                        data=base64.b64decode(msg["dados"]),
                        mime_type="audio/pcm;rate=16000",
                    )
                )
            elif tipo == "texto":
                fila.send_content(
                    types.Content(role="user", parts=[types.Part(text=msg["texto"])])
                )
            elif tipo == "fim_do_audio":
                fila.send_audio_stream_end()

    async def do_agente_para_o_cliente():
        """Eventos do ADK viram mensagens JSON para o cliente."""
        async for evento in runner.run_live(
            user_id=usuario,
            session_id=sessao,
            live_request_queue=fila,
            run_config=montar_run_config(),
        ):
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
            if evento.turn_complete:
                await websocket.send_json({"tipo": "turno_concluido"})

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


if __name__ == "__main__":
    import uvicorn

    porta = int(os.environ.get("ADK_PORT", "8100"))
    logger.info("Servidor em http://127.0.0.1:%s", porta)
    uvicorn.run(app, host="127.0.0.1", port=porta)
