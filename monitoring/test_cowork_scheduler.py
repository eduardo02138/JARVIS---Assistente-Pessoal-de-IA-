"""
F8 — Testes de Copilot Cowork e Tarefas Agendadas (`monitoring/test_cowork_scheduler.py`)

Testes obrigatórios:
- test_tarefa_recorrente_persiste_e_executa
- test_job_pausa_em_confirmacao_e_retoma
- test_retry_respeita_idempotencia
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from cowork_scheduler import CoworkSchedulerService, CoworkStepSpec
from policy_engine import policy_engine
from server import app
from servidor.seguranca import JARVIS_SECRET_TOKEN, emitir_sessao


def _headers() -> dict:
    if JARVIS_SECRET_TOKEN:
        return {"X-Jarvis-Token": JARVIS_SECRET_TOKEN}
    return {}


def test_tarefa_recorrente_persiste_e_executa(tmp_path):
    """
    Garante que uma tarefa agendada recorrente persiste em disco entre reinícios
    do serviço e executa corretamente nos instantes programados.
    """
    storage = tmp_path / "cowork_sched.json"
    uid = emitir_sessao("user_sched_f8")
    t0 = 1_800_000_000.0

    svc1 = CoworkSchedulerService(storage_path=storage)
    sched = svc1.create_schedule(
        user_id=uid,
        title="Resumo Matinal de Telemetria",
        prompt_or_goal="Gerar relatório diário de CPU/GPU",
        schedule_type="recurring",
        interval_seconds=3600.0,
        first_run_at=t0 + 60.0,
    )
    assert sched.schedule_id

    # Recarrega do disco em nova instância (simulando restart do processo)
    svc2 = CoworkSchedulerService(storage_path=storage)
    loaded = svc2.list_schedules(user_id=uid)
    assert len(loaded) == 1
    assert loaded[0].title == "Resumo Matinal de Telemetria"

    # Antes do horário agendado nada dispara
    assert len(svc2.tick(now=t0 + 10.0)) == 0

    # No primeiro disparo (t0 + 65)
    jobs_run1 = svc2.tick(now=t0 + 65.0)
    assert len(jobs_run1) == 1
    assert jobs_run1[0].state == "SUCCEEDED"

    # No segundo disparo 1h depois (t0 + 3670)
    jobs_run2 = svc2.tick(now=t0 + 3670.0)
    assert len(jobs_run2) == 1
    assert jobs_run2[0].state == "SUCCEEDED"

    updated_sched = svc2.list_schedules(user_id=uid)[0]
    assert updated_sched.run_count == 2
    assert len(updated_sched.execution_history_job_ids) == 2


def test_job_pausa_em_confirmacao_e_retoma(tmp_path):
    """
    Garante que um job Cowork multi-etapa pausa em `WAITING_APPROVAL` quando uma
    etapa exige aprovação humana e retoma do checkpoint exato após aprovação,
    sem reexecutar etapas anteriores.
    """
    storage = tmp_path / "cowork_pause.json"
    executed_steps = []

    def _executor(step: CoworkStepSpec):
        executed_steps.append(step.name)
        return {"step": step.name, "done": True}

    svc = CoworkSchedulerService(storage_path=storage, step_executor=_executor)
    uid = emitir_sessao("user_pause_f8")

    job = svc.create_job(
        user_id=uid,
        title="Preparar e Enviar Relatório Executivo",
        steps=[
            {"name": "coletar_metricas", "action": "collect", "idempotency_key": "k_step1"},
            {
                "name": "enviar_relatorio_externo",
                "action": "send_report",
                "params": {"recipient": "diretoria@empresa.com"},
                "idempotency_key": "k_step2",
                "requires_approval": True,
            },
            {"name": "arquivar_copia_local", "action": "archive", "idempotency_key": "k_step3"},
        ],
    )

    # 1. Inicia execução: Etapa 1 conclui, Etapa 2 pausa em WAITING_APPROVAL
    paused = svc.run_or_resume_job(user_id=uid, job_id=job.job_id)
    assert paused.state == "WAITING_APPROVAL"
    assert paused.checkpoint_index == 1
    assert paused.pending_confirmation_id is not None
    assert executed_steps == ["coletar_metricas"]

    # 2. Usuário aprova a confirmação pendente no PolicyEngine
    ok_app = policy_engine.approve_action(
        paused.pending_confirmation_id,
        session_id=uid,
        user_id=uid,
    )
    assert ok_app is True

    # 3. Retoma o job: Etapa 1 NÃO é reexecutada; Etapas 2 e 3 executam e o job vai para SUCCEEDED
    finished = svc.run_or_resume_job(user_id=uid, job_id=job.job_id)
    assert finished.state == "SUCCEEDED"
    assert finished.checkpoint_index == 3
    assert executed_steps == ["coletar_metricas", "enviar_relatorio_externo", "arquivar_copia_local"]


def test_retry_respeita_idempotencia(tmp_path):
    """
    Garante que falhas transitórias aplicam retry até o limite configurado e que
    chaves de idempotência já concluídas nunca executam efeito colateral duplicado.
    """
    storage = tmp_path / "cowork_retry.json"
    call_counts = {"flaky_step": 0, "idempotent_step": 0}

    def _flaky_executor(step: CoworkStepSpec):
        if step.action == "flaky":
            call_counts["flaky_step"] += 1
            if call_counts["flaky_step"] == 1:
                raise RuntimeError("Erro de rede transitório simulado")
            return {"recovered": True, "attempt": call_counts["flaky_step"]}
        call_counts["idempotent_step"] += 1
        return {"ok": True}

    svc = CoworkSchedulerService(storage_path=storage, step_executor=_flaky_executor)
    uid = emitir_sessao("user_idem_f8")

    # Mesmo idempotency_key usado em duas etapas: a segunda deve reaproveitar o resultado sem reexecutar
    job = svc.create_job(
        user_id=uid,
        title="Job com Retry e Idempotência",
        steps=[
            {"name": "etapa_instavel", "action": "flaky", "idempotency_key": "idem_flaky", "max_retries": 2},
            {"name": "etapa_unica_1", "action": "once", "idempotency_key": "idem_shared_key"},
            {"name": "etapa_unica_duplicada", "action": "once", "idempotency_key": "idem_shared_key"},
        ],
    )

    res = svc.run_or_resume_job(user_id=uid, job_id=job.job_id)
    assert res.state == "SUCCEEDED"
    # A etapa instável falhou na 1ª e passou na 2ª tentativa
    assert call_counts["flaky_step"] == 2
    # A etapa com chave de idempotência compartilhada executou apenas 1 vez
    assert call_counts["idempotent_step"] == 1

    # Também valida endpoint REST /api/cowork/jobs
    client = TestClient(app)
    r_api = client.post(
        "/api/cowork/jobs",
        json={
            "sessao": uid,
            "title": "Job via API",
            "steps": [{"name": "passo_api", "action": "noop", "idempotency_key": "api_k1"}],
        },
        headers=_headers(),
    )
    assert r_api.status_code == 200
    assert r_api.json()["job"]["state"] == "SUCCEEDED"
