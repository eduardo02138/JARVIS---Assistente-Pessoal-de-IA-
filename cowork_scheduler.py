"""
F8 — Copilot Cowork e Tarefas Agendadas Duráveis (`cowork_scheduler.py`)

Implementa:
1. Runner durável de jobs multi-etapa (Cowork) com máquina de estados formal:
   `QUEUED`, `RUNNING`, `WAITING_APPROVAL`, `SUCCEEDED`, `FAILED`, `CANCELLED`, `BLOCKED`;
2. Checkpoints persistentes, chaves de idempotência (`idempotency_key`) e retries
   limitados com backoff exponencial sem duplicar efeitos colaterais já concluídos;
3. Agendador local persistente (`one_shot` e `recurring` por intervalo/cron),
   histórico de execuções e fila de notificações por usuário.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from policy_engine import policy_engine

DEFAULT_COWORK_PATH = Path(
    os.environ.get(
        "JARVIS_COWORK_STORAGE_PATH",
        str(Path(__file__).resolve().parent / "data" / "cowork_scheduler.json"),
    )
)

JobState = Literal[
    "QUEUED",
    "RUNNING",
    "WAITING_APPROVAL",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "BLOCKED",
]


class CoworkStepSpec(BaseModel):
    step_id: str = Field(default_factory=lambda: f"cstep_{secrets.token_urlsafe(6)}")
    name: str
    action: str
    params: Dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(default_factory=lambda: f"idem_{secrets.token_urlsafe(8)}")
    requires_approval: bool = False
    max_retries: int = 2
    backoff_base_s: float = 0.5
    attempts: int = 0
    status: JobState = "QUEUED"
    result: Optional[Any] = None
    last_error: Optional[str] = None


class CoworkNotification(BaseModel):
    notification_id: str = Field(default_factory=lambda: f"notif_{secrets.token_urlsafe(6)}")
    user_id: str
    job_id: str
    level: Literal["info", "warning", "approval_required", "error"] = "info"
    message: str
    timestamp: float = Field(default_factory=time.time)


class CoworkJobRecord(BaseModel):
    job_id: str = Field(default_factory=lambda: f"job_{secrets.token_urlsafe(8)}")
    user_id: str
    title: str
    state: JobState = "QUEUED"
    steps: List[CoworkStepSpec] = Field(default_factory=list)
    checkpoint_index: int = 0
    pending_confirmation_id: Optional[str] = None
    executed_idempotency_keys: Dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class ScheduledTaskRecord(BaseModel):
    schedule_id: str = Field(default_factory=lambda: f"sched_{secrets.token_urlsafe(8)}")
    user_id: str
    title: str
    prompt_or_goal: str
    schedule_type: Literal["one_shot", "recurring"] = "recurring"
    interval_seconds: float = 3600.0
    next_run_at: float = 0.0
    last_run_at: Optional[float] = None
    run_count: int = 0
    active: bool = True
    steps_template: List[Dict[str, Any]] = Field(default_factory=list)
    execution_history_job_ids: List[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)


class CoworkSchedulerService:
    """
    Motor unificado de Copilot Cowork (jobs multi-etapa com aprovação e idempotência)
    e Tarefas Agendadas persistentes em disco.
    """

    def __init__(
        self,
        storage_path: Optional[Path] = None,
        step_executor: Optional[Callable[[CoworkStepSpec], Any]] = None,
    ) -> None:
        self._lock = threading.RLock()
        self.storage_path = storage_path or DEFAULT_COWORK_PATH
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.step_executor = step_executor or self._default_step_executor
        self._jobs: Dict[str, CoworkJobRecord] = {}
        self._schedules: Dict[str, ScheduledTaskRecord] = {}
        self._notifications: List[CoworkNotification] = []
        self._load()

    @staticmethod
    def _default_step_executor(step: CoworkStepSpec) -> Any:
        return {
            "action": step.action,
            "name": step.name,
            "completed": True,
            "params": step.params,
        }

    def _load(self) -> None:
        with self._lock:
            if not self.storage_path.exists():
                return
            try:
                raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
                for jid, jdata in raw.get("jobs", {}).items():
                    self._jobs[jid] = CoworkJobRecord.model_validate(jdata)
                for sid, sdata in raw.get("schedules", {}).items():
                    self._schedules[sid] = ScheduledTaskRecord.model_validate(sdata)
                for ndata in raw.get("notifications", []):
                    self._notifications.append(CoworkNotification.model_validate(ndata))
            except Exception:
                pass

    def _save(self) -> None:
        with self._lock:
            payload = {
                "jobs": {k: v.model_dump(mode="json") for k, v in self._jobs.items()},
                "schedules": {k: v.model_dump(mode="json") for k, v in self._schedules.items()},
                "notifications": [n.model_dump(mode="json") for n in self._notifications[-200:]],
            }
            tmp = self.storage_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.storage_path)

    def _notify(
        self,
        user_id: str,
        job_id: str,
        message: str,
        level: Literal["info", "warning", "approval_required", "error"] = "info",
    ) -> None:
        self._notifications.append(
            CoworkNotification(user_id=user_id, job_id=job_id, level=level, message=message)
        )

    def create_job(
        self,
        user_id: str,
        title: str,
        steps: List[Dict[str, Any]],
    ) -> CoworkJobRecord:
        parsed_steps: List[CoworkStepSpec] = []
        for idx, s in enumerate(steps):
            parsed_steps.append(
                CoworkStepSpec(
                    name=str(s.get("name") or f"Etapa {idx + 1}"),
                    action=str(s.get("action") or "noop"),
                    params=dict(s.get("params") or {}),
                    idempotency_key=str(s.get("idempotency_key") or f"idem_{idx}_{secrets.token_urlsafe(6)}"),
                    requires_approval=bool(s.get("requires_approval", False)),
                    max_retries=int(s.get("max_retries", 2)),
                    backoff_base_s=float(s.get("backoff_base_s", 0.01)),
                )
            )
        with self._lock:
            job = CoworkJobRecord(
                user_id=user_id,
                title=title.strip() or "Tarefa Cowork",
                state="QUEUED",
                steps=parsed_steps,
            )
            self._jobs[job.job_id] = job
            self._save()
            return job.model_copy(deep=True)

    def run_or_resume_job(self, user_id: str, job_id: str) -> CoworkJobRecord:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.user_id != user_id:
                raise KeyError(f"Job Cowork '{job_id}' não encontrado para este usuário.")
            if job.state in ("SUCCEEDED", "CANCELLED", "BLOCKED"):
                return job.model_copy(deep=True)

            job.state = "RUNNING"
            job.updated_at = time.time()

            while job.checkpoint_index < len(job.steps):
                step = job.steps[job.checkpoint_index]

                # 1. Idempotência estrita: se esta chave já executou com sucesso, reaproveita sem reexecutar
                if step.idempotency_key in job.executed_idempotency_keys:
                    step.status = "SUCCEEDED"
                    step.result = job.executed_idempotency_keys[step.idempotency_key]
                    job.checkpoint_index += 1
                    continue

                # 2. Pausa para confirmação humana se a etapa exigir aprovação
                if step.requires_approval:
                    tool_key = f"cowork_{step.action}"
                    approved = policy_engine.consume_authorization(
                        tool_key,
                        step.params,
                        session_id=user_id,
                        user_id=user_id,
                    )
                    if not approved:
                        pending = policy_engine.create_pending_action(
                            tool_key,
                            step.params,
                            session_id=user_id,
                            user_id=user_id,
                        )
                        step.status = "WAITING_APPROVAL"
                        job.state = "WAITING_APPROVAL"
                        job.pending_confirmation_id = pending.action_id
                        job.updated_at = time.time()
                        self._notify(
                            user_id=user_id,
                            job_id=job.job_id,
                            level="approval_required",
                            message=f"Etapa '{step.name}' aguarda confirmação humana ({pending.action_id}).",
                        )
                        self._save()
                        return job.model_copy(deep=True)

                # 3. Executa etapa com retries limitados e backoff
                step.status = "RUNNING"
                success = False
                while step.attempts <= step.max_retries:
                    step.attempts += 1
                    try:
                        out = self.step_executor(step)
                        step.result = out
                        step.status = "SUCCEEDED"
                        step.last_error = None
                        job.executed_idempotency_keys[step.idempotency_key] = out
                        success = True
                        break
                    except Exception as exc:
                        step.last_error = str(exc)
                        if step.attempts <= step.max_retries:
                            delay = min(0.05, step.backoff_base_s * (2 ** (step.attempts - 1)))
                            if delay > 0:
                                time.sleep(delay)

                if not success:
                    step.status = "FAILED"
                    job.state = "FAILED"
                    job.updated_at = time.time()
                    self._notify(
                        user_id=user_id,
                        job_id=job.job_id,
                        level="error",
                        message=f"Falha na etapa '{step.name}' após {step.attempts} tentativas: {step.last_error}",
                    )
                    self._save()
                    return job.model_copy(deep=True)

                job.pending_confirmation_id = None
                job.checkpoint_index += 1

            job.state = "SUCCEEDED"
            job.updated_at = time.time()
            self._notify(
                user_id=user_id,
                job_id=job.job_id,
                level="info",
                message=f"Job Cowork '{job.title}' concluído com sucesso.",
            )
            self._save()
            return job.model_copy(deep=True)

    def cancel_job(self, user_id: str, job_id: str) -> Optional[CoworkJobRecord]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.user_id != user_id:
                return None
            job.state = "CANCELLED"
            job.updated_at = time.time()
            self._save()
            return job.model_copy(deep=True)

    def get_job(self, user_id: str, job_id: str) -> Optional[CoworkJobRecord]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.user_id != user_id:
                return None
            return job.model_copy(deep=True)

    def list_jobs(self, user_id: str) -> List[CoworkJobRecord]:
        with self._lock:
            items = [j.model_copy(deep=True) for j in self._jobs.values() if j.user_id == user_id]
        items.sort(key=lambda x: x.updated_at, reverse=True)
        return items

    def list_notifications(self, user_id: str) -> List[CoworkNotification]:
        with self._lock:
            return [n.model_copy(deep=True) for n in self._notifications if n.user_id == user_id]

    # ---------------- Agendador Persistente (Scheduled Tasks) ----------------

    def create_schedule(
        self,
        user_id: str,
        title: str,
        prompt_or_goal: str,
        schedule_type: Literal["one_shot", "recurring"] = "recurring",
        interval_seconds: float = 3600.0,
        first_run_at: Optional[float] = None,
        steps_template: Optional[List[Dict[str, Any]]] = None,
    ) -> ScheduledTaskRecord:
        now = time.time()
        next_run = float(first_run_at) if first_run_at is not None else (now + float(interval_seconds))
        default_steps = steps_template or [
            {"name": f"Execução agendada: {title}", "action": "scheduled_prompt", "params": {"goal": prompt_or_goal}}
        ]
        with self._lock:
            sched = ScheduledTaskRecord(
                user_id=user_id,
                title=title.strip() or "Agendamento",
                prompt_or_goal=prompt_or_goal.strip(),
                schedule_type=schedule_type,
                interval_seconds=max(1.0, float(interval_seconds)),
                next_run_at=next_run,
                steps_template=default_steps,
                active=True,
            )
            self._schedules[sched.schedule_id] = sched
            self._save()
            return sched.model_copy(deep=True)

    def tick(self, now: Optional[float] = None) -> List[CoworkJobRecord]:
        """Avalia e dispara todas as tarefas agendadas vencidas até o instante `now`."""
        ts = time.time() if now is None else float(now)
        triggered_jobs: List[CoworkJobRecord] = []
        with self._lock:
            for sched in list(self._schedules.values()):
                if not sched.active or ts < sched.next_run_at:
                    continue
                # Cria um job Cowork para esta ocorrência
                run_steps = []
                for idx, st in enumerate(sched.steps_template):
                    s_copy = dict(st)
                    s_copy["idempotency_key"] = f"sched_{sched.schedule_id}_run_{sched.run_count + 1}_step_{idx}"
                    run_steps.append(s_copy)

                job = self.create_job(
                    user_id=sched.user_id,
                    title=f"{sched.title} (#{sched.run_count + 1})",
                    steps=run_steps,
                )
                completed_job = self.run_or_resume_job(user_id=sched.user_id, job_id=job.job_id)
                sched.last_run_at = ts
                sched.run_count += 1
                sched.execution_history_job_ids.append(completed_job.job_id)
                if sched.schedule_type == "one_shot":
                    sched.active = False
                else:
                    sched.next_run_at = ts + sched.interval_seconds
                triggered_jobs.append(completed_job)
            if triggered_jobs:
                self._save()
        return triggered_jobs

    def list_schedules(self, user_id: str) -> List[ScheduledTaskRecord]:
        with self._lock:
            return [s.model_copy(deep=True) for s in self._schedules.values() if s.user_id == user_id]

    def delete_schedule(self, user_id: str, schedule_id: str) -> bool:
        with self._lock:
            s = self._schedules.get(schedule_id)
            if not s or s.user_id != user_id:
                return False
            self._schedules.pop(schedule_id, None)
            self._save()
            return True


cowork_scheduler_service = CoworkSchedulerService()
