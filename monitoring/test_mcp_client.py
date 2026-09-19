"""Suíte de testes de certificação do MCP Client no Google ADK (mcp_client_manager.py).

Testa:
1. Carregamento de configurações JSON e filtragem anti-recursão do servidor 'jarvis'.
2. Conexão Stdio de ponta a ponta com servidor MCP e descoberta assíncrona de ferramentas.
3. Execução de ferramentas via McpToolset com governança e PolicyEngine.
4. Encerramento limpo e ordenado de conexões (close_all).
5. Integração com agentes ADK (assistente_rapido e coordenador).
6. Endpoints /api/mcp/servers e /api/health na app unificada (server.py).
"""

import asyncio
import os
import sys
import tempfile
import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
for venv_site in [
    os.path.join(RAIZ, ".venv", "lib", f"python{sys.version_info.major}.{sys.version_info.minor}", "site-packages"),
    os.path.join(RAIZ, ".venv", "lib", "site-packages"),
]:
    if os.path.isdir(venv_site) and venv_site not in sys.path:
        sys.path.insert(0, venv_site)

os.environ.setdefault("SESSION_DB_URL", "memoria")

from fastapi.testclient import TestClient
from policy_engine import RiskLevel, policy_engine
from mcp_client_manager import McpClientManager
from agentes.assistente import criar_agente_rapido, criar_agente_coordenador


def test_mcp_anti_recursa_jarvis_e_disabled():
    """Garante que o servidor 'jarvis' é ignorado no cliente e servidores disabled não sobem."""
    mgr = McpClientManager()
    dummy_config = {
        "jarvis": {
            "command": "python",
            "args": ["jarvis_mcp_server.py"],
        },
        "servidor_desativado": {
            "command": "echo",
            "disabled": True,
        },
    }

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        import json
        json.dump({"mcpServers": dummy_config}, f)
        tmp_path = f.name

    try:
        os.environ["MCP_SERVERS_CONFIG"] = tmp_path
        configs = mgr.carregar_configuracao()
        assert "jarvis" in configs
        toolsets = mgr.carregar_toolsets(forcar_recarga=True)
        # Nem jarvis nem o desativado devem ter gerado toolsets ativos
        assert len(toolsets) == 0
        assert mgr.get_toolset("jarvis") is None
        assert mgr.get_toolset("servidor_desativado") is None
    finally:
        os.environ.pop("MCP_SERVERS_CONFIG", None)
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_mcp_stdio_e2e_e_politica():
    """Testa conexão real via Stdio com um servidor MCP em processo isolado."""
    async def _run():
        server_code = """
from mcp.server.mcpserver import MCPServer
server = MCPServer(name="math_service")

@server.tool()
def multiplicar(x: int, y: int) -> int:
    \"\"\"Multiplica dois inteiros.\"\"\"
    return x * y

if __name__ == "__main__":
    server.run(transport="stdio")
"""
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write(server_code)
            srv_file = f.name

        mgr = McpClientManager()
        cfg = {
            "command": sys.executable,
            "args": [srv_file],
            "default_risk_level": "READ",
            "policies": {
                "multiplicar": "READ",
            },
        }

        try:
            toolset = mgr.registrar_servidor_dinamico("math_srv", cfg)
            assert toolset is not None

            # Valida que a política foi registrada no PolicyEngine
            assert policy_engine.get_risk_level("multiplicar") == RiskLevel.READ

            # Descoberta de ferramentas
            tools = await toolset.get_tools()
            assert len(tools) >= 1
            tool_names = [t.name for t in tools]
            assert "multiplicar" in tool_names

            # Execução da ferramenta via ADK Tool
            mult_tool = next(t for t in tools if t.name == "multiplicar")
            res = await mult_tool.run_async(args={"x": 6, "y": 7}, tool_context=None)
            assert res.get("structuredContent", {}).get("result") == 42
        finally:
            await mgr.close_all()
            if os.path.exists(srv_file):
                os.remove(srv_file)

    asyncio.run(_run())


def test_agentes_incorporam_mcp_toolset():
    """Valida que criar_agente_rapido e coordenador incorporam os toolsets sem erros."""
    from mcp_client_manager import mcp_client_manager

    # Garante estado limpo
    mcp_client_manager._toolsets.clear()
    mcp_client_manager._loaded = False

    agente_rapido = criar_agente_rapido()
    agente_coord = criar_agente_coordenador()

    assert agente_rapido is not None
    assert agente_coord is not None
    assert len(agente_rapido.tools) > 0
    assert len(agente_coord.tools) > 0


def test_endpoints_mcp_app_unificada():
    """Valida os endpoints /api/mcp/servers e /api/health na app unificada (server.py)."""
    import server
    client = TestClient(server.app)

    # Health check deve listar mcp_servers
    res_health = client.get("/api/health")
    assert res_health.status_code == 200
    dados_health = res_health.json()
    assert "mcp_servers" in dados_health

    # Obter token de sessão no loopback
    res_token = client.get("/api/auth/session")
    assert res_token.status_code == 200
    token = res_token.json()["token"]

    # Endpoint /api/mcp/servers deve retornar 200 com lista de servidores
    res_mcp = client.get("/api/mcp/servers", headers={"X-Jarvis-Token": token})
    assert res_mcp.status_code == 200
    dados_mcp = res_mcp.json()
    assert dados_mcp.get("status") == "ok"
    assert isinstance(dados_mcp.get("servers"), list)
