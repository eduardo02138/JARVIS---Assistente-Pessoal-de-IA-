#!/usr/bin/env python3
"""
Servidor MCP (Model Context Protocol) para o J.A.R.V.I.S.
Permite que a IDE Antigravity e agentes autônomos interajam diretamente
com o assistente pessoal, hardware e notificações por voz.
"""

import sys
import os
import json
import urllib.request
import urllib.error

# Adiciona o diretório do assistente ao sys.path
ASSISTANT_DIR = os.path.dirname(os.path.abspath(__file__))
if ASSISTANT_DIR not in sys.path:
    sys.path.insert(0, ASSISTANT_DIR)

import system_tools

from mcp.server.mcpserver import MCPServer

# Inicialização do servidor MCP
server = MCPServer(
    name="jarvis",
    title="JARVIS Assistant MCP",
    description="Servidor MCP do JARVIS para controle de hardware, telemetria e notificações de voz"
)

JARVIS_API_BASE = "http://localhost:8000"

@server.tool()
def jarvis_get_telemetry() -> str:
    """
    Obtém a telemetria em tempo real do computador do usuário (CPU, memória RAM,
    GPU NVIDIA RTX dedicada com VRAM e temperatura, bateria e uptime).
    """
    gpu = system_tools.get_gpu_status()
    sys_status = system_tools.get_system_status()
    
    result = {
        "sistema": sys_status,
        "gpu_nvidia": gpu
    }
    return json.dumps(result, indent=2, ensure_ascii=False)

@server.tool()
def jarvis_notify_voice(message: str) -> str:
    """
    Envia uma mensagem ou alerta para o JARVIS falar em voz alta para o usuário.
    Ideal para avisar quando um build, teste longo ou tarefa de codificação terminar.
    """
    clean_msg = message.strip()
    if not clean_msg:
        return "Mensagem não pode ser vazia."

    prompt_payload = f"[AVISO DO AGENTE ANTIGRAVITY]: {clean_msg}. Por favor, avise o senhor em tom refinado e sucinto."
    url = f"{JARVIS_API_BASE}/api/inject_prompt"
    data = json.dumps({"prompt": prompt_payload}).encode("utf-8")
    
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"}
    )
    
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            return f"Notificação enviada ao JARVIS com sucesso: {res_data.get('message', 'OK')}"
    except urllib.error.URLError as e:
        return f"O servidor Web do JARVIS (porta 8000) parece não estar acessível ({str(e)}). Mensagem não entregue."
    except Exception as e:
        return f"Erro ao enviar notificação de voz: {str(e)}"

@server.tool()
def jarvis_query_status() -> str:
    """
    Verifica a integridade e status operacional do servidor JARVIS (chaves Gemini ativas,
    latência média, modelos e sessões ativas).
    """
    url = f"{JARVIS_API_BASE}/api/health"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as response:
            health = json.loads(response.read().decode("utf-8"))
            return json.dumps(health, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "status": "offline",
            "erro": f"Servidor JARVIS não respondeu em {url}: {str(e)}"
        }, indent=2, ensure_ascii=False)

@server.tool()
def jarvis_list_games(filter_name: str = "") -> str:
    """
    Lista todos os jogos instalados no computador (Steam, Lutris, Epic Games, etc.),
    pastas e comandos de inicialização.
    """
    games_info = system_tools.list_installed_games(filter_name=filter_name)
    return json.dumps(games_info, indent=2, ensure_ascii=False)

@server.tool()
def jarvis_open_gemini_bridge() -> str:
    """
    Abre a pasta 'gemini' de auditoria e canal de mensagens diretas na IDE Antigravity.
    Permite que o usuário e a IDE interajam através dos arquivos de ponte.
    """
    import gemini_bridge
    res = gemini_bridge.open_gemini_bridge()
    return json.dumps(res, indent=2, ensure_ascii=False)

@server.tool()
def jarvis_read_bridge_audit(limit: int = 20) -> str:
    """
    Lê os últimos eventos e comandos auditados no arquivo gemini/audit.jsonl.
    """
    import gemini_bridge
    if not os.path.exists(gemini_bridge.AUDIT_JSONL):
        return json.dumps({"eventos": [], "mensagem": "Nenhum evento registrado ainda."}, ensure_ascii=False)
    
    events = []
    try:
        with open(gemini_bridge.AUDIT_JSONL, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    events.append(json.loads(line.strip()))
        return json.dumps(events[-limit:], indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"erro": f"Falha ao ler audit.jsonl: {str(e)}"}, ensure_ascii=False)

if __name__ == "__main__":
    server.run(transport="stdio")

