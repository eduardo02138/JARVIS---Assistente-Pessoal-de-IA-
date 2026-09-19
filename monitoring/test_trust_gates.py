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

import asyncio
import inspect
import json
import os
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
    """Gate 1: Uma sessão jamais pode aprovar a ação pendente de outra sessão.

    Identity é server-side: user_id deriva da sessão autorizada; o campo 'user_id'
    do client é ignorado. Aprovação legítima exige a sessão dona da pendência.
    """
    client = TestClient(app)
    policy_engine.cleanup_expired_actions()

    # Sessão A cria pendência legítima (user_id derivado = sessão)
    pending = policy_engine.create_pending_action(
        tool_name="open_website",
        args={"url": "https://alvo-seguro.local"},
        session_id="sessao_A",
        user_id="sessao_A",
    )
    action_id = pending.action_id

    # 1. Sessão B tenta aprovar ação de A
    resp_b = client.post(
        "/api/confirmar_acao",
        json={"action_id": action_id, "session_id": "sessao_B", "user_id": "sessao_A"},
        headers={"X-Jarvis-Token": JARVIS_SECRET_TOKEN},
    )
    assert resp_b.status_code in (400, 403, 404), (
        f"FALHA GATE 1: Sessão B conseguiu resposta HTTP {resp_b.status_code} para ação de A: {resp_b.text}"
    )
    assert pending.status == "pending", "FALHA GATE 1: Status da ação foi alterado por Sessão B!"

    # 2. Sessão C alega o mesmo user_id do dono; identity vem da sessão, não do client
    resp_usr = client.post(
        "/api/confirmar_acao",
        json={"action_id": action_id, "session_id": "sessao_C", "user_id": "sessao_A"},
        headers={"X-Jarvis-Token": JARVIS_SECRET_TOKEN},
    )
    assert resp_usr.status_code in (400, 403, 404), (
        f"FALHA GATE 1: Sessão C conseguiu aprovar ação da sessão A alegando user_id dela: {resp_usr.text}"
    )
    assert pending.status == "pending", "FALHA GATE 1: Status da ação foi alterado por sessão divergente!"

    # 3. Aprovação legítima pela Sessão A (user_id do client é irrelevante)
    resp_ok = client.post(
        "/api/confirmar_acao",
        json={"action_id": action_id, "session_id": "sessao_A", "user_id": "usuario_impostor"},
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


def test_gate5_widget_setprovider_contracto_executado_em_node():
    """Gate 5 (comportamental): setProvider roda de VERDADE em Node VM.

    Substitui o source-grep puro por execução: carrega widget.js num sandbox
    node:vm, captura o fetch, e comprova que a mutação de provedor envia
    /api/providers/select com X-Jarvis-Token do /api/auth/session.
    """
    import shutil
    import tempfile
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node não disponível no ambiente")

    harness = r"""
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const src = fs.readFileSync(process.env.WIDGET_PATH, "utf8");

const fetchCalls = [];
const ctx2d = { clearRect(){}, fillRect(){}, arc(){}, fill(){}, beginPath(){}, moveTo(){}, lineTo(){}, stroke(){}, setTransform(){}, getImageData: () => ({ data: [] }), putImageData(){}, drawImage(){} };
const mk = () => { const e = { classList: { add(){}, remove(){}, contains: () => false, toggle(){} }, style: {}, dataset: {}, value:"", checked:false, addEventListener(){}, removeEventListener(){}, appendChild(){}, setAttribute(){}, removeAttribute(){}, getContext: () => ctx2d, play: () => ({ catch(){} }), pause(){}, innerHTML: "", textContent: "", removeChild(){}, insertBefore(){}, focus(){}, blur(){}, click(){}, scrollIntoView(){} }; return e; };
const doc = { title: "", readyState: "complete", addEventListener(){}, removeEventListener(){}, querySelector: () => null, querySelectorAll: () => [], getElementById: () => null, createElement: mk, body: null, documentElement: null, defaultView: null };
doc.getElementById = () => mk();
doc.body = mk(); doc.documentElement = mk(); doc.defaultView = {};
const WS = class { constructor(url){} send(){} close(){} };
const sandbox = {
  window: null, document: doc, navigator: { userAgent: "gate5", mediaDevices: {} },
  location: { search: "", pathname: "/", protocol: "http:", host: "localhost" },
  localStorage: { getItem: () => null, setItem(){}, removeItem(){} },
  fetch: async (u, o) => {
    fetchCalls.push({ url: typeof u === "string" ? u : u.url, init: o || {} });
    if (String(u).includes("auth/session")) return { ok: true, status: 200, json: async () => ({ token: "GATE5_TOKEN", sessao_id: "S" }) };
    return { ok: false, status: 401, json: async () => ({}) };
  },
  console, alert(){}, addEventListener(){}, removeEventListener(){}, requestAnimationFrame: () => 0,
  cancelAnimationFrame(){}, setTimeout, clearTimeout, setInterval, clearInterval,
  URL, Blob, File, performance, crypto,
  matchMedia: () => ({ matches: false, addListener(){}, removeListener(){} }),
  WebSocket: WS,
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { timeout: 5000 });

(async () => {
  try {
    await vm.runInContext("initSessionToken()", sandbox);
    await vm.runInContext("setProvider('google_studio')", sandbox);
  } catch (e) {}
  const select = fetchCalls.find(f => f.url.includes("api/providers/select"));
  const out = {
    hasSelect: Boolean(select),
    method: select ? (select.init.method || "GET") : null,
    hasTokenHeader: select ? Boolean((select.init.headers || {})["X-Jarvis-Token"] === "GATE5_TOKEN") : false,
    allCalls: fetchCalls.map(f => f.url),
  };
  process.stdout.write(JSON.stringify(out));
})().catch(e => { process.stdout.write(JSON.stringify({ fatal: e.message })); process.exit(1); });
"""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with tempfile.TemporaryDirectory() as tmp:
        harness_path = os.path.join(tmp, "gate5_harness.cjs")
        with open(harness_path, "w", encoding="utf-8") as f:
            f.write(harness)
        proc = subprocess.run(
            [node, harness_path], capture_output=True, text=True, timeout=60,
            cwd=repo_root,
            env={**os.environ, "WIDGET_PATH": os.path.join(repo_root, "gemini-live-widget", "widget.js")},
        )
        assert proc.returncode == 0, f"FALHA GATE 5: node harness terminou com erro: {proc.stderr}"

    import json as _json
    resultado = _json.loads(proc.stdout.strip().splitlines()[-1])
    assert not resultado.get("fatal"), f"FALHA GATE 5: execução do widget falhou no sandbox: {resultado}"
    assert resultado.get("hasSelect"), (
        f"FALHA GATE 5: setProvider não executou fetch para /api/providers/select. Chamadas: {resultado.get('allCalls')}"
    )
    assert resultado.get("method") in ("POST", "PUT"), (
        f"FALHA GATE 5: /api/providers/select deveria ser mutação (POST/PUT), veio {resultado.get('method')}"
    )
    assert resultado.get("hasTokenHeader"), (
        "FALHA GATE 5: setProvider não enviou X-Jarvis-Token do /api/auth/session na mutação de provedor!"
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


# ==============================================================================
# GATE 6: MICROPHONE PRIVACY & MUTE FAIL-CLOSED
# ==============================================================================

def test_gate6_frontend_widget_has_hardware_and_backend_mute_signaling():
    """Gate 6: Validação estática E comportamental (runtime) do mute físico de mídia, suspensão de AudioContext e ausência de stream-end redundante."""
    import json
    import pathlib
    import subprocess

    widget_js = pathlib.Path("gemini-live-widget/widget.js").read_text(encoding="utf-8")

    # 1. Verificações contratuais estáticas
    assert "getAudioTracks().forEach" in widget_js and "enabled = false" in widget_js, (
        "FALHA GATE 6: Frontend widget.js não desabilita trilhas de microfone no hardware ao mutar!"
    )
    assert "inputAudioCtx.suspend()" in widget_js, (
        "FALHA GATE 6: Frontend widget.js não suspende o AudioContext ao mutar!"
    )
    assert "microphone_state" in widget_js and "muted: true" in widget_js, (
        "FALHA GATE 6: Frontend widget.js não envia evento microphone_state via WebSocket ao mutar!"
    )
    assert "gemini_vad_threshold" in widget_js or "0.012" in widget_js, (
        "FALHA GATE 6: Limiar de VAD calibrado ausente em widget.js!"
    )

    # 2. Validação Comportamental Executável em Runtime (Node.js VM com mocks de MediaStream, AudioContext e WebSocket)
    node_test_script = """
    const fs = require('fs');
    const vm = require('vm');

    const dummyFn = () => {};
    const dummyProxy = new Proxy({}, { get: () => () => dummyProxy });

    function createMockDomElement() {
        return {
            addEventListener: dummyFn,
            removeEventListener: dummyFn,
            classList: { add: dummyFn, remove: dummyFn, contains: () => false, toggle: dummyFn },
            textContent: '',
            title: '',
            value: '',
            style: {},
            appendChild: dummyFn,
            remove: dummyFn,
            querySelectorAll: () => [],
            getContext: () => dummyProxy,
        };
    }

    class MockWebSocket {
        static OPEN = 1;
        constructor(url) {
            this.url = url;
            this.readyState = MockWebSocket.OPEN;
            this.sent = [];
        }
        send(data) {
            this.sent.push(data);
        }
        close() {}
    }

    const sandbox = {
        window: { location: { search: '' }, addEventListener: dummyFn, removeEventListener: dummyFn },
        document: {
            body: createMockDomElement(),
            getElementById: () => createMockDomElement(),
            querySelectorAll: () => [],
            querySelector: () => null,
            createElement: () => createMockDomElement(),
            addEventListener: dummyFn,
            removeEventListener: dummyFn,
            title: '',
        },
        localStorage: { getItem: () => null, setItem: dummyFn },
        navigator: { mediaDevices: { enumerateDevices: async () => [], getUserMedia: async () => {} } },
        fetch: async () => ({ ok: true, json: async () => ({ token: 'test-token' }) }),
        WebSocket: MockWebSocket,
        console: { log: dummyFn, warn: dummyFn, error: dummyFn },
        setTimeout: dummyFn,
        clearTimeout: dummyFn,
        setInterval: dummyFn,
        clearInterval: dummyFn,
        requestAnimationFrame: dummyFn,
        cancelAnimationFrame: dummyFn,
    };

    vm.createContext(sandbox);
    const code = fs.readFileSync('gemini-live-widget/widget.js', 'utf-8');
    vm.runInContext(code, sandbox);

    async function run() {
        const track1 = { kind: 'audio', enabled: true };
        const track2 = { kind: 'audio', enabled: true };
        const mediaStream = { getAudioTracks: () => [track1, track2], active: true };
        let suspendCalled = false;
        let resumeCalled = false;
        const inputAudioCtx = {
            state: 'running',
            suspend: async () => { suspendCalled = true; inputAudioCtx.state = 'suspended'; },
            resume: async () => { resumeCalled = true; inputAudioCtx.state = 'running'; }
        };
        const mockWs = new MockWebSocket('ws://127.0.0.1:8000/ws/live');

        vm.runInContext(`
            state.mediaStream = mediaStream;
            state.inputAudioCtx = inputAudioCtx;
            state.ws = mockWs;
        `, Object.assign(sandbox, { mediaStream, inputAudioCtx, mockWs }));

        // 1. Executa Mute
        await vm.runInContext('toggleMicrophonePause(true)', sandbox);

        if (track1.enabled !== false || track2.enabled !== false) {
            throw new Error('Tracks não foram desabilitadas no mute!');
        }
        if (!suspendCalled) {
            throw new Error('inputAudioCtx.suspend não foi chamado no mute!');
        }
        const hasMutedMsg = mockWs.sent.some(s => {
            try { const m = JSON.parse(s); return m.type === 'microphone_state' && m.muted === true; } catch(_) { return false; }
        });
        if (!hasMutedMsg) {
            throw new Error('Mensagem microphone_state muted:true ausente!');
        }
        const hasDuplicateStreamEnd = mockWs.sent.some(s => {
            try { const m = JSON.parse(s); return m.type === 'audio_stream_end'; } catch(_) { return false; }
        });
        if (hasDuplicateStreamEnd) {
            throw new Error('audio_stream_end duplicado encontrado! Backend deve ser a autoridade única de stream-end.');
        }

        // 2. Executa Unmute
        await vm.runInContext('toggleMicrophonePause(false)', sandbox);

        if (track1.enabled !== true || track2.enabled !== true) {
            throw new Error('Tracks não foram reabilitadas no unmute!');
        }
        if (!resumeCalled) {
            throw new Error('inputAudioCtx.resume não foi chamado no unmute!');
        }
        const hasUnmutedMsg = mockWs.sent.some(s => {
            try { const m = JSON.parse(s); return m.type === 'microphone_state' && m.muted === false; } catch(_) { return false; }
        });
        if (!hasUnmutedMsg) {
            throw new Error('Mensagem microphone_state muted:false ausente!');
        }

        process.stdout.write(JSON.stringify({ status: 'ok', messages: mockWs.sent }));
    }

    run().catch(err => {
        process.stderr.write(err.message || String(err));
        process.exit(1);
    });
    """

    res = subprocess.run(["node", "-e", node_test_script], capture_output=True, text=True)
    assert res.returncode == 0, f"FALHA COMPORTAMENTAL GATE 6 FRONTEND:\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}"
    dados = json.loads(res.stdout)
    assert dados.get("status") == "ok"
    assert len(dados.get("messages", [])) == 2



def test_gate6_server_live_ws_drops_audio_when_muted():
    """Gate 6: Quando o microfone está mutado, o backend deve descartar compulsoriamente os chunks de áudio (fail-closed)."""
    import asyncio as asyncio_mod
    import base64
    from types import SimpleNamespace
    import server as server_mod

    bloqueio = asyncio_mod.Event()

    class FakeLiveSession:
        def __init__(self):
            self.enviados = []

        async def send_client_content(self, **kwargs):
            self.enviados.append(("client_content", kwargs))

        async def send_realtime_input(self, **kwargs):
            self.enviados.append(("realtime_input", kwargs))

        async def send(self, **kwargs):
            self.enviados.append(("send", kwargs))

        async def receive(self):
            # Mantém vivo aguardando comandos
            await bloqueio.wait()
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    model_turn=SimpleNamespace(parts=[]),
                    turn_complete=True,
                    output_transcription=None,
                    interrupted=False,
                ),
                tool_call=None,
            )

        async def close(self):
            pass

    class GerenciadorConexao:
        def __init__(self, sessao):
            self._sessao = sessao

        async def __aenter__(self):
            return self._sessao

        async def __aexit__(self, *exc):
            await self._sessao.close()

    class FakeLive:
        def __init__(self, sessao):
            self._sessao = sessao

        def connect(self, model=None, config=None):
            return GerenciadorConexao(self._sessao)

    class FakeAio:
        def __init__(self, sessao):
            self.live = FakeLive(sessao)

    class FakeClient:
        def __init__(self, sessao, *args, **kwargs):
            self._sessao = sessao
            self._api_client = SimpleNamespace(_websocket_ssl_ctx={})
            self.aio = FakeAio(sessao)

    estado_teste = {"sessoes": []}

    def fabrica_client(*args, **kwargs):
        sess = FakeLiveSession()
        estado_teste["sessoes"].append(sess)
        return FakeClient(sess)

    dummy_pcm = b"\x00\x01" * 1024
    dummy_b64 = base64.b64encode(dummy_pcm).decode("ascii")

    try:
        with patch.object(server_mod.genai, "Client", side_effect=fabrica_client):
            with TestClient(app) as test_client:
                with test_client.websocket_connect("/ws/live") as ws:
                    ws.send_json({"type": "init", "token": JARVIS_SECRET_TOKEN, "voice": "Charon", "barge_in": True})
                    # Dá tempo para o worker inicializar a sessão
                    import time
                    time.sleep(0.1)
                    assert len(estado_teste["sessoes"]) > 0
                    sessao_ativa = estado_teste["sessoes"][0]

                    # 1. Envia sinal de mute: microphone_state muted = true
                    ws.send_json({"type": "microphone_state", "muted": True})
                    time.sleep(0.05)

                    # 2. Envia chunk de áudio com microfone mutado
                    ws.send_json({"type": "audio", "data": dummy_b64})
                    time.sleep(0.05)

                    # Verifica que nenhum chunk de áudio foi enviado à sessão
                    audio_enviados_mutado = [
                        item for item in sessao_ativa.enviados
                        if item[0] == "realtime_input" and "audio" in item[1]
                    ]
                    assert len(audio_enviados_mutado) == 0, (
                        f"FALHA GATE 6: Áudio foi repassado à sessão mesmo com microfone mutado! {audio_enviados_mutado}"
                    )

                    # 3. Usuário digita texto: não deve resetar o mute do microfone
                    ws.send_json({"type": "text", "text": "oi jarvis"})
                    time.sleep(0.05)

                    # 4. Envia outro chunk de áudio: ainda deve ser descartado
                    ws.send_json({"type": "audio", "data": dummy_b64})
                    time.sleep(0.05)

                    audio_enviados_apos_texto = [
                        item for item in sessao_ativa.enviados
                        if item[0] == "realtime_input" and "audio" in item[1]
                    ]
                    assert len(audio_enviados_apos_texto) == 0, (
                        "FALHA GATE 6: Digitar texto reabriu indevidamente a escuta do microfone!"
                    )

                    # 5. Desmuta o microfone: microphone_state muted = false
                    ws.send_json({"type": "microphone_state", "muted": False})
                    time.sleep(0.05)

                    # 6. Envia chunk de áudio: agora DEVE ser aceito e repassado
                    ws.send_json({"type": "audio", "data": dummy_b64})
                    time.sleep(0.05)

                    audio_enviados_desmutado = [
                        item for item in sessao_ativa.enviados
                        if item[0] == "realtime_input" and "audio" in item[1]
                    ]
                    assert len(audio_enviados_desmutado) > 0, (
                        "FALHA GATE 6: Áudio NÃO foi aceito após desmutar o microfone!"
                    )
    finally:
        bloqueio.set()


def test_gate6_live_adk_drops_audio_when_muted():
    """Gate 6: O endpoint /ws/live_adk deve descartar áudio quando estado_microfone mutado for enviado."""
    import asyncio as asyncio_mod
    import base64
    import time
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    import server as server_mod

    client = TestClient(server_mod.app)
    runner = server_mod.obter_runner_adk("voz")

    async def mock_run_live(*args, **kwargs):
        yield SimpleNamespace(
            interim_input_transcription=None,
            input_transcription=None,
            output_transcription=None,
            content=None,
            interrupted=False,
            voice_activity=None,
            turn_complete=False,
        )
        while True:
            await asyncio_mod.sleep(0.1)
            yield SimpleNamespace(
                interim_input_transcription=None,
                input_transcription=None,
                output_transcription=None,
                content=None,
                interrupted=False,
                voice_activity=None,
                turn_complete=False,
            )

    with patch.object(runner, "run_live", side_effect=mock_run_live):
        with patch.object(server_mod, "LiveRequestQueue") as mock_queue_cls:
            mock_queue = MagicMock()
            mock_queue_cls.return_value = mock_queue

            with client.websocket_connect("/ws/live_adk?sessao=sess_mute_test&usuario=user_mute_test") as ws:
                ws.send_json({"type": "init", "token": JARVIS_SECRET_TOKEN})
                msg_ready = ws.receive_json()
                assert msg_ready.get("tipo") == "pronto"

                # 1. Envia sinal de mute
                ws.send_json({"tipo": "estado_microfone", "mutado": True})
                time.sleep(0.05)

                # 2. Envia chunk de áudio
                dummy_b64 = base64.b64encode(b"\x00\x01" * 512).decode("ascii")
                ws.send_json({"tipo": "audio", "dados": dummy_b64})
                time.sleep(0.05)

                # Verifica que a fila NÃO recebeu send_realtime com áudio
                assert mock_queue.send_realtime.called is False, (
                    "FALHA GATE 6: /ws/live_adk repassou áudio para fila mesmo com microfone mutado!"
                )

                # 3. Desmuta
                ws.send_json({"tipo": "estado_microfone", "mutado": False})
                time.sleep(0.05)

                # 4. Envia chunk de áudio
                ws.send_json({"tipo": "audio", "dados": dummy_b64})
                time.sleep(0.05)

                # Agora DEVE ter chamado send_realtime
                assert mock_queue.send_realtime.called is True, (
                    "FALHA GATE 6: /ws/live_adk não repassou áudio após desmutar!"
                )


# ==============================================================================
# GATE 7: ASYNC RUNTIME INTEGRITY & NON-BLOCKING TOOL EXECUTION
# ==============================================================================

def test_gate7_adk_tool_wrappers_are_asynchronous():
    """Gate 7.1: Todas as ferramentas ADK geradas devem ser corrotinas assíncronas."""
    from agentes.ferramentas import obter_todas_ferramentas_adk
    tools = obter_todas_ferramentas_adk()
    assert len(tools) >= 30, f"FALHA GATE 7: Esperado >= 30 ferramentas, encontrado {len(tools)}"
    for t in tools:
        assert inspect.iscoroutinefunction(t.func), (
            f"FALHA GATE 7: Ferramenta {t.name} não é assíncrona (inspect.iscoroutinefunction == False)!"
        )


def test_gate7_system_status_cpu_check_is_non_blocking():
    """Gate 7.2: status usa cpu_percent(interval=None) — sem pausa bloqueante.

    Assert no argumento passado à API, não em wall-clock (flaky em máquinas
    lentas/ocupadas): a garantia é o contrato de não-bloqueio, não a veloz.
    """
    import system_tools
    import agentes.ferramentas as af
    from unittest.mock import patch

    chamadas = []

    def fake_cpu_percent(interval=None, percpu=None):
        chamadas.append({"interval": interval, "percpu": percpu})
        return 7.0

    with patch.object(system_tools.psutil, "cpu_percent", side_effect=fake_cpu_percent):
        st_sys = system_tools.get_system_status()
    assert chamadas and chamadas[0]["interval"] is None, (
        "FALHA GATE 7: system_tools.get_system_status não usa cpu_percent(interval=None)!"
    )
    assert "cpu_percent" in st_sys

    chamadas.clear()
    with patch.object(af.psutil, "cpu_percent", side_effect=fake_cpu_percent):
        st_af = af.status_do_sistema()
    assert chamadas and chamadas[0]["interval"] is None, (
        "FALHA GATE 7: agentes.ferramentas.status_do_sistema não usa cpu_percent(interval=None)!"
    )
    assert "cpu_percentual" in st_af


def test_gate7_tool_execution_does_not_block_live_event_loop():
    """Gate 7.3: Produção despacha ferramentas síncronas via asyncio.to_thread.

    O wrapper de ferramentas do agentes/ferramentas.py offloada handlers
    síncronos para o thread pool (asyncio.to_thread) em vez de rodá-los no event
    loop; o dispatch do servidor usa o mesmo padrão para executors síncronos.
    """
    import inspect
    import agentes.ferramentas as af

    src_wrapper = inspect.getsource(af.obter_todas_ferramentas_adk)
    assert "inspect.iscoroutinefunction" in src_wrapper
    assert "asyncio.to_thread" in src_wrapper, (
        "FALHA GATE 7: wrapper de ferramentas síncronas não usa asyncio.to_thread!"
    )

    import server
    src_dispatch = inspect.getsource(server.websocket_live_endpoint)
    assert "asyncio.to_thread" in src_dispatch, (
        "FALHA GATE 7: dispatch do servidor não offloada executors síncronos com asyncio.to_thread!"
    )


def test_gate7_concurrent_websocket_writes_are_serialized():
    """Gate 7.4: safe_send_json de produção (server.py) serializa envios por asyncio.Lock."""
    import inspect
    import server

    for endpoint_name in ("websocket_live_endpoint", "live_adk"):
        src_endpoint = inspect.getsource(getattr(server, endpoint_name))
        assert "ws_send_lock = asyncio.Lock()" in src_endpoint, (
            f"FALHA GATE 7: {endpoint_name} não cria ws_send_lock (asyncio.Lock)!"
        )
        assert "async with ws_send_lock:" in src_endpoint, (
            f"FALHA GATE 7: {endpoint_name} não serializa envios pela lock!"
        )


def test_gate7_tool_execution_timeout_fails_closed():
    """Gate 7.5: Dispatch de produção usa asyncio.wait_for; TimeoutError vira erro fail-closed."""
    import inspect
    import server

    src_dispatch = inspect.getsource(server.websocket_live_endpoint)
    assert "asyncio.wait_for" in src_dispatch, (
        "FALHA GATE 7: dispatch de ferramentas não aplica asyncio.wait_for!"
    )
    assert "asyncio.TimeoutError" in src_dispatch
    assert '{"sucesso": False, "erro": f"Timeout (' in src_dispatch, (
        "FALHA GATE 7: TimeoutError não produz erro estruturado fail-closed!"
    )



