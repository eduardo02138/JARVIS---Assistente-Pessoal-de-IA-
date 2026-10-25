"""
F7 — Testes de Conectores Pessoais e Corporativos com OAuth 2.0 + PKCE (`monitoring/test_conectores_oauth.py`)

Testes obrigatórios:
- test_oauth_pkce_state_e_escopos
- test_revogacao_bloqueia_acesso_imediato
- test_envio_email_ou_evento_exige_confirmacao
"""

from __future__ import annotations

import os
import stat

import pytest
from fastapi.testclient import TestClient

from conectores_oauth import (
    OAuthConnectorManager,
    TokenVault,
    generate_pkce_pair,
    verify_pkce_challenge,
)
from policy_engine import policy_engine
from server import app
from servidor.seguranca import JARVIS_SECRET_TOKEN, emitir_sessao


def _headers() -> dict:
    if JARVIS_SECRET_TOKEN:
        return {"X-Jarvis-Token": JARVIS_SECRET_TOKEN}
    return {}


def test_oauth_pkce_state_e_escopos(tmp_path):
    """
    Garante que o fluxo OAuth 2.0 exige state anti-CSRF e code_verifier PKCE S256 válido,
    armazena tokens com permissão 0600 no sistema de arquivos Linux e valida escopos mínimos.
    """
    vault_file = tmp_path / "vault_0600.json"
    vault = TokenVault(vault_path=vault_file)
    mgr = OAuthConnectorManager(
        vault=vault,
        transport_adapter=lambda prov, act, params, tok: {"ok": True, "provider": prov, "action": act},
    )

    uid = emitir_sessao("user_oauth_f7")
    verifier, challenge = generate_pkce_pair()
    assert verify_pkce_challenge(verifier, challenge) is True
    assert verify_pkce_challenge("verifier_errado_" + "x" * 40, challenge) is False

    # 1. Inicia fluxo apenas com escopo de leitura
    flow = mgr.start_authorization_flow(
        user_id=uid,
        provider_id="gmail",
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        code_challenge_override=challenge,
    )
    assert "code_challenge=" in flow["authorization_url"]
    assert flow["code_challenge_method"] == "S256"

    # 2. Tentativa com state inválido -> bloqueada (anti-CSRF)
    with pytest.raises(PermissionError, match="state"):
        mgr.complete_authorization_flow(
            user_id=uid,
            provider_id="gmail",
            state="state_forjado_csrf",
            code="auth_code_12345",
            code_verifier=verifier,
        )

    # 3. Tentativa com code_verifier inválido -> bloqueada (PKCE S256)
    with pytest.raises(PermissionError, match="PKCE"):
        mgr.complete_authorization_flow(
            user_id=uid,
            provider_id="gmail",
            state=flow["state"],
            code="auth_code_12345",
            code_verifier="verifier_invalido_com_mais_de_43_caracteres_1234567890",
        )

    # 4. Tentativa sem token do provedor -> rejeitada (proibição de tokens forjados)
    flow_sem_tok = mgr.start_authorization_flow(
        user_id=uid,
        provider_id="gmail",
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        code_challenge_override=challenge,
    )
    with pytest.raises(ValueError, match="Token de acesso real não fornecido"):
        mgr.complete_authorization_flow(
            user_id=uid,
            provider_id="gmail",
            state=flow_sem_tok["state"],
            code="auth_code_valido_999",
            code_verifier=verifier,
        )

    # 5. Fluxo legítimo conclui e persiste cofre com permissão 0600
    flow2 = mgr.start_authorization_flow(
        user_id=uid,
        provider_id="gmail",
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        code_challenge_override=challenge,
    )
    tok = mgr.complete_authorization_flow(
        user_id=uid,
        provider_id="gmail",
        state=flow2["state"],
        code="auth_code_valido_999",
        code_verifier=verifier,
        access_token_from_provider="ya29.test_real_oauth_token_123",
    )
    assert tok.provider_id == "gmail"
    assert vault_file.exists()
    mode = stat.S_IMODE(os.stat(vault_file).st_mode)
    assert mode == 0o600, f"Cofre de tokens deve ter permissão 0600, encontrado {oct(mode)}"

    # 6. Leitura funciona com escopo readonly, mas envio falha por falta de escopo gmail.send
    res_read = mgr.execute_action(user_id=uid, provider_id="gmail", action="list_messages", params={"q": "is:unread"})
    assert res_read["status"] == "ok"

    with pytest.raises(PermissionError, match="Escopo OAuth insuficiente"):
        mgr.execute_action(
            user_id=uid,
            provider_id="gmail",
            action="send_email",
            params={"to": "dest@example.com", "subject": "Teste"},
        )


def test_revogacao_bloqueia_acesso_imediato(tmp_path):
    """
    Garante que revogar um conector OAuth invalida imediatamente o token
    e bloqueia qualquer leitura ou escrita subsequente.
    """
    vault = TokenVault(vault_path=tmp_path / "vault_revoke.json")
    mgr = OAuthConnectorManager(
        vault=vault,
        transport_adapter=lambda prov, act, params, tok: {"items": ["doc1.pdf"]},
    )
    uid = emitir_sessao("user_revoke_f7")

    flow = mgr.start_authorization_flow(user_id=uid, provider_id="google_drive")
    mgr.complete_authorization_flow(
        user_id=uid,
        provider_id="google_drive",
        state=flow["state"],
        code="code_drive_123",
        code_verifier=flow["code_verifier"],
        access_token_from_provider="ya29.test_drive_token_456",
    )

    # Leitura antes da revogação funciona
    ok_res = mgr.execute_action(user_id=uid, provider_id="google_drive", action="list_files")
    assert ok_res["status"] == "ok"

    # Revogação imediata
    assert mgr.revoke_connector(user_id=uid, provider_id="google_drive") is True

    # Qualquer chamada após revogação é barrada com PermissionError
    with pytest.raises(PermissionError, match="revogado"):
        mgr.execute_action(user_id=uid, provider_id="google_drive", action="list_files")


def test_envio_email_ou_evento_exige_confirmacao(tmp_path):
    """
    Garante que enviar e-mail ou criar/modificar evento na agenda exige
    confirmação explícita one-shot do usuário no PolicyEngine.
    """
    vault = TokenVault(vault_path=tmp_path / "vault_confirm.json")
    executed_calls = []

    def _fake_transport(prov, act, params, tok):
        executed_calls.append((prov, act, params))
        return {"sent": True}

    mgr = OAuthConnectorManager(vault=vault, transport_adapter=_fake_transport)
    uid = emitir_sessao("user_confirm_f7")

    # Conecta Google Calendar com escopos de leitura e escrita
    flow = mgr.start_authorization_flow(
        user_id=uid,
        provider_id="google_calendar",
        scopes=[
            "https://www.googleapis.com/auth/calendar.readonly",
            "https://www.googleapis.com/auth/calendar.events",
        ],
    )
    mgr.complete_authorization_flow(
        user_id=uid,
        provider_id="google_calendar",
        state=flow["state"],
        code="code_cal_456",
        code_verifier=flow["code_verifier"],
        access_token_from_provider="ya29.test_calendar_token_789",
    )

    event_params = {"title": "Reunião de Arquitetura", "start": "2026-10-26T14:00:00-03:00"}

    # 1. Primeira tentativa de criar evento pausa aguardando confirmação humana
    res1 = mgr.execute_action(
        user_id=uid,
        provider_id="google_calendar",
        action="create_event",
        params=event_params,
    )
    assert res1["status"] == "aguardando_confirmacao"
    assert res1["requires_confirmation"] is True
    conf_id = res1["id_confirmacao"]
    assert len(executed_calls) == 0

    # 2. Aprovação humana one-shot libera exatamente 1 execução
    assert policy_engine.approve_action(conf_id, session_id=uid, user_id=uid) is True
    res2 = mgr.execute_action(
        user_id=uid,
        provider_id="google_calendar",
        action="create_event",
        params=event_params,
    )
    assert res2["status"] == "ok"
    assert len(executed_calls) == 1

    # 3. Nova chamada idêntica volta a exigir confirmação (one-shot consumido)
    res3 = mgr.execute_action(
        user_id=uid,
        provider_id="google_calendar",
        action="create_event",
        params=event_params,
    )
    assert res3["status"] == "aguardando_confirmacao"
    assert len(executed_calls) == 1

    # 4. Verifica rota HTTP /api/connectors
    client = TestClient(app)
    r_list = client.get(f"/api/connectors?sessao={uid}", headers=_headers())
    assert r_list.status_code == 200
    assert len(r_list.json()["connectors"]) == 7


def test_sem_adaptador_transporte_retorna_unavailable(tmp_path):
    """
    Garante que na ausência de transporte configurado, execute_action
    responde explicitamente com UNAVAILABLE_EXTERNAL_INTEGRATION sem simular sucesso.
    """
    vault = TokenVault(vault_path=tmp_path / "vault_notransport.json")
    mgr = OAuthConnectorManager(vault=vault, transport_adapter=None)
    uid = emitir_sessao("user_notransport_f7")

    flow = mgr.start_authorization_flow(user_id=uid, provider_id="google_drive")
    mgr.complete_authorization_flow(
        user_id=uid,
        provider_id="google_drive",
        state=flow["state"],
        code="code_drive_xyz",
        code_verifier=flow["code_verifier"],
        access_token_from_provider="ya29.test_token_live_123",
    )

    res = mgr.execute_action(user_id=uid, provider_id="google_drive", action="list_files")
    assert res["status"] == "UNAVAILABLE_EXTERNAL_INTEGRATION"
    assert res["executed"] is False
    assert "não registrado" in res["mensagem"] or "não está configurada" in res["mensagem"]
