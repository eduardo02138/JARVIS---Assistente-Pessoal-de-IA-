# -*- coding: utf-8 -*-
"""
Suíte de Testes da Fase F2 — Orquestração e Contratos (Planner -> Router -> Executor -> Verifier).

Valida:
1. JarvisRouter: modos explícitos e seleção inteligente (smart) para rápido, profundo, estudo, pesquisa e computador.
2. JarvisPlanner + Executor + Verifier: execução ponta a ponta via AuthorityKernel e verificação de chaves esperadas.
3. Persistência durável, pausa em WAITING_APPROVAL e retomada pós-reinício (checkpoint) SEM duplicar efeitos colaterais.
4. Controle estrito de orçamento (max_tool_calls / max_steps), cancelamento cooperativo e isolamento de sessão via REST.
"""

from fastapi.testclient import TestClient

from authority_kernel import authority_kernel
from orquestrador import (
    ConversationMode,
    JarvisOrchestrator,
    TaskBudget,
    TaskState,
)
from policy_engine import RiskLevel, policy_engine
from server import JARVIS_SECRET_TOKEN, app
from servidor.seguranca import emitir_sessao


def test_f2_router_modos_e_smart_detection():
    """Valida a seleção determinística de modos, caminhos, especialistas e modelos no JarvisRouter."""
    orch = JarvisOrchestrator()

    # 1. Smart mode: pergunta curta -> RAPIDO
    r_fast = orch.router.route("que horas são?", requested_mode="smart")
    assert r_fast.effective_mode == ConversationMode.RAPIDO
    assert r_fast.specialist == "agente_rapido"

    # 2. Smart mode: pedido de estudo -> ESTUDO
    r_study = orch.router.route("me ensine passo a passo como aprender algoritmos de grafos", requested_mode="smart")
    assert r_study.effective_mode == ConversationMode.ESTUDO
    assert r_study.specialist == "tutor_socratico"

    # 3. Smart mode: pedido de pesquisa profunda -> PESQUISA
    r_research = orch.router.route("faça um deep research e relatório completo com fontes sobre fusão nuclear", requested_mode="smart")
    assert r_research.effective_mode == ConversationMode.PESQUISA
    assert r_research.requires_grounding is True
    assert r_research.specialist == "especialista_pesquisa"

    # 4. Smart mode: análise profunda -> PROFUNDO
    r_deep = orch.router.route("pense fundo e compare detalhadamente arquiteturas de microkernel", requested_mode="smart")
    assert r_deep.effective_mode == ConversationMode.PROFUNDO
    assert r_deep.thinking_level == "high"

    # 5. Modo explícito sobrescreve heurística
    r_explicit = orch.router.route("oi", requested_mode="estudo")
    assert r_explicit.effective_mode == ConversationMode.ESTUDO


def test_f2_checkpoint_pausa_aprovacao_e_retomada_sem_duplicar_efeitos(tmp_path):
    """Gate F2: Tarefa longa pausa em WAITING_APPROVAL, reinicia do disco e retoma sem duplicar efeitos."""
    store_file = str(tmp_path / "tasks_checkpoint.json")
    sessao = emitir_sessao("sessao-f2-checkpoint")

    efeitos_passo_1 = {"chamadas": 0}
    efeitos_passo_2 = {"chamadas": 0}

    def tool_passo_1(tag: str = "a"):
        efeitos_passo_1["chamadas"] += 1
        return {"sucesso": True, "tag": tag, "contagem": efeitos_passo_1["chamadas"]}

    def tool_passo_2_sensivel(destino: str = "https://exemplo.org"):
        efeitos_passo_2["chamadas"] += 1
        return {"sucesso": True, "destino": destino, "contagem": efeitos_passo_2["chamadas"]}

    policy_engine.register_tool_policy("f2_tool_passo_1", RiskLevel.READ)
    policy_engine.register_tool_policy("f2_tool_passo_2_sensivel", RiskLevel.EXTERNAL_WRITE)
    authority_kernel.register_executor("f2_tool_passo_1", tool_passo_1, source="test_f2")
    authority_kernel.register_executor("f2_tool_passo_2_sensivel", tool_passo_2_sensivel, source="test_f2")

    orch1 = JarvisOrchestrator(store_path=store_file)
    task = orch1.submit_task(
        goal="Executar coleta local, ação externa sensível e síntese final",
        session_id=sessao,
        user_id=sessao,
        mode="profundo",
        explicit_steps=[
            {
                "step_id": "s1",
                "title": "Passo 1: Coleta segura",
                "tool_name": "f2_tool_passo_1",
                "tool_args": {"tag": "inicial"},
                "expected_keys": ["sucesso", "tag"],
            },
            {
                "step_id": "s2",
                "title": "Passo 2: Envio externo com confirmação",
                "tool_name": "f2_tool_passo_2_sensivel",
                "tool_args": {"destino": "https://parceiro.seguro"},
                "depends_on": ["s1"],
                "expected_keys": ["sucesso", "destino"],
            },
            {
                "step_id": "s3",
                "title": "Passo 3: Síntese verificada",
                "kind": "synthesis",
                "depends_on": ["s1", "s2"],
            },
        ],
    )

    # 1ª Execução: Passo 1 executa (SUCCEEDED), Passo 2 bloqueia em WAITING_APPROVAL
    t_paused = orch1.run_task(task.task_id, session_id=sessao)
    assert t_paused.state == TaskState.WAITING_APPROVAL
    assert t_paused.checkpoint_index == 1
    assert efeitos_passo_1["chamadas"] == 1
    assert efeitos_passo_2["chamadas"] == 0
    pending_id = t_paused.steps[1].pending_action_id
    assert pending_id is not None

    # Simula reinício completo do orquestrador lendo estado durável do disco
    orch2 = JarvisOrchestrator(store_path=store_file)
    loaded = orch2.get_task(task.task_id, session_id=sessao)
    assert loaded is not None
    assert loaded.state == TaskState.WAITING_APPROVAL
    assert loaded.steps[0].state == TaskState.SUCCEEDED

    # Usuário aprova a ação pendente do Passo 2
    assert policy_engine.approve_action(pending_id, session_id=sessao, user_id=sessao) is True

    # Retoma execução na nova instância: Passo 1 NÃO pode rodar de novo!
    t_done = orch2.run_task(task.task_id, session_id=sessao)
    assert t_done.state == TaskState.SUCCEEDED
    assert efeitos_passo_1["chamadas"] == 1, "Efeito colateral do Passo 1 foi duplicado após retomada!"
    assert efeitos_passo_2["chamadas"] == 1, "Passo 2 deveria ter executado exatamente 1 vez!"
    assert t_done.verification is not None and t_done.verification.passed is True
    assert t_done.verification.verified_steps == 3


def test_f2_orcamento_cancelamento_e_isolamento_rest():
    """Valida limites de orçamento (TaskBudget), cancelamento e endpoints REST do Orquestrador."""
    client = TestClient(app)
    headers = {"X-Jarvis-Token": JARVIS_SECRET_TOKEN}
    sessao_a = emitir_sessao("sessao-f2-rest-a")
    sessao_b = emitir_sessao("sessao-f2-rest-b")

    # 1. Endpoint de diagnóstico de rota
    r_route = client.post(
        "/api/orchestrator/route",
        json={"texto": "investigue a fundo e faça um dossiê", "modo": "smart"},
        headers=headers,
    )
    assert r_route.status_code == 200
    assert r_route.json()["roteamento"]["effective_mode"] == "pesquisa"

    # 2. Cria tarefa com orçamento restrito (max_tool_calls=1) mas 2 chamadas de ferramenta -> BLOCKED
    r_budget = client.post(
        "/api/orchestrator/tasks",
        json={
            "sessao": sessao_a,
            "goal": "Teste de limite de orçamento",
            "budget": {"max_steps": 5, "max_tool_calls": 1, "max_duration_s": 30.0},
            "steps": [
                {"step_id": "b1", "tool_name": "get_current_datetime", "tool_args": {}},
                {"step_id": "b2", "tool_name": "get_current_datetime", "tool_args": {}},
            ],
            "executar_imediato": True,
        },
        headers=headers,
    )
    assert r_budget.status_code == 200
    task_budget = r_budget.json()["task"]
    assert task_budget["state"] == "BLOCKED"
    assert "orçamento máximo" in (task_budget.get("error") or "").lower()

    # 3. Isolamento entre sessões: Sessão B não vê a tarefa da Sessão A
    r_get_b = client.get(
        f"/api/orchestrator/tasks/{task_budget['task_id']}?sessao={sessao_b}",
        headers=headers,
    )
    assert r_get_b.status_code == 404

    # 4. Cria tarefa sem execução imediata e cancela antes de rodar
    r_queued = client.post(
        "/api/orchestrator/tasks",
        json={
            "sessao": sessao_a,
            "goal": "Consultar data e hora atuais",
            "executar_imediato": False,
        },
        headers=headers,
    )
    assert r_queued.status_code == 200
    tid_q = r_queued.json()["task"]["task_id"]
    assert r_queued.json()["task"]["state"] == "QUEUED"

    r_cancel = client.post(
        f"/api/orchestrator/tasks/{tid_q}/cancel",
        json={"sessao": sessao_a},
        headers=headers,
    )
    assert r_cancel.status_code == 200
    assert r_cancel.json()["task"]["state"] == "CANCELLED"
