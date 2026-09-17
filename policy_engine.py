"""
Motor de Políticas e Controle de Capacidades do J.A.R.V.I.S.
Classifica cada ferramenta em níveis de risco e gerencia autorização prévia à execução.
"""

import logging
import os
import re
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

# Mapeamento inicial de ferramentas nativas e de plug-ins para seus níveis de risco
TOOL_RISK_MAP: Dict[str, RiskLevel] = {
    # READ: Informação e Consulta
    "get_system_status": RiskLevel.READ,
    "get_gpu_status": RiskLevel.READ,
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
    "open_website": RiskLevel.LOW_WRITE,
    "play_music": RiskLevel.LOW_WRITE,
    "take_quick_note": RiskLevel.LOW_WRITE,
    "set_ide_mode": RiskLevel.LOW_WRITE,
    "open_default_app": RiskLevel.LOW_WRITE,
    "manage_user_preference": RiskLevel.LOW_WRITE,
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

    # EXTERNAL_WRITE: Comunicação com serviços de terceiros
    "social_feed_post_update": RiskLevel.EXTERNAL_WRITE,
    "live_stream_send_alert": RiskLevel.EXTERNAL_WRITE,
    "search_web": RiskLevel.EXTERNAL_WRITE,

    # PRIVILEGED: Agente autônomo e controle de sistema
    "antigravity_run_prompt": RiskLevel.PRIVILEGED,
    "set_control_mode": RiskLevel.PRIVILEGED,
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

# Combinações de teclas que continuam exigindo confirmação mesmo com lease ativa
HOTKEYS_PERIGOSAS = {
    ("alt", "f4"),
    ("ctrl", "alt", "delete"),
    ("ctrl", "alt", "backspace"),
    ("ctrl", "alt", "f1"),
    ("ctrl", "alt", "f2"),
    ("ctrl", "alt", "t"),
    ("super",),
    ("super", "l"),
}

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

    def register_tool_policy(self, tool_name: str, risk_level: RiskLevel):
        """Registra ou atualiza o nível de risco de uma ferramenta (ex: via plug-in)."""
        self._custom_policies[tool_name] = risk_level

    def get_risk_level(self, tool_name: str) -> Optional[RiskLevel]:
        """Retorna o nível de risco da ferramenta, ou None se ela não tiver política."""
        if tool_name in self._custom_policies:
            return self._custom_policies[tool_name]
        return TOOL_RISK_MAP.get(tool_name)

    # ---------------- Lease de Controle Físico (mouse e teclado) ----------------

    def grant_control_lease(self, owner: str = "hud", ttl_s: int = CONTROL_LEASE_TTL_S) -> Dict[str, Any]:
        """Concede autoridade temporária de controle físico após confirmação do usuário."""
        self._control_lease_expira_em = time.time() + ttl_s
        self._control_lease_owner = owner
        logger.info(f"Lease de controle concedida a '{owner}' por {ttl_s}s.")
        return self.control_lease_status()

    def revoke_control_lease(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Revoga a autoridade de controle físico.

        Com session_id, só a sessão dona da lease pode revogá-la: assim uma segunda
        conexão não derruba o Modo Controle de quem realmente recebeu a autoridade.
        """
        if session_id is not None and self._control_lease_owner not in (None, session_id):
            logger.info("Revogação ignorada: a lease pertence a outra sessão.")
            return self.control_lease_status()
        self._control_lease_expira_em = 0.0
        self._control_lease_owner = None
        logger.info("Lease de controle revogada.")
        return self.control_lease_status()

    def is_control_lease_active(self, session_id: Optional[str] = None) -> bool:
        """A lease pertence à sessão que a recebeu: outra sessão não herda a autoridade."""
        if time.time() >= self._control_lease_expira_em:
            return False
        if session_id is not None and self._control_lease_owner != session_id:
            return False
        return True

    def control_lease_status(self) -> Dict[str, Any]:
        """Estado da lease. 'owner' é mantido mesmo após expirar, para a sessão dona
        conseguir identificar que a autoridade dela acabou."""
        restante = max(0.0, self._control_lease_expira_em - time.time())
        return {
            "ativa": restante > 0,
            "segundos_restantes": int(restante),
            "owner": self._control_lease_owner
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
                 session_id: Optional[str] = None) -> PolicyDecision:
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

        # PRIVILEGED: Ações delegadas ao Antigravity (com verificação de prompt)
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

# Instância global do Policy Engine
policy_engine = PolicyEngine()
