"""
Motor de Políticas e Controle de Capacidades do J.A.R.V.I.S.
Classifica cada ferramenta em níveis de risco e gerencia autorização prévia à execução.
"""

import logging
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

    # LOW_WRITE: Ações locais seguras
    "adjust_volume": RiskLevel.LOW_WRITE,
    "open_application": RiskLevel.LOW_WRITE,
    "open_website": RiskLevel.LOW_WRITE,
    "play_music": RiskLevel.LOW_WRITE,
    "take_quick_note": RiskLevel.LOW_WRITE,
    "set_ide_mode": RiskLevel.LOW_WRITE,
    "set_control_mode": RiskLevel.LOW_WRITE,
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
}

class PolicyEngine:
    """Gerencia regras de governança e autorização de ferramentas."""

    def __init__(self):
        self._custom_policies: Dict[str, RiskLevel] = {}

    def register_tool_policy(self, tool_name: str, risk_level: RiskLevel):
        """Registra ou atualiza o nível de risco de uma ferramenta (ex: via plug-in)."""
        self._custom_policies[tool_name] = risk_level

    def get_risk_level(self, tool_name: str) -> RiskLevel:
        """Retorna o nível de risco associado à ferramenta."""
        if tool_name in self._custom_policies:
            return self._custom_policies[tool_name]
        return TOOL_RISK_MAP.get(tool_name, RiskLevel.LOW_WRITE)

    def evaluate(self, tool_name: str, args: Optional[Dict[str, Any]] = None) -> PolicyDecision:
        """
        Avalia se a execução da ferramenta está autorizada e sob quais condições.
        """
        risk = self.get_risk_level(tool_name)
        args = args or {}

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
            # Verificação básica de sanidade no prompt
            if not prompt or len(prompt.strip()) < 3:
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
