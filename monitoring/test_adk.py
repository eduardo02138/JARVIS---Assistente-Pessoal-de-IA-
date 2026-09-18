# -*- coding: utf-8 -*-
"""Suíte de testes de certificação do Google ADK & Policy Gate (Fase P0.17).

Testa:
1. Roteamento Inteligente (escolher_caminho: rápido vs coordenador).
2. Ausência de auto-autorização: o LLM não possui ferramenta para burlar o Policy Engine.
3. Policy Gate One-Shot: bloqueio preventivo, token único com TTL monotônico e consumo imediato.
4. Isolamento estrito de Sessão e Usuário: session_id e user_id são obrigatórios para aprovação.
5. Autenticação obrigatória nos endpoints HTTP do ADK (401 sem token / 200 com token).
6. Handshake seguro no WebSocket /ws/live_adk e confirmação digitada no Live.
7. Confirmação comportamental por VOZ no Live ADK (input_transcription -> 'sim' -> origem 'voz' -> consumo one-shot).
8. Smoke test do servidor_adk.py standalone (garantindo ausência de erros de importação em runtime).
"""

import sys
import os
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
for venv_site in [
    os.path.join(RAIZ, ".venv", "lib", f"python{sys.version_info.major}.{sys.version_info.minor}", "site-packages"),
    os.path.join(RAIZ, ".venv", "lib", "site-packages")
]:
    if os.path.isdir(venv_site) and venv_site not in sys.path:
        sys.path.insert(0, venv_site)
import asyncio
import concurrent.futures
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from policy_engine import policy_engine
from agentes.roteador import escolher_caminho, CAMINHO_RAPIDO, CAMINHO_COMPLEXO
from agentes.assistente import criar_agente_rapido, criar_agente_coordenador, guarda_de_ferramentas


def test_roteador_inteligente():
    """Valida que o roteador separa comandos simples de fluxos complexos multi-agente."""
    assert escolher_caminho("que horas são?")[0] == CAMINHO_RAPIDO
    assert escolher_caminho("qual o status do sistema?")[0] == CAMINHO_RAPIDO
    assert escolher_caminho("pesquise sobre python")[0] == CAMINHO_RAPIDO
    assert escolher_caminho("abra o site")[0] == CAMINHO_RAPIDO

    assert escolher_caminho("analise este problema complexo passo a passo")[0] == CAMINHO_COMPLEXO
    assert escolher_caminho("coordene o especialista e depois compare todas as métricas passo a passo")[0] == CAMINHO_COMPLEXO
    print(" [✔ PASS] Roteador Inteligente ADK (escolher_caminho: rápido vs coordenador)")


def test_ausencia_de_auto_autorizacao_no_llm():
    """Garante que nenhuma ferramenta exposta aos agentes permite auto-aprovação de privilégios."""
    agente_rapido = criar_agente_rapido()
    nomes_rapido = [getattr(t, "name", getattr(t, "__name__", str(t))) for t in agente_rapido.tools]
    assert "autorizar_acao" not in nomes_rapido
    assert "approve_action" not in nomes_rapido

    agente_coord = criar_agente_coordenador()
    nomes_coord = [getattr(t, "name", getattr(t, "__name__", str(t))) for t in agente_coord.tools]
    assert "autorizar_acao" not in nomes_coord
    assert "approve_action" not in nomes_coord
    print(" [✔ PASS] Ausência de Auto-Autorização: LLM não possui ferramenta de auto-liberação")


def test_policy_engine_bloqueio_e_one_shot():
    """Valida bloqueio preventivo pelo PolicyEngine, autorização única e revogação pós-consumo."""
    class MockTool:
        name = "abrir_site"

    class MockContext:
        session_id = "sessao-teste-adk-01"
        user_id = "usuario-teste-01"

    session_id = "sessao-teste-adk-01"
    user_id = "usuario-teste-01"
    args = {"url": "https://exemplo.com"}

    # 1. Primeira execução: Bloqueada pelo PolicyEngine aguardando aprovação
    resultado_bloqueio = guarda_de_ferramentas(MockTool(), args, MockContext())
    assert isinstance(resultado_bloqueio, dict), "Deveria retornar dict bloqueando a ferramenta"
    assert resultado_bloqueio.get("status") == "bloqueado_aguardando_confirmacao"
    action_id = resultado_bloqueio.get("id_confirmacao")
    assert action_id is not None

    # 2. Usuário legítimo aprova a ação informando session_id e user_id
    aprovado = policy_engine.approve_action(action_id, session_id=session_id, user_id=user_id)
    assert aprovado is True

    # 3. Agora a execução deve ser autorizada e o token consumido (one-shot)
    resultado_liberado = guarda_de_ferramentas(MockTool(), args, MockContext())
    assert resultado_liberado is None, "Deveria retornar None (permitir execução original)"

    # 4. Segunda execução imediata com os mesmos argumentos DEVE FALHAR (token já consumido)
    resultado_reexecucao = guarda_de_ferramentas(MockTool(), args, MockContext())
    assert isinstance(resultado_reexecucao, dict), "Re-execução deveria ser bloqueada (one-shot violado)"
    assert resultado_reexecucao.get("status") == "bloqueado_aguardando_confirmacao"
    print(" [✔ PASS] Policy Gate One-Shot: Bloqueio, aprovação única e revogação imediata pós-consumo")


def test_isolamento_estrito_sessao_e_usuario():
    """Valida que session_id e user_id são estritamente obrigatórios para listar e aprovar ações."""
    session_a = "sessao-isolada-A"
    user_a = "usuario-A"
    session_b = "sessao-isolada-B"
    user_b = "usuario-B"
    args = {"url": "https://google.com"}

    pending_a = policy_engine.create_pending_action("abrir_site", args, session_id=session_a, user_id=user_a, ttl=10.0)
    assert len(pending_a.args_hash) == 64, f"Hash deve ser SHA-256 completo (64 hex): {len(pending_a.args_hash)}"

    # Sessão B NÃO pode listar a pendência da Sessão A
    pendentes_b = policy_engine.list_pending_actions(session_id=session_b, user_id=user_b)
    ids_b = [p.action_id for p in pendentes_b]
    assert pending_a.action_id not in ids_b, "Vazamento de isolamento: Sessão B conseguiu ver pendência de A!"

    # Tentativa de aprovação sem informar session_id DEVE FALHAR
    assert policy_engine.approve_action(pending_a.action_id, session_id=None, user_id=user_a) is False
    # Tentativa de aprovação sem informar user_id DEVE FALHAR
    assert policy_engine.approve_action(pending_a.action_id, session_id=session_a, user_id=None) is False
    # Tentativa de aprovação por sessão alheia DEVE FALHAR
    assert policy_engine.approve_action(pending_a.action_id, session_id=session_b, user_id=user_a) is False
    # Tentativa de aprovação por usuário alheio DEVE FALHAR
    assert policy_engine.approve_action(pending_a.action_id, session_id=session_a, user_id=user_b) is False

    # approve_latest_pending sem session_id DEVE retornar None (não aprova nada global)
    assert policy_engine.approve_latest_pending(session_id="", user_id=user_a) is None
    assert policy_engine.approve_latest_pending(session_id=session_b, user_id=user_b) is None

    # Sessão A aprova legitimamente com sessão e usuário corretos
    aprovou_dono = policy_engine.approve_action(pending_a.action_id, session_id=session_a, user_id=user_a)
    assert aprovou_dono is True

    # Sessão B NÃO pode consumir a autorização aprovada de A
    assert policy_engine.consume_authorization("abrir_site", args, session_id=session_b, user_id=user_b) is False

    # Sessão A consome com sucesso (one-shot)
    assert policy_engine.consume_authorization("abrir_site", args, session_id=session_a, user_id=user_a) is True
    print(" [✔ PASS] Isolamento Estrito de Sessão e Usuário Obrigatórios (Anti-Bypass)")


def test_autenticacao_http_endpoints_adk():
    """Valida que todos os endpoints ADK exigem autenticação obrigatória via JARVIS_TOKEN e validação de payload."""
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

    # 4. Requisição autenticada sem parâmetro 'sessao' em confirmar_acao -> 400 Bad Request
    auth_headers = {"X-Jarvis-Token": JARVIS_SECRET_TOKEN}
    r_sem_sessao = client.post("/api/confirmar_acao", json={"id_confirmacao": "fake"}, headers=auth_headers)
    assert r_sem_sessao.status_code == 400, f"Esperado 400 por ausência de sessão, obtido {r_sem_sessao.status_code}"

    # 5. Requisições autenticadas com X-Jarvis-Token são aceitas
    r_pend_auth = client.get("/api/acoes_pendentes?sessao=sessao-valida", headers=auth_headers)
    assert r_pend_auth.status_code == 200, f"Esperado 200 com token, obtido {r_pend_auth.status_code}"
    print(" [✔ PASS] Autenticação Obrigatória nos Endpoints ADK (401 sem token / 400 sem sessão)")


def test_autenticacao_e_confirmacao_live_adk_por_texto():
    """Valida handshake autenticado e aprovação por texto digitado durante sessão Live ADK."""
    from server import app, JARVIS_SECRET_TOKEN
    client = TestClient(app)
    session_id = "sessao-live-texto-test"
    user_id = "local"

    # Cria ação pendente na sessão de teste
    args = {"url": "https://brave.com"}
    pending = policy_engine.create_pending_action("abrir_site", args, session_id=session_id, user_id=user_id)
    assert pending.status == "pending"

    # Conexão não autorizada ao WebSocket -> Rejeitada com 1008
    with client.websocket_connect(f"/ws/live_adk?sessao={session_id}&usuario={user_id}") as ws_unauth:
        ws_unauth.send_json({"type": "init", "token": "token-falso-invalido"})
        msg_err = ws_unauth.receive_json()
        assert msg_err.get("tipo") == "erro"

    # Conexão autorizada com handshake
    try:
        with client.websocket_connect(f"/ws/live_adk?sessao={session_id}&usuario={user_id}") as ws:
            ws.send_json({"type": "init", "token": JARVIS_SECRET_TOKEN})
            msg_ready = ws.receive_json()
            assert msg_ready.get("tipo") == "pronto"

            # Envia texto 'sim' digitado na sessão Live
            ws.send_json({"tipo": "texto", "texto": "sim"})
            msg_confirm = ws.receive_json()
            assert msg_confirm.get("tipo") == "acao_aprovada"
            assert msg_confirm.get("action_id") == pending.action_id
            ws.close()
    except (concurrent.futures.CancelledError, asyncio.CancelledError):
        pass

    # Comprova que a aprovação refletiu no PolicyEngine e consome o token (one-shot)
    consumido = policy_engine.consume_authorization("abrir_site", args, session_id=session_id, user_id=user_id)
    assert consumido is True
    print(" [✔ PASS] Handshake Seguro no Live ADK e Confirmação Digitada no Live")


def test_confirmacao_comportamental_voz_live_adk():
    """Valida o fluxo comportamental completo de aprovação por COMANDO DE VOZ via Gemini Live."""
    from server import app, JARVIS_SECRET_TOKEN, obter_runner_adk
    client = TestClient(app)
    session_id = "sessao-live-voice-test"
    user_id = "usuario-voz"

    # 1. Cria ação sensível pendente vinculada à sessão e usuário
    args = {"url": "https://github.com"}
    pending = policy_engine.create_pending_action("abrir_site", args, session_id=session_id, user_id=user_id)
    assert pending.status == "pending"

    # 2. Cria mock do evento de transcrição gerado pelo Gemini Live
    class MockAudioTranscription:
        text = "sim, pode autorizar"

    class MockLiveEvent:
        input_transcription = MockAudioTranscription()
        output_transcription = None
        content = None
        interrupted = False
        turn_complete = False

    async def mock_run_live(*args_live, **kwargs_live):
        yield MockLiveEvent()

    runner = obter_runner_adk("voz")
    with patch.object(runner, "run_live", side_effect=mock_run_live):
        try:
            with client.websocket_connect(f"/ws/live_adk?sessao={session_id}&usuario={user_id}") as ws:
                ws.send_json({"type": "init", "token": JARVIS_SECRET_TOKEN})
                msg_ready = ws.receive_json()
                assert msg_ready.get("tipo") == "pronto"

                # 3. O WebSocket recebe a transcrição do usuário e a mensagem de aprovação por voz
                msg_trans = ws.receive_json()
                assert msg_trans.get("tipo") == "transcricao_usuario"
                assert "sim" in msg_trans.get("texto", "").lower()

                msg_aprovada = ws.receive_json()
                assert msg_aprovada.get("tipo") == "acao_aprovada"
                assert msg_aprovada.get("origem") == "voz"
                assert msg_aprovada.get("action_id") == pending.action_id
                assert pending.tool_name in msg_aprovada.get("mensagem", "")
                ws.close()
        except (concurrent.futures.CancelledError, asyncio.CancelledError):
            pass

    # 4. Comprova que o PolicyEngine aprovou e consome o token one-shot
    consumido = policy_engine.consume_authorization("abrir_site", args, session_id=session_id, user_id=user_id)
    assert consumido is True, "Ação autorizada por comando de voz não pôde ser consumida!"
    print(" [✔ PASS] Confirmação Comportamental por Comando de VOZ no Live ADK (origem: voz)")


def test_smoke_servidor_adk_standalone():
    """Valida que servidor_adk.py funciona de forma standalone sem falhas de importação em runtime."""
    import servidor_adk
    assert hasattr(servidor_adk, "app")
    assert hasattr(servidor_adk, "verify_jarvis_token")
    client = TestClient(servidor_adk.app)
    resp_saude = client.get("/api/health")
    assert resp_saude.status_code == 200
    assert resp_saude.json().get("status") == "online"

    resp_auth = client.get("/api/auth/session")
    assert resp_auth.status_code == 200
    assert "token" in resp_auth.json()
    print(" [✔ PASS] Smoke Test servidor_adk.py Standalone (Runtime e Endpoints)")


def executar_todos_testes_adk():
    print("=== EXECUTANDO TESTES DO GOOGLE ADK & POLICY GATE (FASE P0.17) ===")
    test_roteador_inteligente()
    test_ausencia_de_auto_autorizacao_no_llm()
    test_policy_engine_bloqueio_e_one_shot()
    test_isolamento_estrito_sessao_e_usuario()
    test_autenticacao_http_endpoints_adk()
    test_autenticacao_e_confirmacao_live_adk_por_texto()
    test_confirmacao_comportamental_voz_live_adk()
    test_smoke_servidor_adk_standalone()
    print("Todos os 8 cenários do módulo ADK P0.17 passaram com 100% de conformidade!")

if __name__ == "__main__":
    executar_todos_testes_adk()
