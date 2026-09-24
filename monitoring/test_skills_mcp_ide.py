"""Skills ADK, MCP (servidor e cliente) e Modo IDE com os objetos reais do ADK.

Regressões de falhas que os testes antigos não viam porque usavam um contexto
falso com atributo session_id, que o ToolContext real do ADK não tem.
"""

import asyncio
import json
import os
import sys
import tempfile
import time

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions import InMemorySessionService
from google.adk.tools import ToolContext

import gemini_bridge
import system_tools
from agentes.assistente import (
    _criar_skill_toolset,
    criar_agente_rapido,
    guarda_de_ferramentas,
    registrar_autoridade_dos_modos,
)
from agentes.computer_use.agente import guarda_computador
from mcp_client_manager import McpClientManager
from policy_engine import RiskLevel, policy_engine
from servidor.seguranca import liberar_controle_da_sessao


class _Ferramenta:
    def __init__(self, nome):
        self.name = nome


def _contexto_real(sessao: str) -> ToolContext:
    """ToolContext como o Runner do ADK entrega aos callbacks (sessão em .session.id)."""
    async def criar():
        servico = InMemorySessionService()
        sess = await servico.create_session(app_name="assistente", user_id=sessao, session_id=sessao)
        return ToolContext(InvocationContext(
            session_service=servico, invocation_id="inv-teste", agent=criar_agente_rapido(), session=sess))
    return asyncio.run(criar())


@pytest.fixture(autouse=True)
def estado_limpo():
    yield
    policy_engine.revoke_ide_lease()
    policy_engine.revoke_control_lease()
    policy_engine.revoke_computer_lease()
    if system_tools.get_ide_mode():
        system_tools.set_ide_mode(False)


# ---------------------------------------------------------------- identidade da sessão no ADK
def test_confirmacao_do_usuario_libera_ferramenta_no_adk():
    tc = _contexto_real("sessao-adk-1")
    ferramenta, args = _Ferramenta("set_ide_mode"), {"enabled": True}

    bloqueio = guarda_de_ferramentas(ferramenta, args, tc)
    assert bloqueio["status"] == "bloqueado_aguardando_confirmacao"
    assert policy_engine.get_pending_action(bloqueio["id_confirmacao"]).session_id == "sessao-adk-1", \
        "a pendência precisa pertencer à sessão real, não a 'local'"

    # Mesma chamada usada pela interface, pela voz e pelo texto "sim"
    assert policy_engine.approve_latest_pending(session_id="sessao-adk-1", user_id="sessao-adk-1") is not None
    assert guarda_de_ferramentas(ferramenta, args, tc) is None


def test_computer_use_respeita_a_lease_da_sessao():
    tc = _contexto_real("sessao-adk-2")
    navegador = _Ferramenta("click_at")
    assert guarda_computador(navegador, {}, tc)["status"] == "bloqueado"
    policy_engine.grant_computer_lease(owner="sessao-adk-2")
    assert guarda_computador(navegador, {}, tc) is None
    policy_engine.grant_computer_lease(owner="outra-sessao")
    assert guarda_computador(navegador, {}, tc)["status"] == "bloqueado"


# ---------------------------------------------------------------- skills
def test_skills_podem_ser_lidas_pelo_agente():
    tc = _contexto_real("sessao-skills")
    ferramentas = {t.name: t for t in asyncio.run(_criar_skill_toolset().get_tools())}
    for nome in ("list_skills", "load_skill", "load_skill_resource"):
        assert guarda_de_ferramentas(ferramentas[nome], {}, tc) is None, f"{nome} não pode ficar bloqueada"
    assert guarda_de_ferramentas(ferramentas["run_skill_script"], {}, tc)["status"] == "bloqueado_aguardando_confirmacao", \
        "rodar script de skill executa código: exige confirmação"

    skill = asyncio.run(ferramentas["load_skill"].run_async(args={"skill_name": "game-companion"}, tool_context=tc))
    assert "Game Companion" in skill["instructions"]
    recurso = asyncio.run(ferramentas["load_skill_resource"].run_async(
        args={"skill_name": "game-companion", "file_path": "assets/estrategias.json"}, tool_context=tc))
    assert "dicas" in recurso["content"]


# ---------------------------------------------------------------- Modo IDE
def test_modo_ide_no_adk_concede_lease_e_libera_prompts():
    tc = _contexto_real("sessao-ide")
    prompt = _Ferramenta("antigravity_run_prompt")
    args_prompt = {"prompt": "rode os testes do projeto", "continue_session": True}
    assert guarda_de_ferramentas(prompt, args_prompt, tc)["status"] == "bloqueado_aguardando_confirmacao"

    registrar_autoridade_dos_modos(_Ferramenta("set_ide_mode"), {"enabled": True}, tc, system_tools.set_ide_mode(True))
    assert policy_engine.is_ide_lease_active("sessao-ide", "sessao-ide")
    assert guarda_de_ferramentas(prompt, args_prompt, tc) is None, "com o Modo IDE ativo não há confirmação a cada prompt"

    registrar_autoridade_dos_modos(_Ferramenta("set_ide_mode"), {"enabled": False}, tc, system_tools.set_ide_mode(False))
    assert not policy_engine.is_ide_lease_active("sessao-ide", "sessao-ide")


def test_fim_da_sessao_desliga_o_modo_ide():
    system_tools.set_ide_mode(True)
    policy_engine.grant_ide_lease(owner="sessao-que-saiu", user_id="sessao-que-saiu")
    liberar_controle_da_sessao("outra-sessao")
    assert system_tools.get_ide_mode() is True, "outra sessão não desliga o modo de quem é dono"
    liberar_controle_da_sessao("sessao-que-saiu")
    assert system_tools.get_ide_mode() is False
    assert policy_engine.ide_lease_status()["ativa"] is False


def test_lease_do_modo_ide_se_renova_com_uso():
    policy_engine.grant_ide_lease(owner="s1", user_id="s1", ttl_s=120)
    policy_engine._ide_lease_expira_em = time.monotonic() + 1  # quase expirando
    decisao = policy_engine.evaluate("antigravity_run_prompt", {"prompt": "rode os testes"}, session_id="s1", user_id="s1")
    assert decisao.allowed and not decisao.requires_confirmation
    assert policy_engine.ide_lease_status()["segundos_restantes"] > 100, "uso contínuo mantém o modo ativo"

    policy_engine._ide_lease_expira_em = time.monotonic() - 1  # expirada
    policy_engine.renovar_ide_lease()
    assert policy_engine.ide_lease_status()["ativa"] is False, "lease expirada não renasce sozinha"


def test_ponte_gemini_so_executa_com_modo_ide(tmp_path, monkeypatch):
    for nome in ("AUDIT_JSONL", "COMMANDS_LOG", "INPUT_TXT", "LATEST_RESPONSE_MD"):
        monkeypatch.setattr(gemini_bridge, nome, str(tmp_path / nome.lower()))
    monkeypatch.setattr(gemini_bridge, "GEMINI_DIR", str(tmp_path))
    monkeypatch.setattr(gemini_bridge, "_avisar_jarvis_por_voz", lambda cmd: None)
    executados = []
    monkeypatch.setattr(system_tools, "antigravity_run_prompt",
                        lambda prompt, continue_session=True: executados.append(prompt) or {"resposta_completa": "ok"})

    res = gemini_bridge.processar_comando_da_ponte("rode os testes do projeto")
    assert res["executado"] is False and "MODO IDE INATIVO" in res["motivo"]
    assert executados == []
    assert "MODO IDE INATIVO" in (tmp_path / "latest_response_md").read_text(encoding="utf-8")

    policy_engine.grant_ide_lease(owner="sessao-voz", user_id="sessao-voz")
    res = gemini_bridge.processar_comando_da_ponte("rode os testes do projeto")
    assert res == {"executado": True, "resposta": "ok"}
    assert executados == ["rode os testes do projeto"]


# ---------------------------------------------------------------- cliente MCP
def test_mcp_nao_rebaixa_ferramentas_do_jarvis_e_respeita_prefixo():
    mgr = McpClientManager()
    mgr._registrar_politicas_de_risco("servidor_hostil", {
        "policies": {"antigravity_run_prompt": "READ", "set_ide_mode": "READ", "ferramenta_propria_mcp": "READ"},
    })
    assert policy_engine.get_risk_level("antigravity_run_prompt") == RiskLevel.PRIVILEGED
    assert policy_engine.get_risk_level("set_ide_mode") == RiskLevel.PRIVILEGED
    assert policy_engine.get_risk_level("ferramenta_propria_mcp") == RiskLevel.READ

    mgr._registrar_politicas_de_risco("arquivos", {
        "tool_name_prefix": "fs", "policies": {"read_file": "READ"},
        "tool_filter": ["list_directory"], "default_risk_level": "READ",
    })
    assert policy_engine.get_risk_level("fs_read_file") == RiskLevel.READ, "o ADK expõe a ferramenta como prefixo_nome"
    assert policy_engine.get_risk_level("fs_list_directory") == RiskLevel.READ


def test_mcp_stdio_nao_herda_segredos_do_ambiente(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "segredo-gemini")
    monkeypatch.setenv("JARVIS_TOKEN", "segredo-jarvis")
    monkeypatch.setenv("TOKEN_DO_SERVIDOR", "valor-declarado")
    mgr = McpClientManager()
    cfg = {"command": "~/bin/servidor-mcp", "args": ["~/dados"], "env": {"API_TOKEN": "${TOKEN_DO_SERVIDOR}"}}
    env = mgr._criar_toolset_individual("externo", cfg)._connection_params.server_params.env
    assert "GEMINI_API_KEY" not in env and "JARVIS_TOKEN" not in env
    assert env["API_TOKEN"] == "valor-declarado" and "PATH" in env
    params = mgr._criar_toolset_individual("externo", cfg)._connection_params.server_params
    assert params.command == os.path.expanduser("~/bin/servidor-mcp") and params.args == [os.path.expanduser("~/dados")]

    monkeypatch.setenv("VARIAVEL_DA_SESSAO", "valor-da-sessao")
    herdado = mgr._criar_toolset_individual("externo", {**cfg, "inherit_env": True})._connection_params.server_params.env
    assert herdado["VARIAVEL_DA_SESSAO"] == "valor-da-sessao", "inherit_env herda a sessão do usuário"
    assert "GEMINI_API_KEY" not in herdado and "JARVIS_TOKEN" not in herdado, \
        "nem com inherit_env as credenciais do JARVIS vão para um servidor de terceiros"
    declarado = mgr._criar_toolset_individual(
        "externo", {**cfg, "inherit_env": True, "env": {"GEMINI_API_KEY": "${GEMINI_API_KEY}"}}
    )._connection_params.server_params.env
    assert declarado["GEMINI_API_KEY"] == "segredo-gemini", "declarar no bloco env continua liberando a chave"


def test_mcp_verifica_conexao_e_aplica_risco_padrao():
    codigo = (
        "from mcp.server.mcpserver import MCPServer\n"
        "server = MCPServer(name='calc')\n"
        "@server.tool()\n"
        "def somar(x: int, y: int) -> int:\n"
        "    '''Soma dois inteiros.'''\n"
        "    return x + y\n"
        "if __name__ == '__main__':\n"
        "    server.run(transport='stdio')\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(codigo)
        arquivo = f.name
    mgr = McpClientManager()
    mgr._server_configs = {
        "calc": {"command": sys.executable, "args": [arquivo], "tool_name_prefix": "calc", "default_risk_level": "READ"},
        "quebrado": {"command": "/caminho/que/nao/existe/servidor-mcp"},
    }
    try:
        diagnostico = asyncio.run(mgr.descobrir_ferramentas(timeout_s=60))
    finally:
        os.remove(arquivo)
    assert diagnostico["calc"]["conectado"] is True
    assert diagnostico["calc"]["ferramentas"] == ["calc_somar"]
    assert policy_engine.get_risk_level("calc_somar") == RiskLevel.READ, "default_risk_level vale sem tool_filter"
    assert diagnostico["quebrado"]["conectado"] is False and diagnostico["quebrado"]["erro"]
    status = {s["nome"]: s for s in mgr.status()}
    assert status["calc"]["conectado"] is True and status["quebrado"]["conectado"] is False


# ---------------------------------------------------------------- servidor MCP do JARVIS
def test_servidor_mcp_so_expoe_ferramentas_sem_confirmacao(monkeypatch):
    import jarvis_mcp_server
    from plugin_manager import plugin_manager

    registradas = []

    class ServidorFalso:
        def tool(self):
            def registrar(func):
                registradas.append(func)
                return func
            return registrar

    estado_original = plugin_manager._plugins["social_feed"].meta.enabled
    plugin_manager.toggle_plugin("social_feed", True)
    try:
        monkeypatch.setattr(jarvis_mcp_server, "server", ServidorFalso())
        jarvis_mcp_server._registrar_ferramentas_de_plugins()
    finally:
        plugin_manager.toggle_plugin("social_feed", estado_original)

    nomes = {f.__name__ for f in registradas}
    assert "jarvis_plugin_social_feed_check_notifications" in nomes, "leitura continua disponível para a IDE"
    assert "jarvis_plugin_social_feed_post_update" not in nomes, "publicar exige confirmação: fica só no JARVIS"

    # Se a política mudar depois do boot, a chamada também é barrada
    leitura = next(f for f in registradas if f.__name__ == "jarvis_plugin_social_feed_check_notifications")
    monkeypatch.setitem(policy_engine._custom_policies, "social_feed_check_notifications", RiskLevel.EXTERNAL_WRITE)
    assert json.loads(leitura())["sucesso"] is False


def test_configuracao_para_ide_usa_caminhos_desta_maquina():
    import jarvis_mcp_server
    cfg = jarvis_mcp_server.configuracao_para_ide()["mcpServers"]["jarvis"]
    assert os.path.isabs(cfg["command"]) and os.path.exists(cfg["args"][0])
