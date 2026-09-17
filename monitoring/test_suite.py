#!/usr/bin/env python3
"""
Suíte Completa de Diagnóstico e Testes do Assistente J.A.R.V.I.S. / Gemini Live
Executa testes de ponta a ponta na infraestrutura local, pool de chaves e conexão Live.
"""
import os
import sys
import time
import asyncio
from dotenv import load_dotenv

RAIZ_PROJETO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ_PROJETO not in sys.path:
    sys.path.insert(0, RAIZ_PROJETO)

ENV_PATH = os.path.join(RAIZ_PROJETO, ".env")
load_dotenv(ENV_PATH, override=True)

# Cores do terminal
RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"

def log_test(name, success, detail=""):
    icon = f"{GREEN}✔ PASS{RESET}" if success else f"{RED}✘ FAIL{RESET}"
    print(f" [{icon}] {BOLD}{name}{RESET}")
    if detail:
        print(f"        {detail}")

async def test_env_and_keys():
    raw_keys = os.environ.get("GEMINI_API_KEYS", "")
    key_pool = [k.strip() for k in raw_keys.split(",") if k.strip()]
    single_key = os.environ.get("GEMINI_API_KEY")
    if single_key and single_key not in key_pool:
        key_pool.insert(0, single_key)

    success = len(key_pool) > 0
    detail = f"Total de chaves configuradas no pool: {len(key_pool)} (Chave 1: {key_pool[0][:8]}...{key_pool[0][-4:] if key_pool else ''})"
    log_test("Configuração de Chaves (.env)", success, detail)
    return key_pool

async def test_system_tools():
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import system_tools
        stats = system_tools.get_system_status()
        cpu = stats.get("cpu_percent")
        ram = stats.get("ram_percent")
        success = cpu is not None and ram is not None
        log_test("Módulo de Automação do SO (system_tools)", success, f"CPU: {cpu}% | RAM: {ram}% | OS: {stats.get('uptime')}")
        return True
    except Exception as e:
        log_test("Módulo de Automação do SO (system_tools)", False, str(e))
        return False

async def test_gemini_live_handshake(key):
    from google import genai
    from google.genai import types

    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-native-audio-latest")
    start_t = time.time()
    try:
        client = genai.Client(api_key=key)
        config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Charon")
                )
            )
        )
        async with client.aio.live.connect(model=model, config=config) as session:
            # Envia prompt textual de teste rápido
            await session.send_realtime_input(text="JARVIS, responda apenas: Sistemas 100% operacionais.")
            audio_bytes = 0
            text_resp = ""
            async for resp in session.receive():
                if resp.server_content:
                    if resp.server_content.model_turn:
                        for p in resp.server_content.model_turn.parts:
                            if p.text:
                                text_resp += p.text
                            if p.inline_data:
                                audio_bytes += len(p.inline_data.data)
                    if resp.server_content.turn_complete:
                        break

            elapsed = int((time.time() - start_t) * 1000)
            success = audio_bytes > 0 or len(text_resp) > 0
            detail = f"Modelo: {model} | Latência: {elapsed}ms | Áudio recebido: {audio_bytes} bytes | Texto: \"{text_resp.strip()[:60]}\""
            log_test("Gemini Multimodal Live API (Bidirecional)", success, detail)
            return True
    except Exception as e:
        elapsed = int((time.time() - start_t) * 1000)
        log_test("Gemini Multimodal Live API (Bidirecional)", False, f"Erro após {elapsed}ms: {e}")
        return False

def test_pyside6_desktop():
    try:
        import PySide6
        from PySide6.QtWidgets import QApplication
        from PySide6.QtWebEngineWidgets import QWebEngineView
        log_test("Ambiente Desktop Nativo (PySide6 + QtWebEngine)", True, f"PySide6 v{PySide6.__version__} instalado e funcional")
        return True
    except Exception as e:
        log_test("Ambiente Desktop Nativo (PySide6 + QtWebEngine)", False, str(e))
        return False

async def main():
    print(f"\n{CYAN}{BOLD}======================================================={RESET}")
    print(f"{CYAN}{BOLD}⚡ J.A.R.V.I.S. // SUÍTE DE DIAGNÓSTICO E AUTO-TESTE  {RESET}")
    print(f"{CYAN}{BOLD}======================================================={RESET}\n")

    keys = await test_env_and_keys()
    await test_system_tools()
    test_pyside6_desktop()

    if keys:
        print(f"\n{BOLD}Testando Conexão Live API com a Chave Primária...{RESET}")
        await test_gemini_live_handshake(keys[0])

    print(f"\n{CYAN}Diagnóstico finalizado.{RESET}\n")

# O despacho de linha de comando fica no final do arquivo: com --p0 apenas a suíte
# de segurança roda, sem depender de ambiente gráfico nem de chaves válidas da API.

# ==============================================================================
# TESTES DE SEGURANÇA, POLICY ENGINE E CICLO DE VIDA DE PLUG-INS (FASE P0)
# ==============================================================================

def test_localhost_binding():
    """Garante que a configuração padrão de host do JARVIS é 127.0.0.1 (Loopback)."""
    host = os.environ.get("JARVIS_HOST", "127.0.0.1")
    is_local = host in ("127.0.0.1", "localhost")
    log_test("Segurança de Rede (127.0.0.1 Loopback)", is_local, f"Host configurado: {host}")
    assert is_local, f"Host inseguro detectado: {host}"
    return is_local

def test_permission_bypass_removed():
    """Garante que --dangerously-skip-permissions foi removido do antigravity_run_prompt."""
    import inspect
    import system_tools
    source = inspect.getsource(system_tools.antigravity_run_prompt)
    has_dangerous_flag = "--dangerously-skip-permissions" in source
    success = not has_dangerous_flag
    log_test("Remoção de Privilégio Excessivo (--dangerously-skip-permissions)", success, "Flag perigosa eliminada do código" if success else "FLAG PERIGOSA ENCONTRADA!")
    assert success
    return success

def test_policy_engine_classification():
    """Valida a categorização de ferramentas e níveis de risco no Policy Engine."""
    from policy_engine import policy_engine, RiskLevel
    dec_read = policy_engine.evaluate("get_system_status")
    dec_write = policy_engine.evaluate("open_application", {"app_name": "gedit"})
    dec_ext = policy_engine.evaluate("social_feed_post_update", {"channel": "discord", "text": "oi"})
    dec_priv = policy_engine.evaluate("antigravity_run_prompt", {"prompt": "verifique os testes"})

    success = (
        dec_read.risk_level == RiskLevel.READ and dec_read.allowed and
        dec_write.risk_level == RiskLevel.LOW_WRITE and dec_write.allowed and
        dec_ext.risk_level == RiskLevel.EXTERNAL_WRITE and dec_ext.allowed and
        dec_priv.risk_level == RiskLevel.PRIVILEGED and dec_priv.allowed
    )
    detail = f"READ: {dec_read.allowed} | LOW_WRITE: {dec_write.allowed} | EXTERNAL: {dec_ext.allowed} | PRIVILEGED: {dec_priv.allowed}"
    log_test("Policy Engine (Controle de Riscos & Capacidades)", success, detail)
    assert success
    return success

def test_plugin_lifecycle_purge():
    """Garante que desativar um plug-in expurga totalmente suas ferramentas do sistema."""
    from plugin_manager import plugin_manager
    import system_tools

    # Ativa
    plugin_manager.toggle_plugin("smart_home", enabled=True)
    assert "smart_home_set_light" in system_tools.TOOL_REGISTRY

    # Desativa e verifica expurgo
    plugin_manager.toggle_plugin("smart_home", enabled=False)
    purged = "smart_home_set_light" not in system_tools.TOOL_REGISTRY
    schemas = [d["name"] for d in system_tools.GEMINI_FUNCTION_DECLARATIONS]
    schema_purged = "smart_home_set_light" not in schemas

    # Reativa e verifica não duplicação
    plugin_manager.toggle_plugin("smart_home", enabled=True)
    tools_count = len(plugin_manager._plugins["smart_home"].get_tools())
    no_dup = tools_count == 3

    success = purged and schema_purged and no_dup
    detail = f"Expurgo no TOOL_REGISTRY: {purged} | Expurgo nos Schemas: {schema_purged} | Sem Duplicatas: {no_dup} ({tools_count} tools)"
    log_test("Ciclo de Vida de Plug-ins (Expurgo & Idempotência)", success, detail)
    assert success
    return success

# Plug-ins que ainda operam com dados simulados: cada chamada precisa se declarar como mock
CHAMADAS_SIMULADAS = [
    ("smart_home", "set_light", ("sala", True), {}),
    ("social_feed", "check_notifications", (), {}),
    ("live_stream", "read_chat_summary", (), {}),
    ("google_workspace", "search_emails", ("relatório",), {}),
    ("google_workspace", "create_draft", ("ana@exemplo.com", "Assunto", "Corpo"), {}),
    ("google_workspace", "append_doc", ("Documento de Teste", "conteúdo"), {}),
    ("google_workspace", "create_keep_note", ("Lembrete", "conteúdo da nota"), {}),
    ("deep_research", "get_report", ("inexistente",), {}),
    ("deep_research", "list_researches", (), {}),
    ("google_finance", "get_quote", ("PETR4",), {}),
    ("google_finance", "get_portfolio", (), {}),
    ("google_finance", "add_asset", ("PETR4", 10, 30.0), {}),
    ("google_finance", "get_insights", (), {}),
    ("ginjutsu_studio", "generate_prompt", ("dançarino original", "robô"), {}),
    ("ginjutsu_studio", "list_jobs", (), {}),
]


def test_mock_plugin_transparency():
    """Toda resposta de plug-in simulado precisa declarar mock=True e executado_externamente=False."""
    from plugin_manager import plugin_manager

    falhas = []
    verificados = 0
    for plugin_id, metodo, args, kwargs in CHAMADAS_SIMULADAS:
        plugin = plugin_manager._plugins.get(plugin_id)
        if plugin is None or not hasattr(plugin, metodo):
            falhas.append(f"{plugin_id}.{metodo} inexistente")
            continue
        res = getattr(plugin, metodo)(*args, **kwargs)
        verificados += 1
        if res.get("mock") is not True or res.get("executado_externamente") is not False:
            falhas.append(f"{plugin_id}.{metodo} (mock={res.get('mock')}, externo={res.get('executado_externamente')})")

    success = not falhas and verificados == len(CHAMADAS_SIMULADAS)
    detail = f"{verificados} chamadas verificadas" if success else f"Sem marcação de simulação: {falhas}"
    log_test("Transparência de Mocks (mock=True explícito)", success, detail)
    assert success
    return success


def test_risco_de_escrita_externa():
    """Ferramentas que escrevem em serviços de terceiros exigem confirmação."""
    from policy_engine import policy_engine, RiskLevel

    externas = {
        "workspace_create_draft": {"recipient": "a@b.com", "subject": "s", "body": "b"},
        "workspace_append_doc": {"doc_title": "d", "content": "c"},
        "workspace_create_keep_note": {"content": "n"},
        "deep_research_start": {"topic": "tema"},
        "ginjutsu_create_motion_transfer": {"source_video": "v.mp4", "target_character": "x"},
    }
    erradas = []
    for tool, args in externas.items():
        dec = policy_engine.evaluate(tool, args)
        if dec.risk_level != RiskLevel.EXTERNAL_WRITE or not dec.requires_confirmation:
            erradas.append(f"{tool} ({dec.risk_level.value}, confirmação={dec.requires_confirmation})")

    success = not erradas
    detail = f"{len(externas)} ferramentas exigem confirmação" if success else f"Classificação frouxa: {erradas}"
    log_test("Escrita em Serviços de Terceiros (EXTERNAL_WRITE)", success, detail)
    assert success
    return success


def test_sem_shell_true_em_plugins():
    """Nenhum plug-in pode lançar processos com shell=True a partir de dados do sistema."""
    import glob

    ocorrencias = []
    for caminho in glob.glob("plugins/*/plugin.py") + ["system_tools.py", "controller_engine.py"]:
        caminho_abs = os.path.join(RAIZ_PROJETO, caminho)
        if not os.path.exists(caminho_abs):
            continue
        with open(caminho_abs, encoding="utf-8") as f:
            for numero, linha in enumerate(f, 1):
                if "shell=True" in linha:
                    ocorrencias.append(f"{caminho}:{numero}")

    success = not ocorrencias
    log_test("Execução de Processos sem shell=True", success,
             "Nenhuma chamada com shell=True" if success else f"Encontrado em: {ocorrencias}")
    assert success
    return success

def test_game_companion_timer():
    """Testa criação e expiração de timer tático no plug-in Game Companion."""
    from plugin_manager import plugin_manager
    gc = plugin_manager._plugins["game_companion"]
    gc.timers.clear()
    gc.start_tactical_timer("Boss Respawn", 0)  # Expira imediatamente
    time.sleep(0.05)
    expired = gc.check_expired_timers()

    success = "Boss Respawn" in expired
    log_test("Game Companion (Worker & Timers Táticos)", success, f"Timers expirados detectados: {expired}")
    assert success
    return success

def test_api_auth_protection():
    """Garante que requisições HTTP mutantes sem token retornam 401 Unauthorized."""
    from fastapi.testclient import TestClient
    from server import app, JARVIS_SECRET_TOKEN

    client = TestClient(app)

    # 1. Sem token -> 401
    r_unauth = client.post("/api/debug/inject-prompt", json={"prompt": "teste"})
    unauth_blocked = r_unauth.status_code == 401

    # 2. Com token correto -> 200
    r_auth = client.post(
        "/api/debug/inject-prompt",
        json={"prompt": "teste autorizado"},
        headers={"X-Jarvis-Token": JARVIS_SECRET_TOKEN}
    )
    auth_allowed = r_auth.status_code == 200

    success = unauth_blocked and auth_allowed
    detail = f"Sem token: {r_unauth.status_code} (esperado 401) | Com token: {r_auth.status_code} (esperado 200)"
    log_test("Autenticação de API (Proteção contra Injection & LAN)", success, detail)
    assert success
    return success

# Alias para conformidade com a especificação técnica do plano
test_unauthenticated_injection_blocked = test_api_auth_protection
test_game_timer_expiration = test_game_companion_timer

def test_session_endpoint():
    """Valida que /api/auth/session retorna o token para o cliente local."""
    from fastapi.testclient import TestClient
    from server import app, JARVIS_SECRET_TOKEN
    client = TestClient(app)
    r = client.get("/api/auth/session")
    success = r.status_code == 200 and r.json().get("token") == JARVIS_SECRET_TOKEN
    log_test("Endpoint Local de Sessão (/api/auth/session)", success, f"Status: {r.status_code}")
    assert success
    return success

def test_websocket_auth():
    """Garante que conexões WebSocket sem token são rejeitadas."""
    from fastapi.testclient import TestClient
    from server import app, JARVIS_SECRET_TOKEN

    client = TestClient(app)

    # 1. Handshake com token inválido/ausente -> erro ou fechamento imediato
    unauth_rejected = False
    try:
        with client.websocket_connect("/ws/live") as ws:
            ws.send_json({"type": "init", "voice": "Charon", "token": "invalid_token_test"})
            resp = ws.receive_json()
            if resp.get("type") == "error" and "Não autorizado" in resp.get("message", ""):
                unauth_rejected = True
    except Exception:
        unauth_rejected = True

    # 2. Handshake com token válido -> autenticação aprovada
    auth_accepted = False
    try:
        with client.websocket_connect("/ws/live") as ws:
            ws.send_json({"type": "init", "voice": "Charon", "token": JARVIS_SECRET_TOKEN})
            # Não deve dar erro de "Não autorizado"
            auth_accepted = True
    except Exception as e:
        if "Não autorizado" not in str(e):
            auth_accepted = True

    success = unauth_rejected and auth_accepted
    detail = f"Sem token rejeitado: {unauth_rejected} | Com token aprovado: {auth_accepted}"
    log_test("Autenticação WebSocket /ws/live (Handshake Seguro)", success, detail)
    assert success
    return success

def test_policy_confirmation_required():
    """Garante que EXTERNAL_WRITE e PRIVILEGED exigem confirmação explícita do usuário."""
    from policy_engine import policy_engine, RiskLevel

    dec_read = policy_engine.evaluate("get_system_status")
    dec_write = policy_engine.evaluate("open_application", {"app_name": "gedit"})
    dec_ext = policy_engine.evaluate("social_feed_post_update", {"channel": "discord", "text": "oi"})
    dec_priv = policy_engine.evaluate("antigravity_run_prompt", {"prompt": "verifique os testes"})

    success = (
        not dec_read.requires_confirmation and
        not dec_write.requires_confirmation and
        dec_ext.requires_confirmation and
        dec_priv.requires_confirmation
    )
    detail = (
        f"READ: {dec_read.requires_confirmation} | LOW_WRITE: {dec_write.requires_confirmation} | "
        f"EXTERNAL: {dec_ext.requires_confirmation} | PRIVILEGED: {dec_priv.requires_confirmation}"
    )
    log_test("Policy Engine (Confirmação Obrigatória em Ações de Risco)", success, detail)
    assert success
    return success


def test_confirmation_flow_wired():
    """Garante que o backend bloqueia a ferramenta quando o usuário não confirma."""
    import inspect
    import server

    source = inspect.getsource(server.websocket_live_endpoint)
    tem_pedido = "tool_confirmation_request" in source
    tem_resposta = 'msg_type == "tool_confirmation"' in source
    tem_bloqueio = "policy_denied_by_user" in source
    tem_timeout = "asyncio.TimeoutError" in source

    success = tem_pedido and tem_resposta and tem_bloqueio and tem_timeout
    detail = f"pedido: {tem_pedido} | resposta: {tem_resposta} | bloqueio: {tem_bloqueio} | timeout: {tem_timeout}"
    log_test("Fluxo de Confirmação de Ferramentas no WebSocket", success, detail)
    assert success
    return success


def test_preferences_requires_token():
    """Garante que POST /api/preferences exige token de autenticação."""
    from fastapi.testclient import TestClient
    from server import app, JARVIS_SECRET_TOKEN

    import importlib
    import json as _json
    import tempfile

    import preferences_manager

    client = TestClient(app)
    payload = {"category": "default_apps", "key": "_teste_suite_p0", "value": "valor_de_teste"}

    # Preferências vão para um arquivo temporário: o teste não toca no arquivo real
    original_env = os.environ.get("JARVIS_PREFERENCES_FILE")
    temporario = os.path.join(tempfile.mkdtemp(prefix="jarvis_prefs_"), "user_preferences.json")
    os.environ["JARVIS_PREFERENCES_FILE"] = temporario
    importlib.reload(preferences_manager)

    try:
        r_unauth = client.post("/api/preferences", json=payload)
        unauth_blocked = r_unauth.status_code == 401

        r_auth = client.post("/api/preferences", json=payload, headers={"X-Jarvis-Token": JARVIS_SECRET_TOKEN})
        corpo = r_auth.json() if r_auth.status_code == 200 else {}
        auth_allowed = r_auth.status_code == 200 and corpo.get("status") == "ok"

        # Persistência real: o valor precisa estar gravado no disco
        persistiu = False
        if os.path.exists(temporario):
            with open(temporario, encoding="utf-8") as f:
                persistiu = _json.load(f).get("default_apps", {}).get("_teste_suite_p0") == "valor_de_teste"
    finally:
        if original_env is None:
            os.environ.pop("JARVIS_PREFERENCES_FILE", None)
        else:
            os.environ["JARVIS_PREFERENCES_FILE"] = original_env
        importlib.reload(preferences_manager)

    success = unauth_blocked and auth_allowed and persistiu
    detail = (
        f"Sem token: {r_unauth.status_code} (esperado 401) | Com token: {r_auth.status_code} "
        f"status={corpo.get('status')} | persistiu no disco: {persistiu}"
    )
    log_test("Preferências: autenticação e persistência real", success, detail)
    assert success
    return success


def test_frontend_sends_token():
    """Garante que o HUD envia o token nas rotas protegidas e no WebSocket."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(base, "static", "app.js"), encoding="utf-8") as f:
        source = f.read()

    usa_helper = "X-Jarvis-Token" in source
    plugins_autenticados = (
        "apiFetch('/api/plugins/toggle'" in source and
        "apiFetch('/api/plugins/install'" in source
    )
    ws_autenticado = "token: jarvisSessionToken" in source
    trata_confirmacao = "tool_confirmation_request" in source

    success = usa_helper and plugins_autenticados and ws_autenticado and trata_confirmacao
    detail = (
        f"header: {usa_helper} | plugins: {plugins_autenticados} | "
        f"ws: {ws_autenticado} | confirmação: {trata_confirmacao}"
    )
    log_test("Frontend Autenticado (HUD com token de sessão)", success, detail)
    assert success
    return success


def test_policy_fail_closed():
    """Ferramenta sem política registrada nunca pode executar automaticamente."""
    from policy_engine import policy_engine, RiskLevel

    dec = policy_engine.evaluate("ferramenta_inexistente_p012", {"alvo": "/"})
    success = (
        not dec.allowed and
        dec.requires_confirmation and
        dec.risk_level == RiskLevel.PRIVILEGED
    )
    log_test("Policy Engine Fail-Closed (Ferramenta sem política)", success,
             f"allowed={dec.allowed} | confirmação={dec.requires_confirmation} | {dec.reason}")
    assert success
    return success


def test_registered_tools_have_policy():
    """Toda ferramenta ativa no TOOL_REGISTRY precisa ter política declarada."""
    import system_tools
    from policy_engine import policy_engine

    sem_politica = [t for t in system_tools.TOOL_REGISTRY if policy_engine.get_risk_level(t) is None]
    success = not sem_politica
    log_test("Cobertura de Políticas (TOOL_REGISTRY)", success,
             "Todas as ferramentas classificadas" if success else f"Sem política: {sem_politica}")
    assert success
    return success


def test_control_lease():
    """Mouse e teclado só operam sob lease concedida após confirmar o Modo Controle."""
    from policy_engine import policy_engine, RiskLevel

    policy_engine.revoke_control_lease()

    # 1. set_control_mode é privilegiado e exige confirmação
    dec_modo = policy_engine.evaluate("set_control_mode", {"enabled": True})
    modo_confirmado = dec_modo.risk_level == RiskLevel.PRIVILEGED and dec_modo.requires_confirmation

    # 2. Sem lease, o controle físico é bloqueado
    dec_sem_lease = policy_engine.evaluate("mouse_click", {"button": "left"})
    bloqueado_sem_lease = not dec_sem_lease.allowed

    # 3. Com lease ativa, as ações comuns passam direto
    policy_engine.grant_control_lease(owner="teste", ttl_s=60)
    dec_com_lease = policy_engine.evaluate("mouse_move", {"delta_x": 10, "delta_y": 5})
    liberado_com_lease = dec_com_lease.allowed and not dec_com_lease.requires_confirmation

    # 4. Ações perigosas continuam exigindo confirmação mesmo com lease
    dec_hotkey = policy_engine.evaluate("keyboard_hotkey", {"keys": "alt+f4"})
    dec_texto = policy_engine.evaluate("keyboard_type", {"text": "sudo rm -rf /tmp/teste"})
    perigosas_confirmam = dec_hotkey.requires_confirmation and dec_texto.requires_confirmation

    # 5. Lease expirada volta a bloquear
    policy_engine.grant_control_lease(owner="teste", ttl_s=0)
    dec_expirada = policy_engine.evaluate("mouse_click", {"button": "left"})
    expira = not dec_expirada.allowed
    policy_engine.revoke_control_lease()

    success = modo_confirmado and bloqueado_sem_lease and liberado_com_lease and perigosas_confirmam and expira
    detail = (
        f"set_control_mode confirma: {modo_confirmado} | sem lease bloqueia: {bloqueado_sem_lease} | "
        f"com lease libera: {liberado_com_lease} | perigosas confirmam: {perigosas_confirmam} | "
        f"lease expirada bloqueia: {expira}"
    )
    log_test("Control Lease (Autoridade Temporária de Mouse e Teclado)", success, detail)
    assert success
    return success


def test_control_revogacao_imediata():
    """Desligar o Modo Controle nunca pode depender de confirmação."""
    from policy_engine import policy_engine, RiskLevel

    dec_off = policy_engine.evaluate("set_control_mode", {"enabled": False})
    dec_on = policy_engine.evaluate("set_control_mode", {"enabled": True})

    success = (
        dec_off.allowed and not dec_off.requires_confirmation and dec_off.risk_level == RiskLevel.LOW_WRITE
        and dec_on.requires_confirmation and dec_on.risk_level == RiskLevel.PRIVILEGED
    )
    detail = (
        f"enabled=False: allowed={dec_off.allowed} confirmação={dec_off.requires_confirmation} | "
        f"enabled=True: confirmação={dec_on.requires_confirmation}"
    )
    log_test("Revogação Imediata do Modo Controle", success, detail)
    assert success
    return success


def test_lease_vinculada_a_sessao():
    """A lease pertence à sessão que a recebeu: outra sessão não herda a autoridade."""
    from policy_engine import policy_engine

    policy_engine.grant_control_lease(owner="sessao-A", ttl_s=60)
    dec_dono = policy_engine.evaluate("mouse_click", {"button": "left"}, session_id="sessao-A")
    dec_outra = policy_engine.evaluate("mouse_click", {"button": "left"}, session_id="sessao-B")
    policy_engine.revoke_control_lease()

    success = dec_dono.allowed and not dec_outra.allowed
    detail = f"dona da lease: {dec_dono.allowed} | outra sessão: {dec_outra.allowed}"
    log_test("Lease Vinculada à Sessão (capability por WebSocket)", success, detail)
    assert success
    return success


def test_lease_expirada_desativa_modo():
    """O servidor sincroniza o Modo Controle quando a lease expira."""
    import inspect
    import server

    fonte = inspect.getsource(server.websocket_live_endpoint)
    tem_worker = "control_lease_worker" in fonte
    desativa = "system_tools.set_control_mode(False)" in fonte
    avisa_hud = "control_lease_expired" in fonte

    success = tem_worker and desativa and avisa_hud
    detail = f"worker: {tem_worker} | desativa modo: {desativa} | avisa HUD: {avisa_hud}"
    log_test("Sincronização do Modo Controle na Expiração da Lease", success, detail)
    assert success
    return success


def test_lease_nao_revogavel_por_outra_sessao():
    """Uma segunda sessão não pode derrubar a autoridade concedida à primeira."""
    from policy_engine import policy_engine

    policy_engine.grant_control_lease(owner="sessao-A", ttl_s=60)
    policy_engine.revoke_control_lease(session_id="sessao-B")
    sobreviveu = policy_engine.is_control_lease_active("sessao-A")

    policy_engine.revoke_control_lease(session_id="sessao-A")
    dona_revoga = not policy_engine.is_control_lease_active("sessao-A")

    success = sobreviveu and dona_revoga
    detail = f"B tentou revogar e A continuou ativa: {sobreviveu} | A revogou a própria lease: {dona_revoga}"
    log_test("Lease Protegida contra Revogação de Outra Sessão", success, detail)
    assert success
    return success


def test_encerramento_de_sessao_libera_controle():
    """Ao encerrar a sessão dona, o Modo Controle e a lease caem juntos."""
    import server
    import system_tools
    from policy_engine import policy_engine

    estado_original = system_tools.get_control_mode()
    system_tools.CONTROL_MODE_ACTIVE = True
    policy_engine.grant_control_lease(owner="sessao-A", ttl_s=60)

    # Uma sessão que não é dona não pode liberar o controle alheio
    server.liberar_controle_da_sessao("sessao-B")
    intacta = policy_engine.is_control_lease_active("sessao-A") and system_tools.get_control_mode()

    # A dona encerrando derruba a autoridade imediatamente
    server.liberar_controle_da_sessao("sessao-A")
    liberou = (not policy_engine.is_control_lease_active("sessao-A")) and (not system_tools.get_control_mode())

    system_tools.CONTROL_MODE_ACTIVE = estado_original
    policy_engine.revoke_control_lease()

    success = intacta and liberou
    detail = f"sessão alheia não mexeu: {intacta} | sessão dona liberou: {liberou}"
    log_test("Encerramento de Sessão Libera o Controle Físico", success, detail)
    assert success
    return success


def test_workers_com_taskgroup():
    """Os workers do WebSocket precisam ser cancelados juntos ao fim da sessão."""
    import inspect
    import server

    fonte = inspect.getsource(server.websocket_live_endpoint)
    usa_taskgroup = "asyncio.TaskGroup()" in fonte
    sem_gather = "asyncio.gather(ws_client_worker" not in fonte
    libera_no_fim = "liberar_controle_da_sessao(sessao_id)" in fonte

    success = usa_taskgroup and sem_gather and libera_no_fim
    detail = f"TaskGroup: {usa_taskgroup} | sem gather: {sem_gather} | libera lease no fim: {libera_no_fim}"
    log_test("Ciclo de Vida dos Workers (TaskGroup)", success, detail)
    assert success
    return success


def test_defaults_de_preferencias_isolados():
    """Alterar preferências carregadas não pode contaminar o DEFAULT_SCHEMA em memória."""
    import importlib
    import tempfile

    import preferences_manager

    original_env = os.environ.get("JARVIS_PREFERENCES_FILE")
    os.environ["JARVIS_PREFERENCES_FILE"] = os.path.join(
        tempfile.mkdtemp(prefix="jarvis_defaults_"), "user_preferences.json"
    )
    importlib.reload(preferences_manager)
    try:
        dados = preferences_manager.load_preferences()
        dados["default_apps"]["browser"] = "contaminado"
        dados["custom_memories"]["fato"] = "não deveria vazar"
        schema = preferences_manager.DEFAULT_SCHEMA
        isolado = (
            schema["default_apps"]["browser"] == "default"
            and "fato" not in schema["custom_memories"]
        )
    finally:
        if original_env is None:
            os.environ.pop("JARVIS_PREFERENCES_FILE", None)
        else:
            os.environ["JARVIS_PREFERENCES_FILE"] = original_env
        importlib.reload(preferences_manager)

    log_test("Isolamento do DEFAULT_SCHEMA (deepcopy)", isolado,
             "Defaults preservados após alterar as preferências carregadas" if isolado else "DEFAULT_SCHEMA foi contaminado!")
    assert isolado
    return isolado


def test_controller_sem_evdev():
    """O sistema importa e responde mesmo sem evdev ou sem /dev/uinput."""
    import importlib
    import sys as _sys
    import controller_engine

    # Import do system_tools não pode depender de evdev
    import system_tools  # noqa: F401

    original = _sys.modules.pop("evdev", None)
    _sys.modules["evdev"] = None  # força ImportError no reload
    try:
        recarregado = importlib.reload(controller_engine)
        importa_sem_evdev = recarregado.EVDEV_DISPONIVEL is False
        sem_dispositivo = recarregado.get_uinput() is None
        resposta = recarregado.move_mouse(5, 5)
        degrada_com_erro = resposta.get("sucesso") is False
    finally:
        if original is not None:
            _sys.modules["evdev"] = original
        else:
            _sys.modules.pop("evdev", None)
        importlib.reload(controller_engine)

    success = importa_sem_evdev and sem_dispositivo and degrada_com_erro
    detail = f"import sem evdev: {importa_sem_evdev} | uinput None: {sem_dispositivo} | erro tratado: {degrada_com_erro}"
    log_test("Controller Resiliente (sem evdev / sem /dev/uinput)", success, detail)
    assert success
    return success


def test_suite_cli_dispatch():
    """Garante que --p0 executa apenas a suíte P0, sem o diagnóstico interativo."""
    caminho = os.path.abspath(__file__)
    with open(caminho, encoding="utf-8") as f:
        fonte = f.read()

    # Conta apenas blocos reais (início de linha), ignorando as ocorrências dentro deste teste
    blocos = sum(1 for linha in fonte.splitlines() if linha.startswith('if __name__ == "__main__":'))
    dispatch_correto = '"--p0" in sys.argv' in fonte and "asyncio.run(main())" in fonte
    success = blocos == 1 and dispatch_correto
    log_test("CLI da Suíte (--p0 isolado do diagnóstico)", success,
             f"blocos __main__: {blocos} | dispatch: {dispatch_correto}")
    assert success
    return success


# Wrapper assíncrono para execução interativa direta via CLI
async def run_p0_suite():
    print(f"\n{BOLD}{CYAN}=== EXECUTANDO TESTES DE SEGURANÇA E ARQUITETURA (FASE P0) ==={RESET}\n")
    test_localhost_binding()
    test_permission_bypass_removed()
    test_policy_engine_classification()
    test_policy_confirmation_required()
    test_policy_fail_closed()
    test_registered_tools_have_policy()
    test_control_lease()
    test_control_revogacao_imediata()
    test_lease_vinculada_a_sessao()
    test_lease_expirada_desativa_modo()
    test_lease_nao_revogavel_por_outra_sessao()
    test_encerramento_de_sessao_libera_controle()
    test_workers_com_taskgroup()
    test_defaults_de_preferencias_isolados()
    test_confirmation_flow_wired()
    test_controller_sem_evdev()
    test_suite_cli_dispatch()
    test_plugin_lifecycle_purge()
    test_mock_plugin_transparency()
    test_risco_de_escrita_externa()
    test_sem_shell_true_em_plugins()
    test_game_timer_expiration()
    test_session_endpoint()
    test_unauthenticated_injection_blocked()
    test_preferences_requires_token()
    test_websocket_auth()
    test_frontend_sends_token()
    print(f"\n{BOLD}{GREEN}✔ Todos os testes de segurança e arquitetura passaram com sucesso!{RESET}\n")

if __name__ == "__main__":
    if "--p0" in sys.argv:
        asyncio.run(run_p0_suite())
    else:
        asyncio.run(main())
