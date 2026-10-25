"""Rotas do agente ADK: chat de texto, confirmações pendentes e Modo Computador."""

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
        return await _turno_de_chat(texto, sessao, payload.get("caminho"))


async def _turno_de_chat(texto: str, sessao: str, caminho_forcado: Optional[str]):
    usuario = sessao
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

    return {
        "status": "ok",
        "caminho": caminho,
        "motivo_do_roteamento": motivo,
        "provedor": "google_studio",
        "modelo": runner.agent.model,
        "resposta": resposta.strip(),
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


