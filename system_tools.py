def get_gpu_status() -> dict:
    """Verifica e retorna o uso, temperatura e memória VRAM da GPU dedicada."""
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu", "--format=csv,noheader,nounits"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2.0
        )
        if res.returncode == 0 and res.stdout.strip():
            parts = [p.strip() for p in res.stdout.strip().split(",")]
            if len(parts) >= 6:
                return {
                    "disponivel": True,
                    "modelo": parts[0],
                    "uso_gpu": f"{parts[1]}%",
                    "vram_usada_mb": f"{parts[3]} MB",
                    "vram_total_mb": f"{parts[4]} MB",
                    "temperatura": f"{parts[5]}°C",
                    "mensagem": f"Placa de vídeo {parts[0]}: uso em {parts[1]}%, temperatura em {parts[5]}°C, {parts[3]} MB de {parts[4]} MB VRAM utilizados."
                }
    except Exception:
        pass
    return {"disponivel": False, "mensagem": "Nenhuma GPU dedicada detectada."}

"""
Ferramentas de sistema operacional e automações do JARVIS.
"""
import os
import sys
import subprocess
import datetime
import psutil
import shutil
import glob

NOTES_FILE = os.path.expanduser("~/jarvis_notes.txt")

# Mapeamento de nomes comuns em português para executáveis no Linux
APP_ALIASES = {
    "navegador": "google-chrome",
    "chrome": "google-chrome",
    "browser": "google-chrome",
    "firefox": "firefox",
    "vscode": "code",
    "vs code": "code",
    "codigo": "code",
    "terminal": "x-terminal-emulator",
    "calculadora": "gnome-calculator",
    "calc": "gnome-calculator",
    "arquivos": "nautilus",
    "gerenciador de arquivos": "nautilus",
    "spotify": "spotify",
    "editor": "gedit"
}

def get_system_status() -> dict:
    """
    Retorna telemetria detalhada de hardware: uso de CPU, memória RAM,
    espaço em disco, status da bateria (se disponível) e processos ativos.
    """
    cpu_percent = psutil.cpu_percent(interval=0.1)
    cpu_cores = psutil.cpu_count(logical=True)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    
    battery_info = "Alimentação contínua / Sem bateria"
    battery = psutil.sensors_battery()
    if battery:
        plugged = "conectado à tomada" if battery.power_plugged else "na bateria"
        battery_info = f"{battery.percent}% ({plugged})"

    boot_time = datetime.datetime.fromtimestamp(psutil.boot_time())
    uptime = datetime.datetime.now() - boot_time
    hours, remainder = divmod(int(uptime.total_seconds()), 3600)
    minutes, _ = divmod(remainder, 60)

    return {
        "cpu_percent": f"{cpu_percent}%",
        "cpu_cores": cpu_cores,
        "ram_used_gb": f"{memory.used / (1024**3):.2f} GB",
        "ram_total_gb": f"{memory.total / (1024**3):.2f} GB",
        "ram_percent": f"{memory.percent}%",
        "disk_free_gb": f"{disk.free / (1024**3):.2f} GB",
        "disk_percent": f"{disk.percent}%",
        "battery": battery_info,
        "gpu": get_gpu_status(),
        "uptime": f"{hours} horas e {minutes} minutos",
        "status_geral": "Todos os subsistemas operando em parâmetros nominais, senhor."
    }

def get_current_datetime() -> dict:
    """Retorna data, dia da semana e horário exato com precisão de segundos."""
    now = datetime.datetime.now()
    dias = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]
    dia_semana = dias[now.weekday()]
    return {
        "data": now.strftime("%d/%m/%Y"),
        "hora": now.strftime("%H:%M:%S"),
        "dia_da_semana": dia_semana,
        "mensagem": f"Hoje é {dia_semana}, dia {now.strftime('%d de %B de %Y')}, e são {now.strftime('%H horas e %M minutos')}."
    }

def list_installed_games(filter_name: str = "") -> dict:
    """
    Varre e lista todos os jogos e aplicativos instalados no computador,
    identificando a distribuidora/plataforma (Steam, Lutris, Epic/Heroic, Wine ou Nativo),
    a pasta de instalação e comandos de execução.
    """
    games = []
    vdf_paths = [
        os.path.expanduser("~/.steam/steam/steamapps/libraryfolders.vdf"),
        os.path.expanduser("~/.local/share/Steam/steamapps/libraryfolders.vdf"),
        os.path.expanduser("~/.var/app/com.valvesoftware.Steam/data/Steam/steamapps/libraryfolders.vdf"),
        "/run/media/edu/gamer/SteamLibrary/steamapps/libraryfolders.vdf"
    ]
    steam_lib_dirs = set([
        os.path.expanduser("~/.steam/steam/steamapps"),
        os.path.expanduser("~/.local/share/Steam/steamapps"),
        "/run/media/edu/gamer/SteamLibrary/steamapps"
    ])

    for vp in vdf_paths:
        if os.path.exists(vp):
            try:
                with open(vp, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if '"path"' in line:
                            parts = line.split('"')
                            if len(parts) >= 4:
                                p = os.path.join(parts[3], "steamapps")
                                if os.path.exists(p):
                                    steam_lib_dirs.add(p)
            except Exception:
                pass

    ignored_names = ["Steamworks Common Redistributables", "Proton", "Steam Linux Runtime"]

    for sdir in steam_lib_dirs:
        if not os.path.exists(sdir):
            continue
        for acf in glob.glob(os.path.join(sdir, "appmanifest_*.acf")):
            try:
                with open(acf, "r", encoding="utf-8", errors="ignore") as f:
                    txt = f.read()
                name = ""
                appid = ""
                installdir = ""
                for line in txt.splitlines():
                    if '"name"' in line and not name:
                        parts = line.split('"')
                        if len(parts) >= 4: name = parts[3]
                    elif '"appid"' in line and not appid:
                        parts = line.split('"')
                        if len(parts) >= 4: appid = parts[3]
                    elif '"installdir"' in line and not installdir:
                        parts = line.split('"')
                        if len(parts) >= 4: installdir = parts[3]

                if name and appid and not any(ign in name for ign in ignored_names):
                    games.append({
                        "nome": name,
                        "distribuidora": "Steam",
                        "appid": appid,
                        "pasta": os.path.join(sdir, "common", installdir) if installdir else sdir,
                        "comando": f"steam steam://rungameid/{appid}"
                    })
            except Exception:
                pass

    # Varre .desktop locais em busca de outros jogos (Lutris, Wine, etc.)
    desktop_dirs = [
        os.path.expanduser("~/.local/share/applications"),
        "/usr/share/applications"
    ]
    for d in desktop_dirs:
        if not os.path.exists(d):
            continue
        for df in glob.glob(os.path.join(d, "*.desktop")):
            try:
                with open(df, "r", encoding="utf-8", errors="ignore") as fp:
                    dtxt = fp.read()
                dname = ""
                dexec = ""
                dcats = ""
                for l in dtxt.splitlines():
                    if l.startswith("Name=") and not dname:
                        dname = l.split("=", 1)[1]
                    elif l.startswith("Exec=") and not dexec:
                        dexec = l.split("=", 1)[1]
                    elif l.startswith("Categories="):
                        dcats = l.split("=", 1)[1]

                if ("Game" in dcats or "lutris" in dexec.lower()) and dname:
                    dist = "Lutris" if "lutris" in dexec.lower() else "Nativo Linux"
                    if not any(g["nome"].lower() == dname.lower() for g in games):
                        games.append({
                            "nome": dname,
                            "distribuidora": dist,
                            "appid": None,
                            "pasta": df,
                            "comando": dexec.split("%")[0].strip()
                        })
            except Exception:
                pass

    # Deduplica
    seen = set()
    unique = []
    for g in games:
        key = (g["nome"].lower(), g["distribuidora"])
        if key not in seen:
            seen.add(key)
            unique.append(g)

    if filter_name:
        q = filter_name.strip().lower()
        alias_filter = {
            "gta": "grand theft auto",
            "gta 5": "grand theft auto v",
            "gta v": "grand theft auto v",
            "rdr2": "red dead redemption 2",
            "red dead": "red dead redemption 2",
            "mk11": "mortal kombat 11",
            "rivals": "marvel rivals"
        }
        q_target = alias_filter.get(q, q)
        unique = [g for g in unique if q in g["nome"].lower() or q_target in g["nome"].lower() or q in g["distribuidora"].lower()]

    nomes = [g["nome"] for g in unique]
    if unique:
        resumo = f"Senhor, localizei {len(unique)} jogo(s) na sua biblioteca: " + ", ".join(nomes[:6])
        if len(nomes) > 6:
            resumo += f" e mais {len(nomes) - 6} outros."
        else:
            resumo += "."
    else:
        resumo = "Nenhum jogo localizado com esses parâmetros, senhor."

    return {
        "sucesso": True,
        "total": len(unique),
        "jogos": unique,
        "mensagem": resumo
    }

def open_application(app_name: str) -> dict:
    """
    Inicia um aplicativo, jogo ou utilitário no computador do usuário.
    Aceita nomes diretos de jogos (ex: 'Marvel Rivals', 'GTA', 'Red Dead', 'Overwatch', etc.)
    ou utilitários do sistema operacional (ex: 'steam', 'terminal', 'calculadora', 'chrome').
    """
    clean_name = app_name.strip().lower()

    # 1. Verifica se corresponde a algum jogo instalado
    aliases_game = {
        "gta": "grand theft auto",
        "gta 5": "grand theft auto v",
        "gta v": "grand theft auto v",
        "rdr2": "red dead redemption 2",
        "red dead": "red dead redemption 2",
        "mk11": "mortal kombat 11",
        "mortal kombat": "mortal kombat 11",
        "star wars": "star wars jedi",
        "jedi": "star wars jedi",
        "detroit": "detroit: become human",
        "rivals": "marvel rivals",
        "marvel rivals": "marvel rivals",
        "overwatch": "overwatch",
        "uncharted": "uncharted"
    }
    target_match = aliases_game.get(clean_name, clean_name)

    try:
        games_info = list_installed_games()
        for g in games_info.get("jogos", []):
            gn = g["nome"].lower()
            if target_match in gn or gn in target_match:
                cmd = g["comando"].split()
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                return {
                    "sucesso": True,
                    "tipo": "jogo",
                    "jogo": g["nome"],
                    "distribuidora": g["distribuidora"],
                    "mensagem": f"Iniciando o jogo '{g['nome']}' através da {g['distribuidora']}, senhor. Bom jogo!"
                }
    except Exception as exc:
        pass

    # 2. Aliases e binários do sistema
    target_exec = APP_ALIASES.get(clean_name, clean_name)
    resolved = shutil.which(target_exec)
    if not resolved:
        if "terminal" in clean_name:
            for term in ["gnome-terminal", "konsole", "xfce4-terminal", "xterm"]:
                if shutil.which(term):
                    resolved = term
                    break
        elif "calc" in clean_name:
            for calc in ["gnome-calculator", "kcalc", "galculator"]:
                if shutil.which(calc):
                    resolved = calc
                    break

    if not resolved:
        # Tenta lançar via gtk-launch se existir .desktop
        for d in [os.path.expanduser("~/.local/share/applications"), "/usr/share/applications"]:
            if os.path.exists(d):
                for df in glob.glob(os.path.join(d, "*.desktop")):
                    base = os.path.basename(df).lower()
                    if clean_name in base:
                        desktop_id = os.path.basename(df)
                        try:
                            subprocess.Popen(["gtk-launch", desktop_id], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                            return {
                                "sucesso": True,
                                "mensagem": f"Aplicativo '{desktop_id}' inicializado com sucesso, senhor."
                            }
                        except Exception:
                            pass

        return {
            "sucesso": False,
            "mensagem": f"Desculpe, senhor. Não localizei o executável ou jogo '{app_name}' instalado no sistema."
        }

    try:
        subprocess.Popen([resolved], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return {
            "sucesso": True,
            "mensagem": f"Aplicativo '{target_exec}' inicializado com sucesso, senhor."
        }
    except Exception as e:
        return {
            "sucesso": False,
            "mensagem": f"Falha ao inicializar o aplicativo: {str(e)}"
        }


def search_web(query: str) -> dict:
    """
    Abre o navegador padrão com a pesquisa solicitada pelo usuário no Google.
    """
    import urllib.parse
    encoded = urllib.parse.quote(query)
    search_url = f"https://www.google.com/search?q={encoded}"
    try:
        subprocess.Popen(["xdg-open", search_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return {
            "sucesso": True,
            "query": query,
            "mensagem": f"Abrindo resultados de busca para '{query}' no seu navegador, senhor."
        }
    except Exception as e:
        return {
            "sucesso": False,
            "mensagem": f"Não foi possível abrir o navegador para a pesquisa: {str(e)}"
        }

def adjust_volume(action: str, percent: int = 10) -> dict:
    """
    Ajusta o volume do áudio do sistema operacional.
    Valores para 'action': 'aumentar', 'diminuir', 'mutar', 'desmutar', 'definir'.
    """
    try:
        if action == "aumentar":
            subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"+{percent}%"], check=True)
            msg = f"Volume aumentado em {percent}%, senhor."
        elif action == "diminuir":
            subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"-{percent}%"], check=True)
            msg = f"Volume reduzido em {percent}%, senhor."
        elif action == "mutar":
            subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"], check=True)
            msg = "Áudio do sistema silenciado, senhor."
        elif action == "desmutar":
            subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"], check=True)
            msg = "Áudio reativado, senhor."
        elif action == "definir":
            subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{percent}%"], check=True)
            msg = f"Volume calibrado para {percent}%, senhor."
        else:
            msg = f"Ação de volume '{action}' não compreendida."
        return {"sucesso": True, "mensagem": msg}
    except Exception as e:
        # Tenta fallback com amixer
        try:
            if action == "aumentar":
                subprocess.run(["amixer", "-D", "pulse", "sset", "Master", f"{percent}%+"], check=True)
            elif action == "diminuir":
                subprocess.run(["amixer", "-D", "pulse", "sset", "Master", f"{percent}%-"], check=True)
            return {"sucesso": True, "mensagem": f"Volume ajustado via amixer, senhor."}
        except Exception as e2:
            return {"sucesso": False, "mensagem": f"Não foi possível alterar o volume: {str(e2)}"}

def take_quick_note(note_text: str) -> dict:
    """Registra uma anotação rápida solicitada pelo usuário no bloco de notas do JARVIS."""
    timestamp = datetime.datetime.now().strftime("[%d/%m/%Y %H:%M:%S]")
    entry = f"{timestamp} {note_text.strip()}\n"
    try:
        with open(NOTES_FILE, "a", encoding="utf-8") as f:
            f.write(entry)
        return {
            "sucesso": True,
            "mensagem": f"Anotação registrada com precisão em seus arquivos, senhor: '{note_text}'"
        }
    except Exception as e:
        return {"sucesso": False, "mensagem": f"Erro ao gravar anotação: {str(e)}"}

def read_notes() -> dict:
    """Lê as anotações e lembretes arquivados."""
    if not os.path.exists(NOTES_FILE):
        return {"sucesso": True, "notas": "Nenhuma anotação encontrada em seus arquivos, senhor."}
    try:
        with open(NOTES_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        recent = lines[-5:] if len(lines) > 5 else lines
        return {
            "sucesso": True,
            "total_notas": len(lines),
            "notas_recentes": "".join(recent).strip()
        }
    except Exception as e:
        return {"sucesso": False, "mensagem": f"Erro ao acessar notas: {str(e)}"}

# ----------------- INTEGRAÇÃO COM ANTIGRAVITY IDE & MCP -----------------
def antigravity_open_workspace(path: str = "/home/edu/Documentos/assistente") -> dict:
    """Abre um diretório ou projeto na IDE Antigravity."""
    target_path = os.path.expanduser(path)
    if not os.path.exists(target_path):
        return {"sucesso": False, "mensagem": f"O diretório '{target_path}' não foi encontrado, senhor."}
    try:
        subprocess.Popen(["/usr/bin/antigravity", target_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"sucesso": True, "mensagem": f"Projeto em '{target_path}' aberto com sucesso na IDE Antigravity, senhor."}
    except Exception as e:
        return {"sucesso": False, "mensagem": f"Falha ao abrir a IDE Antigravity: {str(e)}"}

def antigravity_open_file(file_path: str, line_number: int = 1) -> dict:
    """Abre um arquivo de código diretamente na IDE Antigravity, focando na linha desejada."""
    full_path = os.path.expanduser(file_path)
    if not os.path.isabs(full_path):
        full_path = os.path.abspath(os.path.join(os.path.dirname(__file__), full_path))
    if not os.path.exists(full_path):
        return {"sucesso": False, "mensagem": f"Arquivo '{full_path}' não localizado, senhor."}
    try:
        cmd = ["/usr/bin/antigravity", "-g", f"{full_path}:{line_number or 1}"]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"sucesso": True, "mensagem": f"Arquivo '{os.path.basename(full_path)}' aberto na linha {line_number or 1} na IDE Antigravity, senhor."}
    except Exception as e:
        return {"sucesso": False, "mensagem": f"Falha ao abrir arquivo na IDE Antigravity: {str(e)}"}

def antigravity_list_mcps() -> dict:
    """Lista todos os servidores MCP configurados e ativos na IDE Antigravity."""
    try:
        res = subprocess.run(["/home/edu/.local/bin/agy", "mcp", "list"], capture_output=True, text=True, timeout=10)
        output = res.stdout.strip()
        return {
            "sucesso": True,
            "raw_output": output,
            "mensagem": f"Senhor, obtive a lista de servidores MCP da IDE Antigravity:\n{output}"
        }
    except Exception as e:
        return {"sucesso": False, "mensagem": f"Erro ao consultar servidores MCP do Antigravity: {str(e)}"}

# Estado global do Modo IDE
IDE_MODE_ACTIVE = False

def set_ide_mode(enabled: bool) -> dict:
    """Ativa ou desativa o Modo IDE contínuo entre o JARVIS e a IDE Antigravity."""
    global IDE_MODE_ACTIVE
    IDE_MODE_ACTIVE = bool(enabled)
    if IDE_MODE_ACTIVE:
        return {
            "sucesso": True,
            "ide_mode": True,
            "mensagem": "Modo IDE ativado com sucesso, senhor. A partir de agora, qualquer instrução técnica ou comando de código será direcionado diretamente ao agente Antigravity, mantendo o contexto contínuo da conversa."
        }
    else:
        return {
            "sucesso": True,
            "ide_mode": False,
            "mensagem": "Modo IDE desativado. Retornando ao protocolo padrão do sistema, senhor."
        }

def get_ide_mode() -> bool:
    """Retorna True se o modo IDE estiver ativo."""
    return IDE_MODE_ACTIVE

def antigravity_open_gemini_bridge() -> dict:
    """Abre a pasta 'gemini' de auditoria e canal direto de mensagens na IDE Antigravity."""
    import gemini_bridge
    return gemini_bridge.open_gemini_bridge()

def antigravity_run_prompt(prompt: str, continue_session: bool = True) -> dict:
    """Envia uma solicitação ou instrução técnica diretamente para o agente de IA da IDE Antigravity (agy CLI). Mantém o contexto de conversas anteriores."""
    clean_p = prompt.strip()
    if not clean_p:
        return {"sucesso": False, "mensagem": "Instrução para o Antigravity não pode ser vazia, senhor."}
    try:
        cmd = ["/home/edu/.local/bin/agy"]
        if continue_session:
            cmd.append("-c")
        cmd.extend(["-p", clean_p, "--dangerously-skip-permissions"])
        res = subprocess.run(
            cmd,
            cwd="/home/edu/Documentos/assistente",
            capture_output=True,
            text=True,
            timeout=60
        )
        ans = res.stdout.strip() or res.stderr.strip()
        resumo = ans[:350] + ("..." if len(ans) > 350 else "")

        # Registra no log de auditoria da pasta gemini
        try:
            import gemini_bridge
            gemini_bridge.log_audit_event("JARVIS_PROMPT", "dispatch_to_antigravity", clean_p)
            gemini_bridge.log_audit_event("ANTIGRAVITY", "response_received", ans)
            gemini_bridge.update_latest_response(f"Prompt: {clean_p[:60]}", ans)
        except Exception:
            pass

        return {
            "sucesso": True,
            "resposta_completa": ans,
            "mensagem": f"O agente Antigravity respondeu: {resumo}"
        }
    except subprocess.TimeoutExpired:
        return {"sucesso": False, "mensagem": "O agente Antigravity continua processando em segundo plano, senhor."}
    except Exception as e:
        return {"sucesso": False, "mensagem": f"Falha ao comunicar com o agente Antigravity: {str(e)}"}

# Declarações de Schema para Gemini Function Calling
GEMINI_FUNCTION_DECLARATIONS = [
    {
        "name": "list_installed_games",
        "description": "Varre e lista todos os jogos e aplicativos instalados no computador, identificando a distribuidora/plataforma (Steam, Lutris, Epic Games/Heroic, Wine ou Nativo Linux), a pasta de instalação e comandos de inicialização.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "filter_name": {
                    "type": "STRING",
                    "description": "Filtro opcional para buscar um jogo ou distribuidora específica (ex: 'Steam', 'GTA', 'Rivals')."
                }
            }
        }
    },
    {
        "name": "get_gpu_status",
        "description": "Obtém a telemetria em tempo real da placa de vídeo dedicada (GPU NVIDIA GeForce RTX): uso em porcentagem, VRAM utilizada e total, e temperatura em graus Celsius.",
        "parameters": {"type": "OBJECT", "properties": {}}
    },
    {
        "name": "get_system_status",
        "description": "Obtém a telemetria em tempo real do sistema: uso de CPU, memória RAM, bateria, disco e tempo ligado.",
        "parameters": {"type": "OBJECT", "properties": {}}
    },
    {
        "name": "get_current_datetime",
        "description": "Obtém data, dia da semana e horário exato.",
        "parameters": {"type": "OBJECT", "properties": {}}
    },
    {
        "name": "open_application",
        "description": "Abre um programa ou aplicativo local no computador (ex: 'chrome', 'vscode', 'terminal', 'calculadora').",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Nome do programa desejado."
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "search_web",
        "description": "Pesquisa um termo ou assunto na web abrindo o navegador do usuário.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "O termo ou pergunta a ser pesquisada."
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "adjust_volume",
        "description": "Ajusta o volume do áudio do computador.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "Ação: 'aumentar', 'diminuir', 'mutar', 'desmutar', ou 'definir'."
                },
                "percent": {
                    "type": "INTEGER",
                    "description": "Porcentagem do ajuste (ex: 10 para aumentar 10%, ou 50 para definir em 50%). Opcional."
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "take_quick_note",
        "description": "Salva uma anotação rápida ou lembrete para o usuário.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "note_text": {
                    "type": "STRING",
                    "description": "O conteúdo da anotação a ser lembrada."
                }
            },
            "required": ["note_text"]
        }
    },
    {
        "name": "read_notes",
        "description": "Lê as anotações e lembretes recentes arquivados pelo JARVIS.",
        "parameters": {"type": "OBJECT", "properties": {}}
    },
    {
        "name": "antigravity_open_workspace",
        "description": "Abre um diretório ou projeto de desenvolvimento na IDE Antigravity.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {
                    "type": "STRING",
                    "description": "Caminho absoluto ou relativo da pasta/projeto a abrir na IDE (padrão: diretório do assistente)."
                }
            }
        }
    },
    {
        "name": "antigravity_open_file",
        "description": "Abre um arquivo específico de código na IDE Antigravity, focando diretamente no número da linha desejada.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "file_path": {
                    "type": "STRING",
                    "description": "Caminho do arquivo a ser aberto (ex: 'server.py' ou '/home/edu/Documentos/assistente/system_tools.py')."
                },
                "line_number": {
                    "type": "INTEGER",
                    "description": "Número da linha na qual posicionar o cursor (padrão: 1)."
                }
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "antigravity_list_mcps",
        "description": "Lista todos os servidores MCP (Model Context Protocol) configurados e ativos na IDE Antigravity.",
        "parameters": {"type": "OBJECT", "properties": {}}
    },
    {
        "name": "antigravity_run_prompt",
        "description": "Envia uma solicitação ou comando técnico diretamente para o agente autônomo da IDE Antigravity (agy CLI).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "prompt": {
                    "type": "STRING",
                    "description": "Instrução ou tarefa para o agente Antigravity executar."
                }
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "set_ide_mode",
        "description": "Ativa ou desativa o Modo IDE contínuo. Use enabled=true quando o usuário disser 'iniciar modo IDE', 'ativar modo IDE' ou 'modo desenvolvedor'. Use enabled=false quando disser 'sair do modo IDE', 'desativar modo IDE' ou 'encerrar modo IDE'.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "enabled": {
                    "type": "BOOLEAN",
                    "description": "True para ativar o modo IDE, False para desativar."
                }
            },
            "required": ["enabled"]
        }
    },
    {
        "name": "antigravity_open_gemini_bridge",
        "description": "Abre a pasta 'gemini' de auditoria e canal direto de mensagens na IDE Antigravity.",
        "parameters": {"type": "OBJECT", "properties": {}}
    }
]

TOOL_REGISTRY = {
    "list_installed_games": list_installed_games,
    "get_gpu_status": get_gpu_status,
    "get_system_status": get_system_status,
    "get_current_datetime": get_current_datetime,
    "open_application": open_application,
    "search_web": search_web,
    "adjust_volume": adjust_volume,
    "take_quick_note": take_quick_note,
    "read_notes": read_notes,
    "antigravity_open_workspace": antigravity_open_workspace,
    "antigravity_open_file": antigravity_open_file,
    "antigravity_list_mcps": antigravity_list_mcps,
    "antigravity_run_prompt": antigravity_run_prompt,
    "set_ide_mode": set_ide_mode,
    "antigravity_open_gemini_bridge": antigravity_open_gemini_bridge,
}



