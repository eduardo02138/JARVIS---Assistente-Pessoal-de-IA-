"""
Módulo Gemini Bridge & Auditoria Forense
Ponte bidirecional baseada em arquivos e log de auditoria
entre o usuário, J.A.R.V.I.S. e a IDE Antigravity.
"""

import os
import sys
import json
import time
import asyncio
import subprocess
import logging
from datetime import datetime

logger = logging.getLogger("GEMINI_BRIDGE")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GEMINI_DIR = os.path.join(BASE_DIR, "gemini")
AUDIT_JSONL = os.path.join(GEMINI_DIR, "audit.jsonl")
COMMANDS_LOG = os.path.join(GEMINI_DIR, "commands.log")
INPUT_TXT = os.path.join(GEMINI_DIR, "input.txt")
LATEST_RESPONSE_MD = os.path.join(GEMINI_DIR, "latest_response.md")

PLACEHOLDER_TEXT = "# Digite seu comando ou instrução para o Antigravity aqui e salve o arquivo."

def log_audit_event(sender: str, action: str, content: any, metadata: dict = None):
    """Registra uma interação no log JSONL e no arquivo de texto contínuo."""
    now_iso = datetime.now().isoformat()
    entry = {
        "timestamp": now_iso,
        "sender": sender,
        "action": action,
        "content": content,
        "metadata": metadata or {}
    }
    
    # Grava no JSONL estruturado
    try:
        with open(AUDIT_JSONL, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error(f"Erro ao gravar em audit.jsonl: {e}")

    # Grava no commands.log amigável para leitura humana
    try:
        log_line = f"[{now_iso}] [{sender.upper()}] ({action}): {str(content)[:250]}\n"
        with open(COMMANDS_LOG, "a", encoding="utf-8") as f:
            f.write(log_line)
    except Exception as e:
        logger.error(f"Erro ao gravar em commands.log: {e}")

def update_latest_response(title: str, content: str):
    """Atualiza o arquivo latest_response.md com a resposta mais recente em Markdown."""
    try:
        now_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        md_text = f"# 🪐 Última Resposta do Sistema ({now_str})\n\n### {title}\n\n{content}\n"
        with open(LATEST_RESPONSE_MD, "w", encoding="utf-8") as f:
            f.write(md_text)
    except Exception as e:
        logger.error(f"Erro ao atualizar latest_response.md: {e}")

def open_gemini_bridge() -> dict:
    """Abre a pasta gemini diretamente como projeto na IDE Antigravity."""
    if not os.path.exists(GEMINI_DIR):
        os.makedirs(GEMINI_DIR, exist_ok=True)
    try:
        subprocess.Popen(["/usr/bin/antigravity", GEMINI_DIR], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log_audit_event("JARVIS", "open_workspace", {"path": GEMINI_DIR})
        return {
            "sucesso": True,
            "caminho": GEMINI_DIR,
            "mensagem": f"Pasta de auditoria 'gemini' aberta com sucesso na IDE Antigravity, senhor."
        }
    except Exception as e:
        return {"sucesso": False, "mensagem": f"Falha ao abrir a pasta gemini na IDE Antigravity: {str(e)}"}

async def gemini_file_watcher_task():
    """
    Monitora assincronamente o arquivo gemini/input.txt.
    Quando o usuário digita e salva uma instrução, o assistente a executa
    diretamente no agente Antigravity e registra a resposta.
    """
    logger.info("Iniciando monitor assíncrono de arquivos da pasta gemini...")
    last_mtime = 0
    if os.path.exists(INPUT_TXT):
        last_mtime = os.path.getmtime(INPUT_TXT)

    while True:
        try:
            await asyncio.sleep(1.5)
            if not os.path.exists(INPUT_TXT):
                continue

            current_mtime = os.path.getmtime(INPUT_TXT)
            if current_mtime > last_mtime:
                last_mtime = current_mtime
                with open(INPUT_TXT, "r", encoding="utf-8") as f:
                    raw_content = f.read().strip()

                # Ignora se for apenas o placeholder padrão ou vazio
                clean_lines = [l for l in raw_content.splitlines() if l.strip() and not l.strip().startswith("#")]
                cmd = "\n".join(clean_lines).strip()
                if cmd:
                    logger.info(f"Comando detectado em gemini/input.txt: '{cmd}'")
                    # Reseta o arquivo input.txt com o placeholder para a próxima instrução
                    with open(INPUT_TXT, "w", encoding="utf-8") as f:
                        f.write(f"# Último comando enviado às {datetime.now().strftime('%H:%M:%S')}: {cmd[:60]}...\n{PLACEHOLDER_TEXT}\n")
                    last_mtime = os.path.getmtime(INPUT_TXT)

                    # Registra comando de entrada no log de auditoria
                    log_audit_event("USER_FILE", "input_command", cmd)

                    # Avalia a política de segurança antes de executar ação privilegiada
                    from policy_engine import policy_engine
                    args_call = {"prompt": cmd, "continue_session": True}
                    decision = policy_engine.evaluate("antigravity_run_prompt", args_call, session_id="gemini_bridge")
                    if not decision.allowed:
                        logger.warning(f"Execução bloqueada por política no watcher gemini/input.txt: {decision.reason}")
                        log_audit_event("POLICY", "blocked_command", decision.reason, {"input_command": cmd})
                        update_latest_response(f"Comando: {cmd[:60]}", f"[BLOQUEADO PELO POLICY ENGINE]: {decision.reason}")
                        continue

                    if decision.requires_confirmation:
                        # Checa se há autorização one-shot já concedida pelo usuário
                        if not policy_engine.consume_authorization("antigravity_run_prompt", args_call, session_id="gemini_bridge", user_id="gemini_bridge"):
                            pending = policy_engine.create_pending_action(
                                tool_name="antigravity_run_prompt",
                                args=args_call,
                                session_id="gemini_bridge",
                                user_id="gemini_bridge",
                                ttl=60.0,
                            )
                            msg_bloqueio = f"[CONFIRMAÇÃO NECESSÁRIA]: Execução de prompt sensível bloqueada. ID da pendência: {pending.action_id}."
                            logger.warning(msg_bloqueio)
                            log_audit_event("POLICY", "pending_confirmation", msg_bloqueio, {"input_command": cmd, "action_id": pending.action_id})
                            update_latest_response(f"Comando: {cmd[:60]}", msg_bloqueio)
                            continue

                    # Executa o comando via Antigravity agy CLI
                    import system_tools
                    res = system_tools.antigravity_run_prompt(cmd, continue_session=True)
                    ans = res.get("resposta_completa", res.get("mensagem", ""))

                    # Registra resposta
                    log_audit_event("ANTIGRAVITY", "command_response", ans, {"input_command": cmd})
                    update_latest_response(f"Comando: {cmd[:60]}", ans)

                    # Tenta notificar o JARVIS por voz via inject_prompt se o servidor estiver rodando
                    try:
                        import urllib.request
                        url = "http://localhost:8000/api/inject-prompt"
                        prompt_msg = f"[AVISO PONTE GEMINI]: O senhor enviou um comando pelo arquivo da IDE: '{cmd[:60]}'. O agente Antigravity concluiu a execução."
                        data = json.dumps({"prompt": prompt_msg}).encode("utf-8")
                        token = os.environ.get("JARVIS_TOKEN") or os.environ.get("JARVIS_SECRET_TOKEN", "")
                        headers = {"Content-Type": "application/json"}
                        if token:
                            headers["Authorization"] = f"Bearer {token}"
                        req = urllib.request.Request(url, data=data, headers=headers)
                        with urllib.request.urlopen(req, timeout=3) as resp:
                            pass
                    except Exception:
                        pass
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Erro no watcher do gemini/input.txt: {e}")
            await asyncio.sleep(2)
