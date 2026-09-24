"""
Ferramentas de sistema operacional e automações do JARVIS.
"""
import os
import subprocess
import datetime
import psutil
import shutil
import glob
import json
import shlex
import time
import threading
from typing import List, Optional
import preferences_manager
import controller_engine
import perfil_maquina
import processos
import rede_segura
import gemini_bridge
from gemini_bridge import AGY_BIN, ANTIGRAVITY_BIN, WORKSPACE_DIR

_last_gpu_result = None
_last_gpu_time = 0.0
_last_gpu_lock = threading.Lock()

def _gpu_nvidia() -> Optional[dict]:
    """Telemetria completa pelo nvidia-smi (driver proprietário da NVIDIA)."""
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu", "--format=csv,noheader,nounits"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2.0
        )
    except Exception:
        return None
    if res.returncode != 0 or not res.stdout.strip():
        return None
    parts = [p.strip() for p in res.stdout.strip().splitlines()[0].split(",")]
    if len(parts) < 6:
        return None
    return {
        "disponivel": True,
        "modelo": parts[0],
        "uso_gpu": f"{parts[1]}%",
        "vram_usada_mb": f"{parts[3]} MB",
        "vram_total_mb": f"{parts[4]} MB",
        "temperatura": f"{parts[5]}°C",
        "mensagem": f"Placa de vídeo {parts[0]}: uso em {parts[1]}%, temperatura em {parts[5]}°C, {parts[3]} MB de {parts[4]} MB VRAM utilizados."
    }


def _gpu_sysfs() -> Optional[dict]:
    """Telemetria pela interface padrão do kernel (GPUs AMD com driver amdgpu)."""
    t = perfil_maquina.telemetria_gpu_sysfs()
    if not t:
        return None
    uso = f"{t['uso_percentual']}%"
    temperatura = f"{t['temperatura_c']}°C" if t["temperatura_c"] is not None else None
    vram_usada = f"{t['vram_usada_mb']} MB" if t["vram_usada_mb"] is not None else None
    vram_total = f"{t['vram_total_mb']} MB" if t["vram_total_mb"] is not None else None
    detalhes = [f"uso em {uso}"]
    if temperatura:
        detalhes.append(f"temperatura em {temperatura}")
    if vram_usada and vram_total:
        detalhes.append(f"{vram_usada} de {vram_total} VRAM utilizados")
    return {
        "disponivel": True,
        "modelo": t["modelo"],
        "uso_gpu": uso,
        "vram_usada_mb": vram_usada,
        "vram_total_mb": vram_total,
        "temperatura": temperatura,
        "mensagem": f"Placa de vídeo {t['modelo']}: " + ", ".join(detalhes) + "."
    }


def _gpu_sem_telemetria() -> dict:
    """GPUs sem interface de telemetria (Intel, nouveau, VMs): informa ao menos o modelo."""
    placas = perfil_maquina.placas_de_video()
    if not placas:
        return {"disponivel": False, "mensagem": "Nenhuma placa de vídeo foi detectada nesta máquina, senhor."}
    principal = placas[0]
    return {
        "disponivel": True,
        "modelo": principal["modelo"],
        "tipo": principal["tipo"],
        "telemetria_em_tempo_real": False,
        "mensagem": (
            f"Placa de vídeo {principal['modelo']} ({principal['tipo']}) detectada, senhor. "
            "O driver dela não expõe uso e temperatura em tempo real."
        )
    }


def get_gpu_status() -> dict:
    """Uso, VRAM e temperatura da placa de vídeo, em qualquer fabricante.

    NVIDIA via nvidia-smi, AMD via sysfs (amdgpu); nas demais informa o modelo.
    """
    global _last_gpu_result, _last_gpu_time
    now = time.monotonic()
    if _last_gpu_result is not None and (now - _last_gpu_time < 2.0):
        return _last_gpu_result

    with _last_gpu_lock:
        now = time.monotonic()
        if _last_gpu_result is not None and (now - _last_gpu_time < 2.0):
            return _last_gpu_result
        try:
            _last_gpu_result = _gpu_nvidia() or _gpu_sysfs() or _gpu_sem_telemetria()
        except Exception:
            _last_gpu_result = {"disponivel": False, "mensagem": "Não foi possível consultar a placa de vídeo."}
        _last_gpu_time = now
        return _last_gpu_result

NOTES_FILE = os.path.expanduser("~/jarvis_notes.txt")

def get_system_status() -> dict:
    """
    Retorna telemetria detalhada de hardware: uso de CPU, memória RAM,
    espaço em disco, status da bateria (se disponível) e processos ativos.
    """
    cpu_percent = psutil.cpu_percent(interval=None)
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
        "status_geral": "Todos os subsistemas operando em parâmetros nominais, senhor.",
        "mensagem": f"CPU em {cpu_percent}%, Memória RAM em {memory.percent}% ({memory.used / (1024**3):.1f} GB de {memory.total / (1024**3):.1f} GB usados), Disco com {disk.free / (1024**3):.1f} GB livres. Todos os subsistemas nominais."
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

_mounts_cache: set = set()
_mounts_cache_time: float = 0.0
_mounts_cache_lock = threading.Lock()
MOUNTS_CACHE_TTL_S = 60.0


def _discover_slow_mounts() -> set:
    """Descobre pontos de montagem externos com cache TTL.

    Stat/glob em /run/media, /media e /mnt podem bloquear segundos em discos
    lentos/desconectados; a varredura roda no máximo uma vez por TTL.
    """
    global _mounts_cache, _mounts_cache_time
    now = time.monotonic()
    if _mounts_cache and (now - _mounts_cache_time) < MOUNTS_CACHE_TTL_S:
        return set(_mounts_cache)
    with _mounts_cache_lock:
        now = time.monotonic()
        if _mounts_cache and (now - _mounts_cache_time) < MOUNTS_CACHE_TTL_S:
            return set(_mounts_cache)
        discovered = set()
        try:
            with open("/proc/mounts", "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        mp = parts[1].replace("\\040", " ")
                        if mp.startswith("/run/media/") or mp.startswith("/media/") or mp.startswith("/mnt/"):
                            if os.path.isdir(mp):
                                discovered.add(mp)
        except Exception:
            pass
        for base in ["/run/media", "/media", "/mnt"]:
            if os.path.exists(base):
                for pattern in [os.path.join(base, "*"), os.path.join(base, "*", "*")]:
                    for d in glob.glob(pattern):
                        if os.path.isdir(d):
                            discovered.add(d)
        _mounts_cache = discovered
        _mounts_cache_time = now
        return set(discovered)


def list_installed_games(filter_name: str = "") -> dict:
    """
    Varre e lista todos os jogos e aplicativos instalados no computador,
    identificando a distribuidora/plataforma (Steam, Lutris, Epic/Heroic, Wine ou Nativo),
    a pasta de instalação e comandos de execução.
    """
    games = []

    # 1. Descoberta dinâmica de pontos de montagem (SSDs, HDs e mídias externas) com cache,
    # incluindo discos montados em caminhos próprios (ex.: /data, /jogos)
    discovered_mounts = _discover_slow_mounts()
    discovered_mounts |= {m["ponto_de_montagem"] for m in perfil_maquina.montagens() if m["ponto_de_montagem"] != "/"}

    # 2. Descoberta de bibliotecas Steam locais e em outros discos/SSDs
    vdf_paths = [
        os.path.expanduser("~/.steam/steam/steamapps/libraryfolders.vdf"),
        os.path.expanduser("~/.local/share/Steam/steamapps/libraryfolders.vdf"),
        os.path.expanduser("~/.var/app/com.valvesoftware.Steam/data/Steam/steamapps/libraryfolders.vdf"),
    ]
    steam_lib_dirs = set([
        os.path.expanduser("~/.steam/steam/steamapps"),
        os.path.expanduser("~/.local/share/Steam/steamapps"),
    ])

    for m in discovered_mounts:
        for rel in [
            "SteamLibrary/steamapps",
            "steamapps",
            "Program Files (x86)/Steam/steamapps",
            "Program Files/Steam/steamapps"
        ]:
            cand = os.path.join(m, rel)
            if os.path.isdir(cand):
                steam_lib_dirs.add(cand)
                vdf = os.path.join(cand, "libraryfolders.vdf")
                if os.path.exists(vdf):
                    vdf_paths.append(vdf)

    for vp in vdf_paths:
        if os.path.exists(vp):
            try:
                with open(vp, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if '"path"' in line:
                            parts = line.split('"')
                            if len(parts) >= 4:
                                raw_p = parts[3].replace("\\\\", "/")
                                if raw_p.startswith("/"):
                                    p = os.path.join(raw_p, "steamapps")
                                    if os.path.exists(p):
                                        steam_lib_dirs.add(p)
                                else:
                                    folder_name = os.path.basename(raw_p.rstrip("/\\"))
                                    if folder_name:
                                        for m in discovered_mounts:
                                            cand = os.path.join(m, folder_name, "steamapps")
                                            if os.path.exists(cand):
                                                steam_lib_dirs.add(cand)
            except Exception:
                pass

    ignored_names = ["Steamworks Common Redistributables", "Proton", "Steam Linux Runtime"]

    # Steam nativo expõe o binário "steam"; o Flatpak só registra o protocolo steam://
    lancador_steam = "steam" if shutil.which("steam") else "xdg-open"

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
                    pasta_jogo = os.path.join(sdir, "common", installdir) if installdir else sdir
                    games.append({
                        "nome": name,
                        "distribuidora": "Steam",
                        "appid": appid,
                        "pasta": pasta_jogo,
                        "disco": perfil_maquina.identificar_disco(pasta_jogo),
                        "comando": f"{lancador_steam} steam://rungameid/{appid}"
                    })
            except Exception:
                pass

    # 3. Varredura de jogos GOG Galaxy instalados nos discos/SSDs
    gog_dirs = [
        os.path.expanduser("~/.local/share/GOG.com"),
        os.path.expanduser("~/GOG Games"),
    ]
    for m in discovered_mounts:
        gog_dirs.extend([
            os.path.join(m, "Program Files (x86)/GOG Galaxy/Games"),
            os.path.join(m, "Program Files/GOG Galaxy/Games"),
            os.path.join(m, "GOG Games"),
        ])

    for gd in gog_dirs:
        if not os.path.isdir(gd):
            continue
        try:
            for entry in os.listdir(gd):
                game_dir = os.path.join(gd, entry)
                if not os.path.isdir(game_dir):
                    continue
                info_files = glob.glob(os.path.join(game_dir, "goggame-*.info"))
                if info_files:
                    try:
                        with open(info_files[0], "r", encoding="utf-8", errors="ignore") as f:
                            info_data = json.load(f)
                        g_name = info_data.get("name", entry)
                        g_id = str(info_data.get("gameId", ""))
                        games.append({
                            "nome": g_name,
                            "distribuidora": "GOG Galaxy",
                            "appid": g_id,
                            "pasta": game_dir,
                            "disco": perfil_maquina.identificar_disco(game_dir),
                            "comando": f"xdg-open {shlex.quote(game_dir)}"
                        })
                    except Exception:
                        pass
        except Exception:
            pass

    # 4. Atalhos .desktop de jogos em todos os diretórios XDG (nativos, Lutris, Heroic, Flatpak e Snap)
    for app in perfil_maquina.aplicativos_instalados():
        exec_min = app["exec"].lower()
        eh_jogo = "Game" in app["categorias"] or any(m in exec_min for m in ("lutris", "heroic", "steam://rungameid"))
        nome = app["nome_original"] or app["nome"]
        argv = perfil_maquina.argv_do_exec(app["exec"])
        if app["oculto"] or not eh_jogo or not nome or not argv:
            continue
        if "lutris" in exec_min:
            dist = "Lutris"
        elif "heroic" in exec_min:
            dist = "Heroic"
        elif "steam://rungameid" in exec_min:
            dist = "Steam"
        elif "flatpak" in os.path.basename(argv[0]):
            dist = "Flatpak"
        elif "/snap/" in argv[0] or app["arquivo"].startswith("/var/lib/snapd"):
            dist = "Snap"
        else:
            dist = "Nativo Linux"
        if not any(g["nome"].lower() == nome.lower() for g in games):
            games.append({
                "nome": nome,
                "distribuidora": dist,
                "appid": None,
                "pasta": app["arquivo"],
                "disco": perfil_maquina.identificar_disco(app["arquivo"]),
                "comando": shlex.join(argv)
            })

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
        unique = [g for g in unique if q in g["nome"].lower() or q_target in g["nome"].lower() or q in g["distribuidora"].lower() or q in g.get("disco", "").lower()]

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
    if not app_name or not isinstance(app_name, str):
        return {
            "sucesso": False,
            "mensagem": "Nome de aplicativo inválido."
        }

    clean_name = app_name.strip().lower()

    # Prevenção contra execução de binários arbitrários e path traversal
    if "/" in clean_name or "\\" in clean_name or ".." in clean_name:
        return {
            "sucesso": False,
            "mensagem": "Caminhos de arquivo não são permitidos por segurança. Especifique apenas o nome do aplicativo ou jogo."
        }

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
        # Consulta preferências salvas para este jogo (ex: launcher favorito)
        saved_game_pref = preferences_manager.get_preference("game_preferences", clean_name) or preferences_manager.get_preference("game_preferences", target_match)
        pref_distribuidora = saved_game_pref.get("distribuidora", "").lower() if isinstance(saved_game_pref, dict) else ""

        games_info = list_installed_games()
        all_matches = []
        for g in games_info.get("jogos", []):
            gn = g["nome"].lower()
            if target_match in gn or gn in target_match:
                all_matches.append(g)

        if all_matches:
            # Se o usuário tiver preferência por distribuidora (ex: Epic vs Steam), prioriza
            chosen_game = all_matches[0]
            if pref_distribuidora:
                for cand in all_matches:
                    if pref_distribuidora in cand.get("distribuidora", "").lower():
                        chosen_game = cand
                        break

            cmd = shlex.split(chosen_game["comando"])
            if isinstance(saved_game_pref, dict) and saved_game_pref.get("custom_args"):
                cmd.extend(shlex.split(saved_game_pref["custom_args"]))

            processos.abrir_desanexado(cmd)
            return {
                "sucesso": True,
                "tipo": "jogo",
                "jogo": chosen_game["nome"],
                "distribuidora": chosen_game["distribuidora"],
                "mensagem": f"Iniciando o jogo '{chosen_game['nome']}' através da {chosen_game['distribuidora']}, senhor. Bom jogo!"
            }
    except Exception:
        pass

    # 2. Aplicativos: categorias (navegador, terminal, calculadora...) resolvidas nesta máquina,
    # catálogo de executáveis seguros ou atalho .desktop instalado (inclui Flatpak e Snap).
    # Nunca executa um caminho arbitrário: só binários do catálogo ou atalhos do sistema.
    SAFE_APP_CATALOG = {
        "steam", "code", "firefox", "google-chrome", "chromium", "spotify",
        "discord", "obs", "vlc", "gedit", "nautilus", "x-terminal-emulator",
        "gnome-calculator", "gnome-terminal", "konsole", "xfce4-terminal", "xterm"
    }
    resolvido = None
    if clean_name not in perfil_maquina.ALIASES_DE_CATEGORIA and clean_name in SAFE_APP_CATALOG:
        caminho = shutil.which(clean_name)
        if caminho:
            resolvido = {"tipo": "binario", "argv": [caminho], "rotulo": clean_name}
    if resolvido is None:
        resolvido = perfil_maquina.resolver_aplicativo(clean_name)

    if not resolvido:
        return {
            "sucesso": False,
            "mensagem": f"Desculpe, senhor. Não localizei o aplicativo ou jogo '{app_name}' instalado nesta máquina."
        }

    try:
        if resolvido["tipo"] == "binario":
            processos.abrir_desanexado(resolvido["argv"])
        elif not _iniciar_entrada_desktop(resolvido["entrada"]):
            return {"sucesso": False, "mensagem": f"Não consegui iniciar '{resolvido['rotulo']}', senhor."}
        return {
            "sucesso": True,
            "aplicativo": resolvido["rotulo"],
            "mensagem": f"Aplicativo '{resolvido['rotulo']}' inicializado com sucesso, senhor."
        }
    except Exception as e:
        return {
            "sucesso": False,
            "mensagem": f"Falha ao inicializar o aplicativo: {str(e)}"
        }


def _iniciar_entrada_desktop(entrada: dict) -> bool:
    """Abre um atalho .desktop pelo lançador do próprio desktop; em último caso, pela linha Exec."""
    lancadores = []
    if shutil.which("gtk-launch"):
        lancadores.append(["gtk-launch", entrada["id"]])
    if shutil.which("gio"):
        lancadores.append(["gio", "launch", entrada["arquivo"]])
    for kio in ("kioclient6", "kioclient5", "kioclient"):
        if shutil.which(kio):
            lancadores.append([kio, "exec", entrada["arquivo"]])
            break
    for argv in lancadores:
        try:
            proc = processos.abrir_desanexado(argv)
        except OSError:
            continue
        try:
            if proc.wait(timeout=3) == 0:
                return True
        except subprocess.TimeoutExpired:
            return True  # lançador ainda ativo: o aplicativo está abrindo
    argv = perfil_maquina.argv_do_exec(entrada["exec"])
    if argv and shutil.which(argv[0]):
        processos.abrir_desanexado(argv)
        return True
    return False


def search_web(query: str) -> dict:
    """
    Abre o navegador padrão com a pesquisa solicitada pelo usuário no Google.
    """
    import urllib.parse
    encoded = urllib.parse.quote(query)
    search_url = f"https://www.google.com/search?q={encoded}"
    try:
        processos.abrir_desanexado(["xdg-open", search_url])
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

MENSAGENS_DE_VOLUME = {
    "aumentar": "Volume aumentado em {pct}%, senhor.",
    "diminuir": "Volume reduzido em {pct}%, senhor.",
    "mutar": "Áudio do sistema silenciado, senhor.",
    "desmutar": "Áudio reativado, senhor.",
    "definir": "Volume calibrado para {pct}%, senhor.",
}


def _comandos_de_volume(acao: str, pct: int) -> List[List[str]]:
    """O mesmo ajuste em cada controle de áudio disponível: PulseAudio/PipeWire (pactl),
    PipeWire nativo (wpctl) e ALSA (amixer), nessa ordem."""
    tabelas = [
        ("pactl", {
            "aumentar": ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"+{pct}%"],
            "diminuir": ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"-{pct}%"],
            "mutar": ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"],
            "desmutar": ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"],
            "definir": ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"],
        }),
        ("wpctl", {
            "aumentar": ["wpctl", "set-volume", "-l", "1.5", "@DEFAULT_AUDIO_SINK@", f"{pct}%+"],
            "diminuir": ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{pct}%-"],
            "mutar": ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1"],
            "desmutar": ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"],
            "definir": ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{pct}%"],
        }),
        ("amixer", {
            "aumentar": ["amixer", "-q", "sset", "Master", f"{pct}%+"],
            "diminuir": ["amixer", "-q", "sset", "Master", f"{pct}%-"],
            "mutar": ["amixer", "-q", "sset", "Master", "mute"],
            "desmutar": ["amixer", "-q", "sset", "Master", "unmute"],
            "definir": ["amixer", "-q", "sset", "Master", f"{pct}%"],
        }),
    ]
    return [tabela[acao] for binario, tabela in tabelas if shutil.which(binario)]


def adjust_volume(action: str, percent: int = 10) -> dict:
    """
    Ajusta o volume do áudio do sistema operacional.
    Valores para 'action': 'aumentar', 'diminuir', 'mutar', 'desmutar', 'definir'.
    """
    acao = (action or "").strip().lower()
    if acao not in MENSAGENS_DE_VOLUME:
        return {"sucesso": False, "mensagem": f"Ação de volume '{action}' não compreendida. Use: {', '.join(MENSAGENS_DE_VOLUME)}."}
    try:
        pct = max(0, min(int(percent), 150))
    except (TypeError, ValueError):
        pct = 10

    comandos = _comandos_de_volume(acao, pct)
    if not comandos:
        return {"sucesso": False, "mensagem": "Nenhum controle de áudio encontrado nesta máquina (pactl, wpctl ou amixer), senhor."}
    erros = []
    for cmd in comandos:
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=5)
            return {"sucesso": True, "controle": cmd[0], "mensagem": MENSAGENS_DE_VOLUME[acao].format(pct=pct)}
        except Exception as e:
            erros.append(f"{cmd[0]}: {e}")
    return {"sucesso": False, "mensagem": "Não foi possível alterar o volume: " + "; ".join(erros)}

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

def open_website(url: str) -> dict:
    """Abre qualquer site ou endereço web diretamente no navegador padrão."""
    clean_url = url.strip()
    if not clean_url.startswith("http://") and not clean_url.startswith("https://"):
        clean_url = "https://" + clean_url
    try:
        processos.abrir_desanexado(["xdg-open", clean_url])
        return {
            "sucesso": True,
            "url": clean_url,
            "mensagem": f"Acessando o site '{clean_url}' no seu navegador, senhor."
        }
    except Exception as e:
        return {"sucesso": False, "mensagem": f"Falha ao abrir o site: {str(e)}"}

def read_web_page(url: str, max_chars: int = 4000) -> dict:
    """
    Lê e extrai o conteúdo textual legível de uma página ou artigo da web (notícias, documentação, artigos, etc.)
    para que você possa ler, explicar ou resumir as informações diretamente ao senhor em áudio.
    """
    from html.parser import HTMLParser

    clean_url = url.strip()
    if not clean_url.startswith("http://") and not clean_url.startswith("https://"):
        clean_url = "https://" + clean_url

    cabecalhos = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip",
    }

    try:
        # Só páginas públicas: loopback, rede local e metadados de nuvem são recusados,
        # inclusive por redirecionamento, e o download tem teto de tamanho.
        try:
            pagina = rede_segura.ler_url_publica(clean_url, timeout=8, cabecalhos=cabecalhos)
        except rede_segura.DestinoBloqueado as bloqueio:
            return {
                "sucesso": False,
                "url": clean_url,
                "bloqueado": True,
                "mensagem": f"Leitura recusada, senhor: {bloqueio.motivo}. Só leio páginas públicas da internet."
            }
        tipo = pagina.cabecalhos.get_content_type()
        if not (tipo.startswith("text/") or tipo.endswith(("+xml", "+json"))
                or tipo in ("application/xml", "application/json")):
            return {
                "sucesso": False,
                "url": clean_url,
                "mensagem": f"O endereço '{clean_url}' não é uma página de texto ({tipo}), senhor."
            }
        charset = pagina.cabecalhos.get_content_charset() or "utf-8"
        try:
            html_text = pagina.corpo.decode(charset, errors="ignore")
        except LookupError:  # charset desconhecido declarado pelo servidor
            html_text = pagina.corpo.decode("utf-8", errors="ignore")

        title = ""
        text = ""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_text, "html.parser")
            if soup.title and soup.title.string:
                title = soup.title.string.strip()
            for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "svg", "form"]):
                tag.decompose()
            text = " ".join(soup.get_text(separator=" ").split())
        except Exception:
            class SimpleExtractor(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.chunks = []
                    self.in_title = False
                    self.in_ignore = False
                    self.title = ""
                    self.ignore_tags = {"script", "style", "noscript", "header", "footer", "nav", "svg"}

                def handle_starttag(self, tag, attrs):
                    if tag.lower() in self.ignore_tags:
                        self.in_ignore = True
                    if tag.lower() == "title":
                        self.in_title = True

                def handle_endtag(self, tag):
                    if tag.lower() in self.ignore_tags:
                        self.in_ignore = False
                    if tag.lower() == "title":
                        self.in_title = False

                def handle_data(self, data):
                    if self.in_title:
                        self.title += data.strip() + " "
                    elif not self.in_ignore:
                        s = data.strip()
                        if s:
                            self.chunks.append(s)

            parser = SimpleExtractor()
            parser.feed(html_text)
            title = parser.title.strip()
            text = " ".join(parser.chunks)

        limit = max(500, min(int(max_chars), 12000))
        conteudo_truncado = text[:limit]
        if len(text) > limit:
            conteudo_truncado += " ... [conteúdo resumido por limite de tamanho]"

        return {
            "sucesso": True,
            "url": clean_url,
            "titulo": title or "Página da Web",
            "conteudo": conteudo_truncado,
            "tamanho_total": len(text),
            "mensagem": f"Conteúdo da página '{title or clean_url}' extraído com sucesso, senhor."
        }
    except Exception as e:
        return {
            "sucesso": False,
            "url": clean_url,
            "mensagem": f"Não foi possível ler o conteúdo da página '{clean_url}': {str(e)}"
        }

def play_music(query: str, platform: str = None) -> dict:
    """
    Busca e toca qualquer música, álbum ou artista na plataforma preferida do usuário (YouTube, Spotify, Deezer).
    Se 'platform' não for informada, consulta a preferência padrão gravada na memória persistente.
    Se nenhuma preferência estiver cadastrada, solicita que o assistente pergunte a preferência do usuário.
    """
    import urllib.parse

    target_platform = platform.strip().lower() if platform else None
    saved_platform = preferences_manager.get_preference("default_apps", "music_platform")

    # Se a plataforma não veio no parâmetro, checa a memória persistente
    if not target_platform:
        if saved_platform:
            target_platform = str(saved_platform).lower()
        else:
            # Não há preferência cadastrada: avisa para perguntar ao usuário
            return {
                "sucesso": True,
                "status": "definir_preferencia",
                "precisa_perguntar_preferencia": True,
                "query": query,
                "mensagem": (
                    f"Senhor, ainda não temos uma plataforma padrão de música definida em suas preferências. "
                    f"Pergunte educadamente ao senhor: 'Senhor, qual plataforma prefere utilizar como padrão para reproduzir músicas: YouTube ou Spotify?' "
                    f"Quando o senhor responder, salve a escolha dele chamando manage_user_preference(action='set', category='default_apps', key='music_platform', value=escolha) "
                    f"e em seguida toque a música com play_music(query='{query}', platform=escolha)."
                )
            }
    else:
        # Se veio plataforma e a memória ainda estava vazia, ou se foi explicitamente definida, salva como padrão
        if not saved_platform:
            preferences_manager.set_preference("default_apps", "music_platform", target_platform)

    encoded = urllib.parse.quote(query)

    # Execução por plataforma
    if "spotify" in target_platform:
        spotify_bin = shutil.which("spotify")
        if spotify_bin:
            try:
                processos.abrir_desanexado([spotify_bin, f"--uri=spotify:search:{encoded}"])
                return {
                    "sucesso": True,
                    "plataforma": "Spotify (Desktop)",
                    "busca": query,
                    "mensagem": f"Reproduzindo '{query}' no aplicativo Spotify, senhor."
                }
            except Exception:
                pass
        spotify_url = f"https://open.spotify.com/search/{encoded}"
        try:
            processos.abrir_desanexado(["xdg-open", spotify_url])
            return {
                "sucesso": True,
                "plataforma": "Spotify (Web)",
                "busca": query,
                "mensagem": f"Reproduzindo '{query}' no Spotify, senhor."
            }
        except Exception as e:
            return {"sucesso": False, "mensagem": f"Falha ao abrir Spotify: {str(e)}"}

    elif "deezer" in target_platform:
        deezer_url = f"https://www.deezer.com/search/{encoded}"
        try:
            processos.abrir_desanexado(["xdg-open", deezer_url])
            return {
                "sucesso": True,
                "plataforma": "Deezer",
                "busca": query,
                "mensagem": f"Reproduzindo '{query}' no Deezer, senhor."
            }
        except Exception as e:
            return {"sucesso": False, "mensagem": f"Falha ao abrir Deezer: {str(e)}"}

    else:
        # Padrão YouTube
        music_url = f"https://www.youtube.com/results?search_query={encoded}"
        try:
            processos.abrir_desanexado(["xdg-open", music_url])
            return {
                "sucesso": True,
                "plataforma": "YouTube",
                "busca": query,
                "mensagem": f"Reproduzindo '{query}' no YouTube, senhor. Boa sessão."
            }
        except Exception as e:
            return {"sucesso": False, "mensagem": f"Falha ao iniciar reprodução no YouTube: {str(e)}"}

ALLOWED_DEFAULT_APPS = {
    "gedit", "code", "antigravity", "kate", "nano", "vim", "subl",
    "firefox", "google-chrome", "chromium", "brave",
    "vlc", "mpv", "rhythmbox", "eog", "xdg-open", "default"
}

# ----------------- GERENCIAMENTO DE MEMÓRIA & PREFERÊNCIAS -----------------
def manage_user_preference(action: str, category: str, key: str = None, value: str = None) -> dict:
    """
    Gerencia preferências e memórias persistentes do usuário.
    'action': 'get', 'set', 'list', 'delete'.
    'category': 'default_apps', 'game_preferences', 'file_associations', 'custom_memories'.
    """
    action = action.strip().lower()
    category = category.strip().lower()

    if action == "list":
        if category:
            data = preferences_manager.load_preferences().get(category, {})
            return {"sucesso": True, "categoria": category, "dados": data, "mensagem": f"Preferências da categoria '{category}' recuperadas, senhor."}
        else:
            data = preferences_manager.get_all_preferences()
            return {"sucesso": True, "todas_preferencias": data, "mensagem": "Todas as preferências e memórias foram recuperadas, senhor."}

    if not key:
        return {"sucesso": False, "mensagem": "A chave (key) é obrigatória para esta ação."}

    key = key.strip().lower()

    if action == "get":
        val = preferences_manager.get_preference(category, key)
        return {
            "sucesso": True,
            "categoria": category,
            "chave": key,
            "valor": val,
            "mensagem": f"A preferência gravada para '{key}' em '{category}' é: '{val}', senhor." if val is not None else f"Nenhuma preferência gravada para '{key}' em '{category}', senhor."
        }

    elif action == "set":
        if value is None:
            return {"sucesso": False, "mensagem": "O valor (value) é obrigatório para definir uma preferência."}
        if category == "default_apps":
            clean_bin = os.path.basename(str(value).strip().lower().split()[0])
            if clean_bin not in ALLOWED_DEFAULT_APPS:
                return {
                    "sucesso": False,
                    "mensagem": f"Executável '{value}' rejeitado por segurança. Binários permitidos para default_apps: {sorted(list(ALLOWED_DEFAULT_APPS))}"
                }
            value = clean_bin
        ok = preferences_manager.set_preference(category, key, value)
        return {
            "sucesso": ok,
            "categoria": category,
            "chave": key,
            "valor": value,
            "mensagem": f"Preferência gravada com sucesso na sua memória persistente, senhor: '{key}' definida como '{value}'." if ok else "Falha ao gravar preferência."
        }

    elif action == "delete":
        ok = preferences_manager.delete_preference(category, key)
        return {
            "sucesso": ok,
            "mensagem": f"Preferência '{key}' removida com sucesso da memória, senhor." if ok else "Falha ao remover preferência."
        }

    return {"sucesso": False, "mensagem": f"Ação de preferência '{action}' desconhecida."}

def set_game_preference(game_name: str, preferred_distributor: str, custom_args: str = None) -> dict:
    """
    Grava na memória persistente qual launcher/distribuidora (Steam, Epic, Lutris, Heroic)
    ou argumentos o senhor prefere usar para um jogo específico.
    """
    clean_game = game_name.strip().lower()
    val = {
        "distribuidora": preferred_distributor.strip().lower(),
        "custom_args": custom_args.strip() if custom_args else ""
    }
    ok = preferences_manager.set_preference("game_preferences", clean_game, val)
    return {
        "sucesso": ok,
        "jogo": clean_game,
        "preferencias": val,
        "mensagem": f"Preferências do jogo '{clean_game}' gravadas na memória com sucesso, senhor." if ok else "Falha ao gravar preferência de jogo."
    }

def open_default_app(app_type: str, target: str = None) -> dict:
    """
    Abre o aplicativo padrão configurado pelo usuário para um tipo específico de tarefa ou arquivo.
    Tipos suportados: 'browser', 'music', 'email', 'text_editor', 'image_viewer', 'video_player'.
    """
    clean_type = app_type.strip().lower()
    app_pref = preferences_manager.get_preference("default_apps", clean_type, "default")

    try:
        if clean_type == "browser":
            url = target or "https://www.google.com"
            clean_bin = os.path.basename(str(app_pref).strip().lower().split()[0])
            if clean_bin in ALLOWED_DEFAULT_APPS and clean_bin != "default" and shutil.which(clean_bin):
                processos.abrir_desanexado([clean_bin, url])
            else:
                processos.abrir_desanexado(["xdg-open", url])
            return {"sucesso": True, "mensagem": "Navegador padrão aberto com sucesso, senhor."}

        elif clean_type == "text_editor":
            editor = app_pref if app_pref != "default" else "antigravity"
            file_target = target or "."
            clean_bin = os.path.basename(str(editor).strip().lower().split()[0])
            if clean_bin in ALLOWED_DEFAULT_APPS and shutil.which(clean_bin):
                processos.abrir_desanexado([clean_bin, file_target])
            else:
                processos.abrir_desanexado(["xdg-open", file_target])
            return {"sucesso": True, "mensagem": f"Editor de texto ({clean_bin}) aberto para '{file_target}', senhor."}

        elif clean_type == "email":
            mailto = f"mailto:{target}" if target else "mailto:"
            processos.abrir_desanexado(["xdg-open", mailto])
            return {"sucesso": True, "mensagem": "Cliente de e-mail padrão aberto, senhor."}

        elif clean_type in ["image_viewer", "video_player", "music"]:
            if target and os.path.exists(os.path.expanduser(target)):
                processos.abrir_desanexado(["xdg-open", os.path.expanduser(target)])
                return {"sucesso": True, "mensagem": f"Arquivo '{target}' aberto com o visualizador padrão, senhor."}
            else:
                return {"sucesso": False, "mensagem": f"Por favor, especifique o caminho do arquivo para abrir com o {clean_type}."}

        else:
            if target:
                processos.abrir_desanexado(["xdg-open", target])
                return {"sucesso": True, "mensagem": f"Alvo '{target}' aberto com aplicativo padrão do sistema, senhor."}
            return {"sucesso": False, "mensagem": f"Tipo de aplicativo '{clean_type}' não reconhecido."}
    except Exception as e:
        return {"sucesso": False, "mensagem": f"Erro ao abrir aplicativo padrão: {str(e)}"}


def _pasta_de_capturas() -> str:
    """Subpasta de capturas dentro da pasta de imagens do usuário, no idioma do sistema."""
    base = perfil_maquina.pasta_de_imagens()
    for nome in ("Screenshots", "Capturas de tela", "Capturas de pantalla"):
        if os.path.isdir(os.path.join(base, nome)):
            return os.path.join(base, nome)
    idioma = (os.environ.get("LANG") or os.environ.get("LANGUAGE") or "").lower()
    return os.path.join(base, "Capturas de tela" if idioma.startswith("pt") else "Screenshots")


def _comandos_de_captura(saida: str) -> List[List[str]]:
    """Ferramentas de captura disponíveis para a sessão gráfica atual, na ordem de preferência."""
    sessao = perfil_maquina.sessao_grafica()
    ambiente = perfil_maquina.sistema_operacional()["ambiente_grafico"]
    spectacle = ["spectacle", "-b", "-n", "-f", "-o", saida]
    gnome = ["gnome-screenshot", "-f", saida]
    if sessao == "wayland":
        # Ferramentas X11 capturariam só o XWayland (imagem preta): ficam de fora
        ordem = [spectacle, gnome, ["grim", saida]] if ambiente == "KDE" else [gnome, ["grim", saida], spectacle]
    elif sessao == "x11":
        ordem = [
            ["maim", saida],
            ["scrot", saida],
            ["import", "-window", "root", saida],
            gnome,
            spectacle,
            ["xfce4-screenshooter", "-f", "-s", saida],
            # Sem -video_size: o x11grab captura a área de trabalho inteira, em qualquer resolução
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "x11grab", "-i", os.environ.get("DISPLAY", ":0"),
             "-frames:v", "1", "-update", "1", saida],
        ]
    else:
        return []
    return [cmd for cmd in ordem if shutil.which(cmd[0])]


def take_screenshot(filename: str = None) -> dict:
    """Tira uma captura de tela completa e salva com nome de arquivo personalizado."""
    shots_dir = _pasta_de_capturas()

    now_str = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    # Só o nome do arquivo: um caminho enviado pelo modelo não pode sair da pasta de capturas
    clean_name = os.path.basename((filename or "").strip()) or f"captura_{now_str}.png"
    if not clean_name.lower().endswith((".png", ".jpg", ".jpeg")):
        clean_name += ".png"
    out_path = os.path.join(shots_dir, clean_name)
    raiz, extensao = os.path.splitext(out_path)
    temporario = f"{raiz}.jarvis-tmp{extensao}"

    comandos = _comandos_de_captura(temporario)
    if not comandos:
        sessao = perfil_maquina.sessao_grafica()
        dica = ("instale grim (Sway/Hyprland), spectacle (KDE) ou gnome-screenshot (GNOME)" if sessao == "wayland"
                else "instale maim, scrot ou imagemagick" if sessao == "x11"
                else "o assistente não está rodando dentro de uma sessão gráfica")
        return {"sucesso": False, "mensagem": f"Nenhuma ferramenta de captura de tela disponível, senhor: {dica}."}

    os.makedirs(shots_dir, exist_ok=True)
    erros = []
    for cmd in comandos:
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        except Exception as e:
            erros.append(f"{cmd[0]}: {e}")
            continue
        if os.path.exists(temporario) and os.path.getsize(temporario) > 0:
            os.replace(temporario, out_path)
            return {
                "sucesso": True,
                "arquivo": out_path,
                "nome": os.path.basename(out_path),
                "ferramenta": cmd[0],
                "mensagem": f"Captura de tela salva com sucesso em '{os.path.basename(out_path)}', senhor."
            }
        erros.append(f"{cmd[0]}: {(res.stderr or 'sem imagem gerada').strip()[:80]}")
    if os.path.exists(temporario):
        os.remove(temporario)
    return {"sucesso": False, "mensagem": "Falha ao capturar a tela: " + "; ".join(erros)}

# ----------------- INTEGRAÇÃO COM ANTIGRAVITY IDE & MCP -----------------
def antigravity_open_workspace(path: str = WORKSPACE_DIR) -> dict:
    """Abre um diretório ou projeto na IDE Antigravity."""
    target_path = os.path.expanduser(path)
    if not os.path.exists(target_path):
        return {"sucesso": False, "mensagem": f"O diretório '{target_path}' não foi encontrado, senhor."}
    try:
        processos.abrir_desanexado([ANTIGRAVITY_BIN, target_path])
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
        cmd = [ANTIGRAVITY_BIN, "-g", f"{full_path}:{line_number or 1}"]
        processos.abrir_desanexado(cmd)
        return {"sucesso": True, "mensagem": f"Arquivo '{os.path.basename(full_path)}' aberto na linha {line_number or 1} na IDE Antigravity, senhor."}
    except Exception as e:
        return {"sucesso": False, "mensagem": f"Falha ao abrir arquivo na IDE Antigravity: {str(e)}"}

def antigravity_list_mcps() -> dict:
    """Lista todos os servidores MCP configurados e ativos na IDE Antigravity."""
    try:
        res = processos.executar([AGY_BIN, "mcp", "list"], capture_output=True, text=True, timeout=10)
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
    alvo = bool(enabled)
    if alvo == IDE_MODE_ACTIVE:
        # Guarda idempotente: evita que o modelo repita a ativação/desativação
        # indevidamente em saudações ou por ruído, sem trocar o estado nem mensagem
        # de "ativado" enganosa quando o modo já estava ativo.
        return {
            "sucesso": True,
            "ide_mode": IDE_MODE_ACTIVE,
            "ja_estava_no_estado": True,
            "mensagem": ("O Modo IDE já está ativo, senhor." if IDE_MODE_ACTIVE
                         else "O Modo IDE já está desativado, senhor.")
        }
    IDE_MODE_ACTIVE = alvo
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

# Estado global do Modo Controle (Mouse, Teclado e Janelas)
CONTROL_MODE_ACTIVE = False

def get_control_mode() -> bool:
    """Retorna True se o modo Controle de periféricos estiver ativo."""
    return CONTROL_MODE_ACTIVE

def set_control_mode(enabled: bool) -> dict:
    """Ativa ou desativa o Modo Controle físico de mouse, teclado e janelas."""
    global CONTROL_MODE_ACTIVE
    CONTROL_MODE_ACTIVE = bool(enabled)
    if CONTROL_MODE_ACTIVE:
        windows = controller_engine.get_open_windows()
        resolution = controller_engine.get_screen_geometry()
        mouse_pos = controller_engine.get_mouse_position()
        titles = [w["titulo"] for w in windows]
        titles_summary = ", ".join(titles[:6])
        if len(titles) > 6:
            titles_summary += f" e mais {len(titles) - 6} outros"

        msg = (
            f"Modo Controle ativado com sucesso, senhor. "
            f"Resolução do monitor identificada em {resolution}. "
            f"Janelas e aplicativos abertos: {titles_summary}. "
            f"O mouse e o teclado virtual estão calibrados e sob seu comando."
        )
        return {
            "sucesso": True,
            "control_mode": True,
            "resolucao": resolution,
            "mouse_posicao": mouse_pos,
            "janelas_abertas": windows,
            "total_janelas": len(windows),
            "mensagem": msg
        }
    else:
        return {
            "sucesso": True,
            "control_mode": False,
            "mensagem": "Modo Controle desativado, senhor. Retornando ao modo de assistência padrão."
        }

def list_open_windows() -> dict:
    """Lista detalhadamente todas as janelas e programas abertos no computador."""
    windows = controller_engine.get_open_windows()
    resolution = controller_engine.get_screen_geometry()
    titles = [w["titulo"] for w in windows]
    return {
        "sucesso": True,
        "total": len(windows),
        "resolucao_tela": resolution,
        "janelas": windows,
        "mensagem": f"Senhor, identifiquei {len(windows)} janela(s) e aplicativo(s) em execução: " + ", ".join(titles[:6]) + ("..." if len(titles) > 6 else ".")
    }

def mouse_move(delta_x: int, delta_y: int) -> dict:
    """Desloca o cursor do mouse em coordenadas relativas (delta_x, delta_y)."""
    if not CONTROL_MODE_ACTIVE:
        return {"sucesso": False, "mensagem": "O Modo Controle precisa estar ativado para operar o mouse, senhor. Diga 'ativar modo controle'."}
    return controller_engine.move_mouse(delta_x, delta_y)

def mouse_click(button: str = "left", double: bool = False) -> dict:
    """Executa um clique com o mouse ('left', 'right', 'middle') ou duplo-clique."""
    if not CONTROL_MODE_ACTIVE:
        return {"sucesso": False, "mensagem": "O Modo Controle precisa estar ativado para operar o mouse, senhor. Diga 'ativar modo controle'."}
    return controller_engine.click_mouse(button, double)

def mouse_scroll(direction: str = "down", amount: int = 3) -> dict:
    """Rola a página ou janela usando a roda do mouse ('up' ou 'down')."""
    if not CONTROL_MODE_ACTIVE:
        return {"sucesso": False, "mensagem": "O Modo Controle precisa estar ativado para operar o mouse, senhor. Diga 'ativar modo controle'."}
    return controller_engine.scroll_mouse(direction, amount)

def keyboard_type(text: str) -> dict:
    """Digita uma sequência de texto na janela ativa através do teclado virtual."""
    if not CONTROL_MODE_ACTIVE:
        return {"sucesso": False, "mensagem": "O Modo Controle precisa estar ativado para usar o teclado, senhor. Diga 'ativar modo controle'."}
    return controller_engine.type_text(text)

def keyboard_hotkey(keys: str) -> dict:
    """Executa um atalho de teclado na janela ativa (ex: 'ctrl+c', 'alt+tab', 'super', 'enter')."""
    if not CONTROL_MODE_ACTIVE:
        return {"sucesso": False, "mensagem": "O Modo Controle precisa estar ativado para acionar atalhos, senhor. Diga 'ativar modo controle'."}
    return controller_engine.press_hotkey(keys)


def antigravity_open_gemini_bridge() -> dict:
    """Abre a pasta 'gemini' de auditoria e canal direto de mensagens na IDE Antigravity."""
    return gemini_bridge.open_gemini_bridge()

def antigravity_run_prompt(prompt: str, continue_session: bool = True) -> dict:
    """Envia uma solicitação ou instrução técnica diretamente para o agente de IA da IDE Antigravity (agy CLI). Mantém o contexto de conversas anteriores."""
    clean_p = prompt.strip()
    if not clean_p:
        return {"sucesso": False, "mensagem": "Instrução para o Antigravity não pode ser vazia, senhor."}
    try:
        cmd = [AGY_BIN]
        if continue_session:
            cmd.append("-c")
        cmd.extend(["-p", clean_p])
        res = processos.executar(
            cmd,
            cwd=WORKSPACE_DIR,
            capture_output=True,
            text=True,
            timeout=85
        )
        ans = res.stdout.strip() or res.stderr.strip()
        resumo = ans[:350] + ("..." if len(ans) > 350 else "")

        # Registra no log de auditoria da pasta gemini
        try:
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

def toggle_telemetry_overlay(enabled: bool = True) -> dict:
    """
    Exibe ou oculta a sobreposição (HUD) de telemetria em tempo real na tela/janela do assistente,
    mostrando métricas de CPU, GPU, VRAM e RAM.
    """
    sys_status = get_system_status()
    gpu_status = sys_status.get("gpu", {})
    cpu_p = sys_status.get("cpu_percent", "0%")
    ram_p = sys_status.get("ram_percent", "0%")
    ram_used = sys_status.get("ram_used_gb", "0 GB")
    ram_total = sys_status.get("ram_total_gb", "0 GB")
    # Valores ausentes ficam None: a interface mostra "--" em vez de números inventados
    gpu_model = gpu_status.get("modelo") if gpu_status.get("disponivel") else None
    gpu_temp = gpu_status.get("temperatura")
    gpu_uso = gpu_status.get("uso_gpu")
    vram_usada = gpu_status.get("vram_usada_mb")
    vram_total = gpu_status.get("vram_total_mb")
    if gpu_model:
        resumo_gpu = gpu_model + (f" em {gpu_temp}" if gpu_temp else "") + (f" com {gpu_uso} de uso" if gpu_uso else "")
    else:
        resumo_gpu = "nenhuma placa de vídeo com telemetria"

    return {
        "sucesso": True,
        "active": bool(enabled),
        "telemetry": {
            "cpu_percent": cpu_p,
            "cpu_cores": sys_status.get("cpu_cores", 0),
            "ram_used_gb": ram_used,
            "ram_total_gb": ram_total,
            "ram_percent": ram_p,
            "gpu_modelo": gpu_model,
            "gpu_uso": gpu_uso,
            "gpu_temp": gpu_temp,
            "vram_usada": vram_usada,
            "vram_total": vram_total,
            "uptime": sys_status.get("uptime", "")
        },
        "mensagem": f"Telemetria em tela {'ativada e visível na sua janela' if enabled else 'ocultada'}, senhor. Processador em {cpu_p}, RAM em {ram_p} e {resumo_gpu}."
    }

def get_machine_profile() -> dict:
    """Identifica a máquina do usuário: sistema, ambiente gráfico, processador, memória, placas de vídeo, discos, tela, áudio e aplicativos padrão."""
    perfil = perfil_maquina.perfil_da_maquina(forcar=True)
    return {
        "sucesso": True,
        **perfil,
        "mensagem": f"Senhor, identifiquei sua máquina: {perfil_maquina.resumo_da_maquina()}."
    }

# Declarações de Schema para Gemini Function Calling
GEMINI_FUNCTION_DECLARATIONS = [
    {
        "name": "toggle_telemetry_overlay",
        "description": "Exibe ou oculta a tela/painel de telemetria de hardware (CPU, placa de vídeo, VRAM, RAM e temperatura) diretamente na janela do assistente sobreposta na tela do usuário.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "enabled": {
                    "type": "BOOLEAN",
                    "description": "True para mostrar/abrir a telemetria na janela, False para fechar/ocultar."
                }
            }
        }
    },
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
        "description": "Obtém a telemetria em tempo real da placa de vídeo da máquina (NVIDIA, AMD ou Intel): modelo, uso em porcentagem, VRAM utilizada e total, e temperatura em graus Celsius quando o driver informa.",
        "parameters": {"type": "OBJECT", "properties": {}}
    },
    {
        "name": "get_system_status",
        "description": "Obtém a telemetria em tempo real do sistema: uso de CPU, memória RAM, bateria, disco e tempo ligado.",
        "parameters": {"type": "OBJECT", "properties": {}}
    },
    {
        "name": "get_machine_profile",
        "description": "Identifica a máquina do usuário: distribuição Linux, ambiente gráfico (Wayland/X11), processador, memória RAM, placas de vídeo, discos (tipo, modelo, capacidade e espaço livre), resolução da tela, servidor de áudio e aplicativos padrão. Use quando o senhor perguntar sobre o computador dele ou quando a resposta depender do hardware.",
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
                    "description": "Caminho do arquivo a ser aberto (ex: 'server.py' ou '/caminho/absoluto/do/projeto/system_tools.py')."
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
    },
    {
        "name": "open_website",
        "description": "Abre qualquer site ou endereço web diretamente no navegador padrão do senhor.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "url": {
                    "type": "STRING",
                    "description": "Endereço do site a ser aberto (ex: 'github.com', 'youtube.com', 'https://globo.com')."
                }
            },
            "required": ["url"]
        }
    },
    {
        "name": "read_web_page",
        "description": "Lê e extrai o conteúdo textual legível de uma página ou artigo da web (notícias, documentação, artigos, etc.) para que você possa ler, explicar ou resumir as informações diretamente ao senhor em áudio.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "url": {
                    "type": "STRING",
                    "description": "Endereço da página web ou notícia a ser lida (ex: 'https://g1.globo.com', 'https://techcrunch.com/...')."
                },
                "max_chars": {
                    "type": "INTEGER",
                    "description": "Limite máximo de caracteres a extrair (padrão: 4000)."
                }
            },
            "required": ["url"]
        }
    },
    {
        "name": "play_music",
        "description": "Busca e reproduz qualquer música, cantor, banda ou gênero musical. Se a plataforma não for especificada, utilizará a preferência salva pelo usuário ou perguntará educadamente na primeira vez se prefere YouTube ou Spotify.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "Nome da música, artista ou playlist desejada (ex: 'Queen Bohemian Rhapsody', 'AC/DC', 'synthwave lo-fi')."
                },
                "platform": {
                    "type": "STRING",
                    "description": "Plataforma opcional para reproduzir ('youtube', 'spotify', 'deezer'). Se omitido, consulta a preferência padrão na memória."
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "take_screenshot",
        "description": "Tira uma captura de tela completa do computador e salva com nome personalizado na pasta de capturas de tela do usuário.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "filename": {
                    "type": "STRING",
                    "description": "Nome opcional do arquivo para salvar a captura (ex: 'erro_antigravity.png' ou 'grafico.png')."
                }
            }
        }
    },
    {
        "name": "manage_user_preference",
        "description": "Consulta, salva, lista ou remove preferências e memórias persistentes do usuário (aplicativos padrão, plataformas favoritas, hábitos ou configurações).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "Ação desejada: 'get' (consultar), 'set' (salvar/atualizar), 'list' (listar categoria), 'delete' (remover)."
                },
                "category": {
                    "type": "STRING",
                    "description": "Categoria da preferência: 'default_apps', 'game_preferences', 'file_associations', 'custom_memories'."
                },
                "key": {
                    "type": "STRING",
                    "description": "Nome da chave da preferência (ex: 'music_platform', 'browser', 'gta v', 'nome_favorito')."
                },
                "value": {
                    "type": "STRING",
                    "description": "Valor a ser salvo quando a ação for 'set' (ex: 'spotify', 'youtube', 'firefox')."
                }
            },
            "required": ["action", "category"]
        }
    },
    {
        "name": "set_game_preference",
        "description": "Grava na memória persistente a distribuidora/launcher (Steam, Epic, Lutris, Heroic) ou parâmetros customizados preferidos para iniciar um jogo específico.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "game_name": {
                    "type": "STRING",
                    "description": "Nome do jogo (ex: 'GTA 5', 'Red Dead', 'Marvel Rivals')."
                },
                "preferred_distributor": {
                    "type": "STRING",
                    "description": "Distribuidora ou plataforma preferida (ex: 'steam', 'epic', 'heroic', 'lutris')."
                },
                "custom_args": {
                    "type": "STRING",
                    "description": "Argumentos ou parâmetros adicionais opcionais de inicialização."
                }
            },
            "required": ["game_name", "preferred_distributor"]
        }
    },
    {
        "name": "open_default_app",
        "description": "Abre o aplicativo padrão configurado pelo usuário no sistema para uma categoria ou tipo de arquivo (semelhante aos Aplicativos Padrão do Windows).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_type": {
                    "type": "STRING",
                    "description": "Tipo de aplicativo ou tarefa: 'browser', 'music', 'email', 'text_editor', 'image_viewer', 'video_player'."
                },
                "target": {
                    "type": "STRING",
                    "description": "Arquivo, endereço web ou destinatário a ser aberto no aplicativo correspondente."
                }
            },
            "required": ["app_type"]
        }
    },
    {
        "name": "set_control_mode",
        "description": "Ativa ou desativa o Modo Controle físico do computador. Use enabled=true quando o senhor disser 'modo controle ativar' ou 'ativar modo controle'. Ao ativar, faz a varredura completa das janelas abertas e calibra o mouse e teclado virtual. Use enabled=false para desativar.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "enabled": {
                    "type": "BOOLEAN",
                    "description": "True para ativar o Modo Controle, False para desativar."
                }
            },
            "required": ["enabled"]
        }
    },
    {
        "name": "list_open_windows",
        "description": "Lista todas as janelas e programas abertos em execução no computador (ex: Steam, VS Code, Antigravity, Navegador, etc.).",
        "parameters": {"type": "OBJECT", "properties": {}}
    },
    {
        "name": "mouse_move",
        "description": "Desloca o cursor do mouse na tela através de deslocamentos relativos (delta_x, delta_y). Requer Modo Controle ativo.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "delta_x": {
                    "type": "INTEGER",
                    "description": "Deslocamento horizontal em pixels (positivo para direita, negativo para esquerda)."
                },
                "delta_y": {
                    "type": "INTEGER",
                    "description": "Deslocamento vertical em pixels (positivo para baixo, negativo para cima)."
                }
            },
            "required": ["delta_x", "delta_y"]
        }
    },
    {
        "name": "mouse_click",
        "description": "Executa um clique com o mouse ('left', 'right', 'middle') ou duplo-clique. Requer Modo Controle ativo.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "button": {
                    "type": "STRING",
                    "description": "Botão a ser clicado: 'left' (esquerdo), 'right' (direito) ou 'middle' (meio). Padrão é 'left'."
                },
                "double": {
                    "type": "BOOLEAN",
                    "description": "True para executar duplo-clique rápido."
                }
            }
        }
    },
    {
        "name": "mouse_scroll",
        "description": "Rola a tela/página usando o scroll do mouse para cima ('up') ou para baixo ('down'). Requer Modo Controle ativo.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "direction": {
                    "type": "STRING",
                    "description": "Direção da rolagem: 'up' (cima) ou 'down' (baixo)."
                },
                "amount": {
                    "type": "INTEGER",
                    "description": "Quantidade de passos/linhas de rolagem (padrão: 3)."
                }
            }
        }
    },
    {
        "name": "keyboard_type",
        "description": "Digita uma frase ou texto diretamente na janela ou campo de texto ativo. Requer Modo Controle ativo.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "text": {
                    "type": "STRING",
                    "description": "O texto a ser digitado pelo teclado virtual."
                }
            },
            "required": ["text"]
        }
    },
    {
        "name": "keyboard_hotkey",
        "description": "Executa atalhos de teclado na janela ativa (ex: 'ctrl+c', 'ctrl+v', 'alt+tab', 'super', 'enter', 'esc'). Requer Modo Controle ativo.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "keys": {
                    "type": "STRING",
                    "description": "Combinação de teclas separadas por '+' (ex: 'alt+tab', 'ctrl+shift+t', 'ctrl+c', 'super')."
                }
            },
            "required": ["keys"]
        }
    }
]

TOOL_REGISTRY = {
    "list_installed_games": list_installed_games,
    "get_gpu_status": get_gpu_status,
    "get_machine_profile": get_machine_profile,
    "get_system_status": get_system_status,
    "toggle_telemetry_overlay": toggle_telemetry_overlay,
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
    "open_website": open_website,
    "read_web_page": read_web_page,
    "play_music": play_music,
    "take_screenshot": take_screenshot,
    "manage_user_preference": manage_user_preference,
    "set_game_preference": set_game_preference,
    "open_default_app": open_default_app,
    "set_control_mode": set_control_mode,
    "list_open_windows": list_open_windows,
    "mouse_move": mouse_move,
    "mouse_click": mouse_click,
    "mouse_scroll": mouse_scroll,
    "keyboard_type": keyboard_type,
    "keyboard_hotkey": keyboard_hotkey,
}

# Cópias imutáveis de referência para reconstrução dinâmica
BASE_TOOL_REGISTRY = dict(TOOL_REGISTRY)
BASE_GEMINI_FUNCTION_DECLARATIONS = list(GEMINI_FUNCTION_DECLARATIONS)

def rebuild_registry(dynamic_tools: list):
    """
    Restaura o registro base do sistema e injeta exclusivamente as ferramentas
    dos plug-ins ativos, garantindo que desativações expurguem as funções do modelo.
    """
    TOOL_REGISTRY.clear()
    TOOL_REGISTRY.update(BASE_TOOL_REGISTRY)

    GEMINI_FUNCTION_DECLARATIONS.clear()
    GEMINI_FUNCTION_DECLARATIONS.extend(BASE_GEMINI_FUNCTION_DECLARATIONS)

    existing_names = set(BASE_TOOL_REGISTRY.keys())
    for t in dynamic_tools:
        TOOL_REGISTRY[t.name] = t.handler
        if t.name not in existing_names:
            GEMINI_FUNCTION_DECLARATIONS.append({
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters
            })
            existing_names.add(t.name)




