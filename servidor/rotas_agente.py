"""Rotas do agente ADK: chat de texto, confirmações pendentes e Modo Computador."""

import base64
import time
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from google.genai import types

from agentes.assistente import MODELO_TEXTO_RESERVA
from agentes.roteador import CAMINHO_RAPIDO, escolher_caminho
from live_protocolo import palavra_confirma
from monitoring.logger import logger
from policy_engine import policy_engine
from provider_router import OmniRouteProvider, provider_router
from servidor.comum import agendar_tarefa_do_servidor, trava_da_sessao
from servidor.falhas import classificar_falha
from servidor.runtime_adk import (
    CAMINHO_COMPUTADOR,
    girar_chave_adk,
    memory_service_adk,
    obter_runner_adk,
    runners_adk,
    session_service_adk,
)
from servidor.seguranca import sessao_aceita, verify_jarvis_token

router = APIRouter()


@router.get("/api/acoes_pendentes")
async def listar_pendentes(
    sessao: Optional[str] = Query(None),
    _=Depends(verify_jarvis_token)
):
    """Lista pendências ativas filtradas com estrito isolamento por sessão.

    Identidade vem da sessão server-side (user_id = sessão), nunca do cliente.
    """
    if not sessao_aceita(sessao):
        return JSONResponse(
            {"status": "erro", "mensagem": "Parâmetro 'sessao' explícito e válido é obrigatório."},
            status_code=403,
        )
    pendentes = policy_engine.list_pending_actions(session_id=sessao, user_id=sessao)
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


@router.post("/api/confirmar_acao")
async def confirmar_acao(payload: dict, _=Depends(verify_jarvis_token)):
    action_id = payload.get("id_confirmacao") or payload.get("action_id") or payload.get("id")
    aprovado = payload.get("aprovado", True)
    session_id = payload.get("sessao") or payload.get("session_id")

    if not sessao_aceita(session_id):
        return JSONResponse({"status": "erro", "mensagem": "Parâmetro 'sessao' explícito e válido é obrigatório para confirmar ações."}, status_code=400)

    user_id = session_id

    if not action_id:
        ambiguas = policy_engine.has_ambiguous_pending(session_id=session_id, user_id=user_id)
        if ambiguas:
            return JSONResponse(
                {
                    "status": "erro",
                    "codigo": "confirmacao_ambigua",
                    "mensagem": "Múltiplas ações pendentes nesta sessão. Informe 'id_confirmacao' explicitamente para desambiguar.",
                    "pendentes": [a.action_id for a in ambiguas],
                },
                status_code=409,
            )
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


@router.post("/api/computer/mode")
async def alternar_modo_computador(payload: dict, _=Depends(verify_jarvis_token)):
    """Ativa/desativa a lease do Modo Computador (navegador via Computer Use).

    Ativar exige confirmação prévia registrada (fluxo de pendências do PolicyEngine).
    Desativar revoga a lease e fecha o Chromium compartilhado do runner.
    """
    ativo = bool(payload.get("ativo"))
    sessao = payload.get("sessao")
    if not sessao_aceita(sessao):
        return JSONResponse(
            {"status": "erro", "mensagem": "Parâmetro 'sessao' explícito e válido é obrigatório."},
            status_code=400,
        )
    usuario = sessao

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


@router.post("/api/chat")
async def api_chat_adk(payload: dict, _=Depends(verify_jarvis_token)):
    """Turno textual unificado: o roteador escolhe entre o agente rápido e o coordenador."""
    texto = (payload.get("texto") or "").strip()
    if not texto:
        return JSONResponse({"status": "erro", "mensagem": "Texto vazio"}, status_code=400)

    sessao = payload.get("sessao")
    if not sessao_aceita(sessao):
        return JSONResponse(
            {"status": "erro", "mensagem": "Parâmetro 'sessao' explícito e válido é obrigatório."},
            status_code=400,
        )
    # Turnos da mesma sessão rodam em fila: dois pedidos simultâneos não intercalam
    # eventos na mesma conversa do ADK. Sessões diferentes seguem em paralelo.
    async with trava_da_sessao(sessao):
        return await _turno_de_chat(
            texto,
            sessao,
            payload.get("caminho"),
            modo_solicitado=payload.get("modo") or payload.get("mode"),
            conversation_id=payload.get("conversation_id"),
        )


async def _turno_de_chat(
    texto: str,
    sessao: str,
    caminho_forcado: Optional[str],
    modo_solicitado: Optional[str] = None,
    conversation_id: Optional[str] = None,
):
    from orquestrador import ConversationMode, jarvis_orchestrator
    from pesquisa_e_memoria import format_mode_instruction, history_manager

    usuario = sessao
    decisao_rota = jarvis_orchestrator.router.route(
        texto, requested_mode=modo_solicitado or ConversationMode.SMART
    )
    modo_efetivo = decisao_rota.effective_mode
    conv_id = conversation_id or f"conv_{sessao}"

    marcas_navegador = (
        "modo computador",
        "use o navegador",
        "controle o navegador",
        "controlar o navegador",
        "navegação automática",
    )

    texto_min = texto.lower()
    if caminho_forcado in ("rapido", "complexo", CAMINHO_COMPUTADOR):
        caminho, motivo = caminho_forcado, "escolha explícita"
    elif any(marca in texto_min for marca in marcas_navegador):
        caminho, motivo = CAMINHO_COMPUTADOR, "solicitação de operação do navegador (Computer Use)"
    elif modo_solicitado and modo_solicitado.strip().lower() in ("profundo", "analise", "estudo", "pesquisa"):
        caminho, motivo = "complexo", decisao_rota.reason
    elif modo_solicitado and modo_solicitado.strip().lower() == "rapido":
        caminho, motivo = CAMINHO_RAPIDO, decisao_rota.reason
    else:
        caminho, motivo = escolher_caminho(texto)

    # Registra turno do usuário no histórico isolado por sessão/usuário
    history_manager.append_turn(
        user_id=usuario,
        conversation_id=conv_id,
        role="user",
        text=texto,
        mode=modo_efetivo.value,
    )

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

    # Verifica palavras de confirmação verbal ou digitada do usuário.
    # Executa apenas no caminho com runner (ferramentas): no branch OmniRoute
    # (text-only) a aprovação ficaria órfã — pendência aprovada sem consumível.
    if palavra_confirma(texto):
        ambiguas = policy_engine.has_ambiguous_pending(session_id=sessao, user_id=usuario)
        if ambiguas:
            logger.warning(
                "Confirmação genérica '%s' recusada por ambiguidade (%d pendências na sessão '%s').",
                texto,
                len(ambiguas),
                sessao,
            )
            return JSONResponse(
                {
                    "status": "confirmacao_ambigua",
                    "codigo": "confirmacao_ambigua",
                    "mensagem": (
                        f"Existem {len(ambiguas)} ações pendentes simultaneamente nesta sessão. "
                        "Por segurança, aprove explicitamente pelo ID da pendência (/api/confirmar_acao)."
                    ),
                    "pendentes": [
                        {"id": a.action_id, "ferramenta": a.tool_name, "argumentos": a.args}
                        for a in ambiguas
                    ],
                },
                status_code=409,
            )
        pending = policy_engine.approve_latest_pending(session_id=sessao, user_id=usuario)
        if pending:
            logger.info("Usuário confirmou verbalmente a ação pendente: %s (%s)", pending.action_id, pending.tool_name)
            texto = f"O usuário confirmou expressamente a execução da ação '{pending.tool_name}'. Execute-a agora."
            # O coordenador tem o catálogo completo para executar a ação liberada
            caminho, motivo = "complexo", "confirmação de ação pendente"

    if caminho == CAMINHO_RAPIDO:
        tipo_runner = "rapido"
    elif caminho == CAMINHO_COMPUTADOR:
        tipo_runner = CAMINHO_COMPUTADOR
    else:
        tipo_runner = "coordenador"

    try:
        await session_service_adk.create_session(
            app_name="assistente", user_id=usuario, session_id=sessao
        )
    except Exception:
        pass

    resposta = ""
    ferramentas_executadas = []
    resposta_ok = False
    ultima_falha = None
    # None = modelo padrão do caminho; o reserva só entra quando o problema é do modelo
    modelo_da_tentativa: Optional[str] = None

    for _tentativa in range(4):
        runner = obter_runner_adk(tipo_runner, modelo_da_tentativa)
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
            ultima_falha = classificar_falha(err)
            logger.warning("Falha do provedor no chat (%s): %s", ultima_falha.motivo.value, ultima_falha.detalhe)
            if ultima_falha.girar_chave and girar_chave_adk():
                continue
            # O modelo de texto reserva não entende a configuração de Computer Use
            if ultima_falha.trocar_modelo and modelo_da_tentativa is None and tipo_runner != CAMINHO_COMPUTADOR:
                modelo_da_tentativa = MODELO_TEXTO_RESERVA
                logger.warning("Tentando o modelo reserva %s.", MODELO_TEXTO_RESERVA)
                continue
            if ultima_falha.usar_segundo_provedor:
                break
            return JSONResponse({
                "status": "erro",
                "mensagem": f"Erro na execução do agente: {ultima_falha.mensagem()}",
                "motivo_da_falha": ultima_falha.motivo.value,
                "caminho": caminho
            }, status_code=500)

    if not resposta_ok:
        # Failover automático para o segundo provedor (OmniRoute)
        motivo_failover = ultima_falha.motivo.value if ultima_falha else "desconhecido"
        logger.info("Google AI Studio indisponível (%s). Acionando OmniRoute como segundo provedor...", motivo_failover)
        try:
            resp_texto = await chamar_omniroute_chat(texto)
            return {
                "status": "ok",
                "caminho": caminho,
                "motivo_do_roteamento": f"Failover ({motivo_failover}): Google AI Studio indisponível -> OmniRoute acionado como 2º provedor",
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
                "motivo_da_falha": motivo_failover,
                "mensagem": ("Google AI Studio e segundo provedor (OmniRoute) indisponíveis: "
                             f"{ultima_falha.mensagem() if ultima_falha else omni_err}")
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

    agendar_tarefa_do_servidor(_salvar_memoria_bg())

    resposta_final = resposta.strip()
    history_manager.append_turn(
        user_id=usuario,
        conversation_id=conv_id,
        role="assistant",
        text=resposta_final,
        mode=modo_efetivo.value,
    )

    return {
        "status": "ok",
        "caminho": caminho,
        "modo": (modo_solicitado or "smart"),
        "modo_efetivo": modo_efetivo.value,
        "conversation_id": conv_id,
        "motivo_do_roteamento": motivo,
        "provedor": "google_studio",
        "modelo": runner.agent.model,
        "resposta": resposta_final,
        "ferramentas": ferramentas_executadas,
    }


@router.get("/api/authority/journal")
async def consultar_journal_autoridade(
    sessao: Optional[str] = Query(None),
    ferramenta: Optional[str] = Query(None),
    limite: int = Query(50),
    _=Depends(verify_jarvis_token),
):
    """Consulta o journal causal do AuthorityKernel filtrado estritamente pela sessão requisitante."""
    if not sessao_aceita(sessao):
        return JSONResponse(
            {"status": "erro", "mensagem": "Parâmetro 'sessao' explícito e válido é obrigatório."},
            status_code=403,
        )
    from authority_kernel import authority_kernel

    entradas = authority_kernel.get_journal(
        session_id=sessao,
        tool_name=ferramenta,
        limit=max(1, min(int(limite or 50), 200)),
    )
    return {"status": "ok", "sessao": sessao, "entradas": entradas}


# ---------------- Orquestrador Estruturado (Fase F2: Planner -> Router -> Executor -> Verifier) ----------------


@router.post("/api/orchestrator/route")
async def diagnosticar_roteamento(payload: dict, _=Depends(verify_jarvis_token)):
    """Avalia o JarvisRouter para um prompt e modo conversacional."""
    from orquestrador import jarvis_orchestrator

    prompt = (payload.get("texto") or payload.get("prompt") or "").strip()
    if not prompt:
        return JSONResponse({"status": "erro", "mensagem": "Texto vazio."}, status_code=400)
    modo = payload.get("modo") or payload.get("mode") or "smart"
    untrusted = bool(payload.get("untrusted_input", False))
    decisao = jarvis_orchestrator.router.route(prompt, requested_mode=modo, untrusted_input=untrusted)
    return {"status": "ok", "roteamento": decisao.model_dump(mode="json")}


@router.post("/api/orchestrator/tasks")
async def criar_tarefa_orquestrada(payload: dict, _=Depends(verify_jarvis_token)):
    """Cria (e opcionalmente executa) uma tarefa estruturada no Orquestrador."""
    from orquestrador import TaskBudget, jarvis_orchestrator

    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse(
            {"status": "erro", "mensagem": "Parâmetro 'sessao' explícito e válido é obrigatório."},
            status_code=400,
        )
    goal = (payload.get("goal") or payload.get("objetivo") or payload.get("texto") or "").strip()
    if not goal:
        return JSONResponse({"status": "erro", "mensagem": "Objetivo ('goal') vazio."}, status_code=400)

    modo = payload.get("modo") or payload.get("mode") or "smart"
    steps = payload.get("steps") or payload.get("explicit_steps")
    raw_budget = payload.get("budget")
    budget = TaskBudget.model_validate(raw_budget) if isinstance(raw_budget, dict) else None

    task = jarvis_orchestrator.submit_task(
        goal=goal,
        session_id=sessao,
        user_id=sessao,
        mode=modo,
        explicit_steps=steps,
        budget=budget,
    )
    if payload.get("executar_imediato", True):
        task = jarvis_orchestrator.run_task(task.task_id, session_id=sessao)

    return {"status": "ok", "task": task.model_dump(mode="json")}


@router.get("/api/orchestrator/tasks")
async def listar_tarefas_orquestradas(
    sessao: Optional[str] = Query(None),
    estado: Optional[str] = Query(None),
    _=Depends(verify_jarvis_token),
):
    """Lista tarefas da sessão com isolamento estrito."""
    if not sessao_aceita(sessao):
        return JSONResponse(
            {"status": "erro", "mensagem": "Parâmetro 'sessao' explícito e válido é obrigatório."},
            status_code=403,
        )
    from orquestrador import TaskState, jarvis_orchestrator

    filtro_estado = TaskState(estado) if estado else None
    tarefas = jarvis_orchestrator.list_tasks(session_id=sessao, state=filtro_estado)
    return {
        "status": "ok",
        "sessao": sessao,
        "tarefas": [t.model_dump(mode="json") for t in tarefas],
    }


@router.get("/api/orchestrator/tasks/{task_id}")
async def obter_tarefa_orquestrada(
    task_id: str,
    sessao: Optional[str] = Query(None),
    _=Depends(verify_jarvis_token),
):
    if not sessao_aceita(sessao):
        return JSONResponse(
            {"status": "erro", "mensagem": "Parâmetro 'sessao' explícito e válido é obrigatório."},
            status_code=403,
        )
    from orquestrador import jarvis_orchestrator

    task = jarvis_orchestrator.get_task(task_id, session_id=sessao)
    if not task:
        return JSONResponse({"status": "erro", "mensagem": "Tarefa não encontrada nesta sessão."}, status_code=404)
    return {"status": "ok", "task": task.model_dump(mode="json")}


@router.post("/api/orchestrator/tasks/{task_id}/run")
async def retomar_tarefa_orquestrada(
    task_id: str,
    payload: dict,
    _=Depends(verify_jarvis_token),
):
    """Executa ou retoma (resume) uma tarefa a partir do último checkpoint concluído."""
    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse(
            {"status": "erro", "mensagem": "Parâmetro 'sessao' explícito e válido é obrigatório."},
            status_code=400,
        )
    from orquestrador import jarvis_orchestrator

    try:
        task = jarvis_orchestrator.run_task(task_id, session_id=sessao)
        return {"status": "ok", "task": task.model_dump(mode="json")}
    except KeyError:
        return JSONResponse({"status": "erro", "mensagem": "Tarefa não encontrada nesta sessão."}, status_code=404)


@router.post("/api/orchestrator/tasks/{task_id}/cancel")
async def cancelar_tarefa_orquestrada(
    task_id: str,
    payload: dict,
    _=Depends(verify_jarvis_token),
):
    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse(
            {"status": "erro", "mensagem": "Parâmetro 'sessao' explícito e válido é obrigatório."},
            status_code=400,
        )
    from orquestrador import jarvis_orchestrator

    task = jarvis_orchestrator.cancel_task(task_id, session_id=sessao)
    if not task:
        return JSONResponse({"status": "erro", "mensagem": "Tarefa não encontrada nesta sessão."}, status_code=404)
    return {"status": "ok", "task": task.model_dump(mode="json")}


# ---------------- Histórico, Memória Personalizada e Deep Research Real (Fase F3) ----------------


@router.get("/api/history/conversations")
async def listar_conversas_usuario(
    sessao: Optional[str] = Query(None),
    _=Depends(verify_jarvis_token),
):
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=403)
    from pesquisa_e_memoria import history_manager

    return {"status": "ok", "sessao": sessao, "conversas": history_manager.list_conversations(user_id=sessao)}


@router.get("/api/history/conversations/{conversation_id}")
async def reabrir_conversa_usuario(
    conversation_id: str,
    sessao: Optional[str] = Query(None),
    _=Depends(verify_jarvis_token),
):
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=403)
    from pesquisa_e_memoria import history_manager

    conv = history_manager.get_conversation(user_id=sessao, conversation_id=conversation_id)
    if not conv:
        return JSONResponse({"status": "erro", "mensagem": "Conversa não encontrada para este usuário."}, status_code=404)
    return {"status": "ok", "conversa": conv.model_dump(mode="json")}


@router.delete("/api/history/conversations/{conversation_id}")
async def excluir_conversa_especifica(
    conversation_id: str,
    sessao: Optional[str] = Query(None),
    _=Depends(verify_jarvis_token),
):
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=403)
    from pesquisa_e_memoria import history_manager

    ok = history_manager.delete_conversation(user_id=sessao, conversation_id=conversation_id)
    if not ok:
        return JSONResponse({"status": "erro", "mensagem": "Conversa não encontrada."}, status_code=404)
    return {"status": "ok", "excluida": conversation_id}


@router.delete("/api/history/conversations")
async def excluir_todo_historico_usuario(
    sessao: Optional[str] = Query(None),
    _=Depends(verify_jarvis_token),
):
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=403)
    from pesquisa_e_memoria import history_manager

    removidas = history_manager.delete_all_conversations(user_id=sessao)
    return {"status": "ok", "conversas_excluidas": removidas}


@router.post("/api/history/share")
async def criar_link_compartilhamento_estatico(payload: dict, _=Depends(verify_jarvis_token)):
    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=400)
    conv_id = payload.get("conversation_id")
    if not conv_id:
        return JSONResponse({"status": "erro", "mensagem": "conversation_id obrigatório."}, status_code=400)
    from pesquisa_e_memoria import history_manager

    snap = history_manager.create_share_snapshot(
        user_id=sessao,
        conversation_id=conv_id,
        title=payload.get("title"),
    )
    if not snap:
        return JSONResponse({"status": "erro", "mensagem": "Conversa não encontrada nesta sessão."}, status_code=404)
    return {
        "status": "ok",
        "share_id": snap.share_id,
        "share_url": f"/api/history/shared/{snap.share_id}",
        "created_at": snap.created_at,
    }


@router.get("/api/history/shared/{share_id}")
async def visualizar_conversa_compartilhada(share_id: str):
    """Retorna apenas o snapshot estático congelado (não expõe a sessão ativa nem mensagens posteriores)."""
    from pesquisa_e_memoria import history_manager

    snap = history_manager.get_shared_snapshot(share_id)
    if not snap:
        return JSONResponse({"status": "erro", "mensagem": "Link compartilhado inexistente ou revogado."}, status_code=404)
    return {"status": "ok", "snapshot": snap}


@router.delete("/api/history/shared/{share_id}")
async def revogar_link_compartilhado(
    share_id: str,
    sessao: Optional[str] = Query(None),
    _=Depends(verify_jarvis_token),
):
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=403)
    from pesquisa_e_memoria import history_manager

    ok = history_manager.revoke_share_snapshot(user_id=sessao, share_id=share_id)
    if not ok:
        return JSONResponse({"status": "erro", "mensagem": "Link não encontrado para este usuário."}, status_code=404)
    return {"status": "ok", "revogado": share_id}


@router.post("/api/memory/facts")
async def salvar_fato_memoria(payload: dict, _=Depends(verify_jarvis_token)):
    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=400)
    key = (payload.get("key") or payload.get("chave") or "").strip()
    value = (payload.get("value") or payload.get("valor") or "").strip()
    if not key or not value:
        return JSONResponse({"status": "erro", "mensagem": "Campos 'key' e 'value' obrigatórios."}, status_code=400)
    from pesquisa_e_memoria import user_memory_manager

    fact = user_memory_manager.remember_fact(
        user_id=sessao,
        key=key,
        value=value,
        category=payload.get("category") or "custom",
        ttl_seconds=payload.get("ttl_seconds"),
    )
    if fact is None:
        return {
            "status": "ignorado_opt_out",
            "mensagem": "Personalização de memória desativada para este usuário.",
        }
    return {"status": "ok", "memoria": fact.model_dump(mode="json")}


@router.get("/api/memory/facts")
async def buscar_fatos_memoria(
    sessao: Optional[str] = Query(None),
    q: str = Query(""),
    _=Depends(verify_jarvis_token),
):
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=403)
    from pesquisa_e_memoria import user_memory_manager

    fatos = user_memory_manager.search_facts(user_id=sessao, query=q)
    return {"status": "ok", "sessao": sessao, "memorias": [f.model_dump(mode="json") for f in fatos]}


@router.delete("/api/memory/facts/{memory_id}")
async def excluir_fato_memoria_especifico(
    memory_id: str,
    sessao: Optional[str] = Query(None),
    _=Depends(verify_jarvis_token),
):
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=403)
    from pesquisa_e_memoria import user_memory_manager

    ok = user_memory_manager.delete_fact(user_id=sessao, memory_id=memory_id)
    if not ok:
        return JSONResponse({"status": "erro", "mensagem": "Memória não encontrada."}, status_code=404)
    return {"status": "ok", "excluida": memory_id}


@router.delete("/api/memory/facts")
async def excluir_todas_memorias_usuario(
    sessao: Optional[str] = Query(None),
    _=Depends(verify_jarvis_token),
):
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=403)
    from pesquisa_e_memoria import user_memory_manager

    removidas = user_memory_manager.delete_all_facts(user_id=sessao)
    return {"status": "ok", "memorias_excluidas": removidas}


@router.get("/api/memory/retention")
async def obter_politica_retencao_memoria(
    sessao: Optional[str] = Query(None),
    _=Depends(verify_jarvis_token),
):
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=403)
    from pesquisa_e_memoria import user_memory_manager

    pol = user_memory_manager.get_retention_policy(user_id=sessao)
    return {"status": "ok", "politica": pol.model_dump(mode="json")}


@router.post("/api/memory/retention")
async def configurar_politica_retencao_memoria(payload: dict, _=Depends(verify_jarvis_token)):
    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=400)
    from pesquisa_e_memoria import user_memory_manager

    pol = user_memory_manager.set_retention_policy(
        user_id=sessao,
        personalization_enabled=payload.get("personalization_enabled"),
        retention_days=payload.get("retention_days"),
    )
    return {"status": "ok", "politica": pol.model_dump(mode="json")}


@router.post("/api/research/deep")
async def executar_deep_research_real(payload: dict, _=Depends(verify_jarvis_token)):
    """Executa o motor real de Deep Research com decomposição de sub-perguntas e citações SHA-256."""
    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=400)
    topic = (payload.get("topic") or payload.get("topico") or payload.get("query") or "").strip()
    if not topic:
        return JSONResponse({"status": "erro", "mensagem": "Campo 'topic' obrigatório."}, status_code=400)
    from pesquisa_e_memoria import deep_research_engine

    report = deep_research_engine.execute_research(
        topic=topic,
        user_id=sessao,
        session_id=sessao,
        sources=payload.get("sources"),
        fetch_urls=payload.get("fetch_urls"),
        max_subquestions=int(payload.get("max_subquestions", 4)),
    )
    return {"status": "ok", "relatorio": report.model_dump(mode="json")}


@router.get("/api/research/deep")
async def listar_relatorios_deep_research(
    sessao: Optional[str] = Query(None),
    _=Depends(verify_jarvis_token),
):
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=403)
    from pesquisa_e_memoria import deep_research_engine

    return {"status": "ok", "relatorios": deep_research_engine.list_reports(user_id=sessao)}


@router.get("/api/research/deep/{report_id}")
async def obter_relatorio_deep_research(
    report_id: str,
    sessao: Optional[str] = Query(None),
    _=Depends(verify_jarvis_token),
):
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=403)
    from pesquisa_e_memoria import deep_research_engine

    rep = deep_research_engine.get_report(report_id=report_id, user_id=sessao)
    if not rep:
        return JSONResponse({"status": "erro", "mensagem": "Relatório não encontrado nesta sessão."}, status_code=404)
    return {"status": "ok", "relatorio": rep.model_dump(mode="json")}


# ---------------- Documentos, Índice RAG Local com ACL e Copilot Pages (Fase F4) ----------------


@router.post("/api/documents/upload")
async def upload_documentos_rag(payload: dict, _=Depends(verify_jarvis_token)):
    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=400)
    conv_id = payload.get("conversation_id") or f"conv_{sessao}"
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        if payload.get("filename"):
            files = [payload]
        else:
            return JSONResponse({"status": "erro", "mensagem": "Nenhum arquivo enviado."}, status_code=400)

    from documentos_rag_pages import rag_index

    try:
        ingested = rag_index.ingest_batch(user_id=sessao, conversation_id=conv_id, files=files)
        return {
            "status": "ok",
            "documentos": [d.model_dump(mode="json") for d in ingested],
        }
    except ValueError as exc:
        return JSONResponse({"status": "erro", "mensagem": str(exc)}, status_code=400)


@router.get("/api/documents")
async def listar_documentos_rag(
    sessao: Optional[str] = Query(None),
    conversation_id: Optional[str] = Query(None),
    _=Depends(verify_jarvis_token),
):
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=403)
    from documentos_rag_pages import rag_index

    docs = rag_index.list_documents(user_id=sessao, conversation_id=conversation_id)
    return {"status": "ok", "documentos": [d.model_dump(mode="json") for d in docs]}


@router.delete("/api/documents/{doc_id}")
async def excluir_documento_rag(
    doc_id: str,
    sessao: Optional[str] = Query(None),
    _=Depends(verify_jarvis_token),
):
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=403)
    from documentos_rag_pages import rag_index

    ok = rag_index.delete_document(user_id=sessao, doc_id=doc_id)
    if not ok:
        return JSONResponse({"status": "erro", "mensagem": "Documento não encontrado nesta sessão."}, status_code=404)
    return {"status": "ok", "excluido": doc_id}


@router.post("/api/documents/query")
async def consultar_indice_rag(payload: dict, _=Depends(verify_jarvis_token)):
    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=400)
    query_text = (payload.get("query") or payload.get("q") or "").strip()
    if not query_text:
        return JSONResponse({"status": "erro", "mensagem": "Consulta vazia."}, status_code=400)
    from documentos_rag_pages import rag_index

    chunks = rag_index.query(
        user_id=sessao,
        query_text=query_text,
        conversation_id=payload.get("conversation_id"),
        top_k=int(payload.get("top_k", 5)),
    )
    return {"status": "ok", "resultados": chunks}


@router.post("/api/pages")
async def criar_copilot_page(payload: dict, _=Depends(verify_jarvis_token)):
    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=400)
    title = (payload.get("title") or payload.get("titulo") or "Nova Página").strip()
    content = payload.get("content_markdown") or payload.get("content") or ""
    conv_id = payload.get("conversation_id") or f"conv_{sessao}"
    from documentos_rag_pages import pages_manager

    page = pages_manager.create_page(
        user_id=sessao,
        conversation_id=conv_id,
        title=title,
        content_markdown=content,
        summary=payload.get("summary") or "Versão inicial",
    )
    return {"status": "ok", "page": page.model_dump(mode="json")}


@router.get("/api/pages")
async def listar_copilot_pages(
    sessao: Optional[str] = Query(None),
    _=Depends(verify_jarvis_token),
):
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=403)
    from documentos_rag_pages import pages_manager

    return {"status": "ok", "pages": pages_manager.list_pages(user_id=sessao)}


@router.get("/api/pages/{page_id}")
async def obter_copilot_page(
    page_id: str,
    sessao: Optional[str] = Query(None),
    _=Depends(verify_jarvis_token),
):
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=403)
    from documentos_rag_pages import pages_manager

    page = pages_manager.get_page(user_id=sessao, page_id=page_id)
    if not page:
        return JSONResponse({"status": "erro", "mensagem": "Página não encontrada."}, status_code=404)
    return {"status": "ok", "page": page.model_dump(mode="json")}


@router.put("/api/pages/{page_id}")
@router.post("/api/pages/{page_id}/update")
async def atualizar_copilot_page(
    page_id: str,
    payload: dict,
    _=Depends(verify_jarvis_token),
):
    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=400)
    content = payload.get("content_markdown") if "content_markdown" in payload else payload.get("content", "")
    from documentos_rag_pages import pages_manager

    page = pages_manager.update_page(
        user_id=sessao,
        page_id=page_id,
        content_markdown=str(content),
        title=payload.get("title"),
        summary=payload.get("summary") or "Edição iterativa",
    )
    if not page:
        return JSONResponse({"status": "erro", "mensagem": "Página não encontrada nesta sessão."}, status_code=404)
    return {"status": "ok", "page": page.model_dump(mode="json")}


@router.post("/api/pages/{page_id}/restore")
async def restaurar_versao_copilot_page(
    page_id: str,
    payload: dict,
    _=Depends(verify_jarvis_token),
):
    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=400)
    ver_num = int(payload.get("version_number", 1))
    from documentos_rag_pages import pages_manager

    page = pages_manager.restore_version(user_id=sessao, page_id=page_id, version_number=ver_num)
    if not page:
        return JSONResponse({"status": "erro", "mensagem": "Página ou versão não encontrada."}, status_code=404)
    return {"status": "ok", "page": page.model_dump(mode="json")}


@router.get("/api/pages/{page_id}/export")
async def exportar_copilot_page(
    page_id: str,
    sessao: Optional[str] = Query(None),
    formato: str = Query("md"),
    _=Depends(verify_jarvis_token),
):
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=403)
    from documentos_rag_pages import pages_manager

    try:
        exp = pages_manager.export_page(user_id=sessao, page_id=page_id, fmt=formato)
    except ValueError as exc:
        return JSONResponse({"status": "erro", "mensagem": str(exc)}, status_code=400)
    if not exp:
        return JSONResponse({"status": "erro", "mensagem": "Página não encontrada."}, status_code=404)
    return {"status": "ok", "exportacao": exp}


@router.delete("/api/pages/{page_id}")
async def excluir_copilot_page(
    page_id: str,
    sessao: Optional[str] = Query(None),
    _=Depends(verify_jarvis_token),
):
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=403)
    from documentos_rag_pages import pages_manager

    ok = pages_manager.delete_page(user_id=sessao, page_id=page_id)
    if not ok:
        return JSONResponse({"status": "erro", "mensagem": "Página não encontrada."}, status_code=404)
    return {"status": "ok", "excluida": page_id}


# ---------------------------------------------------------------------------
# F5: Rotas de Voz (Ditado, Read Aloud) e Visão Consentida (Copilot Vision)
# ---------------------------------------------------------------------------


@router.post("/api/voice/dictation/start")
async def iniciar_modo_ditado(payload: dict, _=Depends(verify_jarvis_token)):
    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=400)
    from voz_visao_linux import voice_dictation_service

    state = voice_dictation_service.start_dictation(
        session_id=sessao,
        language_code=payload.get("language_code") or "pt-BR",
        clear=bool(payload.get("clear", False)),
    )
    return {"status": "ok", "dictation": state.model_dump(mode="json")}


@router.post("/api/voice/dictation/append")
async def adicionar_segmento_ditado(payload: dict, _=Depends(verify_jarvis_token)):
    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=400)
    from voz_visao_linux import voice_dictation_service

    try:
        state = voice_dictation_service.append_segment(
            session_id=sessao,
            text_segment=str(payload.get("text") or payload.get("segment") or ""),
        )
    except PermissionError as exc:
        return JSONResponse({"status": "erro", "mensagem": str(exc)}, status_code=403)
    return {"status": "ok", "dictation": state.model_dump(mode="json")}


@router.post("/api/voice/dictation/stop")
async def parar_modo_ditado(payload: dict, _=Depends(verify_jarvis_token)):
    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=400)
    from voz_visao_linux import voice_dictation_service

    state = voice_dictation_service.stop_dictation(session_id=sessao)
    return {"status": "ok", "dictation": state.model_dump(mode="json")}


@router.post("/api/voice/dictation/clear")
async def limpar_buffer_ditado(payload: dict, _=Depends(verify_jarvis_token)):
    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=400)
    from voz_visao_linux import voice_dictation_service

    state = voice_dictation_service.clear_buffer(session_id=sessao)
    return {"status": "ok", "dictation": state.model_dump(mode="json")}


@router.get("/api/voice/dictation/state")
async def obter_estado_ditado(
    sessao: Optional[str] = Query(None),
    _=Depends(verify_jarvis_token),
):
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=403)
    from voz_visao_linux import voice_dictation_service

    state = voice_dictation_service.get_state(session_id=sessao)
    return {"status": "ok", "dictation": state.model_dump(mode="json")}


@router.post("/api/voice/read_aloud/start")
async def iniciar_leitura_voz_alta(payload: dict, _=Depends(verify_jarvis_token)):
    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=400)
    from voz_visao_linux import read_aloud_service

    state = read_aloud_service.start_read_aloud(
        session_id=sessao,
        text=str(payload.get("text") or payload.get("texto") or ""),
        voice_name=str(payload.get("voice_name") or payload.get("voz") or "Aoede"),
        rate=float(payload.get("rate") or payload.get("velocidade") or 1.0),
        language_code=str(payload.get("language_code") or "pt-BR"),
    )
    return {"status": "ok", "read_aloud": state.model_dump(mode="json")}


@router.post("/api/voice/read_aloud/pause")
async def pausar_leitura_voz_alta(payload: dict, _=Depends(verify_jarvis_token)):
    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=400)
    from voz_visao_linux import read_aloud_service

    state = read_aloud_service.pause(session_id=sessao)
    return {"status": "ok", "read_aloud": state.model_dump(mode="json")}


@router.post("/api/voice/read_aloud/resume")
async def retomar_leitura_voz_alta(payload: dict, _=Depends(verify_jarvis_token)):
    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=400)
    from voz_visao_linux import read_aloud_service

    state = read_aloud_service.resume(session_id=sessao)
    return {"status": "ok", "read_aloud": state.model_dump(mode="json")}


@router.post("/api/voice/read_aloud/stop")
async def parar_leitura_voz_alta(payload: dict, _=Depends(verify_jarvis_token)):
    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=400)
    from voz_visao_linux import read_aloud_service

    state = read_aloud_service.stop(session_id=sessao)
    return {"status": "ok", "read_aloud": state.model_dump(mode="json")}


@router.get("/api/voice/read_aloud/state")
async def estado_leitura_voz_alta(
    sessao: Optional[str] = Query(None),
    _=Depends(verify_jarvis_token),
):
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=403)
    from voz_visao_linux import read_aloud_service

    state = read_aloud_service.get_state(session_id=sessao)
    return {"status": "ok", "read_aloud": state.model_dump(mode="json")}


@router.post("/api/vision/consent/grant")
async def conceder_consentimento_visual(payload: dict, _=Depends(verify_jarvis_token)):
    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=400)
    sources = payload.get("sources") or ["screen", "window", "upload"]
    if isinstance(sources, str):
        sources = [sources]
    from voz_visao_linux import consented_vision_service

    try:
        rec = consented_vision_service.grant_consent(
            session_id=sessao,
            sources=sources,
            ttl_seconds=float(payload.get("ttl_seconds") or 900.0),
            target_window_title=payload.get("target_window_title"),
        )
    except ValueError as exc:
        return JSONResponse({"status": "erro", "mensagem": str(exc)}, status_code=400)
    return {"status": "ok", "consent": rec.model_dump(mode="json")}


@router.post("/api/vision/consent/revoke")
async def revogar_consentimento_visual(payload: dict, _=Depends(verify_jarvis_token)):
    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=400)
    from voz_visao_linux import consented_vision_service

    revoked = consented_vision_service.revoke_consent(session_id=sessao)
    return {"status": "ok", "revoked": revoked}


@router.get("/api/vision/consent/status")
async def status_consentimento_visual(
    sessao: Optional[str] = Query(None),
    source: str = Query("screen"),
    _=Depends(verify_jarvis_token),
):
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=403)
    from voz_visao_linux import consented_vision_service

    ok, reason, rec = consented_vision_service.check_consent(session_id=sessao, source=source)  # type: ignore[arg-type]
    return {
        "status": "ok",
        "authorized": ok,
        "reason": reason,
        "consent": rec.model_dump(mode="json") if rec else None,
    }


@router.post("/api/vision/analyze")
async def analisar_visao_consentida(payload: dict, _=Depends(verify_jarvis_token)):
    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=400)
    source = (payload.get("source") or "screen").strip()
    question = (payload.get("question") or payload.get("pergunta") or "").strip()
    if not question:
        return JSONResponse({"status": "erro", "mensagem": "Campo 'question' obrigatório."}, status_code=400)
    image_b64 = payload.get("image_base64") or payload.get("data") or ""
    img_bytes = base64.b64decode(image_b64) if image_b64 else None
    from voz_visao_linux import consented_vision_service

    try:
        res = consented_vision_service.analyze_visual_input(
            session_id=sessao,
            source=source,  # type: ignore[arg-type]
            question=question,
            image_bytes=img_bytes,
            ocr_hint_text=payload.get("ocr_hint_text"),
            requested_tools=payload.get("requested_tools"),
        )
    except PermissionError as exc:
        return JSONResponse({"status": "bloqueado", "mensagem": str(exc)}, status_code=403)
    return res


@router.get("/api/linux/multimedia_stack")
async def inspecionar_stack_multimidia_linux(_=Depends(verify_jarvis_token)):
    from voz_visao_linux import detect_linux_multimedia_stack

    return {"status": "ok", "stack": detect_linux_multimedia_stack()}


# ---------------------------------------------------------------------------
# F6: Rotas de Navegador Autônomo Seguro (Browser Policy) e Copilot Journeys
# ---------------------------------------------------------------------------


@router.post("/api/browser/evaluate")
async def avaliar_acao_navegador(payload: dict, _=Depends(verify_jarvis_token)):
    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=400)
    action_name = (payload.get("action") or payload.get("action_name") or "").strip()
    if not action_name:
        return JSONResponse({"status": "erro", "mensagem": "Campo 'action' obrigatório."}, status_code=400)
    from browser_journeys import browser_policy_guard

    ev = browser_policy_guard.evaluate_browser_action(
        session_id=sessao,
        action_name=action_name,
        args=payload.get("args") or {},
        from_page_dom_context=bool(payload.get("from_page_dom_context", False)),
        dom_snippet=payload.get("dom_snippet"),
    )
    return {"status": "ok", "evaluation": ev.model_dump(mode="json")}


@router.post("/api/browser/sanitize_dom")
async def sanitizar_dom_navegador(payload: dict, _=Depends(verify_jarvis_token)):
    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=400)
    from browser_journeys import browser_policy_guard

    res = browser_policy_guard.sanitize_dom_content(str(payload.get("dom_text") or payload.get("text") or ""))
    return {"status": "ok", **res}


@router.get("/api/journeys/settings")
async def obter_configuracao_journeys(
    sessao: Optional[str] = Query(None),
    _=Depends(verify_jarvis_token),
):
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=403)
    from browser_journeys import journeys_manager

    s = journeys_manager.get_settings(user_id=sessao)
    return {"status": "ok", "settings": s.model_dump(mode="json")}


@router.post("/api/journeys/settings")
async def configurar_journeys(payload: dict, _=Depends(verify_jarvis_token)):
    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=400)
    from browser_journeys import journeys_manager

    s = journeys_manager.configure_settings(
        user_id=sessao,
        enabled=payload.get("enabled"),
        retention_days=payload.get("retention_days"),
    )
    return {"status": "ok", "settings": s.model_dump(mode="json")}


@router.post("/api/journeys")
async def criar_journey(payload: dict, _=Depends(verify_jarvis_token)):
    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=400)
    topic = (payload.get("topic") or payload.get("titulo") or "").strip()
    if not topic:
        return JSONResponse({"status": "erro", "mensagem": "Campo 'topic' obrigatório."}, status_code=400)
    from browser_journeys import journeys_manager

    jrn = journeys_manager.create_journey(
        user_id=sessao,
        topic=topic,
        summary=str(payload.get("summary") or ""),
    )
    if jrn is None:
        return JSONResponse(
            {
                "status": "opt_in_obrigatorio",
                "mensagem": "Journeys está desativado por padrão (opt-in). Ative em /api/journeys/settings.",
            },
            status_code=403,
        )
    return {"status": "ok", "journey": jrn.model_dump(mode="json")}


@router.post("/api/journeys/{journey_id}/steps")
async def adicionar_passo_journey(
    journey_id: str,
    payload: dict,
    _=Depends(verify_jarvis_token),
):
    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=400)
    from browser_journeys import journeys_manager

    jrn = journeys_manager.add_step(
        user_id=sessao,
        journey_id=journey_id,
        title=str(payload.get("title") or "Página visitada"),
        step_type=payload.get("step_type") or "page_visit",
        url=payload.get("url"),
        query_or_note=str(payload.get("query_or_note") or ""),
    )
    if jrn is None:
        return JSONResponse(
            {"status": "erro", "mensagem": "Journey não encontrada ou opt-in desativado."},
            status_code=404,
        )
    return {"status": "ok", "journey": jrn.model_dump(mode="json")}


@router.get("/api/journeys")
async def listar_journeys(
    sessao: Optional[str] = Query(None),
    q: str = Query(""),
    _=Depends(verify_jarvis_token),
):
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=403)
    from browser_journeys import journeys_manager

    items = journeys_manager.list_journeys(user_id=sessao, query=q)
    return {"status": "ok", "sessao": sessao, "journeys": [j.model_dump(mode="json") for j in items]}


@router.post("/api/journeys/{journey_id}/resume")
async def retomar_journey(
    journey_id: str,
    payload: dict,
    _=Depends(verify_jarvis_token),
):
    sessao = payload.get("sessao") or payload.get("session_id")
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=400)
    from browser_journeys import journeys_manager

    jrn = journeys_manager.resume_journey(user_id=sessao, journey_id=journey_id)
    if not jrn:
        return JSONResponse({"status": "erro", "mensagem": "Journey não encontrada."}, status_code=404)
    return {"status": "ok", "journey": jrn.model_dump(mode="json")}


@router.delete("/api/journeys/{journey_id}")
async def excluir_journey_especifica(
    journey_id: str,
    sessao: Optional[str] = Query(None),
    _=Depends(verify_jarvis_token),
):
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=403)
    from browser_journeys import journeys_manager

    ok = journeys_manager.delete_journey(user_id=sessao, journey_id=journey_id)
    if not ok:
        return JSONResponse({"status": "erro", "mensagem": "Journey não encontrada."}, status_code=404)
    return {"status": "ok", "excluida": journey_id}


@router.delete("/api/journeys")
async def excluir_todas_journeys(
    sessao: Optional[str] = Query(None),
    _=Depends(verify_jarvis_token),
):
    if not sessao_aceita(sessao):
        return JSONResponse({"status": "erro", "mensagem": "Sessão inválida."}, status_code=403)
    from browser_journeys import journeys_manager

    count = journeys_manager.delete_all_journeys(user_id=sessao)
    return {"status": "ok", "journeys_excluidas": count}






