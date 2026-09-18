"""
R1: Test Trust Gates — Validação Adversarial e Garantia de Contratos
Fase R1: Test Trust

Gates Auditados:
1. Identity Isolation (Multi-session adversarial, isolamento estrito de aprovação)
2. Authorization One-Shot & Canonicalization (Binding exato, imutabilidade, ordem de chaves)
3. Lease Adversarial (Não herança, proteção contra revogação alheia, monotonic expiry)
4. Provider Real & Dispatch (Roteamento determinístico, despacho de chat, transparência Live)
5. Frontend & Backend Contracts (Token nas chamadas mutantes do Widget e Dashboard)
"""

import inspect
import json
import time
from unittest.mock import patch, AsyncMock
import pytest
from fastapi.testclient import TestClient

from policy_engine import policy_engine, RiskLevel
from provider_router import provider_router, OmniRouteProvider, GoogleStudioProvider
from server import app, JARVIS_SECRET_TOKEN


# ==============================================================================
# GATE 1: IDENTITY ISOLATION
# ==============================================================================

def test_gate1_session_a_cannot_approve_session_b():
    """Gate 1: Uma sessão/usuário jamais pode aprovar a ação pendente de outra sessão."""
    client = TestClient(app)
    policy_engine.cleanup_expired_actions()

    # Sessão A cria pendência legítima
    pending = policy_engine.create_pending_action(
        tool_name="open_website",
        args={"url": "https://alvo-seguro.local"},
        session_id="sessao_A",
        user_id="alice",
    )
    action_id = pending.action_id

    # 1. Sessão B tenta aprovar ação de A
    resp_b = client.post(
        "/api/confirmar_acao",
        json={"action_id": action_id, "session_id": "sessao_B", "user_id": "alice"},
        headers={"X-Jarvis-Token": JARVIS_SECRET_TOKEN},
    )
    assert resp_b.status_code in (400, 403, 404), (
        f"FALHA GATE 1: Sessão B conseguiu resposta HTTP {resp_b.status_code} para ação de A: {resp_b.text}"
    )
    assert pending.status == "pending", "FALHA GATE 1: Status da ação foi alterado por Sessão B!"

    # 2. Mesmo session_id com usuário divergente (Bob tentando aprovar ação de Alice)
    resp_usr = client.post(
        "/api/confirmar_acao",
        json={"action_id": action_id, "session_id": "sessao_A", "user_id": "bob"},
        headers={"X-Jarvis-Token": JARVIS_SECRET_TOKEN},
    )
    assert resp_usr.status_code in (400, 403, 404), (
        f"FALHA GATE 1: Usuário Bob conseguiu aprovar ação de Alice: {resp_usr.text}"
    )
    assert pending.status == "pending", "FALHA GATE 1: Status da ação foi alterado por usuário divergente!"

    # 3. Aprovação legítima por Sessão A e Alice
    resp_ok = client.post(
        "/api/confirmar_acao",
        json={"action_id": action_id, "session_id": "sessao_A", "user_id": "alice"},
        headers={"X-Jarvis-Token": JARVIS_SECRET_TOKEN},
    )
    assert resp_ok.status_code == 200 and resp_ok.json().get("status") == "ok", (
        f"FALHA GATE 1: Sessão legítima A não conseguiu aprovar própria ação: {resp_ok.text}"
    )
    assert pending.status == "approved"


def test_gate1_missing_identity_is_fail_closed():
    """Gate 1: Requisições de confirmação sem sessão explícita devem falhar fechado."""
    client = TestClient(app)
    policy_engine.cleanup_expired_actions()

    pending = policy_engine.create_pending_action(
        tool_name="open_website",
        args={"url": "https://alvo-seguro.local"},
        session_id="sessao_isolada_xyz",
        user_id="usr_xyz",
    )

    # Chamada omitindo a sessão
    resp = client.post(
        "/api/confirmar_acao",
        json={"action_id": pending.action_id},
        headers={"X-Jarvis-Token": JARVIS_SECRET_TOKEN},
    )
    assert resp.status_code in (400, 403, 422), (
        f"FALHA GATE 1: Endpoint permitiu confirmar ação sem session_id explícito! Status: {resp.status_code}"
    )
    assert pending.status == "pending"


# ==============================================================================
# GATE 2: AUTHORIZATION ONE-SHOT & CANONICALIZATION
# ==============================================================================

def test_gate2_authorization_is_strictly_one_shot():
    """Gate 2: Uma autorização concedida só pode ser consumida uma única vez (anti-replay)."""
    policy_engine.cleanup_expired_actions()
    args = {"app_name": "gedit"}

    pending = policy_engine.create_pending_action(
        tool_name="open_application",
        args=args,
        session_id="sessao_oneshot",
        user_id="user_oneshot",
    )
    ok_approve = policy_engine.approve_action(pending.action_id, session_id="sessao_oneshot", user_id="user_oneshot")
    assert ok_approve is True

    # Primeiro consumo: DEVE SUCEDER
    consumed_1 = policy_engine.consume_authorization(
        tool_name="open_application",
        args=args,
        session_id="sessao_oneshot",
        user_id="user_oneshot",
    )
    assert consumed_1 is True, "FALHA GATE 2: Primeiro consumo legítimo falhou!"

    # Segundo consumo (Replay Attack): DEVE FALHAR
    consumed_2 = policy_engine.consume_authorization(
        tool_name="open_application",
        args=args,
        session_id="sessao_oneshot",
        user_id="user_oneshot",
    )
    assert consumed_2 is False, "FALHA GATE 2: Replay attack sucedeu! Autorização consumida mais de uma vez."


def test_gate2_tampered_args_after_approval_fails():
    """Gate 2: Alteração de argumentos pós-aprovação deve invalidar o consumo."""
    policy_engine.cleanup_expired_actions()

    # Usuário autorizou abrir o site seguro
    args_aprovados = {"url": "https://banco.local"}
    pending = policy_engine.create_pending_action(
        tool_name="open_website",
        args=args_aprovados,
        session_id="sessao_tamper",
        user_id="user_tamper",
    )
    policy_engine.approve_action(pending.action_id, session_id="sessao_tamper", user_id="user_tamper")

    # Tentativa do agente ou invasor de executar para URL maliciosa usando a mesma autorização
    args_adulterados = {"url": "https://malicioso.local"}
    consumed = policy_engine.consume_authorization(
        tool_name="open_website",
        args=args_adulterados,
        session_id="sessao_tamper",
        user_id="user_tamper",
    )
    assert consumed is False, "FALHA GATE 2: Autorização foi consumida com argumentos adulterados pós-aprovação!"


def test_gate2_args_canonicalization_order_independent():
    """Gate 2: A ordem das chaves JSON nos argumentos não pode alterar o hash de autorização."""
    args_1 = {"url": "https://alvo.local", "modo": "foreground", "timeout": 30}
    args_2 = {"timeout": 30, "url": "https://alvo.local", "modo": "foreground"}

    hash_1 = policy_engine._compute_args_hash(args_1)
    hash_2 = policy_engine._compute_args_hash(args_2)

    assert hash_1 == hash_2, (
        f"FALHA GATE 2: Canonicalização de argumentos falhou: hash_1={hash_1} != hash_2={hash_2}"
    )

    args_diff = {"url": "https://alvo-diferente.local", "modo": "foreground", "timeout": 30}
    hash_diff = policy_engine._compute_args_hash(args_diff)
    assert hash_1 != hash_diff, "FALHA GATE 2: Conteúdos distintos produziram o mesmo hash!"


# ==============================================================================
# GATE 3: LEASE ADVERSARIAL
# ==============================================================================

def test_gate3_leases_are_session_isolated_and_uninheritable():
    """Gate 3: Leases de controle de mouse/teclado são vinculadas estritamente à sessão dona."""
    policy_engine.revoke_control_lease()

    # Sessão 1 obtém lease
    policy_engine.grant_control_lease(owner="sessao_alpha", ttl_s=60)

    # Sessão 1 é ativa
    assert policy_engine.is_control_lease_active(session_id="sessao_alpha") is True

    # Sessão 2 NÃO herda
    assert policy_engine.is_control_lease_active(session_id="sessao_beta") is False

    # Identidade anônima / ausente é Fail-Closed
    assert policy_engine.is_control_lease_active(session_id=None) is False

    # Sessão 2 tenta revogar a lease da Sessão 1: DEVE FALHAR
    policy_engine.revoke_control_lease(session_id="sessao_beta")
    assert policy_engine.is_control_lease_active(session_id="sessao_alpha") is True, (
        "FALHA GATE 3: Sessão Beta conseguiu revogar a lease da Sessão Alpha!"
    )

    # Sessão 1 revoga sua própria lease
    policy_engine.revoke_control_lease(session_id="sessao_alpha")
    assert policy_engine.is_control_lease_active(session_id="sessao_alpha") is False


def test_gate3_lease_expiration_is_strictly_monotonic():
    """Gate 3: Lease expirada bloqueia imediatamente a autoridade."""
    policy_engine.revoke_control_lease()

    # Concede lease com 0.1 segundo
    policy_engine.grant_control_lease(owner="sessao_exp", ttl_s=0.1)
    assert policy_engine.is_control_lease_active(session_id="sessao_exp") is True

    # Espera expirar
    time.sleep(0.15)
    assert policy_engine.is_control_lease_active(session_id="sessao_exp") is False, (
        "FALHA GATE 3: Lease permaneceu ativa após decorrido o TTL!"
    )


# ==============================================================================
# GATE 4: PROVIDER REAL & DISPATCH
# ==============================================================================

def test_gate4_live_provider_honesty():
    """Gate 4: O roteador deve ser transparente e honesto sobre suporte ao Live."""
    provider_router.set_active_provider("omniroute")
    info = provider_router.live_provider()

    assert info["provider"] == "google_studio", "Live bidirecional deve permanecer no Google"
    assert info["requested"] == "omniroute"
    assert info["live_supported"] is False, "OmniRoute não pode alegar suporte a Live se não implementa WS Live"
    assert "OmniRoute" in info["nota"]

    provider_router.set_active_provider("google_studio")
    info_google = provider_router.live_provider()
    assert info_google["live_supported"] is True


# ==============================================================================
# GATE 5: FRONTEND & BACKEND CONTRACTS
# ==============================================================================

def test_gate5_frontend_widget_sends_token_on_provider_select():
    """Gate 5: O widget do Gemini Live deve autenticar mutações de provedor e validar retorno HTTP."""
    with open("gemini-live-widget/widget.js", "r", encoding="utf-8") as f:
        src = f.read()

    # Extrai o corpo da função setProvider
    assert "async function setProvider" in src, "Função setProvider não encontrada no widget.js"
    inicio = src.find("async function setProvider")
    fim = src.find("\n}", inicio)
    set_provider_code = src[inicio:fim]

    # 1. Verifica que /api/providers/select é chamado dentro de setProvider
    assert "/api/providers/select" in set_provider_code, (
        "FALHA GATE 5: setProvider não chama /api/providers/select"
    )

    # 2. Verifica autenticação específica no fetch de setProvider
    assert "X-Jarvis-Token" in set_provider_code or "Authorization" in set_provider_code, (
        "FALHA GATE 5: setProvider não inclui X-Jarvis-Token ou Authorization na requisição de seleção de provedor!"
    )

    # 3. Verifica que valida resp.ok antes de assumir o provedor
    assert "resp.ok" in set_provider_code or "res.ok" in set_provider_code, (
        "FALHA GATE 5: setProvider assume sucesso sem verificar se a resposta HTTP foi bem-sucedida (resp.ok)!"
    )


def test_gate1_static_adk_client_sessions_are_isolated():
    """Gate 1: Clientes do static_adk devem gerar identificadores únicos de sessão e propagá-los no WebSocket."""
    with open("static_adk/app.js", "r", encoding="utf-8") as f:
        src = f.read()

    # 1. Identificador dinâmico por cliente
    assert "crypto.randomUUID" in src or "Math.random" in src or "uuid" in src, (
        "FALHA GATE 1: static_adk/app.js não gera identificador dinâmico/único por cliente!"
    )

    # 2. Propagação estrita da sessão e usuário no WebSocket
    assert "sessao" in src and "usuario" in src, "FALHA GATE 1: Parâmetros de identidade ausentes no app.js"

    # Extrai o bloco de conexão do WebSocket
    assert "new WebSocket" in src, "Instanciação do WebSocket não encontrada"
    idx_ws = src.find("new WebSocket")
    bloco_ws = src[max(0, idx_ws - 300):idx_ws + 100]
    assert ("sessao" in bloco_ws and "ws/live" in bloco_ws), (
        "FALHA GATE 1: static_adk/app.js conecta ao /ws/live sem propagar o query param 'sessao', "
        "causando fragmentação de identidade entre HTTP e WebSocket!"
    )


def test_gate4_chat_dispatches_to_omniroute_when_selected():
    """Gate 4: Quando OmniRoute é selecionado, o chat texto deve despachar diretamente para OmniRoute e ignorar runner Google."""
    client = TestClient(app)
    provider_router.set_active_provider("omniroute")

    with patch("provider_router.OmniRouteProvider.chat", new_callable=AsyncMock) as mock_omni:
        mock_omni.return_value = "Resposta de teste do OmniRoute"
        with patch("server.obter_runner_adk") as mock_runner:
            resp = client.post(
                "/api/chat",
                json={"texto": "Olá assistente", "sessao": "sess_prov", "usuario": "usr_prov"},
                headers={"X-Jarvis-Token": JARVIS_SECRET_TOKEN}
            )
            provider_router.set_active_provider("google_studio")
            assert resp.status_code == 200
            assert resp.json().get("provedor") == "omniroute"
            assert resp.json().get("modelo") == f"omniroute/{OmniRouteProvider.get_model()}"
            assert mock_omni.called is True, "FALHA GATE 4: OmniRouteProvider.chat NÃO foi chamado mesmo com OmniRoute selecionado!"
            assert mock_runner.called is False, "FALHA GATE 4: Google Runner foi chamado indevidamente quando OmniRoute estava ativo!"


def test_gate4_omniroute_observability_respects_custom_model(monkeypatch):
    """Gate 4: A observabilidade do modelo OmniRoute deve refletir fielmente OMNIROUTE_MODEL."""
    monkeypatch.setenv("OMNIROUTE_MODEL", "qwen3.6-plus")
    assert OmniRouteProvider.get_model() == "qwen3.6-plus"

    client = TestClient(app)
    provider_router.set_active_provider("omniroute")
    with patch("provider_router.OmniRouteProvider.chat", new_callable=AsyncMock) as mock_omni:
        mock_omni.return_value = "Resposta Qwen"
        resp = client.post(
            "/api/chat",
            json={"texto": "teste modelo", "sessao": "sess_mod", "usuario": "usr_mod"},
            headers={"X-Jarvis-Token": JARVIS_SECRET_TOKEN}
        )
        provider_router.set_active_provider("google_studio")
        assert resp.status_code == 200
        assert resp.json().get("modelo") == "omniroute/qwen3.6-plus"


def test_agent_skills_spec_compliance():
    """Valida que todas as 8 skills do ecossistema são 100% conformes com a spec Agent Skills oficial."""
    from adk_skill_loader import adk_skill_loader
    relatorio = adk_skill_loader.relatorio()
    assert len(relatorio) == 8, f"Esperado 8 skills, encontrado {len(relatorio)}"
    for item in relatorio:
        assert item["origem_carga"] == "adk_oficial", (
            f"Skill {item['skill_name']} falhou na carga oficial do ADK: {item}"
        )
        assert item["spec_compliant"] is True, (
            f"Skill {item['skill_name']} não é compliant com a spec Agent Skills: {item.get('spec_compliance_error')}"
        )
        assert item["l1_frontmatter_ok"] is True

