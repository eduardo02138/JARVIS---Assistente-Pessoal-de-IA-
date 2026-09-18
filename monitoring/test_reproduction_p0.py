"""
Suíte de Reprodução Estrita dos Achados P0 — Baseline a31a30f
Fase R-1: Freeze & Reproduction

Objetivo:
Provar através de testes que falham (RED) as vulnerabilidades e falhas
funcionais identificadas na auditoria antes de qualquer refatoração.
"""

import inspect
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from policy_engine import policy_engine, RiskLevel
from server import app, JARVIS_SECRET_TOKEN
from system_tools import manage_user_preference
import monitoring.test_suite as test_suite
import agentes.assistente as ast


def test_p0_01_rejects_arbitrary_default_app_executable():
    """P0-01: O sistema deve rejeitar executáveis arbitrários fora da allowlist
    e classificar a alteração de binários como EXTERNAL_WRITE no PolicyEngine."""
    # 1. Tentativa de configurar um binário arbitrário perigoso
    res = manage_user_preference(
        action="set",
        category="default_apps",
        key="text_editor",
        value="arbitrary_malicious_bin",
    )
    assert res.get("sucesso") is False, (
        f"P0-01 VULNERÁVEL: manage_user_preference aceitou executável arbitrário: {res}"
    )

    # 2. Avaliação de risco no PolicyEngine
    risk = policy_engine.get_risk_level("manage_user_preference")
    assert risk == RiskLevel.EXTERNAL_WRITE, (
        f"P0-01 VULNERÁVEL: manage_user_preference classificado como {risk}, mas exige EXTERNAL_WRITE"
    )


def test_p0_02_rejects_cross_session_confirmation():
    """P0-02: /api/confirmar_acao deve rejeitar aprovação com session_id omitido
    ou divergente, não permitindo aprovação cruzada entre sessões."""
    client = TestClient(app)

    # Cria ação pendente estritamente vinculada à sessão 'vitima-sessao-1' e usuária 'alice'
    pending = policy_engine.create_pending_action(
        tool_name="abrir_site",
        args={"url": "https://alvo.local"},
        session_id="vitima-sessao-1",
        user_id="alice",
    )
    action_id = pending.action_id

    # Chamada adversária omitindo a sessão ou passando 'default'
    resp = client.post(
        "/api/confirmar_acao",
        json={"action_id": action_id, "session_id": "default", "user_id": "local"},
        headers={"X-Jarvis-Token": JARVIS_SECRET_TOKEN},
    )

    # Não pode retornar 200 OK aprovando a ação
    assert not (resp.status_code == 200 and resp.json().get("status") == "ok"), (
        f"P0-02 VULNERÁVEL: /api/confirmar_acao permitiu aprovação cruzada! Resposta: {resp.json()}"
    )

    # A ação original deve permanecer pendente
    assert pending.status == "pending", (
        f"P0-02 VULNERÁVEL: Ação foi aprovada indevidamente (status={pending.status})"
    )


def test_p0_04_websocket_auth_requires_real_connected_frame():
    """P0-04: O teste de WebSocket não deve engolir exceções de conexão ou timeout
    assumindo auth_accepted=True em caso de queda do socket."""
    src = inspect.getsource(test_suite.test_websocket_auth)

    # Na baseline, o teste contém:
    # except Exception as e:
    #     if "Não autorizado" not in str(e):
    #         auth_accepted = True
    vulnerable_pattern = 'if "Não autorizado" not in str(e):'
    assert vulnerable_pattern not in src, (
        "P0-04 FALSO POSITIVO COMPROVADO: test_websocket_auth engole exceções com 'auth_accepted = True' se o erro não contiver 'Não autorizado'"
    )


def test_p0_08_equivalent_browser_tools_have_same_policy():
    """P0-08: Ferramentas equivalentes de navegador devem ter rigor idêntico no PolicyEngine."""
    risk_abrir = policy_engine.get_risk_level("abrir_site")
    risk_open = policy_engine.get_risk_level("open_website")

    assert risk_abrir == risk_open, (
        f"P0-08 ASSIMETRIA COMPROVADA: 'abrir_site' ({risk_abrir}) e 'open_website' ({risk_open}) possuem políticas divergentes no PolicyEngine"
    )


def test_p0_09_skill_fallback_has_valid_logger():
    """P0-09: O módulo agentes.assistente deve instanciar logger e não falhar com NameError no fallback."""
    # 1. Verifica presença do logger no módulo
    assert hasattr(ast, "logger") and ast.logger is not None, (
        "P0-09 NameError COMPROVADO: agentes.assistente não define 'logger'"
    )

    # 2. Força execução do fallback
    with patch("adk_skill_loader.adk_skill_loader.carregar", side_effect=RuntimeError("Simulação de falha")):
        # Não deve disparar NameError
        resultado = ast._criar_skill_toolset()
        assert resultado is None
