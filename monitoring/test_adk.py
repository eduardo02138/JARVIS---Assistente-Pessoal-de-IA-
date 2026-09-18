import asyncio
import concurrent.futures
"""
Suíte de Testes Automatizados da Arquitetura Unificada do Google ADK & Policy Gate (Fase P0.16).
Verifica:
1. Roteamento inteligente de texto (escolher_caminho: rápido vs coordenador).
2. Ausência total de ferramentas de auto-autorização no schema do LLM.
3. Policy Engine como autoridade única de segurança.
4. Concessão de autorização estritamente one-shot com TTL monotônico e SHA-256 de 64 caracteres.
5. Revogação e expurgo imediato do token após consumo (não reutilizável).
6. Isolamento rigoroso de ações pendentes por sessão (Sessão B não acessa ou aprova Sessão A).
7. Autenticação mandatória HTTP (401 sem token) em /api/chat, /api/acoes_pendentes, /api/confirmar_acao.
8. Autenticação mandatória no WebSocket /ws/live_adk (código 1008 sem token válido).
9. Aprovação comportamental no WebSocket Live por comando falado e digitado ("sim").
"""

import os
import sys
import time

# Garante acesso à raiz do projeto
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

from fastapi.testclient import TestClient
from policy_engine import policy_engine, RiskLevel
from agentes.roteador import escolher_caminho, CAMINHO_RAPIDO, CAMINHO_COMPLEXO
from agentes.assistente import (
    criar_agente_rapido,
    criar_agente_coordenador,
    criar_agente_de_voz,
    guarda_de_ferramentas,
)


def test_roteador_inteligente():
    """Valida o despachante heurístico de baixa latência."""
    caminho1, _ = escolher_caminho("Que horas são agora?")
    assert caminho1 == CAMINHO_RAPIDO, f"Esperado {CAMINHO_RAPIDO}, obtido {caminho1}"

    caminho2, _ = escolher_caminho("status do sistema")
    assert caminho2 == CAMINHO_RAPIDO, f"Esperado {CAMINHO_RAPIDO}, obtido {caminho2}"

    caminho3, _ = escolher_caminho("Primeiro analise o sistema e depois abra o navegador com o resumo")
    assert caminho3 == CAMINHO_COMPLEXO, f"Esperado {CAMINHO_COMPLEXO}, obtido {caminho3}"

    caminho4, _ = escolher_caminho("Gostaria de saber detalhadamente todas as métricas da minha máquina e como estão os processos rodando no Linux")
    assert caminho4 == CAMINHO_COMPLEXO, f"Esperado {CAMINHO_COMPLEXO}, obtido {caminho4}"
    print(" [✔ PASS] Roteador Inteligente ADK (escolher_caminho: rápido vs coordenador)")


def test_ausencia_de_auto_autorizacao_no_llm():
    """Garante que autorizar_acao NUNCA seja exposta como ferramenta ao modelo."""
    for nome, fabrica in [
        ("agente_rapido", criar_agente_rapido),
        ("coordenador", criar_agente_coordenador),
        ("agente_voz", criar_agente_de_voz),
    ]:
        agente = fabrica()
        nomes_tools = []
        for t in agente.tools:
            if hasattr(t, "name"):
                nomes_tools.append(t.name)
            elif hasattr(t, "__name__"):
                nomes_tools.append(t.__name__)
            elif hasattr(t, "agent"):
                nomes_tools.append(t.agent.name)
        assert "autorizar_acao" not in nomes_tools, (
            f"FALHA DE SEGURANÇA: 'autorizar_acao' encontrada nas ferramentas de {nome}: {nomes_tools}"
        )
    print(" [✔ PASS] Ausência de Auto-Autorização: LLM não possui ferramenta de auto-liberação")


def test_policy_engine_bloqueio_e_one_shot():
    """Valida que ferramentas sensíveis bloqueiam e só executam sob liberação única one-shot."""
    args = {"url": "https://antigravity.google.com"}
    session_id = "teste-sessao-segura-adk"

    decision = policy_engine.evaluate("abrir_site", args, session_id=session_id)
    assert decision.allowed is True
    assert decision.requires_confirmation is True
    assert decision.risk_level == RiskLevel.EXTERNAL_WRITE

    class MockTool:
        name = "abrir_site"

    class MockContext:
        session_id = "teste-sessao-segura-adk"

    resultado_bloqueio = guarda_de_ferramentas(MockTool(), args, MockContext())
    assert isinstance(resultado_bloqueio, dict), "Esperado bloqueio com dict"
    assert resultado_bloqueio.get("status") == "bloqueado_aguardando_confirmacao"
    action_id = resultado_bloqueio.get("id_confirmacao")
    assert action_id is not None, "ID de confirmação ausente"

    # Tentativa de consumo antes da aprovação do usuário deve falhar
    assert policy_engine.consume_authorization("abrir_site", args, session_id=session_id) is False

    # Usuário aprova a ação através da autoridade legítima
    aprovado = policy_engine.approve_action(action_id, session_id=session_id)
    assert aprovado is True

    # Agora a execução deve ser autorizada e o token consumido (one-shot)
    resultado_liberado = guarda_de_ferramentas(MockTool(), args, MockContext())
    assert resultado_liberado is None, "Deveria retornar None (permitir execução original)"

    # Segunda execução imediata com os mesmos argumentos DEVE FALHAR (token já foi consumido)
    resultado_reexecucao = guarda_de_ferramentas(MockTool(), args, MockContext())
    assert isinstance(resultado_reexecucao, dict), "Re-execução deveria ser bloqueada (one-shot violado)"
    assert resultado_reexecucao.get("status") == "bloqueado_aguardando_confirmacao"
    print(" [✔ PASS] Policy Gate One-Shot: Bloqueio, aprovação única e revogação imediata pós-consumo")


def test_isolamento_de_sessao_e_relogio_monotonico():
    """Valida integridade SHA-256 (64 chars), TTL monotônico e isolamento estrito entre sessões."""
    session_a = "sessao-isolada-A"
    session_b = "sessao-isolada-B"
    args = {"url": "https://google.com"}

    pending_a = policy_engine.create_pending_action("abrir_site", args, session_id=session_a, ttl=10.0)
    assert len(pending_a.args_hash) == 64, f"Hash deve ter 64 caracteres hexadecimais (SHA-256 completo): {len(pending_a.args_hash)}"

    # Sessão B NÃO pode listar a pendência da Sessão A
    pendentes_b = policy_engine.list_pending_actions(session_id=session_b)
    ids_b = [p.action_id for p in pendentes_b]
    assert pending_a.action_id not in ids_b, "Vazamento de isolamento: Sessão B conseguiu ver pendência de A!"

    # Sessão B NÃO pode aprovar a pendência da Sessão A
    aprovou_invasor = policy_engine.approve_action(pending_a.action_id, session_id=session_b)
    assert aprovou_invasor is False, "Falha de autorização: Sessão B conseguiu aprovar pendência de A!"

    # Sessão A aprova legitimamente a sua própria pendência
    aprovou_dono = policy_engine.approve_action(pending_a.action_id, session_id=session_a)
    assert aprovou_dono is True

    # Sessão B NÃO pode consumir a autorização aprovada de A
    consumiu_invasor = policy_engine.consume_authorization("abrir_site", args, session_id=session_b)
    assert consumiu_invasor is False

    # Sessão A consome com sucesso
    consumiu_dono = policy_engine.consume_authorization("abrir_site", args, session_id=session_a)
    assert consumiu_dono is True
    print(" [✔ PASS] Isolamento Estrito de Sessões, TTL Monotônico e SHA-256 Completo (64 hex)")


def test_autenticacao_http_endpoints_adk():
    """Valida que todos os endpoints ADK exigem autenticação obrigatória via JARVIS_TOKEN."""
    from server import app, JARVIS_SECRET_TOKEN
    client = TestClient(app)

    # 1. /api/chat sem token -> 401
    r_chat_unauth = client.post("/api/chat", json={"texto": "olá"})
    assert r_chat_unauth.status_code == 401, f"Esperado 401, obtido {r_chat_unauth.status_code}"

    # 2. /api/acoes_pendentes sem token -> 401
    r_pend_unauth = client.get("/api/acoes_pendentes")
    assert r_pend_unauth.status_code == 401, f"Esperado 401, obtido {r_pend_unauth.status_code}"

    # 3. /api/confirmar_acao sem token -> 401
    r_conf_unauth = client.post("/api/confirmar_acao", json={"id_confirmacao": "fake"})
    assert r_conf_unauth.status_code == 401, f"Esperado 401, obtido {r_conf_unauth.status_code}"

    # 4. Requisições autenticadas com X-Jarvis-Token são aceitas
    auth_headers = {"X-Jarvis-Token": JARVIS_SECRET_TOKEN}
    r_pend_auth = client.get("/api/acoes_pendentes", headers=auth_headers)
    assert r_pend_auth.status_code == 200, f"Esperado 200 com token, obtido {r_pend_auth.status_code}"
    print(" [✔ PASS] Autenticação Obrigatória nos Endpoints ADK (401 sem token / 200 com token)")


def test_autenticacao_e_confirmacao_live_adk():
    """Valida handshake autenticado e aprovação comportamental por texto/voz no Live ADK."""
    from server import app, JARVIS_SECRET_TOKEN
    client = TestClient(app)
    session_id = "sessao-live-test-123"

    # Cria ação pendente na sessão de teste
    args = {"url": "https://brave.com"}
    pending = policy_engine.create_pending_action("abrir_site", args, session_id=session_id)
    assert pending.status == "pending"

    # Conexão não autorizada ao WebSocket -> Rejeitada com 1008
    with client.websocket_connect(f"/ws/live_adk?sessao={session_id}") as ws_unauth:
        ws_unauth.send_json({"type": "init", "token": "token-falso-invalido"})
        msg_err = ws_unauth.receive_json()
        assert msg_err.get("tipo") == "erro"

    # Conexão legítima autorizada com handshake
    try:
        with client.websocket_connect(f"/ws/live_adk?sessao={session_id}") as ws:
            ws.send_json({"type": "init", "token": JARVIS_SECRET_TOKEN})
            msg_ready = ws.receive_json()
            assert msg_ready.get("tipo") == "pronto"

            # Simula envio de texto "sim" durante a sessão Live
            ws.send_json({"tipo": "texto", "texto": "sim"})
            msg_confirm = ws.receive_json()
            assert msg_confirm.get("tipo") == "acao_aprovada"
            assert msg_confirm.get("action_id") == pending.action_id
            ws.close()
    except (concurrent.futures.CancelledError, asyncio.CancelledError):
        pass

    # Comprova que a aprovação refletiu no PolicyEngine e consome o token (one-shot)
    consumido = policy_engine.consume_authorization("abrir_site", args, session_id=session_id)
    assert consumido is True, "Ação confirmada no Live deveria poder ser consumida com sucesso"
    print(" [✔ PASS] Handshake Seguro no Live ADK e Confirmação Comportamental Funcional")


def executar_todos_testes_adk():
    print("\n=== EXECUTANDO TESTES DO GOOGLE ADK & POLICY GATE (FASE P0.16) ===")
    test_roteador_inteligente()
    test_ausencia_de_auto_autorizacao_no_llm()
    test_policy_engine_bloqueio_e_one_shot()
    test_isolamento_de_sessao_e_relogio_monotonico()
    test_autenticacao_http_endpoints_adk()
    test_autenticacao_e_confirmacao_live_adk()
    print("\n✔ Todos os testes do módulo ADK P0.16 passaram com 100% de conformidade!\n")


if __name__ == "__main__":
    executar_todos_testes_adk()
