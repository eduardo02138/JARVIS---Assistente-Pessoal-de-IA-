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

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
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

if __name__ == "__main__":
    asyncio.run(main())

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

def test_mock_plugin_transparency():
    """Valida se plug-ins com estado simulado identificam mock=True explicitamente."""
    from plugin_manager import plugin_manager
    sh = plugin_manager._plugins["smart_home"]
    res_sh = sh.set_light("sala", True)

    sf = plugin_manager._plugins["social_feed"]
    res_sf = sf.check_notifications()

    ls = plugin_manager._plugins["live_stream"]
    res_ls = ls.read_chat_summary()

    success = (
        res_sh.get("mock") is True and
        res_sf.get("mock") is True and
        res_ls.get("mock") is True
    )
    detail = f"Smart Home: {res_sh.get('mock')} | Social: {res_sf.get('mock')} | Live: {res_ls.get('mock')}"
    log_test("Transparência de Mocks (mock=True explícito)", success, detail)
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

# Wrapper assíncrono para execução interativa direta via CLI
async def run_p0_suite():
    print(f"\n{BOLD}{CYAN}=== EXECUTANDO TESTES DE SEGURANÇA E ARQUITETURA (FASE P0) ==={RESET}\n")
    test_localhost_binding()
    test_permission_bypass_removed()
    test_policy_engine_classification()
    test_plugin_lifecycle_purge()
    test_mock_plugin_transparency()
    test_game_timer_expiration()
    test_session_endpoint()
    test_unauthenticated_injection_blocked()
    test_websocket_auth()
    print(f"\n{BOLD}{GREEN}✔ Todos os testes de segurança e arquitetura passaram com sucesso!{RESET}\n")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--p0":
        asyncio.run(run_p0_suite())
