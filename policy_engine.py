"""
Motor de Políticas e Controle de Capacidades do J.A.R.V.I.S.
Classifica cada ferramenta em níveis de risco e gerencia autorização prévia à execução.
"""

import logging
import os
import re
import threading
import time
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Any, Optional

logger = logging.getLogger("jarvis.policy_engine")

class RiskLevel(str, Enum):
    READ = "READ"                    # Leitura pura, telemetria, listagens
    LOW_WRITE = "LOW_WRITE"          # Ações locais benignas (volume, abrir app conhecido)
    EXTERNAL_WRITE = "EXTERNAL_WRITE" # Modificações externas ou de rede (postar redes, etc.)
    PRIVILEGED = "PRIVILEGED"        # Execução de código, agente de IDE, scripts de terminal

@dataclass
class PolicyDecision:
    tool_name: str
    risk_level: RiskLevel
    allowed: bool
    requires_confirmation: bool = False
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class PendingAction:
    action_id: str
    tool_name: str
    args: Dict[str, Any]
    args_hash: str
    session_id: Optional[str]
    created_at: float
    expires_at: float
    user_id: Optional[str] = None
    status: str = "pending"  # "pending", "approved", "consumed", "rejected"


# Mapeamento inicial de ferramentas nativas e de plug-ins para seus níveis de risco
TOOL_RISK_MAP: Dict[str, RiskLevel] = {
    # Módulo Google ADK
    "hora_atual": RiskLevel.READ,
    "status_do_sistema": RiskLevel.READ,
    "pesquisar_na_web": RiskLevel.LOW_WRITE,
    "lembrar_preferencia": RiskLevel.LOW_WRITE,
    "consultar_preferencias": RiskLevel.READ,
    "load_memory": RiskLevel.READ,
    "preload_memory": RiskLevel.READ,
    "especialista_sistema": RiskLevel.READ,
    "especialista_navegador": RiskLevel.READ,
    "abrir_site": RiskLevel.EXTERNAL_WRITE,
    # READ: Informação e Consulta
    "get_system_status": RiskLevel.READ,
    "get_gpu_status": RiskLevel.READ,
    "toggle_telemetry_overlay": RiskLevel.READ,
    "get_current_datetime": RiskLevel.READ,
    "list_installed_games": RiskLevel.READ,
    "read_notes": RiskLevel.READ,
    "antigravity_list_mcps": RiskLevel.READ,
    "take_screenshot": RiskLevel.READ,
    "social_feed_check_notifications": RiskLevel.READ,
    "live_stream_read_chat_summary": RiskLevel.READ,
    "game_companion_get_strategy": RiskLevel.READ,
    "smart_home_get_climate": RiskLevel.READ,
    "list_open_windows": RiskLevel.READ,

    # Google Workspace
    "workspace_search_emails": RiskLevel.READ,
    "workspace_create_draft": RiskLevel.EXTERNAL_WRITE,
    "workspace_append_doc": RiskLevel.EXTERNAL_WRITE,
    "workspace_create_keep_note": RiskLevel.EXTERNAL_WRITE,

    # Deep Research
    "deep_research_start": RiskLevel.EXTERNAL_WRITE,
    "deep_research_get_report": RiskLevel.READ,
    "deep_research_list": RiskLevel.READ,

    # Google Finance & Portfolio
    "finance_get_quote": RiskLevel.READ,
    "finance_get_portfolio": RiskLevel.READ,
    "finance_add_asset": RiskLevel.LOW_WRITE,
    "finance_get_insights": RiskLevel.READ,

    # Ginjutsu Video & Motion AI
    "ginjutsu_create_motion_transfer": RiskLevel.EXTERNAL_WRITE,
    "ginjutsu_generate_prompt": RiskLevel.READ,
    "ginjutsu_list_jobs": RiskLevel.READ,

    # Game Companion Expansions
    "game_companion_list_installed_games": RiskLevel.READ,
    "game_companion_launch_game": RiskLevel.LOW_WRITE,


    # LOW_WRITE: Ações locais seguras
    "adjust_volume": RiskLevel.LOW_WRITE,
    "open_application": RiskLevel.LOW_WRITE,
    # Apenas abre o navegador com a URL de busca; não escreve em serviço nenhum
    "search_web": RiskLevel.LOW_WRITE,
    "play_music": RiskLevel.LOW_WRITE,
    "take_quick_note": RiskLevel.LOW_WRITE,
    "open_default_app": RiskLevel.LOW_WRITE,
    "set_game_preference": RiskLevel.LOW_WRITE,
    # Controle físico de mouse e teclado: liberado apenas sob uma lease ativa (ver CONTROL_TOOLS)
    "mouse_move": RiskLevel.LOW_WRITE,
    "mouse_click": RiskLevel.LOW_WRITE,
    "mouse_scroll": RiskLevel.LOW_WRITE,
    "keyboard_type": RiskLevel.LOW_WRITE,
    "keyboard_hotkey": RiskLevel.LOW_WRITE,
    "antigravity_open_workspace": RiskLevel.LOW_WRITE,
    "antigravity_open_file": RiskLevel.LOW_WRITE,
    "antigravity_open_gemini_bridge": RiskLevel.LOW_WRITE,
    "game_companion_set_active_game": RiskLevel.LOW_WRITE,
    "game_companion_tactical_timer": RiskLevel.LOW_WRITE,
    "smart_home_set_light": RiskLevel.LOW_WRITE,
    "smart_home_activate_scene": RiskLevel.LOW_WRITE,
    "live_stream_toggle_status": RiskLevel.LOW_WRITE,

    # EXTERNAL_WRITE: Comunicação com serviços de terceiros e mutação de configuração do sistema
    "open_website": RiskLevel.EXTERNAL_WRITE,
    "manage_user_preference": RiskLevel.EXTERNAL_WRITE,
    "social_feed_post_update": RiskLevel.EXTERNAL_WRITE,
    "live_stream_send_alert": RiskLevel.EXTERNAL_WRITE,


    # PRIVILEGED: Agente autônomo e controle de sistema
    "antigravity_run_prompt": RiskLevel.PRIVILEGED,
    "set_control_mode": RiskLevel.PRIVILEGED,
    "set_ide_mode": RiskLevel.PRIVILEGED,
}

# Ferramentas de controle físico da máquina (mouse e teclado via uinput).
# Só executam enquanto houver uma lease de controle válida, concedida após o usuário
# confirmar set_control_mode.
CONTROL_TOOLS = {
    "mouse_move",
    "mouse_click",
    "mouse_scroll",
    "keyboard_type",
    "keyboard_hotkey",
}

# Duração padrão da lease de controle, em segundos
CONTROL_LEASE_TTL_S = int(os.environ.get("JARVIS_CONTROL_LEASE_TTL", "300"))

# Duração padrão da lease do Modo Computador (navegador via Computer Use)
COMPUTER_LEASE_TTL_S = int(os.environ.get("JARVIS_COMPUTER_LEASE_TTL", "900"))

# Duração padrão da lease do Modo IDE (Antigravity Code Agent)
IDE_LEASE_TTL_S = int(os.environ.get("JARVIS_IDE_LEASE_TTL", "300"))

# Combinações de teclas que continuam exigindo confirmação mesmo com lease ativa
HOTKEYS_PERIGOSAS = {
    ("alt", "f4"),
    ("ctrl", "alt", "delete"),
    ("ctrl", "alt", "backspace"),
    ("ctrl", "alt", "f1"),
    ("ctrl", "alt", "f2"),
    ("ctrl", "alt", "t"),
    ("ctrl", "w"),
    ("ctrl", "q"),
}

COMMANDS_BLOQUEADOS = (
    "rm -rf /",
    "mkfs",
    ":(){ :|:& };:",
    "dd if=",
    "> /dev/sda",
    "chmod -R 777 /",
    "chown -R",
)

# Padrões de texto que indicam comandos destrutivos digitados em terminal
PADROES_TEXTO_PERIGOSO = re.compile(
    r"\b(sudo|rm\s+-[rf]|mkfs|dd\s+if=|shutdown|reboot|poweroff|chmod\s+777|"
    r"curl[^\n|]*\|\s*(ba)?sh|wget[^\n|]*\|\s*(ba)?sh|:\(\)\{)",
    re.IGNORECASE,
)

class PolicyEngine:
    """Gerencia regras de governança e autorização de ferramentas."""

    def __init__(self):
        self._custom_policies: Dict[str, RiskLevel] = {}
        self._control_lease_expira_em: float = 0.0
        self._control_lease_owner: Optional[str] = None
        self._computer_lease_expira_em: float = 0.0
        self._computer_lease_owner: Optional[str] = None
        self._ide_lease_expira_em: float = 0.0
        self._ide_lease_owner: Optional[str] = None
        self._ide_lease_user_id: Optional[str] = None
        self._pending_actions: Dict[str, PendingAction] = {}
        self._lock = threading.RLock()

    def register_tool_policy(self, tool_name: str, risk_level: RiskLevel):
        """Registra ou atualiza o nível de risco de uma ferramenta (ex: via plug-in)."""
        with self._lock:
            self._custom_policies[tool_name] = risk_level

    def get_risk_level(self, tool_name: str) -> Optional[RiskLevel]:
        """Retorna o nível de risco da ferramenta, ou None se ela não tiver política."""
        if tool_name in self._custom_policies:
            return self._custom_policies[tool_name]
        return TOOL_RISK_MAP.get(tool_name)

    # ---------------- Lease de Controle Físico (mouse e teclado) ----------------

    def grant_control_lease(self, owner: str = "hud", ttl_s: int = CONTROL_LEASE_TTL_S) -> Dict[str, Any]:
        """Concede autoridade temporária de controle físico após confirmação do usuário."""
        with self._lock:
            self._control_lease_expira_em = time.monotonic() + ttl_s
            self._control_lease_owner = owner
        logger.info(f"Lease de controle concedida a '{owner}' por {ttl_s}s.")
        return self.control_lease_status()

    def revoke_control_lease(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Revoga a autoridade de controle físico.

        Com session_id, só a sessão dona da lease pode revogá-la: assim uma segunda
        conexão não derruba o Modo Controle de quem realmente recebeu a autoridade.
        """
        with self._lock:
            if session_id is not None and self._control_lease_owner not in (None, session_id):
                logger.info("Revogação ignorada: a lease pertence a outra sessão.")
                return self.control_lease_status()
            self._control_lease_expira_em = 0.0
            self._control_lease_owner = None
        logger.info("Lease de controle revogada.")
        return self.control_lease_status()

    def is_control_lease_active(self, session_id: Optional[str] = None) -> bool:
        """A lease pertence à sessão que a recebeu: outra sessão não herda a autoridade."""
        if time.monotonic() >= self._control_lease_expira_em:
            return False
        if not session_id or self._control_lease_owner != session_id:
            return False
        return True

    def control_lease_status(self) -> Dict[str, Any]:
        """Estado da lease. 'owner' é mantido mesmo após expirar, para a sessão dona
        conseguir identificar que a autoridade dela acabou."""
        restante = max(0.0, self._control_lease_expira_em - time.monotonic())
        return {
            "ativa": restante > 0,
            "segundos_restantes": int(restante),
            "owner": self._control_lease_owner
        }

    # ---------------- Lease do Modo Computador (navegador via Computer Use) ----------------

    def grant_computer_lease(self, owner: str = "sessao-principal", ttl_s: int = COMPUTER_LEASE_TTL_S) -> Dict[str, Any]:
        """Concede autoridade temporária de operação do navegador (Computer Use)."""
        with self._lock:
            self._computer_lease_expira_em = time.monotonic() + ttl_s
            self._computer_lease_owner = owner
        logger.info(f"Lease do Modo Computador concedida a '{owner}' por {ttl_s}s.")
        return self.computer_lease_status()

    def revoke_computer_lease(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Revoga a autoridade do Modo Computador.

        Com session_id, só a sessão dona da lease pode revogá-la, igual ao
        Modo Controle: outra conexão não derruba o navegador de quem opera.
        """
        with self._lock:
            if session_id is not None and self._computer_lease_owner not in (None, session_id):
                logger.info("Revogação ignorada: a lease do computador pertence a outra sessão.")
                return self.computer_lease_status()
            self._computer_lease_expira_em = 0.0
            self._computer_lease_owner = None
        logger.info("Lease do Modo Computador revogada.")
        return self.computer_lease_status()

    def is_computer_lease_active(self, session_id: Optional[str] = None) -> bool:
        """A lease pertence à sessão que a recebeu: outra sessão não navega por ela."""
        if time.monotonic() >= self._computer_lease_expira_em:
            return False
        if not session_id or self._computer_lease_owner != session_id:
            return False
        return True

    def computer_lease_status(self) -> Dict[str, Any]:
        """Estado da lease do Modo Computador."""
        restante = max(0.0, self._computer_lease_expira_em - time.monotonic())
        return {
            "ativa": restante > 0,
            "segundos_restantes": int(restante),
            "owner": self._computer_lease_owner
        }

    # ---------------- Lease do Modo IDE (Antigravity Code Agent) ----------------

    def grant_ide_lease(
        self,
        owner: str = "sessao-principal",
        user_id: Optional[str] = None,
        ttl_s: int = IDE_LEASE_TTL_S
    ) -> Dict[str, Any]:
        """Concede autoridade temporária ao agente Antigravity (Modo IDE)."""
        with self._lock:
            self._ide_lease_expira_em = time.monotonic() + ttl_s
            self._ide_lease_owner = owner
            self._ide_lease_user_id = user_id
        logger.info(f"Lease do Modo IDE concedida a '{owner}' (user: {user_id}) por {ttl_s}s.")
        return self.ide_lease_status()

    def revoke_ide_lease(
        self,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Revoga a autoridade do Modo IDE."""
        if session_id is not None and self._ide_lease_owner not in (None, session_id):
            logger.info("Revogação ignorada: a lease do Modo IDE pertence a outra sessão.")
            return self.ide_lease_status()
        if user_id is not None and self._ide_lease_user_id not in (None, user_id):
            logger.info("Revogação ignorada: a lease do Modo IDE pertence a outro usuário.")
            return self.ide_lease_status()
        with self._lock:
            self._ide_lease_expira_em = 0.0
            self._ide_lease_owner = None
            self._ide_lease_user_id = None
        logger.info("Lease do Modo IDE revogada.")
        return self.ide_lease_status()

    def is_ide_lease_active(
        self,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> bool:
        """A lease pertence estritamente à sessão e usuário que a receberam."""
        if time.monotonic() >= self._ide_lease_expira_em:
            return False
        if self._ide_lease_owner is not None:
            if session_id != self._ide_lease_owner:
                return False
        if self._ide_lease_user_id is not None:
            if user_id != self._ide_lease_user_id:
                return False
        return True

    def ide_lease_status(self) -> Dict[str, Any]:
        """Estado da lease do Modo IDE."""
        restante = max(0.0, self._ide_lease_expira_em - time.monotonic())
        return {
            "ativa": restante > 0,
            "segundos_restantes": int(restante),
            "owner": self._ide_lease_owner,
            "user_id": self._ide_lease_user_id
        }

    def _avaliar_controle_fisico(self, tool_name: str, args: Dict[str, Any],
                                 session_id: Optional[str] = None) -> PolicyDecision:
        """Aplica a lease de controle e escala ações perigosas de teclado."""
        risk = RiskLevel.LOW_WRITE
        if not self.is_control_lease_active(session_id):
            expirada = self._control_lease_expira_em > 0 and time.time() >= self._control_lease_expira_em
            motivo = (
                "Autoridade de controle físico expirada: ative o Modo Controle novamente."
                if expirada else
                "Sem autoridade de controle físico: ative o Modo Controle e confirme para liberar mouse e teclado."
            )
            return PolicyDecision(
                tool_name=tool_name,
                risk_level=risk,
                allowed=False,
                requires_confirmation=False,
                reason=motivo,
                metadata=self.control_lease_status()
            )

        if tool_name == "keyboard_hotkey":
            teclas = tuple(sorted(
                p.strip().lower() for p in str(args.get("keys", "")).split("+") if p.strip()
            ))
            perigosa = any(teclas == tuple(sorted(combo)) for combo in HOTKEYS_PERIGOSAS)
            if perigosa:
                return PolicyDecision(
                    tool_name=tool_name,
                    risk_level=RiskLevel.PRIVILEGED,
                    allowed=True,
                    requires_confirmation=True,
                    reason=f"Atalho sensível ({'+'.join(teclas)}) exige confirmação mesmo com o Modo Controle ativo.",
                    metadata={"keys": args.get("keys")}
                )

        if tool_name == "keyboard_type":
            texto = str(args.get("text", ""))
            if PADROES_TEXTO_PERIGOSO.search(texto):
                return PolicyDecision(
                    tool_name=tool_name,
                    risk_level=RiskLevel.PRIVILEGED,
                    allowed=True,
                    requires_confirmation=True,
                    reason="Texto com comando potencialmente destrutivo: confirmação obrigatória.",
                    metadata={"texto_preview": texto[:120]}
                )

        return PolicyDecision(
            tool_name=tool_name,
            risk_level=risk,
            allowed=True,
            requires_confirmation=False,
            reason="Ação de controle física autorizada pela lease vigente.",
            metadata=self.control_lease_status()
        )

    def evaluate(self, tool_name: str, args: Optional[Dict[str, Any]] = None,
                 session_id: Optional[str] = None,
                 user_id: Optional[str] = None) -> PolicyDecision:
        """
        Avalia se a execução da ferramenta está autorizada e sob quais condições.
        """
        risk = self.get_risk_level(tool_name)
        args = args or {}

        # Fail-closed: ferramenta sem política registrada nunca executa sozinha
        if risk is None:
            logger.warning(f"Ferramenta sem política registrada no Policy Engine: {tool_name}")
            return PolicyDecision(
                tool_name=tool_name,
                risk_level=RiskLevel.PRIVILEGED,
                allowed=False,
                requires_confirmation=True,
                reason="Ferramenta sem política de segurança registrada (fail-closed).",
                metadata={"args": args}
            )

        # Desligar o Modo Controle é imediato para a sessão dona da autoridade.
        # Outra sessão não derruba o controle de quem recebeu a lease.
        if tool_name == "set_control_mode" and args.get("enabled") is False:
            dono = self._control_lease_owner
            if session_id is not None and dono is not None and dono != session_id:
                return PolicyDecision(
                    tool_name=tool_name,
                    risk_level=RiskLevel.LOW_WRITE,
                    allowed=False,
                    requires_confirmation=False,
                    reason="A autoridade de controle pertence a outra sessão: ela mesma precisa desligar o Modo Controle.",
                    metadata=self.control_lease_status()
                )
            return PolicyDecision(
                tool_name=tool_name,
                risk_level=RiskLevel.LOW_WRITE,
                allowed=True,
                requires_confirmation=False,
                reason="Revogação de controle físico autorizada imediatamente."
            )

        # Controle físico de mouse e teclado depende da lease concedida pelo usuário
        if tool_name in CONTROL_TOOLS:
            return self._avaliar_controle_fisico(tool_name, args, session_id)

        # READ e LOW_WRITE: Execução automática transparente
        if risk in (RiskLevel.READ, RiskLevel.LOW_WRITE):
            return PolicyDecision(
                tool_name=tool_name,
                risk_level=risk,
                allowed=True,
                requires_confirmation=False,
                reason=f"Ferramenta de baixo impacto ({risk.value}) autorizada automaticamente."
            )

        # EXTERNAL_WRITE: Exige confirmação explícita do usuário antes de sair da máquina
        if risk == RiskLevel.EXTERNAL_WRITE:
            return PolicyDecision(
                tool_name=tool_name,
                risk_level=risk,
                allowed=True,
                requires_confirmation=True,
                reason=f"Operação externa ({risk.value}) exige confirmação do usuário.",
                metadata={"args": args}
            )

        # PRIVILEGED: Ações delegadas ao Antigravity (com verificação de prompt e lease de Modo IDE)
        if risk == RiskLevel.PRIVILEGED:
            prompt = args.get("prompt", "")
            # Verificação básica de sanidade no prompt (apenas para o agente do Antigravity)
            if tool_name == "antigravity_run_prompt" and (not prompt or len(prompt.strip()) < 3):
                return PolicyDecision(
                    tool_name=tool_name,
                    risk_level=risk,
                    allowed=False,
                    requires_confirmation=False,
                    reason="Prompt privilegiado vazio ou inválido."
                )

            # Se houver uma lease ativa do Modo IDE concedida à sessão e usuário atuais,
            # a delegação contínua é permitida sem requerer nova confirmação individual por prompt.
            if tool_name == "antigravity_run_prompt" and self.is_ide_lease_active(session_id, user_id):
                return PolicyDecision(
                    tool_name=tool_name,
                    risk_level=risk,
                    allowed=True,
                    requires_confirmation=False,
                    reason="Execução autorizada sob Lease ativa do Modo IDE.",
                    metadata={"prompt_preview": prompt[:120], "ide_lease": self.ide_lease_status()}
                )

            return PolicyDecision(
                tool_name=tool_name,
                risk_level=risk,
                allowed=True,
                requires_confirmation=True,
                reason="Agente privilegiado exige confirmação explícita do usuário.",
                metadata={"prompt_preview": prompt[:120]}
            )

        return PolicyDecision(
            tool_name=tool_name,
            risk_level=risk,
            allowed=True,
            requires_confirmation=False,
            reason="Execução autorizada por política padrão."
        )

    # ---------------- Autorização One-Shot com TTL Monotônico & SHA-256 (Policy Gate) ----------------

    @staticmethod
    def _compute_args_hash(args: Dict[str, Any]) -> str:
        import hashlib
        import json
        try:
            canonical = json.dumps(args, sort_keys=True)
        except Exception:
            canonical = str(sorted(args.items()))
        # Digest SHA-256 completo (256 bits / 64 caracteres hexadecimais)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def cleanup_expired_actions(self) -> int:
        """Expurga periodicamente ações expiradas ou consumidas para evitar vazamento de memória."""
        with self._lock:
            now = time.monotonic()
            removidas = 0
            for action_id, action in list(self._pending_actions.items()):
                if now > action.expires_at or action.status in ("consumed", "rejected"):
                    del self._pending_actions[action_id]
                    removidas += 1
            return removidas

    def create_pending_action(
        self,
        tool_name: str,
        args: Dict[str, Any],
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        ttl: float = 60.0
    ) -> PendingAction:
        import secrets
        with self._lock:
            self.cleanup_expired_actions()
            action_id = secrets.token_hex(6)
            now = time.monotonic()
            args_hash = self._compute_args_hash(args)
            pending = PendingAction(
                action_id=action_id,
                tool_name=tool_name,
                args=args,
                args_hash=args_hash,
                session_id=session_id,
                user_id=user_id,
                created_at=now,
                expires_at=now + ttl,
                status="pending"
            )
            self._pending_actions[action_id] = pending
        logger.info(f"Ação pendente criada: {action_id} -> {tool_name} (Sessão: {session_id}, TTL {ttl}s monotônico)")
        return pending

    def approve_action(
        self,
        action_id: str,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> bool:
        with self._lock:
            self.cleanup_expired_actions()
            pending = self._pending_actions.get(action_id)
            if not pending:
                return False
            if time.monotonic() > pending.expires_at:
                pending.status = "rejected"
                return False
            # Isolamento obrigatório: se a ação foi associada a uma sessão/usuário, eles são indispensáveis
            if not session_id:
                logger.warning(f"Tentativa de aprovação de ação sem informar session_id obrigatório: {action_id}")
                return False
            if pending.session_id and pending.session_id != session_id:
                logger.warning(f"Tentativa de aprovação de ação por sessão alheia: {session_id} != {pending.session_id}")
                return False
            if pending.user_id and not user_id:
                logger.warning(f"Tentativa de aprovação de ação sem informar user_id obrigatório: {action_id}")
                return False
            if pending.user_id and user_id and pending.user_id != user_id:
                logger.warning(f"Tentativa de aprovação de ação por usuário alheio: {user_id} != {pending.user_id}")
                return False
            pending.status = "approved"
        logger.info(f"Ação aprovada pelo usuário: {action_id} -> {pending.tool_name} (Sessão: {session_id}, Usuário: {user_id})")
        return True

    def approve_latest_pending(
        self,
        session_id: str,
        user_id: Optional[str] = None
    ) -> Optional[PendingAction]:
        """Aprova a pendência mais recente pertencente EXCLUSIVAMENTE à sessão e usuário fornecidos.
        session_id é OBRIGATÓRIO para impedir aprovação indevida de ações de outras sessões ou globais.
        """
        if not session_id:
            logger.warning("Tentativa de approve_latest_pending sem session_id obrigatório.")
            return None
        with self._lock:
            now = time.monotonic()
            for action_id in reversed(list(self._pending_actions.keys())):
                action = self._pending_actions[action_id]
                if action.status == "pending" and now <= action.expires_at:
                    if action.session_id != session_id:
                        continue
                    if action.user_id and not user_id:
                        continue
                    if action.user_id and user_id and action.user_id != user_id:
                        continue
                    action.status = "approved"
                    logger.info(f"Última ação pendente aprovada: {action_id} -> {action.tool_name} (Sessão: {session_id}, Usuário: {user_id})")
                    return action
            return None

    def reject_action(
        self,
        action_id: str,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> bool:
        with self._lock:
            pending = self._pending_actions.get(action_id)
            if not pending:
                return False
            if not session_id:
                return False
            if pending.session_id and pending.session_id != session_id:
                return False
            if pending.user_id and not user_id:
                return False
            if pending.user_id and user_id and pending.user_id != user_id:
                return False
            pending.status = "rejected"
            del self._pending_actions[action_id]
            return True

    def consume_authorization(
        self,
        tool_name: str,
        args: Dict[str, Any],
        session_id: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> bool:
        """Verifica se há autorização válida, aprovada e com hash de argumentos correspondente.
        Ao encontrar, consome imediatamente (one-shot), revogando para execuções futuras.
        """
        with self._lock:
            now = time.monotonic()
            args_hash = self._compute_args_hash(args)
            for action_id, action in list(self._pending_actions.items()):
                if action.tool_name == tool_name and action.status == "approved" and now <= action.expires_at:
                    if action.args_hash == args_hash:
                        if action.session_id is not None:
                            if not session_id or action.session_id != session_id:
                                continue
                        if action.user_id is not None:
                            if not user_id or action.user_id != user_id:
                                continue
                        # Consumo estritamente único (one-shot): revoga e apaga imediatamente
                        action.status = "consumed"
                        del self._pending_actions[action_id]
                        logger.info(f"Autorização one-shot consumida com sucesso: {action_id} -> {tool_name}")
                        return True
            return False

    def list_pending_actions(
        self,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> list[PendingAction]:
        """Retorna apenas as pendências ativas da sessão e usuário requisitantes.
        Exige ao menos session_id ou user_id para evitar enumeração global não autorizada.
        Quando ambos são informados, aplica a validação estrita de ambos os filtros.
        """
        self.cleanup_expired_actions()
        now = time.monotonic()
        if not session_id and not user_id:
            return []
        resultado = []
        for action in self._pending_actions.values():
            if action.status == "pending" and now <= action.expires_at:
                if session_id is not None and action.session_id != session_id:
                    continue
                if user_id is not None and action.user_id != user_id:
                    continue
                resultado.append(action)
        return resultado

    def get_pending_action(self, action_id: str) -> Optional[PendingAction]:
        return self._pending_actions.get(action_id)

# Instância global do Policy Engine
policy_engine = PolicyEngine()
