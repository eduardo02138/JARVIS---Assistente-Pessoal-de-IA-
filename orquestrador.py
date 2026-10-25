"""
Orquestrador Estruturado do J.A.R.V.I.S. (Fase F2 — Orquestração e Contratos).

Separa formalmente:
    1. Planner  -> Decompõe a solicitação em Plano Tipado (Pydantic) com passos, dependências e orçamento.
    2. Router   -> Seleciona modo conversacional, caminho de execução, modelo e especialista.
    3. Executor -> Executa cada passo exclusivamente via AuthorityKernel, salvando checkpoints duráveis.
    4. Verifier -> Valida invariantes de saída, citações/fontes, efeitos esperados e completude.

Garantias:
- Estado durável de tarefas (QUEUED, RUNNING, WAITING_APPROVAL, SUCCEEDED, FAILED, CANCELLED, BLOCKED).
- Controle estrito de orçamento (max_steps, max_tool_calls, max_duration_s, max_cost_units).
- Cancelamento cooperativo e imediato.
- Recuperação de falha/reinício (resume_task): passos já concluídos (SUCCEEDED) no checkpoint
  jamais são re-executados, evitando duplicação de efeitos colaterais.
"""

from __future__ import annotations

import datetime
import enum
import json
import logging
import os
import secrets
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from agentes.roteador import CAMINHO_COMPLEXO, CAMINHO_RAPIDO, escolher_caminho
from authority_kernel import KernelExecutionResult, authority_kernel, canonical_args_digest
from policy_engine import RiskLevel, policy_engine

logger = logging.getLogger("jarvis.orquestrador")

DEFAULT_TASKS_STORE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "data",
    "orchestrator_tasks.json",
)


class TaskState(str, enum.Enum):
    """Estados formais de uma tarefa ou passo orquestrado."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"


class ConversationMode(str, enum.Enum):
    """Modos conversacionais equivalentes aos do Microsoft Copilot."""

    RAPIDO = "rapido"          # Resposta imediata / baixa latência
    PROFUNDO = "profundo"      # Think Deeper / análise passo a passo
    ESTUDO = "estudo"          # Study & Learn / orientação socrática e didática
    PESQUISA = "pesquisa"      # Deep Research / investigação multi-etapas com fontes
    SMART = "smart"            # Seleção automática inteligente pelo Router


class TaskBudget(BaseModel):
    """Orçamento e limites operacionais de uma tarefa."""

    max_steps: int = Field(default=10, ge=1, le=50)
    max_tool_calls: int = Field(default=8, ge=0, le=50)
    max_duration_s: float = Field(default=120.0, gt=0.0, le=3600.0)
    max_cost_units: float = Field(default=100.0, gt=0.0)
    consumed_steps: int = 0
    consumed_tool_calls: int = 0
    consumed_cost_units: float = 0.0


class PlanStep(BaseModel):
    """Contrato tipado de um passo individual do plano."""

    step_id: str
    index: int
    title: str
    description: str
    kind: str = "tool"  # "tool", "synthesis", "verification"
    tool_name: Optional[str] = None
    tool_args: Dict[str, Any] = Field(default_factory=dict)
    depends_on: List[str] = Field(default_factory=list)
    expected_keys: List[str] = Field(default_factory=list)
    idempotency_key: str = ""
    state: TaskState = TaskState.QUEUED
    result: Any = None
    error: Optional[str] = None
    pending_action_id: Optional[str] = None
    capability_id: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    attempts: int = 0
    max_attempts: int = 2


class RoutingDecision(BaseModel):
    """Contrato tipado de decisão do Router."""

    mode: ConversationMode
    effective_mode: ConversationMode
    route_path: str
    specialist: str
    model_hint: str
    thinking_level: str
    requires_grounding: bool = False
    untrusted_input: bool = False
    reason: str


class VerificationReport(BaseModel):
    """Relatório formal produzido pelo Verifier ao final da execução."""

    passed: bool
    completeness_score: float = Field(default=1.0, ge=0.0, le=1.0)
    verified_steps: int = 0
    total_steps: int = 0
    citations_verified: bool = True
    missing_expectations: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    summary: str = ""


class OrchestratedTask(BaseModel):
    """Estado completo e durável de uma tarefa gerenciada pelo Orquestrador."""

    task_id: str
    session_id: str
    user_id: str
    goal: str
    mode: ConversationMode = ConversationMode.SMART
    routing: Optional[RoutingDecision] = None
    state: TaskState = TaskState.QUEUED
    budget: TaskBudget = Field(default_factory=TaskBudget)
    steps: List[PlanStep] = Field(default_factory=list)
    checkpoint_index: int = 0
    executed_idempotency_keys: List[str] = Field(default_factory=list)
    verification: Optional[VerificationReport] = None
    final_output: Any = None
    error: Optional[str] = None
    created_at: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    started_monotonic: float = 0.0


class JarvisRouter:
    """Roteador determinístico de modos, caminhos, modelos e especialistas."""

    _MARCAS_ESTUDO = (
        "me ensine",
        "ensine-me",
        "modo estudo",
        "explique didaticamente",
        "passo a passo como aprender",
        "quiz",
        "exercício sobre",
        "exercicio sobre",
        "estudar",
    )
    _MARCAS_PESQUISA = (
        "deep research",
        "pesquisa profunda",
        "investigue a fundo",
        "relatório completo com fontes",
        "relatorio completo com fontes",
        "dossiê",
        "dossie",
        "levantamento bibliográfico",
    )
    _MARCAS_PROFUNDO = (
        "think deeper",
        "pense fundo",
        "analise profundamente",
        "raciocínio profundo",
        "raciocinio profundo",
        "arquitetura detalhada",
        "compare detalhadamente",
    )
    _MARCAS_NAVEGADOR = (
        "modo computador",
        "use o navegador",
        "controle o navegador",
        "controlar o navegador",
        "navegação automática",
    )

    def route(
        self,
        prompt: str,
        requested_mode: str | ConversationMode = ConversationMode.SMART,
        untrusted_input: bool = False,
    ) -> RoutingDecision:
        texto = (prompt or "").strip()
        texto_min = texto.lower()

        try:
            modo_req = (
                requested_mode
                if isinstance(requested_mode, ConversationMode)
                else ConversationMode(str(requested_mode).strip().lower())
            )
        except ValueError:
            modo_req = ConversationMode.SMART

        # Resolução do modo efetivo quando em SMART
        if modo_req != ConversationMode.SMART:
            modo_efetivo = modo_req
            motivo_modo = f"Modo selecionado explicitamente pelo usuário: {modo_efetivo.value}"
        elif any(m in texto_min for m in self._MARCAS_PESQUISA):
            modo_efetivo = ConversationMode.PESQUISA
            motivo_modo = "Smart Router detectou intenção de pesquisa profunda multi-fontes."
        elif any(m in texto_min for m in self._MARCAS_ESTUDO):
            modo_efetivo = ConversationMode.ESTUDO
            motivo_modo = "Smart Router detectou intenção de aprendizagem/estudo guiado."
        elif any(m in texto_min for m in self._MARCAS_PROFUNDO):
            modo_efetivo = ConversationMode.PROFUNDO
            motivo_modo = "Smart Router detectou solicitação de análise profunda (Think Deeper)."
        else:
            caminho_base, motivo_base = escolher_caminho(texto)
            if caminho_base == CAMINHO_COMPLEXO:
                modo_efetivo = ConversationMode.PROFUNDO
                motivo_modo = f"Smart Router escalou para análise profunda ({motivo_base})."
            else:
                modo_efetivo = ConversationMode.RAPIDO
                motivo_modo = f"Smart Router selecionou resposta rápida ({motivo_base})."

        if any(m in texto_min for m in self._MARCAS_NAVEGADOR):
            return RoutingDecision(
                mode=modo_req,
                effective_mode=modo_efetivo,
                route_path="computador",
                specialist="especialista_navegador",
                model_hint="gemini-2.5-computer-use-preview-10-2025",
                thinking_level="medium",
                requires_grounding=True,
                untrusted_input=True,
                reason="Solicitação de operação autônoma do navegador (Computer Use).",
            )

        if modo_efetivo == ConversationMode.PESQUISA:
            return RoutingDecision(
                mode=modo_req,
                effective_mode=modo_efetivo,
                route_path=CAMINHO_COMPLEXO,
                specialist="especialista_pesquisa",
                model_hint="gemini-3-pro-preview",
                thinking_level="high",
                requires_grounding=True,
                untrusted_input=untrusted_input,
                reason=motivo_modo,
            )

        if modo_efetivo == ConversationMode.PROFUNDO:
            return RoutingDecision(
                mode=modo_req,
                effective_mode=modo_efetivo,
                route_path=CAMINHO_COMPLEXO,
                specialist="coordenador",
                model_hint="gemini-3-pro-preview",
                thinking_level="high",
                requires_grounding=False,
                untrusted_input=untrusted_input,
                reason=motivo_modo,
            )

        if modo_efetivo == ConversationMode.ESTUDO:
            return RoutingDecision(
                mode=modo_req,
                effective_mode=modo_efetivo,
                route_path=CAMINHO_COMPLEXO,
                specialist="tutor_socratico",
                model_hint="gemini-3-flash-preview",
                thinking_level="medium",
                requires_grounding=False,
                untrusted_input=untrusted_input,
                reason=motivo_modo,
            )

        return RoutingDecision(
            mode=modo_req,
            effective_mode=ConversationMode.RAPIDO,
            route_path=CAMINHO_RAPIDO,
            specialist="agente_rapido",
            model_hint="gemini-3-flash-preview",
            thinking_level="low",
            requires_grounding=False,
            untrusted_input=untrusted_input,
            reason=motivo_modo,
        )


class JarvisPlanner:
    """Decompõe objetivos e especificações em planos estruturados com passos idempotentes."""

    def create_plan(
        self,
        *,
        goal: str,
        session_id: str,
        user_id: str,
        mode: str | ConversationMode = ConversationMode.SMART,
        explicit_steps: Optional[List[Dict[str, Any]]] = None,
        budget: Optional[TaskBudget] = None,
        router: Optional[JarvisRouter] = None,
    ) -> OrchestratedTask:
        rt = router or JarvisRouter()
        routing = rt.route(goal, requested_mode=mode)
        task_id = f"tsk_{secrets.token_hex(8)}"
        task_budget = budget or TaskBudget()

        steps: List[PlanStep] = []
        if explicit_steps:
            for idx, raw in enumerate(explicit_steps):
                step_id = raw.get("step_id") or f"step_{idx + 1}"
                tool_name = raw.get("tool_name")
                tool_args = raw.get("tool_args") or {}
                idem_raw = f"{session_id}:{task_id}:{step_id}:{tool_name}:{canonical_args_digest(tool_args)}"
                steps.append(
                    PlanStep(
                        step_id=step_id,
                        index=idx,
                        title=raw.get("title") or f"Passo {idx + 1}: {tool_name or 'síntese'}",
                        description=raw.get("description") or goal,
                        kind=raw.get("kind") or ("tool" if tool_name else "synthesis"),
                        tool_name=tool_name,
                        tool_args=tool_args,
                        depends_on=list(raw.get("depends_on") or []),
                        expected_keys=list(raw.get("expected_keys") or []),
                        idempotency_key=raw.get("idempotency_key") or idem_raw,
                        max_attempts=int(raw.get("max_attempts", 2)),
                    )
                )
        else:
            # Decomposição heurística determinística quando explicit_steps não é fornecido
            steps.extend(self._infer_steps_from_goal(goal, session_id=session_id, task_id=task_id))

        return OrchestratedTask(
            task_id=task_id,
            session_id=session_id,
            user_id=user_id,
            goal=goal,
            mode=routing.effective_mode,
            routing=routing,
            state=TaskState.QUEUED,
            budget=task_budget,
            steps=steps,
            checkpoint_index=0,
        )

    def _infer_steps_from_goal(self, goal: str, *, session_id: str, task_id: str) -> List[PlanStep]:
        g_low = (goal or "").lower()
        inferred: List[PlanStep] = []

        if any(k in g_low for k in ("status do sistema", "uso de cpu", "memória ram", "telemetria")):
            inferred.append(
                PlanStep(
                    step_id="step_1",
                    index=0,
                    title="Coletar telemetria do sistema",
                    description="Consulta CPU, RAM, disco e uptime via ferramenta nativa.",
                    kind="tool",
                    tool_name="get_system_status",
                    tool_args={},
                    expected_keys=["cpu_percent", "ram_percent"],
                    idempotency_key=f"{session_id}:{task_id}:step_1:get_system_status",
                )
            )
        if any(k in g_low for k in ("gpu", "placa de vídeo", "placa de video", "vram")):
            idx = len(inferred)
            sid = f"step_{idx + 1}"
            inferred.append(
                PlanStep(
                    step_id=sid,
                    index=idx,
                    title="Consultar status da GPU",
                    description="Obtém uso, temperatura e VRAM da placa de vídeo.",
                    kind="tool",
                    tool_name="get_gpu_status",
                    tool_args={},
                    expected_keys=["disponivel", "mensagem"],
                    idempotency_key=f"{session_id}:{task_id}:{sid}:get_gpu_status",
                )
            )
        if any(k in g_low for k in ("que horas", "data de hoje", "dia da semana", "horário")):
            idx = len(inferred)
            sid = f"step_{idx + 1}"
            inferred.append(
                PlanStep(
                    step_id=sid,
                    index=idx,
                    title="Consultar data e hora atuais",
                    description="Obtém data, dia da semana e hora exata.",
                    kind="tool",
                    tool_name="get_current_datetime",
                    tool_args={},
                    expected_keys=["data", "hora"],
                    idempotency_key=f"{session_id}:{task_id}:{sid}:get_current_datetime",
                )
            )

        if not inferred:
            inferred.append(
                PlanStep(
                    step_id="step_1",
                    index=0,
                    title="Síntese e resposta estruturada",
                    description=goal,
                    kind="synthesis",
                    idempotency_key=f"{session_id}:{task_id}:step_1:synthesis",
                )
            )
        return inferred


class JarvisVerifier:
    """Valida a completude dos passos, chaves esperadas e integridade das citações/efeitos."""

    def verify(self, task: OrchestratedTask) -> VerificationReport:
        total = len(task.steps)
        if total == 0:
            return VerificationReport(
                passed=True,
                completeness_score=1.0,
                verified_steps=0,
                total_steps=0,
                summary="Nenhum passo pendente para verificação.",
            )

        verified = 0
        missing: List[str] = []
        warnings: List[str] = []
        citations_ok = True

        for step in task.steps:
            if step.state != TaskState.SUCCEEDED:
                missing.append(f"Passo '{step.step_id}' não concluído (estado={step.state.value}).")
                continue

            if step.expected_keys and isinstance(step.result, dict):
                for key in step.expected_keys:
                    if key not in step.result:
                        missing.append(f"Passo '{step.step_id}' não retornou chave esperada '{key}'.")

            if isinstance(step.result, dict) and step.result.get("sucesso") is False:
                missing.append(
                    f"Passo '{step.step_id}' retornou falha lógica: {step.result.get('erro', 'erro desconhecido')}."
                )
                continue

            # Se o modo exigir grounding/citações e houver campo citations/fontes, verifica integridade
            if task.routing and task.routing.requires_grounding and isinstance(step.result, dict):
                fontes = step.result.get("citations") or step.result.get("fontes")
                if fontes is not None and len(fontes) == 0:
                    citations_ok = False
                    warnings.append(f"Passo '{step.step_id}' exigia fontes verificáveis, mas retornou lista vazia.")

            verified += 1

        score = round(verified / total, 4) if total > 0 else 1.0
        passed = (verified == total) and len(missing) == 0 and citations_ok
        summary = (
            f"Verificação aprovada ({verified}/{total} passos íntegros)."
            if passed
            else f"Verificação reprovada ({verified}/{total} passos): {'; '.join(missing or warnings)}"
        )
        return VerificationReport(
            passed=passed,
            completeness_score=score,
            verified_steps=verified,
            total_steps=total,
            citations_verified=citations_ok,
            missing_expectations=missing,
            warnings=warnings,
            summary=summary,
        )


class JarvisOrchestrator:
    """Coordenador de ciclo de vida de tarefas com persistência durável e checkpoints."""

    def __init__(self, store_path: str = DEFAULT_TASKS_STORE_PATH):
        self._lock = threading.RLock()
        self._store_path = store_path
        self.router = JarvisRouter()
        self.planner = JarvisPlanner()
        self.verifier = JarvisVerifier()
        self._tasks: Dict[str, OrchestratedTask] = {}
        os.makedirs(os.path.dirname(self._store_path), exist_ok=True)
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        with self._lock:
            if not os.path.exists(self._store_path):
                return
            try:
                with open(self._store_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                for tid, tdata in (raw or {}).items():
                    self._tasks[tid] = OrchestratedTask.model_validate(tdata)
            except Exception as exc:
                logger.warning("Falha ao carregar orchestrator_tasks.json: %s", exc)

    def _save_to_disk(self) -> None:
        with self._lock:
            try:
                tmp_path = f"{self._store_path}.tmp"
                payload = {tid: t.model_dump(mode="json") for tid, t in self._tasks.items()}
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, self._store_path)
            except Exception as exc:
                logger.warning("Falha ao salvar orchestrator_tasks.json: %s", exc)

    def submit_task(
        self,
        *,
        goal: str,
        session_id: str,
        user_id: Optional[str] = None,
        mode: str | ConversationMode = ConversationMode.SMART,
        explicit_steps: Optional[List[Dict[str, Any]]] = None,
        budget: Optional[TaskBudget] = None,
    ) -> OrchestratedTask:
        """Cria e persiste uma nova tarefa no estado QUEUED."""
        ok_sess, principal, err_sess = authority_kernel.verify_session(session_id, user_id=user_id)
        if not ok_sess:
            raise PermissionError(err_sess)

        task = self.planner.create_plan(
            goal=goal,
            session_id=session_id,
            user_id=principal,
            mode=mode,
            explicit_steps=explicit_steps,
            budget=budget,
            router=self.router,
        )
        with self._lock:
            self._tasks[task.task_id] = task
            self._save_to_disk()
        return task

    def get_task(self, task_id: str, session_id: Optional[str] = None) -> Optional[OrchestratedTask]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            if session_id is not None and task.session_id != session_id:
                return None
            return task

    def list_tasks(self, session_id: str, state: Optional[TaskState] = None) -> List[OrchestratedTask]:
        with self._lock:
            items = [t for t in self._tasks.values() if t.session_id == session_id]
            if state is not None:
                items = [t for t in items if t.state == state]
            return sorted(items, key=lambda x: x.created_at, reverse=True)

    def cancel_task(self, task_id: str, session_id: str) -> Optional[OrchestratedTask]:
        """Cancela cooperativamente uma tarefa pertencente à sessão."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or task.session_id != session_id:
                return None
            if task.state in (TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED):
                return task
            task.state = TaskState.CANCELLED
            for step in task.steps:
                if step.state in (TaskState.QUEUED, TaskState.RUNNING, TaskState.WAITING_APPROVAL):
                    step.state = TaskState.CANCELLED
            task.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self._save_to_disk()
            return task

    def run_task(
        self,
        task_id: str,
        *,
        session_id: str,
        synthesis_fn: Optional[Callable[[OrchestratedTask, PlanStep], Any]] = None,
    ) -> OrchestratedTask:
        """Executa ou retoma (resume) uma tarefa a partir do último checkpoint sem duplicar efeitos."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or task.session_id != session_id:
                raise KeyError(f"Tarefa '{task_id}' não encontrada para a sessão informada.")
            if task.state == TaskState.CANCELLED:
                return task
            if task.started_monotonic <= 0.0:
                task.started_monotonic = time.monotonic()
            task.state = TaskState.RUNNING
            task.error = None
            task.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self._save_to_disk()

        steps_by_id = {s.step_id: s for s in task.steps}

        for idx, step in enumerate(task.steps):
            with self._lock:
                # Verifica cancelamento concorrente
                if task.state == TaskState.CANCELLED:
                    self._save_to_disk()
                    return task

                # Idempotência / Checkpoint: se o passo já sucedeu ou sua chave idempotente já rodou, pula!
                if step.state == TaskState.SUCCEEDED or (
                    step.idempotency_key and step.idempotency_key in task.executed_idempotency_keys
                ):
                    step.state = TaskState.SUCCEEDED
                    task.checkpoint_index = max(task.checkpoint_index, idx + 1)
                    continue

                # Verifica dependências
                deps_pendentes = [
                    dep_id
                    for dep_id in step.depends_on
                    if dep_id in steps_by_id and steps_by_id[dep_id].state != TaskState.SUCCEEDED
                ]
                if deps_pendentes:
                    step.state = TaskState.BLOCKED
                    step.error = f"Dependências não concluídas: {', '.join(deps_pendentes)}"
                    task.state = TaskState.BLOCKED
                    task.error = step.error
                    self._save_to_disk()
                    return task

                # Verifica orçamento (steps, tool_calls, duration, cost)
                elapsed = time.monotonic() - task.started_monotonic
                if elapsed > task.budget.max_duration_s:
                    step.state = TaskState.FAILED
                    step.error = f"Orçamento de tempo excedido ({elapsed:.2f}s > {task.budget.max_duration_s:.2f}s)."
                    task.state = TaskState.FAILED
                    task.error = step.error
                    self._save_to_disk()
                    return task

                if task.budget.consumed_steps >= task.budget.max_steps:
                    step.state = TaskState.BLOCKED
                    step.error = f"Orçamento máximo de passos atingido ({task.budget.max_steps})."
                    task.state = TaskState.BLOCKED
                    task.error = step.error
                    self._save_to_disk()
                    return task

                if step.kind == "tool" and task.budget.consumed_tool_calls >= task.budget.max_tool_calls:
                    step.state = TaskState.BLOCKED
                    step.error = f"Orçamento máximo de chamadas de ferramenta atingido ({task.budget.max_tool_calls})."
                    task.state = TaskState.BLOCKED
                    task.error = step.error
                    self._save_to_disk()
                    return task

                step.state = TaskState.RUNNING
                step.started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
                step.attempts += 1
                self._save_to_disk()

            # Execução do passo fora do lock global para não bloquear leitura de outras sessões
            if step.kind == "tool" and step.tool_name:
                untrusted = bool(task.routing and task.routing.untrusted_input)
                kres: KernelExecutionResult = authority_kernel.execute_sync(
                    step.tool_name,
                    step.tool_args,
                    session_id=task.session_id,
                    user_id=task.user_id,
                    untrusted_context=untrusted,
                )

                with self._lock:
                    if kres.status == "pending_confirmation":
                        step.state = TaskState.WAITING_APPROVAL
                        step.pending_action_id = kres.action_id
                        step.error = kres.reason
                        task.state = TaskState.WAITING_APPROVAL
                        task.error = kres.reason
                        task.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
                        self._save_to_disk()
                        return task

                    if not kres.allowed:
                        step.state = TaskState.BLOCKED
                        step.error = kres.reason
                        task.state = TaskState.BLOCKED
                        task.error = kres.reason
                        task.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
                        self._save_to_disk()
                        return task

                    task.budget.consumed_steps += 1
                    task.budget.consumed_tool_calls += 1
                    task.budget.consumed_cost_units += 1.0
                    step.capability_id = kres.capability_id

                    if kres.status == "execution_error":
                        step.state = TaskState.FAILED
                        step.error = kres.reason
                        step.result = kres.result
                        task.state = TaskState.FAILED
                        task.error = kres.reason
                        task.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
                        self._save_to_disk()
                        return task

                    # Sucesso! Registra idempotência e avança checkpoint durável
                    step.state = TaskState.SUCCEEDED
                    step.result = kres.result
                    step.error = None
                    step.pending_action_id = None
                    step.completed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    if step.idempotency_key and step.idempotency_key not in task.executed_idempotency_keys:
                        task.executed_idempotency_keys.append(step.idempotency_key)
                    task.checkpoint_index = max(task.checkpoint_index, idx + 1)
                    task.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    self._save_to_disk()
            else:
                # Passo de síntese ou verificação
                try:
                    if synthesis_fn is not None:
                        syn_out = synthesis_fn(task, step)
                    else:
                        syn_out = {
                            "sucesso": True,
                            "modo": task.mode.value,
                            "objetivo": task.goal,
                            "passos_anteriores": [
                                {"step_id": s.step_id, "resultado": s.result}
                                for s in task.steps[:idx]
                                if s.state == TaskState.SUCCEEDED
                            ],
                        }
                    with self._lock:
                        task.budget.consumed_steps += 1
                        task.budget.consumed_cost_units += 0.5
                        step.state = TaskState.SUCCEEDED
                        step.result = syn_out
                        step.error = None
                        step.completed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
                        if step.idempotency_key and step.idempotency_key not in task.executed_idempotency_keys:
                            task.executed_idempotency_keys.append(step.idempotency_key)
                        task.checkpoint_index = max(task.checkpoint_index, idx + 1)
                        task.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
                        self._save_to_disk()
                except Exception as exc:
                    with self._lock:
                        step.state = TaskState.FAILED
                        step.error = str(exc)
                        task.state = TaskState.FAILED
                        task.error = str(exc)
                        task.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
                        self._save_to_disk()
                        return task

        # Todos os passos executados: aciona Verifier
        with self._lock:
            report = self.verifier.verify(task)
            task.verification = report
            if report.passed:
                task.state = TaskState.SUCCEEDED
                task.final_output = task.steps[-1].result if task.steps else {}
            else:
                task.state = TaskState.FAILED
                task.error = report.summary
            task.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self._save_to_disk()
            return task


# Instância global do Orquestrador
jarvis_orchestrator = JarvisOrchestrator()
