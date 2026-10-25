"""
AuthorityKernel — Ponto Único Obrigatório de Autorização, Capacidades e Execução do J.A.R.V.I.S.

Arquitetura Inviolável (Fase F1):
    Intent / Proposal -> PolicyEngine -> AuthorityKernel -> Closed Executor Registry -> Effect + Result -> Causal Journal + State

Garantias:
1. Registro fechado de executores (nenhuma chamada arbitrária fora do catálogo registrado).
2. Identidade canônica server-side (sessão emitida/válida, principal vinculado à sessão).
3. Capabilities one-shot vinculadas a (principal, session_id, resource, tool_name, args_digest, expires_at, max_consumptions=1).
4. Consumo atômico protegido por lock reentrante contra replay e condições de corrida concorrentes.
5. Proteção contra ambiguidade: múltiplas ações pendentes na mesma sessão não podem ser aprovadas por um 'sim' genérico sem action_id.
6. Proteção contra prompt injection indireto (contexto não confiável nunca eleva privilégio nem auto-aprova escrita).
7. Journal causal persistente (JSONL + buffer consultável) com marcação de efeito incerto (effect_uncertain) em timeouts/falhas externas.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import inspect
import json
import logging
import os
import secrets
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from policy_engine import PendingAction, PolicyDecision, RiskLevel, policy_engine

logger = logging.getLogger("jarvis.authority_kernel")

JOURNAL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "monitoring",
    "logs",
    "authority_journal.jsonl",
)

# Ferramentas administrativas ou de auto-aprovação que JAMAIS podem entrar no registro de executores
FORBIDDEN_EXECUTOR_NAMES = frozenset({
    "approve_action",
    "approve_latest_pending",
    "consume_authorization",
    "autorizar_acao",
    "grant_control_lease",
    "grant_ide_lease",
    "grant_computer_lease",
    "register_executor",
    "bypass_policy",
})


def canonical_args_digest(args: Optional[Dict[str, Any]]) -> str:
    """Calcula o digest SHA-256 (64 hex) sobre a serialização canônica dos argumentos."""
    payload = args or {}
    try:
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        canonical = str(sorted(payload.items()))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class CapabilityToken:
    """Capacidade efêmera one-shot emitida pelo AuthorityKernel para uma única execução."""

    capability_id: str
    action_id: str
    principal: str
    session_id: str
    resource: str
    tool_name: str
    args_digest: str
    risk_level: str
    issued_at: float
    expires_at: float
    max_consumptions: int = 1
    consumed_count: int = 0
    status: str = "ACTIVE"  # ACTIVE, CONSUMED, EXPIRED, REVOKED


@dataclass
class CausalJournalEntry:
    """Registro causal imutável de intenção, decisão, capacidade e efeito colateral."""

    entry_id: str
    timestamp: str
    monotonic_ts: float
    phase: str
    principal: str
    session_id: str
    tool_name: str
    args_digest: str
    risk_level: str
    outcome: str
    reason: str
    capability_id: Optional[str] = None
    action_id: Optional[str] = None
    effect_uncertain: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class KernelExecutionResult:
    """Resultado padronizado de autorização e/ou execução via AuthorityKernel."""

    allowed: bool
    status: str
    tool_name: str
    session_id: str
    principal: str
    reason: str = ""
    result: Any = None
    capability_id: Optional[str] = None
    action_id: Optional[str] = None
    journal_entry_id: Optional[str] = None
    effect_uncertain: bool = False
    risk_level: str = "PRIVILEGED"


class AuthorityKernel:
    """Kernel central de autoridade, capacidades one-shot e execução auditada."""

    def __init__(self, journal_path: str = JOURNAL_PATH, max_journal_memory: int = 2000):
        self._lock = threading.RLock()
        self._executors: Dict[str, Callable[..., Any]] = {}
        self._executor_sources: Dict[str, str] = {}
        self._capabilities: Dict[str, CapabilityToken] = {}
        self._consumed_action_ids: set[str] = set()
        self._uncertain_effects: Dict[Tuple[str, str, str], CausalJournalEntry] = {}
        self._journal: Deque[CausalJournalEntry] = deque(maxlen=max_journal_memory)
        self._journal_path = journal_path
        os.makedirs(os.path.dirname(self._journal_path), exist_ok=True)

    # ---------------- Registro Fechado de Executores ----------------

    def register_executor(self, tool_name: str, handler: Callable[..., Any], source: str = "system") -> None:
        """Registra um executor verificado no catálogo fechado do Kernel."""
        if not tool_name or not isinstance(tool_name, str):
            raise ValueError("Nome de ferramenta inválido para registro no AuthorityKernel.")
        if tool_name in FORBIDDEN_EXECUTOR_NAMES:
            raise PermissionError(
                f"Proibido registrar ferramenta de auto-autorização no AuthorityKernel: {tool_name}"
            )
        if not callable(handler):
            raise TypeError(f"Executor para '{tool_name}' deve ser invocável (callable).")
        with self._lock:
            self._executors[tool_name] = handler
            self._executor_sources[tool_name] = source

    def sync_executors(self, registry: Dict[str, Callable[..., Any]], source: str = "system_tools") -> None:
        """Sincroniza o catálogo fechado a partir do registro central de ferramentas."""
        with self._lock:
            # Remove executores antigos da mesma fonte que foram desativados (ex: plug-ins desligados)
            atuais_da_fonte = [k for k, src in self._executor_sources.items() if src == source]
            for k in atuais_da_fonte:
                if k not in registry:
                    self._executors.pop(k, None)
                    self._executor_sources.pop(k, None)
            for name, fn in registry.items():
                if name in FORBIDDEN_EXECUTOR_NAMES:
                    continue
                if callable(fn):
                    self._executors[name] = fn
                    self._executor_sources[name] = source

    def has_executor(self, tool_name: str) -> bool:
        with self._lock:
            return tool_name in self._executors

    def get_executor(self, tool_name: str) -> Optional[Callable[..., Any]]:
        with self._lock:
            return self._executors.get(tool_name)

    def list_executors(self) -> List[str]:
        with self._lock:
            return sorted(self._executors.keys())

    # ---------------- Journal Causal ----------------

    def _append_journal(
        self,
        *,
        phase: str,
        principal: str,
        session_id: str,
        tool_name: str,
        args_digest: str,
        risk_level: str,
        outcome: str,
        reason: str,
        capability_id: Optional[str] = None,
        action_id: Optional[str] = None,
        effect_uncertain: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CausalJournalEntry:
        entry = CausalJournalEntry(
            entry_id=f"jrn_{secrets.token_hex(8)}",
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            monotonic_ts=time.monotonic(),
            phase=phase,
            principal=principal or "",
            session_id=session_id or "",
            tool_name=tool_name or "",
            args_digest=args_digest,
            risk_level=risk_level,
            outcome=outcome,
            reason=reason,
            capability_id=capability_id,
            action_id=action_id,
            effect_uncertain=effect_uncertain,
            metadata=metadata or {},
        )
        with self._lock:
            self._journal.append(entry)
            if effect_uncertain and session_id and tool_name:
                self._uncertain_effects[(session_id, tool_name, args_digest)] = entry
            try:
                with open(self._journal_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
            except Exception as exc:
                logger.warning("Falha ao persistir authority_journal.jsonl: %s", exc)
        return entry

    def get_journal(
        self,
        session_id: Optional[str] = None,
        tool_name: Optional[str] = None,
        phase: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._journal)
        filtrados = []
        for item in reversed(items):
            if session_id is not None and item.session_id != session_id:
                continue
            if tool_name is not None and item.tool_name != tool_name:
                continue
            if phase is not None and item.phase != phase:
                continue
            filtrados.append(asdict(item))
            if len(filtrados) >= max(1, limit):
                break
        return list(reversed(filtrados))

    def is_effect_uncertain(self, session_id: str, tool_name: str, args: Optional[Dict[str, Any]] = None) -> bool:
        """Indica se a última execução externa/privilegiada terminou em estado incerto (timeout/rede)."""
        digest = canonical_args_digest(args)
        with self._lock:
            return (session_id, tool_name, digest) in self._uncertain_effects

    def clear_uncertain_effect(self, session_id: str, tool_name: str, args: Optional[Dict[str, Any]] = None) -> None:
        digest = canonical_args_digest(args)
        with self._lock:
            self._uncertain_effects.pop((session_id, tool_name, digest), None)

    # ---------------- Identidade e Sessão ----------------

    def verify_session(
        self,
        session_id: Optional[str],
        user_id: Optional[str] = None,
        require_issued: Optional[bool] = None,
    ) -> Tuple[bool, str, str]:
        """Valida a sessão e retorna (valida, canonical_principal, motivo_erro)."""
        from servidor.seguranca import sessao_aceita

        if not sessao_aceita(session_id, exigir_emissao=require_issued):
            return False, "", "Sessão inválida, não emitida, revogada ou expirada."
        # Se user_id explícito foi fornecido, mantém se compatível; nas rotas web o principal é a sessão
        principal = user_id if (user_id and user_id not in ("default", "anonimo", "anonymous")) else str(session_id)
        return True, principal, ""

    # ---------------- Capabilities One-Shot ----------------

    def issue_capability(
        self,
        *,
        tool_name: str,
        args: Optional[Dict[str, Any]],
        session_id: str,
        principal: str,
        risk_level: str,
        action_id: Optional[str] = None,
        ttl_s: float = 60.0,
    ) -> CapabilityToken:
        """Emite uma CapabilityToken one-shot estritamente vinculada a principal, sessão, ferramenta e args."""
        now = time.monotonic()
        digest = canonical_args_digest(args)
        cap_id = f"cap_{secrets.token_hex(10)}"
        act_id = action_id or f"auto_{secrets.token_hex(6)}"
        token = CapabilityToken(
            capability_id=cap_id,
            action_id=act_id,
            principal=principal,
            session_id=session_id,
            resource=f"tool:{tool_name}",
            tool_name=tool_name,
            args_digest=digest,
            risk_level=risk_level,
            issued_at=now,
            expires_at=now + ttl_s,
            max_consumptions=1,
            consumed_count=0,
            status="ACTIVE",
        )
        with self._lock:
            self._capabilities[cap_id] = token
        self._append_journal(
            phase="CAPABILITY_ISSUED",
            principal=principal,
            session_id=session_id,
            tool_name=tool_name,
            args_digest=digest,
            risk_level=risk_level,
            outcome="ISSUED",
            reason="Capability one-shot emitida pelo AuthorityKernel.",
            capability_id=cap_id,
            action_id=act_id,
        )
        return token

    def consume_capability(
        self,
        capability_id: str,
        *,
        tool_name: str,
        args: Optional[Dict[str, Any]],
        session_id: str,
        principal: str,
    ) -> Tuple[bool, str]:
        """Consome atomicamente uma capability one-shot, prevenindo replay e corrida."""
        digest = canonical_args_digest(args)
        with self._lock:
            cap = self._capabilities.get(capability_id)
            if not cap:
                self._append_journal(
                    phase="REPLAY_BLOCKED",
                    principal=principal,
                    session_id=session_id,
                    tool_name=tool_name,
                    args_digest=digest,
                    risk_level="PRIVILEGED",
                    outcome="DENIED",
                    reason="Capability inexistente.",
                    capability_id=capability_id,
                )
                return False, "Capability inexistente."

            now = time.monotonic()
            if cap.status == "CONSUMED" or cap.consumed_count >= cap.max_consumptions:
                self._append_journal(
                    phase="REPLAY_BLOCKED",
                    principal=principal,
                    session_id=session_id,
                    tool_name=tool_name,
                    args_digest=digest,
                    risk_level=cap.risk_level,
                    outcome="DENIED",
                    reason="Replay bloqueado: capability one-shot já consumida.",
                    capability_id=capability_id,
                    action_id=cap.action_id,
                )
                return False, "Replay bloqueado: capability já consumida."

            if now > cap.expires_at or cap.status in ("EXPIRED", "REVOKED"):
                cap.status = "EXPIRED"
                self._append_journal(
                    phase="REPLAY_BLOCKED",
                    principal=principal,
                    session_id=session_id,
                    tool_name=tool_name,
                    args_digest=digest,
                    risk_level=cap.risk_level,
                    outcome="DENIED",
                    reason="Capability expirada ou revogada.",
                    capability_id=capability_id,
                    action_id=cap.action_id,
                )
                return False, "Capability expirada ou revogada."

            if (
                cap.session_id != session_id
                or cap.principal != principal
                or cap.tool_name != tool_name
                or cap.args_digest != digest
            ):
                self._append_journal(
                    phase="REPLAY_BLOCKED",
                    principal=principal,
                    session_id=session_id,
                    tool_name=tool_name,
                    args_digest=digest,
                    risk_level=cap.risk_level,
                    outcome="DENIED",
                    reason="Violação de vínculo da capability (sessão, principal, ferramenta ou argumentos divergentes).",
                    capability_id=capability_id,
                    action_id=cap.action_id,
                )
                return False, "Violação de vínculo da capability."

            cap.consumed_count += 1
            cap.status = "CONSUMED"
            if cap.action_id:
                self._consumed_action_ids.add(cap.action_id)

        self._append_journal(
            phase="CAPABILITY_CONSUMED",
            principal=principal,
            session_id=session_id,
            tool_name=tool_name,
            args_digest=digest,
            risk_level=cap.risk_level,
            outcome="CONSUMED",
            reason="Capability one-shot consumida atomicamente.",
            capability_id=capability_id,
            action_id=cap.action_id,
        )
        return True, "OK"

    # ---------------- Autorização / Gate para ADK e Superfícies ----------------

    def authorize_tool_call(
        self,
        tool_name: str,
        args: Optional[Dict[str, Any]],
        *,
        session_id: Optional[str],
        user_id: Optional[str] = None,
        untrusted_context: bool = False,
        require_issued_session: Optional[bool] = None,
    ) -> KernelExecutionResult:
        """Avalia política, contexto não confiável e consome autorização one-shot quando aplicável."""
        args_dict = args or {}
        digest = canonical_args_digest(args_dict)
        eff_session = session_id or ""
        eff_principal = user_id or eff_session

        # 1. Registra intenção no journal causal
        self._append_journal(
            phase="INTENT",
            principal=eff_principal,
            session_id=eff_session,
            tool_name=tool_name,
            args_digest=digest,
            risk_level=str(policy_engine.get_risk_level(tool_name) or "PRIVILEGED"),
            outcome="PROPOSED",
            reason="Proposta de execução recebida pelo AuthorityKernel.",
            metadata={"untrusted_context": untrusted_context},
        )

        # 2. Verifica se efeito anterior idêntico ficou incerto (proteção contra retry cego)
        if eff_session and self.is_effect_uncertain(eff_session, tool_name, args_dict):
            entry = self._append_journal(
                phase="EXECUTION_BLOCKED",
                principal=eff_principal,
                session_id=eff_session,
                tool_name=tool_name,
                args_digest=digest,
                risk_level="PRIVILEGED",
                outcome="BLOCKED_UNCERTAIN_EFFECT",
                reason="Execução anterior terminou com efeito incerto (timeout/falha). Re-execução automática bloqueada.",
                effect_uncertain=True,
            )
            return KernelExecutionResult(
                allowed=False,
                status="blocked",
                tool_name=tool_name,
                session_id=eff_session,
                principal=eff_principal,
                reason=entry.reason,
                journal_entry_id=entry.entry_id,
                effect_uncertain=True,
            )

        # 3. Avalia no PolicyEngine
        decision: PolicyDecision = policy_engine.evaluate(
            tool_name, args_dict, session_id=session_id, user_id=eff_principal
        )
        risk_str = decision.risk_level.value if isinstance(decision.risk_level, RiskLevel) else str(decision.risk_level)

        # 4. Defesa contra Prompt Injection Indireto (conteúdo web/documento/tela não confiável)
        if untrusted_context and decision.risk_level != RiskLevel.READ:
            if decision.risk_level == RiskLevel.PRIVILEGED:
                entry = self._append_journal(
                    phase="EXECUTION_BLOCKED",
                    principal=eff_principal,
                    session_id=eff_session,
                    tool_name=tool_name,
                    args_digest=digest,
                    risk_level=risk_str,
                    outcome="DENIED_UNTRUSTED_CONTEXT",
                    reason="Bloqueado: contexto externo não confiável jamais pode acionar ferramenta PRIVILEGED.",
                )
                return KernelExecutionResult(
                    allowed=False,
                    status="blocked",
                    tool_name=tool_name,
                    session_id=eff_session,
                    principal=eff_principal,
                    reason=entry.reason,
                    journal_entry_id=entry.entry_id,
                    risk_level=risk_str,
                )
            decision = PolicyDecision(
                tool_name=tool_name,
                risk_level=decision.risk_level,
                allowed=True,
                requires_confirmation=True,
                reason="Contexto externo não confiável detectado: confirmação explícita do usuário obrigatória.",
                metadata=decision.metadata,
            )

        if not decision.allowed:
            entry = self._append_journal(
                phase="POLICY_DECISION",
                principal=eff_principal,
                session_id=eff_session,
                tool_name=tool_name,
                args_digest=digest,
                risk_level=risk_str,
                outcome="DENIED",
                reason=decision.reason,
            )
            return KernelExecutionResult(
                allowed=False,
                status="blocked",
                tool_name=tool_name,
                session_id=eff_session,
                principal=eff_principal,
                reason=decision.reason,
                journal_entry_id=entry.entry_id,
                risk_level=risk_str,
            )

        if decision.requires_confirmation:
            with self._lock:
                # Tenta consumir autorização one-shot previamente aprovada
                consumido = policy_engine.consume_authorization(
                    tool_name, args_dict, session_id=session_id, user_id=eff_principal
                )
                if not consumido:
                    pending = policy_engine.create_pending_action(
                        tool_name, args_dict, session_id=session_id, user_id=eff_principal
                    )
                    entry = self._append_journal(
                        phase="POLICY_DECISION",
                        principal=eff_principal,
                        session_id=eff_session,
                        tool_name=tool_name,
                        args_digest=digest,
                        risk_level=risk_str,
                        outcome="PENDING_CONFIRMATION",
                        reason=decision.reason,
                        action_id=pending.action_id,
                    )
                    return KernelExecutionResult(
                        allowed=False,
                        status="pending_confirmation",
                        tool_name=tool_name,
                        session_id=eff_session,
                        principal=eff_principal,
                        reason=decision.reason,
                        action_id=pending.action_id,
                        journal_entry_id=entry.entry_id,
                        risk_level=risk_str,
                    )

                cap = self.issue_capability(
                    tool_name=tool_name,
                    args=args_dict,
                    session_id=eff_session,
                    principal=eff_principal,
                    risk_level=risk_str,
                )
                ok_cap, cap_msg = self.consume_capability(
                    cap.capability_id,
                    tool_name=tool_name,
                    args=args_dict,
                    session_id=eff_session,
                    principal=eff_principal,
                )
                if not ok_cap:
                    return KernelExecutionResult(
                        allowed=False,
                        status="replay_blocked",
                        tool_name=tool_name,
                        session_id=eff_session,
                        principal=eff_principal,
                        reason=cap_msg,
                        risk_level=risk_str,
                    )
                return KernelExecutionResult(
                    allowed=True,
                    status="authorized",
                    tool_name=tool_name,
                    session_id=eff_session,
                    principal=eff_principal,
                    reason="Autorização one-shot consumida com sucesso.",
                    capability_id=cap.capability_id,
                    action_id=cap.action_id,
                    risk_level=risk_str,
                )

        # Ferramenta permitida sem confirmação (READ / LOW_WRITE com lease válida)
        cap = self.issue_capability(
            tool_name=tool_name,
            args=args_dict,
            session_id=eff_session,
            principal=eff_principal,
            risk_level=risk_str,
        )
        ok_cap, cap_msg = self.consume_capability(
            cap.capability_id,
            tool_name=tool_name,
            args=args_dict,
            session_id=eff_session,
            principal=eff_principal,
        )
        return KernelExecutionResult(
            allowed=ok_cap,
            status="authorized" if ok_cap else "replay_blocked",
            tool_name=tool_name,
            session_id=eff_session,
            principal=eff_principal,
            reason=decision.reason if ok_cap else cap_msg,
            capability_id=cap.capability_id,
            action_id=cap.action_id,
            risk_level=risk_str,
        )

    # ---------------- Execução Sincronizada e Assíncrona via Catálogo Fechado ----------------

    def execute_sync(
        self,
        tool_name: str,
        args: Optional[Dict[str, Any]] = None,
        *,
        session_id: Optional[str],
        user_id: Optional[str] = None,
        untrusted_context: bool = False,
        require_issued_session: Optional[bool] = None,
    ) -> KernelExecutionResult:
        """Executa uma ferramenta síncrona exclusivamente através do catálogo fechado do Kernel."""
        args_dict = args or {}
        digest = canonical_args_digest(args_dict)

        ok_sess, principal, err_sess = self.verify_session(
            session_id, user_id=user_id, require_issued=require_issued_session
        )
        if not ok_sess:
            entry = self._append_journal(
                phase="EXECUTION_BLOCKED",
                principal=user_id or "",
                session_id=session_id or "",
                tool_name=tool_name,
                args_digest=digest,
                risk_level="PRIVILEGED",
                outcome="INVALID_SESSION",
                reason=err_sess,
            )
            return KernelExecutionResult(
                allowed=False,
                status="invalid_session",
                tool_name=tool_name,
                session_id=session_id or "",
                principal=user_id or "",
                reason=err_sess,
                journal_entry_id=entry.entry_id,
            )

        executor = self.get_executor(tool_name)
        if executor is None:
            entry = self._append_journal(
                phase="EXECUTION_BLOCKED",
                principal=principal,
                session_id=str(session_id),
                tool_name=tool_name,
                args_digest=digest,
                risk_level="PRIVILEGED",
                outcome="UNKNOWN_EXECUTOR",
                reason=f"Ferramenta '{tool_name}' não pertence ao registro fechado do AuthorityKernel.",
            )
            return KernelExecutionResult(
                allowed=False,
                status="unknown_executor",
                tool_name=tool_name,
                session_id=str(session_id),
                principal=principal,
                reason=entry.reason,
                journal_entry_id=entry.entry_id,
            )

        auth = self.authorize_tool_call(
            tool_name,
            args_dict,
            session_id=str(session_id),
            user_id=principal,
            untrusted_context=untrusted_context,
            require_issued_session=require_issued_session,
        )
        if not auth.allowed:
            return auth

        self._append_journal(
            phase="EXECUTION_STARTED",
            principal=principal,
            session_id=str(session_id),
            tool_name=tool_name,
            args_digest=digest,
            risk_level=auth.risk_level,
            outcome="RUNNING",
            reason="Iniciando execução no catálogo fechado.",
            capability_id=auth.capability_id,
            action_id=auth.action_id,
        )

        try:
            output = executor(**args_dict)
            entry = self._append_journal(
                phase="EXECUTION_SUCCEEDED",
                principal=principal,
                session_id=str(session_id),
                tool_name=tool_name,
                args_digest=digest,
                risk_level=auth.risk_level,
                outcome="SUCCEEDED",
                reason="Execução concluída com sucesso.",
                capability_id=auth.capability_id,
                action_id=auth.action_id,
            )
            return KernelExecutionResult(
                allowed=True,
                status="executed",
                tool_name=tool_name,
                session_id=str(session_id),
                principal=principal,
                reason="OK",
                result=output,
                capability_id=auth.capability_id,
                action_id=auth.action_id,
                journal_entry_id=entry.entry_id,
                risk_level=auth.risk_level,
            )
        except Exception as exc:
            uncertain = auth.risk_level in ("EXTERNAL_WRITE", "PRIVILEGED")
            entry = self._append_journal(
                phase="EXECUTION_FAILED",
                principal=principal,
                session_id=str(session_id),
                tool_name=tool_name,
                args_digest=digest,
                risk_level=auth.risk_level,
                outcome="FAILED",
                reason=str(exc),
                capability_id=auth.capability_id,
                action_id=auth.action_id,
                effect_uncertain=uncertain,
            )
            return KernelExecutionResult(
                allowed=True,
                status="execution_error",
                tool_name=tool_name,
                session_id=str(session_id),
                principal=principal,
                reason=str(exc),
                result={"sucesso": False, "erro": str(exc)},
                capability_id=auth.capability_id,
                action_id=auth.action_id,
                journal_entry_id=entry.entry_id,
                effect_uncertain=uncertain,
                risk_level=auth.risk_level,
            )

    async def execute_async(
        self,
        tool_name: str,
        args: Optional[Dict[str, Any]] = None,
        *,
        session_id: Optional[str],
        user_id: Optional[str] = None,
        timeout_s: float = 30.0,
        untrusted_context: bool = False,
        require_issued_session: Optional[bool] = None,
        direct_loop_tools: Optional[set[str]] = None,
        thread_semaphore: Optional[threading.Semaphore] = None,
    ) -> KernelExecutionResult:
        """Executa uma ferramenta de forma assíncrona através do catálogo fechado do Kernel."""
        args_dict = args or {}
        digest = canonical_args_digest(args_dict)

        ok_sess, principal, err_sess = self.verify_session(
            session_id, user_id=user_id, require_issued=require_issued_session
        )
        if not ok_sess:
            entry = self._append_journal(
                phase="EXECUTION_BLOCKED",
                principal=user_id or "",
                session_id=session_id or "",
                tool_name=tool_name,
                args_digest=digest,
                risk_level="PRIVILEGED",
                outcome="INVALID_SESSION",
                reason=err_sess,
            )
            return KernelExecutionResult(
                allowed=False,
                status="invalid_session",
                tool_name=tool_name,
                session_id=session_id or "",
                principal=user_id or "",
                reason=err_sess,
                journal_entry_id=entry.entry_id,
            )

        executor = self.get_executor(tool_name)
        if executor is None:
            entry = self._append_journal(
                phase="EXECUTION_BLOCKED",
                principal=principal,
                session_id=str(session_id),
                tool_name=tool_name,
                args_digest=digest,
                risk_level="PRIVILEGED",
                outcome="UNKNOWN_EXECUTOR",
                reason=f"Ferramenta '{tool_name}' desconhecida no registro fechado do AuthorityKernel.",
            )
            return KernelExecutionResult(
                allowed=False,
                status="unknown_executor",
                tool_name=tool_name,
                session_id=str(session_id),
                principal=principal,
                reason=entry.reason,
                result={"sucesso": False, "erro": f"Ferramenta {tool_name} desconhecida."},
                journal_entry_id=entry.entry_id,
            )

        auth = self.authorize_tool_call(
            tool_name,
            args_dict,
            session_id=str(session_id),
            user_id=principal,
            untrusted_context=untrusted_context,
            require_issued_session=require_issued_session,
        )
        if not auth.allowed:
            return auth

        self._append_journal(
            phase="EXECUTION_STARTED",
            principal=principal,
            session_id=str(session_id),
            tool_name=tool_name,
            args_digest=digest,
            risk_level=auth.risk_level,
            outcome="RUNNING",
            reason="Iniciando execução assíncrona no catálogo fechado.",
            capability_id=auth.capability_id,
            action_id=auth.action_id,
        )

        try:
            if inspect.iscoroutinefunction(executor):
                output = await asyncio.wait_for(executor(**args_dict), timeout=timeout_s)
            elif direct_loop_tools and tool_name in direct_loop_tools:
                output = executor(**args_dict)
            elif thread_semaphore is not None:
                with thread_semaphore:
                    output = await asyncio.wait_for(asyncio.to_thread(executor, **args_dict), timeout=timeout_s)
            else:
                output = await asyncio.wait_for(asyncio.to_thread(executor, **args_dict), timeout=timeout_s)

            entry = self._append_journal(
                phase="EXECUTION_SUCCEEDED",
                principal=principal,
                session_id=str(session_id),
                tool_name=tool_name,
                args_digest=digest,
                risk_level=auth.risk_level,
                outcome="SUCCEEDED",
                reason="Execução assíncrona concluída com sucesso.",
                capability_id=auth.capability_id,
                action_id=auth.action_id,
            )
            return KernelExecutionResult(
                allowed=True,
                status="executed",
                tool_name=tool_name,
                session_id=str(session_id),
                principal=principal,
                reason="OK",
                result=output,
                capability_id=auth.capability_id,
                action_id=auth.action_id,
                journal_entry_id=entry.entry_id,
                risk_level=auth.risk_level,
            )
        except asyncio.TimeoutError:
            uncertain = auth.risk_level in ("EXTERNAL_WRITE", "PRIVILEGED")
            msg = f"Timeout ({int(timeout_s)}s) na execução da ferramenta {tool_name}."
            entry = self._append_journal(
                phase="EXECUTION_FAILED",
                principal=principal,
                session_id=str(session_id),
                tool_name=tool_name,
                args_digest=digest,
                risk_level=auth.risk_level,
                outcome="TIMEOUT",
                reason=msg,
                capability_id=auth.capability_id,
                action_id=auth.action_id,
                effect_uncertain=uncertain,
            )
            return KernelExecutionResult(
                allowed=True,
                status="execution_error",
                tool_name=tool_name,
                session_id=str(session_id),
                principal=principal,
                reason=msg,
                result={"sucesso": False, "erro": msg},
                capability_id=auth.capability_id,
                action_id=auth.action_id,
                journal_entry_id=entry.entry_id,
                effect_uncertain=uncertain,
                risk_level=auth.risk_level,
            )
        except Exception as exc:
            uncertain = auth.risk_level in ("EXTERNAL_WRITE", "PRIVILEGED")
            entry = self._append_journal(
                phase="EXECUTION_FAILED",
                principal=principal,
                session_id=str(session_id),
                tool_name=tool_name,
                args_digest=digest,
                risk_level=auth.risk_level,
                outcome="FAILED",
                reason=str(exc),
                capability_id=auth.capability_id,
                action_id=auth.action_id,
                effect_uncertain=uncertain,
            )
            return KernelExecutionResult(
                allowed=True,
                status="execution_error",
                tool_name=tool_name,
                session_id=str(session_id),
                principal=principal,
                reason=str(exc),
                result={"sucesso": False, "erro": str(exc)},
                capability_id=auth.capability_id,
                action_id=auth.action_id,
                journal_entry_id=entry.entry_id,
                effect_uncertain=uncertain,
                risk_level=auth.risk_level,
            )


# Instância global única do AuthorityKernel
authority_kernel = AuthorityKernel()
