"""
Gerenciamento de Leases de Modo Controle e Modo IDE (jarvis.core.leases)
Garante autoridade unificada baseada em sessões no PolicyEngine.
"""

import logging
from typing import Any, Dict, Optional

from policy_engine import policy_engine

logger = logging.getLogger("jarvis.leases")


def release_session_leases(session_id: str) -> Dict[str, Any]:
    """
    Revoga com segurança todas as leases (Controle Físico e Modo IDE) detidas por
    uma sessão encerrada, garantindo que o sistema retorne aos parâmetros nominais.
    """
    import system_tools

    resultado = {"session_id": session_id, "control_released": False, "ide_released": False}

    # Revoga lease de controle de periféricos se a sessão for a detentora
    st_ctrl = policy_engine.control_lease_status()
    if st_ctrl.get("owner") == session_id:
        if system_tools.get_control_mode():
            system_tools.set_control_mode(False)
        policy_engine.revoke_control_lease(session_id=session_id)
        resultado["control_released"] = True
        logger.info(f"Lease de controle físico liberada para sessão '{session_id}'.")

    # Revoga lease de IDE se a sessão for a detentora
    st_ide = policy_engine.ide_lease_status()
    if st_ide.get("owner") == session_id:
        policy_engine.revoke_ide_lease(session_id=session_id)
        resultado["ide_released"] = True
        logger.info(f"Lease de Modo IDE liberada para sessão '{session_id}'.")

    return resultado


def is_control_mode_active(session_id: Optional[str] = None) -> bool:
    """Verifica se o Modo Controle físico está ativo para a sessão especificada."""
    st = policy_engine.control_lease_status()
    if not st.get("active"):
        return False
    if session_id and st.get("owner") != session_id:
        return False
    return True


def is_ide_mode_active(session_id: Optional[str] = None) -> bool:
    """Verifica se o Modo IDE está ativo para a sessão especificada."""
    st = policy_engine.ide_lease_status()
    if not st.get("active"):
        return False
    if session_id and st.get("owner") != session_id:
        return False
    return True
