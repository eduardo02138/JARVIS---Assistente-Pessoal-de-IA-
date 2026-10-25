"""
F6 — Navegador Autônomo Seguro (Copilot Actions / Computer Use) e Journeys

Implementa:
1. BrowserPolicyGuard:
   - Política de domínios permitidos/bloqueados e proteção anti-SSRF;
   - Separação estrita entre ações de leitura/navegação e ações sensíveis
     (submit de formulário, login, compra/checkout, download, publicação);
   - Pausa obrigatória para confirmação humana (`WAITING_APPROVAL`) em ações sensíveis;
   - Sanitização e detecção de Prompt Injection indireto em DOM/texto de página web
     (`untrusted_context=True`), impedindo que instruções na página disparem ferramentas.
2. JourneysManager:
   - Agrupamento temático de páginas, buscas e notas por tarefa;
   - Estritamente opt-in por usuário (`enabled=False` por padrão);
   - Retenção configurável (padrão 14 dias), busca, retomada e exclusão granular/total.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from authority_kernel import authority_kernel
from policy_engine import policy_engine
from rede_segura import motivo_de_bloqueio_da_url

DEFAULT_JOURNEYS_PATH = Path(
    os.environ.get(
        "JARVIS_JOURNEYS_PATH",
        str(Path(__file__).resolve().parent / "data" / "copilot_journeys.json"),
    )
)

SENSITIVE_ACTION_KEYWORDS = (
    "submit",
    "enviar",
    "checkout",
    "compra",
    "comprar",
    "buy",
    "purchase",
    "pay",
    "pagar",
    "pagamento",
    "pagamento_cartao",
    "confirm_order",
    "finalizar_compra",
    "finalizar pedido",
    "login",
    "signin",
    "sign_in",
    "senha",
    "password",
    "credit_card",
    "cartao",
    "download",
    "baixar",
    "publish",
    "publicar",
    "delete_account",
)

PROMPT_INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?(previous|prior)\s+instructions", re.IGNORECASE),
    re.compile(r"ignore\s+(todas\s+as\s+)?instru[cç][oõ]es\s+anteriores", re.IGNORECASE),
    re.compile(r"system\s*prompt\s*:", re.IGNORECASE),
    re.compile(r"execute\s+(bash|shell|command|tool)", re.IGNORECASE),
    re.compile(r"run_shell_command|set_control_mode|mouse_click", re.IGNORECASE),
    re.compile(r"<\s*script[^>]*>", re.IGNORECASE),
    re.compile(r"você\s+agora\s+é\s+um\s+agente\s+sem\s+restri", re.IGNORECASE),
    re.compile(r"aprove\s+automaticamente|auto-approve|bypass\s+policy", re.IGNORECASE),
)


class BrowserActionEvaluation(BaseModel):
    allowed: bool
    requires_human_confirmation: bool = False
    category: Literal["READ_NAVIGATION", "SENSITIVE_MUTATION", "BLOCKED_DOMAIN", "BLOCKED_INJECTION"]
    reason: str
    confirmation_id: Optional[str] = None
    untrusted_context: bool = False


class BrowserPolicyGuard:
    """
    Guarda de governança para navegação autônoma (Playwright / Computer Use).
    Garante que o navegador opere em modo leitura por padrão e pause em qualquer
    ação sensível (submit, login, compra, download, publicação) ou bloqueie
    prompt injection vindo do DOM da página.
    """

    def __init__(
        self,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None,
    ) -> None:
        self._lock = threading.RLock()
        self.allowed_domains: List[str] = [d.lower().strip() for d in (allowed_domains or []) if d.strip()]
        default_blocked = [
            "malware.test",
            "phishing.test",
            "evil.example.com",
            "internal.corp",
        ]
        self.blocked_domains: List[str] = list(
            set(default_blocked + [d.lower().strip() for d in (blocked_domains or []) if d.strip()])
        )

    def check_url_policy(self, url: str) -> Tuple[bool, str]:
        raw = (url or "").strip()
        if not raw:
            return False, "URL vazia."
        if "://" not in raw:
            raw = f"https://{raw}"
        motivo_net = motivo_de_bloqueio_da_url(raw)
        if motivo_net:
            return False, f"URL bloqueada pela proteção de rede/SSRF: {motivo_net}"

        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower().strip(".")
        if not host:
            return False, "Hostname inválido na URL."

        with self._lock:
            for b in self.blocked_domains:
                if host == b or host.endswith(f".{b}"):
                    return False, f"Domínio '{host}' consta na lista de bloqueio do navegador."
            if self.allowed_domains:
                if not any(host == a or host.endswith(f".{a}") for a in self.allowed_domains):
                    return False, f"Domínio '{host}' fora da lista de domínios permitidos."
        return True, "ok"

    @staticmethod
    def sanitize_dom_content(raw_dom_or_text: str) -> Dict[str, Any]:
        """
        Inspeciona e sanitiza o texto/DOM extraído de uma página web.
        Todo conteúdo oriundo de página web é marcado como `untrusted_context=True`.
        """
        text = raw_dom_or_text or ""
        matched_patterns: List[str] = []
        sanitized = text
        for pat in PROMPT_INJECTION_PATTERNS:
            if pat.search(sanitized):
                matched_patterns.append(pat.pattern)
                sanitized = pat.sub("[CONTEUDO_WEB_SUSPEITO_NEUTRALIZADO]", sanitized)

        return {
            "sanitized_text": sanitized,
            "untrusted_context": True,
            "injection_detected": len(matched_patterns) > 0,
            "matched_patterns": matched_patterns,
        }

    def is_sensitive_action(self, action_name: str, args: Dict[str, Any]) -> Tuple[bool, str]:
        act = (action_name or "").strip().lower()
        serialized_args = json.dumps(args or {}, ensure_ascii=False).lower()

        if act in (
            "submit_form",
            "checkout",
            "purchase",
            "login",
            "download_file",
            "publish_post",
            "confirm_payment",
        ):
            return True, f"Ação de navegador '{action_name}' é classificada como mutação sensível."

        # Verifica cliques ou digitação em contextos sensíveis (compra, login, submit, pagamento)
        for kw in SENSITIVE_ACTION_KEYWORDS:
            if kw in act or kw in serialized_args:
                return True, f"Ação envolve operação sensível detectada ('{kw}')."

        # Em type_text_at com press_enter=True, se houver indício de formulário ou comando externo
        if act == "type_text_at" and bool(args.get("press_enter", False)):
            target_label = str(args.get("field_label") or args.get("intent") or "").lower()
            if any(k in target_label for k in ("form", "submit", "login", "compra", "checkout", "pagamento", "senha")):
                return True, f"Submissão de formulário via Enter em campo sensível ('{target_label}')."

        return False, "Ação de leitura/navegação padrão."

    def evaluate_browser_action(
        self,
        session_id: str,
        action_name: str,
        args: Optional[Dict[str, Any]] = None,
        from_page_dom_context: bool = False,
        dom_snippet: Optional[str] = None,
    ) -> BrowserActionEvaluation:
        safe_args = dict(args or {})

        # 1. Verifica se a ação foi induzida por Prompt Injection em página web (untrusted_context)
        if dom_snippet:
            dom_check = self.sanitize_dom_content(dom_snippet)
            if dom_check["injection_detected"]:
                authority_kernel.authorize_tool_call(
                    f"browser.{action_name}",
                    safe_args,
                    session_id=session_id,
                    user_id=session_id,
                    untrusted_context=True,
                )
                return BrowserActionEvaluation(
                    allowed=False,
                    requires_human_confirmation=False,
                    category="BLOCKED_INJECTION",
                    reason="Bloqueado: detectada tentativa de Prompt Injection indireto no DOM da página web.",
                    untrusted_context=True,
                )

        if from_page_dom_context:
            sensitive, _ = self.is_sensitive_action(action_name, safe_args)
            if sensitive or action_name not in ("scroll_document", "scroll_at", "hover_at"):
                return BrowserActionEvaluation(
                    allowed=False,
                    requires_human_confirmation=False,
                    category="BLOCKED_INJECTION",
                    reason="Conteúdo não confiável da página (DOM) não possui autoridade para disparar ações ou ferramentas.",
                    untrusted_context=True,
                )

        # 2. Se houver URL nos argumentos, valida política de domínios e SSRF
        target_url = safe_args.get("url")
        if target_url:
            url_ok, url_reason = self.check_url_policy(str(target_url))
            if not url_ok:
                return BrowserActionEvaluation(
                    allowed=False,
                    requires_human_confirmation=False,
                    category="BLOCKED_DOMAIN",
                    reason=url_reason,
                )

        # 3. Avalia se é uma ação sensível (submit, login, compra, download, publicação)
        is_sens, sens_reason = self.is_sensitive_action(action_name, safe_args)
        if is_sens:
            tool_key = f"browser_{action_name}"
            if policy_engine.consume_authorization(
                tool_key,
                safe_args,
                session_id=session_id,
                user_id=session_id,
            ):
                return BrowserActionEvaluation(
                    allowed=True,
                    requires_human_confirmation=False,
                    category="SENSITIVE_MUTATION",
                    reason="Ação sensível previamente aprovada pelo usuário (one-shot).",
                )

            pending = policy_engine.create_pending_action(
                tool_key,
                safe_args,
                session_id=session_id,
                user_id=session_id,
            )
            return BrowserActionEvaluation(
                allowed=False,
                requires_human_confirmation=True,
                category="SENSITIVE_MUTATION",
                reason=f"{sens_reason} Pausa obrigatória para confirmação humana.",
                confirmation_id=pending.action_id,
            )

        return BrowserActionEvaluation(
            allowed=True,
            requires_human_confirmation=False,
            category="READ_NAVIGATION",
            reason="Navegação/observação permitida.",
        )


class JourneyStep(BaseModel):
    step_id: str = Field(default_factory=lambda: f"jstep_{secrets.token_urlsafe(6)}")
    step_type: Literal["page_visit", "search_query", "note"] = "page_visit"
    title: str
    url: Optional[str] = None
    query_or_note: str = ""
    timestamp: float = Field(default_factory=time.time)


class JourneyRecord(BaseModel):
    journey_id: str = Field(default_factory=lambda: f"jrn_{secrets.token_urlsafe(8)}")
    user_id: str
    topic: str
    summary: str = ""
    status: Literal["ACTIVE", "PAUSED", "ARCHIVED"] = "ACTIVE"
    steps: List[JourneyStep] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    expires_at: float = 0.0


class JourneyUserSettings(BaseModel):
    user_id: str
    enabled: bool = False  # Estritamente opt-in por padrão
    retention_days: int = 14
    updated_at: float = Field(default_factory=time.time)


class JourneysManager:
    """
    Copilot Journeys: organiza navegação, buscas e notas por tarefa temática,
    com opt-in explícito, retenção configurável (14 dias por padrão), busca, retomada e exclusão.
    """

    def __init__(self, storage_path: Optional[Path] = None) -> None:
        self._lock = threading.RLock()
        self.storage_path = storage_path or DEFAULT_JOURNEYS_PATH
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._journeys: Dict[str, JourneyRecord] = {}
        self._settings: Dict[str, JourneyUserSettings] = {}
        self._load()

    def _load(self) -> None:
        with self._lock:
            if not self.storage_path.exists():
                return
            try:
                raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
                for jid, jdata in raw.get("journeys", {}).items():
                    self._journeys[jid] = JourneyRecord.model_validate(jdata)
                for uid, sdata in raw.get("settings", {}).items():
                    self._settings[uid] = JourneyUserSettings.model_validate(sdata)
            except Exception:
                pass

    def _save(self) -> None:
        with self._lock:
            payload = {
                "journeys": {k: v.model_dump(mode="json") for k, v in self._journeys.items()},
                "settings": {k: v.model_dump(mode="json") for k, v in self._settings.items()},
            }
            tmp = self.storage_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.storage_path)

    def get_settings(self, user_id: str) -> JourneyUserSettings:
        with self._lock:
            s = self._settings.get(user_id)
            if s is None:
                s = JourneyUserSettings(user_id=user_id, enabled=False, retention_days=14)
                self._settings[user_id] = s
            return s.model_copy(deep=True)

    def configure_settings(
        self,
        user_id: str,
        enabled: Optional[bool] = None,
        retention_days: Optional[int] = None,
    ) -> JourneyUserSettings:
        with self._lock:
            s = self._settings.get(user_id) or JourneyUserSettings(user_id=user_id)
            if enabled is not None:
                s.enabled = bool(enabled)
            if retention_days is not None:
                s.retention_days = max(1, min(int(retention_days), 365))
            s.updated_at = time.time()
            self._settings[user_id] = s
            self._save()
            return s.model_copy(deep=True)

    def create_journey(
        self,
        user_id: str,
        topic: str,
        summary: str = "",
        now: Optional[float] = None,
    ) -> Optional[JourneyRecord]:
        ts = time.time() if now is None else float(now)
        with self._lock:
            settings = self.get_settings(user_id)
            if not settings.enabled:
                return None
            ttl_sec = settings.retention_days * 86400.0
            jrn = JourneyRecord(
                user_id=user_id,
                topic=topic.strip() or "Jornada sem título",
                summary=summary.strip(),
                created_at=ts,
                updated_at=ts,
                expires_at=ts + ttl_sec,
            )
            self._journeys[jrn.journey_id] = jrn
            self._save()
            return jrn.model_copy(deep=True)

    def add_step(
        self,
        user_id: str,
        journey_id: str,
        title: str,
        step_type: Literal["page_visit", "search_query", "note"] = "page_visit",
        url: Optional[str] = None,
        query_or_note: str = "",
        now: Optional[float] = None,
    ) -> Optional[JourneyRecord]:
        ts = time.time() if now is None else float(now)
        with self._lock:
            settings = self.get_settings(user_id)
            if not settings.enabled:
                return None
            jrn = self._journeys.get(journey_id)
            if not jrn or jrn.user_id != user_id:
                return None
            step = JourneyStep(
                step_type=step_type,
                title=title.strip() or "Passo",
                url=url,
                query_or_note=query_or_note.strip(),
                timestamp=ts,
            )
            jrn.steps.append(step)
            jrn.updated_at = ts
            jrn.expires_at = ts + (settings.retention_days * 86400.0)
            self._save()
            return jrn.model_copy(deep=True)

    def purge_expired(self, now: Optional[float] = None) -> int:
        ts = time.time() if now is None else float(now)
        removed = 0
        with self._lock:
            for jid, jrn in list(self._journeys.items()):
                if jrn.expires_at > 0 and ts >= jrn.expires_at:
                    self._journeys.pop(jid, None)
                    removed += 1
            if removed:
                self._save()
        return removed

    def list_journeys(self, user_id: str, query: str = "", now: Optional[float] = None) -> List[JourneyRecord]:
        self.purge_expired(now=now)
        q = (query or "").strip().lower()
        with self._lock:
            items = [j.model_copy(deep=True) for j in self._journeys.values() if j.user_id == user_id]
        if q:
            filtered = []
            for j in items:
                haystack = f"{j.topic} {j.summary} " + " ".join(
                    f"{s.title} {s.url or ''} {s.query_or_note}" for s in j.steps
                )
                if q in haystack.lower():
                    filtered.append(j)
            items = filtered
        items.sort(key=lambda x: x.updated_at, reverse=True)
        return items

    def resume_journey(self, user_id: str, journey_id: str, now: Optional[float] = None) -> Optional[JourneyRecord]:
        self.purge_expired(now=now)
        ts = time.time() if now is None else float(now)
        with self._lock:
            jrn = self._journeys.get(journey_id)
            if not jrn or jrn.user_id != user_id:
                return None
            jrn.status = "ACTIVE"
            jrn.updated_at = ts
            self._save()
            return jrn.model_copy(deep=True)

    def delete_journey(self, user_id: str, journey_id: str) -> bool:
        with self._lock:
            jrn = self._journeys.get(journey_id)
            if not jrn or jrn.user_id != user_id:
                return False
            self._journeys.pop(journey_id, None)
            self._save()
            return True

    def delete_all_journeys(self, user_id: str) -> int:
        with self._lock:
            to_del = [jid for jid, j in self._journeys.items() if j.user_id == user_id]
            for jid in to_del:
                self._journeys.pop(jid, None)
            if to_del:
                self._save()
            return len(to_del)


browser_policy_guard = BrowserPolicyGuard()
journeys_manager = JourneysManager()
