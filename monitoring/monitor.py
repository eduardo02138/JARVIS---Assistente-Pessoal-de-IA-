#!/usr/bin/env python3
"""
Monitor CLI de Telemetria e Depuração em Tempo Real para JARVIS / Gemini Live
Exibe métricas ao vivo de áudio, transcrições, chamadas de tools e rotações de chaves no terminal.
"""
import os
import sys
import time
import json
import urllib.request
import urllib.error

SERVER_URL = "http://127.0.0.1:8000"

# Códigos ANSI para estilização no terminal
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[96m"
BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
MAGENTA = "\033[95m"
WHITE = "\033[97m"
CLEAR_SCREEN = "\033[2J\033[H"

def get_json(endpoint):
    try:
        req = urllib.request.Request(f"{SERVER_URL}{endpoint}", headers={"User-Agent": "JARVIS-Monitor"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

def format_bytes(b):
    if b < 1024:
        return f"{b} B"
    elif b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    else:
        return f"{b / (1024 * 1024):.2f} MB"

def main():
    print(CLEAR_SCREEN, end="")
    print(f"{CYAN}{BOLD}Iniciando Monitor de Telemetria JARVIS...{RESET}")

    last_seen_event_time = ""

    while True:
        telemetry = get_json("/api/debug/telemetry")
        events = get_json("/api/debug/events?limit=15")

        print(CLEAR_SCREEN, end="")
        print(f"{CYAN}{BOLD}╔══════════════════════════════════════════════════════════════════════════════════════╗{RESET}")
        print(f"{CYAN}{BOLD}║         J.A.R.V.I.S. // PAINEL DE TELEMETRIA E MONITORAMENTO EM TEMPO REAL           ║{RESET}")
        print(f"{CYAN}{BOLD}╚══════════════════════════════════════════════════════════════════════════════════════╝{RESET}")

        if not telemetry:
            print(f"\n {RED}{BOLD}● SERVIDOR OFFLINE OU INDISPONÍVEL{RESET} (Tentando conectar em {SERVER_URL})...")
            print(f" {DIM}Certifique-se de que o server.py está em execução.{RESET}\n")
            time.sleep(1.5)
            continue

        conn_status = f"{GREEN}{BOLD}ONLINE / CONECTADO{RESET}" if telemetry.get("active_connection") else f"{YELLOW}AGUARDANDO CLIENTE{RESET}"
        uptime = telemetry.get("uptime", "00:00:00")
        model = telemetry.get("active_model", "N/A")
        voice = telemetry.get("active_voice", "N/A")

        print(f" {BOLD}Status da Conexão:{RESET} {conn_status}   {BOLD}Uptime:{RESET} {uptime}   {BOLD}Modelo:{RESET} {CYAN}{model}{RESET} ({voice})")
        print(f"{DIM}──────────────────────────────────────────────────────────────────────────────────────{RESET}")

        # Linha de Áudio e Pacotes
        u_pkts = telemetry.get("user_audio_packets", 0)
        u_bytes = format_bytes(telemetry.get("user_audio_bytes", 0))
        m_pkts = telemetry.get("model_audio_packets", 0)
        m_bytes = format_bytes(telemetry.get("model_audio_bytes", 0))
        failovers = telemetry.get("failover_count", 0)
        errors = telemetry.get("error_count", 0)

        err_color = RED if errors > 0 else GREEN
        print(f" {BOLD}🎤 Microfone (In):{RESET}  {WHITE}{u_pkts:>6} pkts{RESET} ({u_bytes:<9})  │  {BOLD}🔊 Áudio Gemini (Out):{RESET} {WHITE}{m_pkts:>6} pkts{RESET} ({m_bytes:<9})")
        print(f" {BOLD}🔄 Failovers Pool:{RESET} {YELLOW}{failovers}{RESET} rotações             │  {BOLD}❌ Falhas & Erros:{RESET}      {err_color}{errors}{RESET} registrados")
        print(f"{DIM}──────────────────────────────────────────────────────────────────────────────────────{RESET}")

        # Últimas Transcrições
        last_u = telemetry.get("last_user_transcript") or f"{DIM}(Nenhum comando de voz recente){RESET}"
        last_m = telemetry.get("last_model_transcript") or f"{DIM}(Nenhuma fala recente){RESET}"
        print(f" {CYAN}{BOLD}👤 ÚLTIMA FALA DO USUÁRIO:{RESET}")
        print(f"    \"{last_u[:95]}\"")
        print(f" {MAGENTA}{BOLD}🤖 ÚLTIMA RESPOSTA DO JARVIS:{RESET}")
        print(f"    \"{last_m[:95]}\"")
        print(f"{DIM}──────────────────────────────────────────────────────────────────────────────────────{RESET}")

        # Ferramentas do SO Executadas
        tools_count = telemetry.get("tool_calls_count", 0)
        tools_history = telemetry.get("tool_calls_history", [])
        print(f" {YELLOW}{BOLD}⚙️ FERRAMENTAS DO SISTEMA OPERACIONAL ({tools_count} execuções):{RESET}")
        if tools_history:
            for t in tools_history[-3:]:
                args_str = json.dumps(t.get("args", {}))
                print(f"    • [{t.get('timestamp', '')[11:19]}] {BOLD}{t.get('name')}{RESET}({args_str})")
        else:
            print(f"    {DIM}(Nenhuma ferramenta invocada ainda nesta sessão){RESET}")
        print(f"{DIM}──────────────────────────────────────────────────────────────────────────────────────{RESET}")

        # Stream de Eventos Recentes
        print(f" {WHITE}{BOLD}📋 STREAM DE EVENTOS EM TEMPO REAL:{RESET}")
        if events:
            for ev in events[-6:]:
                t_str = ev.get("timestamp", "")[11:19]
                ev_type = ev.get("type", "").upper()
                d = ev.get("data", {})

                if ev_type == "USER_TEXT":
                    print(f"    [{t_str}] {CYAN}USER{RESET}  -> {d.get('text', '')[:70]}")
                elif ev_type == "MODEL_TEXT":
                    print(f"    [{t_str}] {MAGENTA}MODEL{RESET} -> {d.get('text', '')[:70]}")
                elif ev_type == "TOOL_CALL":
                    print(f"    [{t_str}] {YELLOW}TOOL{RESET}  -> {d.get('name')} {json.dumps(d.get('args', {}))[:50]}")
                elif ev_type == "ERROR":
                    print(f"    [{t_str}] {RED}ERROR{RESET} -> {d.get('message', '')[:70]}")
                elif ev_type == "ACCOUNT_FAILOVER":
                    print(f"    [{t_str}] {YELLOW}SWAP{RESET}  -> Conta {d.get('from_index')} -> Conta {d.get('to_index')}")
                elif "AUDIO" not in ev_type:
                    print(f"    [{t_str}] {DIM}{ev_type:<10}{RESET} {str(d)[:60]}")
        else:
            print(f"    {DIM}Aguardando atividade do assistente...{RESET}")

        print(f"\n {DIM}[Pressione Ctrl+C para sair • Atualização a cada 1s • Web: {SERVER_URL}/debug]{RESET}")
        time.sleep(1.0)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{CYAN}Monitor encerrado.{RESET}\n")
        sys.exit(0)
