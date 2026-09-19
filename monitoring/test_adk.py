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
8. Smoke test do server.py standalone (app unificada) garantindo ausência de erros de importação em runtime.
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

# As suítes abrem vários TestClient(app) em sequência, e cada um cria e destrói
# o próprio event loop. O DatabaseSessionService é um singleton de módulo criado
# no import de server.py, então o engine aiosqlite fica preso
# ao primeiro loop: quando o segundo TestClient sobe, a worker thread do aiosqlite
# chama call_soon_threadsafe num loop já fechado e o pool do SQLAlchemy despeja
# "Event loop is closed", "no active connection" e avisos de coleta de lixo no
# stderr. Nenhum cenário depende de persistência real em disco, e usar o banco de
# verdade ainda faria os testes gravarem em sessoes.db. Uma URL explícita no
# ambiente continua tendo prioridade.
os.environ.setdefault("SESSION_DB_URL", "memoria")

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

    # Cria ação pendente na sessão de teste (identity server-side: user_id = sessão)
    args = {"url": "https://brave.com"}
    pending = policy_engine.create_pending_action("abrir_site", args, session_id=session_id, user_id=session_id)
    assert pending.status == "pending"

    # Conexão não autorizada ao WebSocket -> Rejeitada com 1008
    with client.websocket_connect(f"/ws/live_adk?sessao={session_id}&usuario=local&origem=teste") as ws_unauth:
        ws_unauth.send_json({"type": "init", "token": "token-falso-invalido"})
        msg_err = ws_unauth.receive_json()
        assert msg_err.get("tipo") == "erro"

    # Conexão autorizada com handshake
    try:
        with client.websocket_connect(f"/ws/live_adk?sessao={session_id}&usuario=local") as ws:
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
    consumido = policy_engine.consume_authorization("abrir_site", args, session_id=session_id, user_id=session_id)
    assert consumido is True
    print(" [✔ PASS] Handshake Seguro no Live ADK e Confirmação Digitada no Live")


def test_confirmacao_comportamental_voz_live_adk():
    """Valida o fluxo comportamental completo de aprovação por COMANDO DE VOZ via Gemini Live."""
    from server import app, JARVIS_SECRET_TOKEN, obter_runner_adk
    client = TestClient(app)
    session_id = "sessao-live-voice-test"

    # 1. Cria ação sensível pendente vinculada à sessão (identity server-side)
    args = {"url": "https://github.com"}
    pending = policy_engine.create_pending_action("abrir_site", args, session_id=session_id, user_id=session_id)
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
            with client.websocket_connect(f"/ws/live_adk?sessao={session_id}&usuario=local") as ws:
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
    consumido = policy_engine.consume_authorization("abrir_site", args, session_id=session_id, user_id=session_id)
    assert consumido is True, "Ação autorizada por comando de voz não pôde ser consumida!"
    print(" [✔ PASS] Confirmação Comportamental por Comando de VOZ no Live ADK (origem: voz)")


def test_smoke_servidor_standalone():
    """Valida que server.py funciona de forma standalone sem falhas de importação em runtime."""
    import server
    assert hasattr(server, "app")
    assert hasattr(server, "verify_jarvis_token")
    assert hasattr(server, "obter_runner")
    client = TestClient(server.app)
    resp_saude = client.get("/api/health")
    assert resp_saude.status_code == 200
    assert resp_saude.json().get("status") == "online"
    assert "mcp_servers" in resp_saude.json()

    resp_auth = client.get("/api/auth/session")
    assert resp_auth.status_code == 200
    assert "token" in resp_auth.json()
    print(" [✔ PASS] Smoke Test server.py Standalone (App Unificada e Endpoints)")


def test_confirmacao_chat_http_texto_com_isolamento_user_id():
    """Valida que POST /api/chat ('sim') aprova a pendência usando identity server-side.

    Identity = sessão (user_id derivado do servidor): o campo 'usuario' do client é
    ignorado. Sessões diferentes não aprovam pendências umas das outras (P0.17.1).
    """
    import server
    from unittest.mock import patch, MagicMock

    auth_headers = {"X-Jarvis-Token": server.JARVIS_SECRET_TOKEN}

    # 1. Teste na app unificada (server.py)
    client_server = TestClient(server.app)
    sess_1 = "sessao-chat-srv"
    args_1 = {"url": "https://server.com"}
    pending_1 = policy_engine.create_pending_action("abrir_site", args_1, session_id=sess_1, user_id=sess_1)
    assert pending_1.status == "pending"

    async def mock_run_async(*a, **kw):
        if False:
            yield

    mock_runner_obj = MagicMock()
    mock_runner_obj.run_async = mock_run_async

    with patch("server.obter_runner_adk", return_value=mock_runner_obj):
        resp_1 = client_server.post(
            "/api/chat",
            json={"texto": "sim", "sessao": sess_1, "usuario": "usuario-impostor-ignorado"},
            headers=auth_headers
        )
        assert resp_1.status_code == 200

    # Valida aprovação e consumo one-shot com identity derivada da sessão
    consumido_1 = policy_engine.consume_authorization("abrir_site", args_1, session_id=sess_1, user_id=sess_1)
    assert consumido_1 is True, "server.py /api/chat -> 'sim' falhou em aprovar ação com identity de sessão!"

    # 2. Teste de rejeição por incompatibilidade de sessão
    sess_3 = "sessao-chat-dono"
    sess_invasor = "sessao-chat-invasor"
    pending_3 = policy_engine.create_pending_action("abrir_site", args_1, session_id=sess_3, user_id=sess_3)

    with patch("server.obter_runner_adk", return_value=mock_runner_obj):
        client_server.post(
            "/api/chat",
            json={"texto": "sim", "sessao": sess_invasor, "usuario": sess_3},
            headers=auth_headers
        )
    # Sessão invasora não aprovou a pendência do dono real
    assert policy_engine.consume_authorization("abrir_site", args_1, session_id=sess_3, user_id=sess_3) is False

    print(" [✔ PASS] Confirmação via POST /api/chat ('sim') na app unificada com identity de sessão (P0.17.1)")


def test_todas_ferramentas_conectadas_ao_agente():
    """Garante que todas as 56 ferramentas do ecossistema estão conectadas ao agente ADK."""
    import system_tools
    from plugin_manager import plugin_manager
    from agentes.assistente import criar_agente_coordenador, criar_agente_de_voz, criar_agente_rapido
    from google.adk.tools import FunctionTool

    agente_coord = criar_agente_coordenador()
    agente_voz = criar_agente_de_voz()
    agente_rapido = criar_agente_rapido()

    # Extrai nomes das ferramentas registradas no coordenador
    nomes_coord = set()
    for t in agente_coord.tools:
        if isinstance(t, FunctionTool):
            nomes_coord.add(t.name)
        else:
            nomes_coord.add(getattr(t, "name", getattr(t, "__name__", t.__class__.__name__)))

    # 1. Todas as 29 ferramentas base do sistema precisam estar presentes
    for tool_name in system_tools.BASE_TOOL_REGISTRY.keys():
        assert tool_name in nomes_coord, f"Ferramenta base '{tool_name}' não está conectada ao agente coordenador!"

    # 2. As ferramentas dos plug-ins ativos precisam estar presentes e inativos não expostos
    total_plugins_tools = sum(len(p.get_tools()) for p in plugin_manager._plugins.values())
    assert total_plugins_tools == 27, f"Esperado 27 ferramentas de plug-ins no catálogo, encontrado {total_plugins_tools}"

    active_tools_names = {t.name for t in plugin_manager.get_active_tools()}
    for t_name in active_tools_names:
        assert t_name in nomes_coord, f"Ferramenta de plug-in ativo '{t_name}' não está conectada ao agente coordenador!"

    inactive_plugins = [p for p in plugin_manager._plugins.values() if not p.meta.enabled]
    for p in inactive_plugins:
        for t in p.get_tools():
            assert t.name not in nomes_coord, f"Ferramenta de plug-in inativo '{t.name}' ({p.meta.id}) vazou para o agente coordenador!"

    # 3. Valida schemas, nomes, descrições e cobertura de políticas no PolicyEngine
    for t in agente_coord.tools:
        if isinstance(t, FunctionTool):
            decl = t._get_declaration()
            assert decl is not None, f"Declaração nula para {t.name}"
            assert decl.name == t.name, f"Inconsistência de nome no schema ADK: {decl.name} != {t.name}"
            assert decl.description, f"Ferramenta '{t.name}' conectada sem descrição no schema ADK!"
            # Cobertura no PolicyEngine
            risk = policy_engine.get_risk_level(t.name)
            assert risk is not None, f"Ferramenta '{t.name}' conectada sem registro no PolicyEngine!"

    # 4. Agente de voz deve ter paridade total com o coordenador
    nomes_voz = {getattr(t, "name", getattr(t, "__name__", t.__class__.__name__)) for t in agente_voz.tools}
    assert nomes_coord == nomes_voz, "Divergência de ferramentas entre agente de voz e coordenador!"

    # 5. Agente rápido deve possuir ferramentas diretas essenciais
    nomes_rapido = {getattr(t, "name", getattr(t, "__name__", str(t))) for t in agente_rapido.tools}
    for essencial in ("get_system_status", "get_gpu_status", "adjust_volume", "list_installed_games"):
        assert essencial in nomes_rapido, f"Ferramenta essencial '{essencial}' ausente no agente rápido!"

    print(f" [✔ PASS] Conexão Total de Ferramentas: {len(nomes_coord)} ferramentas conectadas ao Agente (56 do ecossistema + especialistas)")


def test_compaction_config_e_app():
    """Valida que a fábrica unificada do server.py usa App com EventsCompactionConfig, cache e MemoryService."""
    from server import obter_runner_adk, obter_runner

    r_srv = obter_runner_adk("coordenador")
    assert r_srv.app is not None, "Runner deve ser instanciado via App"
    assert r_srv.app.events_compaction_config is not None, "App deve possuir EventsCompactionConfig"
    assert r_srv.app.events_compaction_config.token_threshold == 4000
    assert r_srv.app.events_compaction_config.event_retention_size == 5
    assert r_srv.app.context_cache_config is not None, "App deve possuir ContextCacheConfig"
    assert r_srv.app.context_cache_config.min_tokens == 2048
    assert r_srv.app.context_cache_config.ttl_seconds == 600
    assert r_srv.app.context_cache_config.cache_intervals == 5
    assert r_srv.memory_service is not None, "Runner deve possuir memory_service injetado"

    # obter_runner (alias canônico do servidor ADK antigo) aponta para a mesma fábrica
    r_adk = obter_runner("complexo")
    assert r_adk is r_srv, "obter_runner e obter_runner_adk devem servir o mesmo runner unificado"
    assert r_adk.app is not None, "Runner obter_runner deve ser instanciado via App"
    assert r_adk.app.events_compaction_config is not None
    assert r_adk.app.events_compaction_config.token_threshold == 4000
    assert r_adk.app.events_compaction_config.event_retention_size == 5
    assert r_adk.app.context_cache_config is not None
    assert r_adk.app.context_cache_config.min_tokens == 2048
    assert r_adk.app.context_cache_config.ttl_seconds == 600
    assert r_adk.app.context_cache_config.cache_intervals == 5
    assert r_adk.memory_service is not None, "Runner obter_runner deve possuir memory_service injetado"
    print(" [✔ PASS] Compactação de Contexto (EventsCompactionConfig), Cache (ContextCacheConfig) & Injeção de App na Fábrica Unificada")


def _executar_coro(coro):
    """Executa corrotina tanto fora quanto dentro de um loop de eventos já ativo."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(coro)).result()
    else:
        return asyncio.run(coro)


def test_memoria_longo_prazo_persistente():
    """Valida persistência em disco (JSON), recuperação e ranking de relevância no JarvisMemoryService."""
    import tempfile
    from agentes.memoria import JarvisMemoryService
    from google.adk.sessions import Session
    from google.adk.events import Event
    from google.genai.types import Content, Part

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name

    try:
        # 1. Instância A: adiciona sessão e persiste em disco
        ms_escrita = JarvisMemoryService(tmp_path)
        sess = Session(app_name="assistente", user_id="usuario_teste_mem", id="sess_mem_001")
        ev_u = Event(author="user", content=Content(parts=[Part(text="Meu autor preferido é Isaac Asimov")]))
        ev_m = Event(author="model", content=Content(parts=[Part(text="Registrado com sucesso: Isaac Asimov")]))
        sess.events.extend([ev_u, ev_m])

        _executar_coro(ms_escrita.add_session_to_memory(sess))
        assert os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0, "Arquivo de memória não foi criado no disco"

        # 2. Instância B: recarrega do disco de forma independente
        ms_leitura = JarvisMemoryService(tmp_path)
        res = _executar_coro(ms_leitura.search_memory(
            app_name="assistente",
            user_id="usuario_teste_mem",
            query="quem é meu autor preferido?"
        ))
        assert len(res.memories) > 0, "Nenhuma memória encontrada na busca semântica/palavras-chave"
        textos = [p.text for m in res.memories for p in m.content.parts if p.text]
        assert any("Isaac Asimov" in t for t in textos), f"Isaac Asimov não recuperado: {textos}"
        print(" [✔ PASS] Memória de Longo Prazo Persistente (JarvisMemoryService + busca entre sessões)")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_load_memory_tool_execution():
    """Valida execução assíncrona da ferramenta nativa load_memory conectada ao ToolContext."""
    import tempfile
    from agentes.memoria import JarvisMemoryService
    from google.adk.sessions import Session, InMemorySessionService
    from google.adk.events import Event
    from google.genai.types import Content, Part
    from google.adk.tools import load_memory
    from google.adk.tools.tool_context import ToolContext
    from google.adk.agents.invocation_context import InvocationContext

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name

    try:
        ms = JarvisMemoryService(tmp_path)
        sess = Session(app_name="assistente", user_id="usuario_tool_test", id="sess_tool_001")
        sess.events.append(Event(author="user", content=Content(parts=[Part(text="Minha linguagem favorita é Rust")])))
        _executar_coro(ms.add_session_to_memory(sess))

        inv_ctx = InvocationContext(
            invocation_id="inv_tool_test",
            session_service=InMemorySessionService(),
            session=sess,
            memory_service=ms,
        )
        tool_ctx = ToolContext(invocation_context=inv_ctx)
        resp = _executar_coro(load_memory.run_async(args={"query": "Rust"}, tool_context=tool_ctx))
        assert resp is not None, "Resposta da ferramenta load_memory não pode ser nula"
        assert len(resp.memories) > 0, "Deveria recuperar memórias sobre Rust"
        partes_texto = [p.text for m in resp.memories for p in m.content.parts if p.text]
        assert any("Rust" in t for t in partes_texto)
        print(" [✔ PASS] Ferramenta load_memory: Execução via ToolContext e recuperação de memórias passadas")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_politica_e_governanca_de_memoria():
    """Valida que load_memory e preload_memory são tratadas como READ seguro no PolicyEngine."""
    from policy_engine import RiskLevel

    decisao_load = policy_engine.evaluate("load_memory", {"query": "teste"})
    assert decisao_load.allowed is True, "load_memory deve ser permitida"
    assert decisao_load.requires_confirmation is False, "load_memory não exige confirmação"
    assert decisao_load.risk_level == RiskLevel.READ

    decisao_preload = policy_engine.evaluate("preload_memory", {})
    assert decisao_preload.allowed is True
    assert decisao_preload.requires_confirmation is False
    assert decisao_preload.risk_level == RiskLevel.READ
    print(" [✔ PASS] Governança de Memória: load_memory e preload_memory classificadas como READ automático")


def executar_todos_testes_adk():
    print("=== EXECUTANDO TESTES DO GOOGLE ADK & POLICY GATE (FASE P0.17.2) ===")
    test_roteador_inteligente()
    test_ausencia_de_auto_autorizacao_no_llm()
    test_policy_engine_bloqueio_e_one_shot()
    test_isolamento_estrito_sessao_e_usuario()
    test_autenticacao_http_endpoints_adk()
    test_autenticacao_e_confirmacao_live_adk_por_texto()
    test_confirmacao_comportamental_voz_live_adk()
    test_confirmacao_chat_http_texto_com_isolamento_user_id()
    test_smoke_servidor_standalone()
    test_todas_ferramentas_conectadas_ao_agente()
    test_compaction_config_e_app()
    test_memoria_longo_prazo_persistente()
    test_load_memory_tool_execution()
    test_politica_e_governanca_de_memoria()
    print("Todos os 14 cenários do módulo ADK P0.17.2 passaram com 100% de conformidade!")

if __name__ == "__main__":
    executar_todos_testes_adk()
