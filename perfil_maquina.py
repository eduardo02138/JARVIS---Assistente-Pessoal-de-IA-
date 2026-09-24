"""Identificação automática da máquina em que o J.A.R.V.I.S. roda.

Tudo é descoberto em tempo de execução por interfaces padrão do Linux (/proc,
/sys, /etc/os-release, variáveis XDG e o PATH), sem nomes de disco, GPU,
monitor ou aplicativos fixos: o assistente se adapta a qualquer computador.
Cada detector falha em silêncio e devolve um valor neutro em vez de quebrar,
então um servidor sem tela, uma VM ou um container continuam funcionando.
"""

import glob
import gzip
import os
import platform
import re
import shlex
import shutil
import subprocess
import threading
import time
from typing import Callable, Dict, List, Optional

import psutil

# Raízes das interfaces do sistema (os testes apontam para árvores falsas)
RAIZ_SYS = "/sys"
RAIZ_PROC = "/proc"
RAIZ_DEV = "/dev"
ARQUIVO_OS_RELEASE = "/etc/os-release"
ARQUIVOS_PCI_IDS = (
    "/usr/share/hwdata/pci.ids",
    "/usr/share/misc/pci.ids",
    "/usr/share/pci.ids",
    "/usr/share/hwdata/pci.ids.gz",
    "/usr/share/misc/pci.ids.gz",
)
# Aplicativos Flatpak e Snap nem sempre estão no XDG_DATA_DIRS do processo
DIRETORIOS_EXTRAS_DE_APLICATIVOS = (
    "~/.local/share/flatpak/exports/share",
    "/var/lib/flatpak/exports/share",
    "/var/lib/snapd/desktop",
)
DIRETORIOS_PADRAO_DE_DADOS = "/usr/local/share:/usr/share"

TTL_MONTAGENS_S = 60.0
TTL_PERFIL_S = 300.0

_cache: Dict[str, tuple] = {}
_cache_lock = threading.Lock()


def _em_cache(chave: str, ttl: float, fabrica: Callable, forcar: bool = False):
    agora = time.monotonic()
    with _cache_lock:
        item = _cache.get(chave)
        if item and not forcar and agora - item[0] < ttl:
            return item[1]
    valor = fabrica()
    with _cache_lock:
        _cache[chave] = (time.monotonic(), valor)
    return valor


def limpar_cache() -> None:
    """Descarta tudo que foi detectado (hardware conectado ou trocado)."""
    with _cache_lock:
        _cache.clear()


def _ler(caminho: str, padrao: str = "") -> str:
    try:
        with open(caminho, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return padrao


def _executar(argv: List[str], timeout: float = 2.0) -> str:
    """Saída de um utilitário do sistema, ou "" se ele não existir ou falhar."""
    if not shutil.which(argv[0]):
        return ""
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return ""
    return res.stdout.strip() if res.returncode == 0 else ""


def formatar_tamanho(num_bytes: float) -> str:
    """Capacidade em unidades decimais, como os fabricantes rotulam discos."""
    if not num_bytes:
        return "0 GB"
    if num_bytes >= 1e12:
        valor, unidade = num_bytes / 1e12, "TB"
    elif num_bytes >= 1e9:
        valor, unidade = num_bytes / 1e9, "GB"
    else:
        return f"{max(1, round(num_bytes / 1e6))} MB"
    texto = f"{valor:.1f}" if valor < 10 else f"{valor:.0f}"
    return f"{texto.removesuffix('.0')} {unidade}"


# ---------------------------------------------------------------- sistema
AMBIENTES_CONHECIDOS = (
    "GNOME", "KDE", "XFCE", "Cinnamon", "MATE", "LXQt", "LXDE", "Budgie", "Pantheon",
    "COSMIC", "Unity", "Deepin", "Enlightenment", "Hyprland", "sway", "i3", "niri",
    "river", "Wayfire", "labwc",
)


def _ambiente_grafico() -> str:
    bruto = os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION") or ""
    partes = [p.removeprefix("X-") for p in re.split(r"[:;]", bruto) if p]
    conhecidos = {a.lower(): a for a in AMBIENTES_CONHECIDOS}
    for parte in partes:
        if parte.lower() in conhecidos:
            return conhecidos[parte.lower()]
    return partes[-1] if partes else "desconhecido"


def sessao_grafica() -> str:
    """'wayland', 'x11' ou 'sem sessão gráfica'."""
    sessao = (os.environ.get("XDG_SESSION_TYPE") or "").strip().lower()
    if sessao in ("wayland", "x11"):
        return sessao
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "sem sessão gráfica"


def sistema_operacional() -> dict:
    dados = {}
    for linha in _ler(ARQUIVO_OS_RELEASE).splitlines():
        chave, sep, valor = linha.partition("=")
        if sep:
            dados[chave.strip()] = valor.strip().strip('"').strip("'")
    return {
        "distribuicao": dados.get("PRETTY_NAME") or dados.get("NAME") or platform.system(),
        "kernel": platform.release(),
        "arquitetura": platform.machine(),
        "sessao_grafica": sessao_grafica(),
        "ambiente_grafico": _ambiente_grafico(),
    }


# ---------------------------------------------------------------- processador e memória
def processador() -> dict:
    modelo = ""
    for linha in _ler(os.path.join(RAIZ_PROC, "cpuinfo")).splitlines():
        chave, _, valor = linha.partition(":")
        valor = valor.strip()
        # "model" em x86 é um número de família; só nomes interessam
        if chave.strip().lower() in ("model name", "hardware", "cpu model", "model") and valor and not valor.isdigit():
            modelo = valor
            break
    modelo = modelo or platform.processor() or platform.machine() or "desconhecido"
    # "Intel(R) Core(TM) i7" → "Intel Core i7": o nome é lido em voz alta
    modelo = " ".join(re.sub(r"\((?:R|TM)\)", "", modelo, flags=re.IGNORECASE).split())
    return {
        "modelo": modelo,
        "nucleos_fisicos": psutil.cpu_count(logical=False),
        "nucleos_logicos": psutil.cpu_count(logical=True),
    }


def memoria_total_gb() -> float:
    return round(psutil.virtual_memory().total / 1024**3, 1)


# ---------------------------------------------------------------- placas de vídeo
FABRICANTES_PCI = {
    "0x10de": "NVIDIA", "0x1002": "AMD", "0x1022": "AMD", "0x8086": "Intel",
    "0x1af4": "Virtio", "0x1b36": "QEMU", "0x1234": "QEMU", "0x15ad": "VMware",
    "0x80ee": "VirtualBox", "0x1414": "Microsoft", "0x5143": "Qualcomm",
    "0x1a03": "ASPEED", "0x102b": "Matrox",
}
FABRICANTES_VIRTUAIS = {"0x1af4", "0x1b36", "0x1234", "0x15ad", "0x80ee", "0x1414"}
DRIVERS_DE_FRAMEBUFFER = {"simple-framebuffer", "simpledrm", "efi-framebuffer", "vesafb", "vfb"}


def _nome_na_pci_ids(vendor: str, device: str) -> str:
    fab = vendor.lower().removeprefix("0x")
    disp = device.lower().removeprefix("0x")
    for caminho in ARQUIVOS_PCI_IDS:
        if not os.path.exists(caminho):
            continue
        abrir = gzip.open if caminho.endswith(".gz") else open
        try:
            with abrir(caminho, "rt", encoding="utf-8", errors="replace") as f:
                no_fabricante = False
                for linha in f:
                    if not linha.strip() or linha.startswith("#"):
                        continue
                    if not linha.startswith("\t"):
                        if no_fabricante:
                            break
                        no_fabricante = linha[:4].lower() == fab
                    elif no_fabricante and not linha.startswith("\t\t") and linha[1:5].lower() == disp:
                        return linha[5:].strip()
        except OSError:
            continue
    return ""


def _nome_lspci(slot: str) -> str:
    saida = _executar(["lspci", "-mm", "-s", slot])
    if not saida:
        return ""
    try:
        campos = shlex.split(saida.splitlines()[0])
    except ValueError:
        return ""
    return campos[3] if len(campos) >= 4 else ""


def _nome_amigavel(fabricante: str, bruto: str, device: str) -> str:
    if not bruto:
        return f"{fabricante or 'GPU'} (dispositivo {device or '?'})"
    colchetes = re.findall(r"\[([^\]]+)\]", bruto)
    nome = colchetes[-1] if colchetes else bruto
    if fabricante and fabricante.lower() not in nome.lower():
        nome = f"{fabricante} {nome}"
    return " ".join(nome.split())


def _tipo_de_gpu(vendor: str, nome: str, dispositivo: str) -> str:
    if vendor in FABRICANTES_VIRTUAIS:
        return "virtual"
    if vendor == "0x10de":
        return "dedicada"
    if vendor == "0x8086":
        # Arc A/B-series são placas; "Arc Graphics" dos Core Ultra é integrada
        return "dedicada" if re.search(r"\bArc\s+[AB]\d{3}", nome) else "integrada"
    if vendor in ("0x1002", "0x1022"):
        vram = _ler(os.path.join(dispositivo, "mem_info_vram_total"))
        if vram.isdigit():
            return "dedicada" if int(vram) >= 2 * 1024**3 else "integrada"
    if vendor in ("0x1a03", "0x102b"):
        return "integrada"
    return "desconhecido"


def _placas_brutas() -> List[dict]:
    placas: List[dict] = []
    vistos = set()
    # 1. Controladores de vídeo no barramento PCI (classe 0x03xxxx), com ou sem driver DRM
    for disp in sorted(glob.glob(os.path.join(RAIZ_SYS, "bus", "pci", "devices", "*"))):
        if not _ler(os.path.join(disp, "class")).lower().startswith("0x03"):
            continue
        real = os.path.realpath(disp)
        vistos.add(real)
        vendor = _ler(os.path.join(disp, "vendor")).lower()
        device = _ler(os.path.join(disp, "device")).lower()
        fabricante = FABRICANTES_PCI.get(vendor, "")
        slot = os.path.basename(real)
        nome = _nome_amigavel(fabricante, _nome_lspci(slot) or _nome_na_pci_ids(vendor, device), device)
        link_driver = os.path.join(disp, "driver")
        driver = os.path.basename(os.path.realpath(link_driver)) if os.path.exists(link_driver) else None
        placas.append({
            "fabricante": fabricante or "desconhecido",
            "modelo": nome,
            "tipo": _tipo_de_gpu(vendor, nome, disp),
            "driver": driver,
            "pci": slot,
            "_dispositivo": disp,
        })
    # 2. GPUs de SoC (ARM etc.) que só aparecem como placa DRM, fora do PCI
    for card in sorted(glob.glob(os.path.join(RAIZ_SYS, "class", "drm", "card[0-9]*"))):
        if "-" in os.path.basename(card):
            continue
        disp = os.path.join(card, "device")
        real = os.path.realpath(disp)
        if real in vistos or any(real.startswith(v + os.sep) for v in vistos):
            continue
        link_driver = os.path.join(disp, "driver")
        driver = os.path.basename(os.path.realpath(link_driver)) if os.path.exists(link_driver) else ""
        if not driver or driver in DRIVERS_DE_FRAMEBUFFER:
            continue
        vistos.add(real)
        placas.append({
            "fabricante": driver,
            "modelo": f"GPU integrada ({driver})",
            "tipo": "integrada",
            "driver": driver,
            "pci": None,
            "_dispositivo": disp,
        })
    # Dedicadas primeiro: é a que interessa para telemetria e jogos
    ordem = {"dedicada": 0, "integrada": 1, "desconhecido": 2, "virtual": 3}
    placas.sort(key=lambda p: ordem.get(p["tipo"], 9))
    return placas


def placas_de_video(forcar: bool = False) -> List[dict]:
    placas = _em_cache("placas", TTL_PERFIL_S, _placas_brutas, forcar)
    return [{k: v for k, v in p.items() if not k.startswith("_")} for p in placas]


def telemetria_gpu_sysfs() -> Optional[dict]:
    """Uso, VRAM e temperatura pela interface padrão do kernel (driver amdgpu).

    GPUs NVIDIA são lidas pelo nvidia-smi em system_tools; Intel não expõe uso
    por uma interface padrão, então só o modelo é informado.
    """
    for placa in _em_cache("placas", TTL_PERFIL_S, _placas_brutas):
        disp = placa["_dispositivo"]
        uso = _ler(os.path.join(disp, "gpu_busy_percent"))
        if not uso.isdigit():
            continue
        vram_usada = _ler(os.path.join(disp, "mem_info_vram_used"))
        vram_total = _ler(os.path.join(disp, "mem_info_vram_total"))
        temperatura = None
        for sensor in sorted(glob.glob(os.path.join(disp, "hwmon", "hwmon*", "temp1_input"))):
            leitura = _ler(sensor)
            if leitura.lstrip("-").isdigit():
                temperatura = round(int(leitura) / 1000)
                break
        return {
            "modelo": placa["modelo"],
            "uso_percentual": int(uso),
            "vram_usada_mb": int(vram_usada) // 1024**2 if vram_usada.isdigit() else None,
            "vram_total_mb": int(vram_total) // 1024**2 if vram_total.isdigit() else None,
            "temperatura_c": temperatura,
        }
    return None


# ---------------------------------------------------------------- discos
PREFIXOS_DE_MONTAGEM_IGNORADOS = (
    "/boot", "/efi", "/snap", "/var/snap", "/var/lib/snapd",
    "/var/lib/docker", "/var/lib/containers", "/run/credentials",
)
FS_IGNORADOS = {"squashfs", "erofs"}


def _desescapar_montagem(texto: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), texto)


def _desescapar_udev(texto: str) -> str:
    return re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), texto)


def _rotulos_por_dispositivo() -> Dict[str, str]:
    rotulos = {}
    for link in glob.glob(os.path.join(RAIZ_DEV, "disk", "by-label", "*")):
        alvo = os.path.basename(os.path.realpath(link))
        rotulos[alvo] = _desescapar_udev(os.path.basename(link))
    return rotulos


def _disco_fisico(bloco: str) -> str:
    """Sobe de partição, LUKS, LVM ou RAID até o disco físico (nvme0n1p2 → nvme0n1)."""
    atual, visitados = bloco, set()
    while atual and atual not in visitados:
        visitados.add(atual)
        real = os.path.realpath(os.path.join(RAIZ_SYS, "class", "block", atual))
        if os.path.exists(os.path.join(real, "partition")):
            atual = os.path.basename(os.path.dirname(real))
            continue
        dir_escravos = os.path.join(real, "slaves")
        escravos = sorted(os.listdir(dir_escravos)) if os.path.isdir(dir_escravos) else []
        if escravos:
            atual = escravos[0]
            continue
        break
    return atual


def _info_do_disco(disco: str) -> dict:
    base = os.path.join(RAIZ_SYS, "block", disco)
    real = os.path.realpath(base)
    modelo = " ".join(_ler(os.path.join(base, "device", "model")).split())
    setores = _ler(os.path.join(base, "size"), "0")
    tamanho = int(setores) * 512 if setores.isdigit() else 0
    if disco.startswith("nvme"):
        tipo = "SSD NVMe"
    elif disco.startswith("mmcblk"):
        tipo = "cartão SD/eMMC"
    elif disco.startswith(("vd", "xvd")):
        tipo = "disco virtual"
    elif f"{os.sep}usb" in real:
        tipo = "disco USB"
    elif _ler(os.path.join(base, "removable")) == "1":
        tipo = "disco removível"
    elif _ler(os.path.join(base, "queue", "rotational")) == "1":
        tipo = "HD"
    elif disco.startswith("sd"):
        tipo = "SSD SATA"
    else:
        tipo = "disco"
    partes = [tipo] + ([modelo] if modelo else []) + ([f"({formatar_tamanho(tamanho)})"] if tamanho else [])
    return {
        "disco": disco,
        "tipo": tipo,
        "modelo": modelo or None,
        "tamanho": formatar_tamanho(tamanho) if tamanho else None,
        "resumo": " ".join(partes),
    }


def _montagens_brutas() -> List[dict]:
    rotulos = _rotulos_por_dispositivo()
    info_por_disco: Dict[str, dict] = {}
    montagens = []
    for linha in _ler(os.path.join(RAIZ_PROC, "mounts")).splitlines():
        campos = linha.split()
        if len(campos) < 3:
            continue
        dispositivo, ponto, fs = _desescapar_montagem(campos[0]), _desescapar_montagem(campos[1]), campos[2]
        if fs in FS_IGNORADOS or ponto.startswith(PREFIXOS_DE_MONTAGEM_IGNORADOS):
            continue
        if fs == "zfs":
            pool = dispositivo.split("/")[0]
            montagens.append({"ponto_de_montagem": ponto, "dispositivo": dispositivo, "sistema_de_arquivos": fs,
                              "rotulo": pool, "disco": None, "descricao": f"volume ZFS '{pool}'"})
            continue
        if not dispositivo.startswith("/dev/") or re.match(r"/dev/(loop|ram|zram)", dispositivo):
            continue
        # /dev/mapper/nome → /dev/dm-N: o nome do kernel é o que aparece em /sys
        bloco = os.path.basename(os.path.realpath(os.path.join(RAIZ_DEV, dispositivo[len("/dev/"):])))
        disco = _disco_fisico(bloco)
        if disco not in info_por_disco:
            info_por_disco[disco] = _info_do_disco(disco)
        info = info_por_disco[disco]
        rotulo = rotulos.get(bloco)
        montagens.append({
            "ponto_de_montagem": ponto,
            "dispositivo": dispositivo,
            "sistema_de_arquivos": fs,
            "rotulo": rotulo,
            "disco": info,
            "descricao": info["resumo"] + (f" '{rotulo}'" if rotulo else ""),
        })
    return montagens


def montagens(forcar: bool = False) -> List[dict]:
    return _em_cache("montagens", TTL_MONTAGENS_S, _montagens_brutas, forcar)


def discos(forcar: bool = False) -> List[dict]:
    """Discos físicos com suas partições montadas, espaço livre e rótulos."""
    por_disco: Dict[str, dict] = {}
    for m in montagens(forcar):
        info = m["disco"] or {"disco": m["dispositivo"], "tipo": "volume", "modelo": None,
                              "tamanho": None, "resumo": m["descricao"]}
        item = por_disco.setdefault(info["disco"], {**info, "particoes": []})
        if any(p["dispositivo"] == m["dispositivo"] for p in item["particoes"]):
            continue  # subvolumes btrfs do mesmo dispositivo
        particao = {"ponto_de_montagem": m["ponto_de_montagem"], "dispositivo": m["dispositivo"],
                    "sistema_de_arquivos": m["sistema_de_arquivos"], "rotulo": m["rotulo"],
                    "livre_gb": None, "total_gb": None}
        try:
            uso = shutil.disk_usage(m["ponto_de_montagem"])
            particao["livre_gb"] = round(uso.free / 1e9, 1)
            particao["total_gb"] = round(uso.total / 1e9, 1)
        except OSError:
            pass
        item["particoes"].append(particao)
    return list(por_disco.values())


def identificar_disco(caminho: str) -> str:
    """Descreve o disco onde um caminho está (tipo, modelo, capacidade e rótulo)."""
    try:
        alvo = os.path.realpath(os.path.expanduser(caminho))
    except (OSError, ValueError):
        return "armazenamento local"
    melhor = None
    for m in montagens():
        ponto = m["ponto_de_montagem"]
        if alvo == ponto or alvo.startswith(ponto.rstrip("/") + "/"):
            if melhor is None or len(ponto) > len(melhor["ponto_de_montagem"]):
                melhor = m
    return melhor["descricao"] if melhor else "armazenamento local"


# ---------------------------------------------------------------- tela e áudio
def resolucao_da_tela() -> str:
    """Resolução da área de trabalho ('2560x1440' ou 'A + B' com vários monitores)."""
    if os.environ.get("DISPLAY"):
        saida = _executar(["xrandr", "--current"], timeout=1.5)
        m = re.search(r"current\s+(\d+)\s*x\s*(\d+)", saida)
        if m:
            return f"{m.group(1)}x{m.group(2)}"
        m = re.search(r"dimensions:\s+(\d+x\d+)\s+pixels", _executar(["xdpyinfo"], timeout=1.5))
        if m:
            return m.group(1)
    # Wayland ou sem ferramentas X: modo nativo de cada monitor conectado, direto do kernel
    monitores = []
    for conector in sorted(glob.glob(os.path.join(RAIZ_SYS, "class", "drm", "card*-*"))):
        if _ler(os.path.join(conector, "status")) != "connected":
            continue
        if _ler(os.path.join(conector, "enabled"), "enabled") == "disabled":
            continue
        modos = _ler(os.path.join(conector, "modes")).splitlines()
        if modos:
            monitores.append(modos[0].strip())
    return " + ".join(monitores) if monitores else "desconhecida"


def servidor_de_audio() -> str:
    try:
        nomes = {(p.info.get("name") or "").lower() for p in psutil.process_iter(["name"])}
    except Exception:
        nomes = set()
    if "pipewire" in nomes:
        return "PipeWire"
    if "pulseaudio" in nomes:
        return "PulseAudio"
    if os.path.isdir(os.path.join(RAIZ_PROC, "asound")):
        return "ALSA"
    return "desconhecido"


def pasta_de_imagens() -> str:
    """Pasta de imagens do usuário no idioma do sistema (XDG_PICTURES_DIR)."""
    casa = os.path.expanduser("~")
    saida = _executar(["xdg-user-dir", "PICTURES"])
    if saida and os.path.normpath(saida) != os.path.normpath(casa):
        return saida
    config = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(casa, ".config"), "user-dirs.dirs")
    m = re.search(r'^XDG_PICTURES_DIR="([^"]+)"', _ler(config), re.MULTILINE)
    if m:
        caminho = m.group(1).replace("$HOME", casa)
        if os.path.normpath(caminho) != os.path.normpath(casa):
            return caminho
    for nome in ("Pictures", "Imagens", "Imágenes", "Images", "Bilder"):
        if os.path.isdir(os.path.join(casa, nome)):
            return os.path.join(casa, nome)
    return os.path.join(casa, "Pictures")


# ---------------------------------------------------------------- aplicativos instalados
def diretorios_de_aplicativos() -> List[str]:
    """Diretórios XDG de atalhos .desktop, do mais prioritário ao menos."""
    home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    sistema = (os.environ.get("XDG_DATA_DIRS") or DIRETORIOS_PADRAO_DE_DADOS).split(":")
    vistos, resultado = set(), []
    for base in [home, *sistema, *DIRETORIOS_EXTRAS_DE_APLICATIVOS]:
        if not base:
            continue
        pasta = os.path.join(os.path.expanduser(base), "applications")
        real = os.path.realpath(pasta)
        if real in vistos or not os.path.isdir(pasta):
            continue
        vistos.add(real)
        resultado.append(pasta)
    return resultado


def _ler_entrada_desktop(caminho: str, desktop_id: str) -> Optional[dict]:
    grupo, campos = None, {}
    try:
        with open(caminho, encoding="utf-8", errors="replace") as f:
            for linha in f:
                linha = linha.strip()
                if not linha or linha.startswith("#"):
                    continue
                if linha.startswith("[") and linha.endswith("]"):
                    grupo = linha[1:-1]
                    continue
                if grupo == "Desktop Entry" and "=" in linha:
                    chave, valor = linha.split("=", 1)
                    campos[chave.strip()] = valor.strip()
    except OSError:
        return None
    if campos.get("Type", "Application") != "Application":
        return None
    return {
        "id": desktop_id,
        "arquivo": caminho,
        "nome": campos.get("Name[pt_BR]") or campos.get("Name[pt]") or campos.get("Name", ""),
        "nome_original": campos.get("Name", ""),
        "exec": campos.get("Exec", ""),
        "categorias": [c for c in campos.get("Categories", "").split(";") if c],
        "oculto": campos.get("NoDisplay", "").lower() == "true",
        "removido": campos.get("Hidden", "").lower() == "true",
        "terminal": campos.get("Terminal", "").lower() == "true",
    }


def _aplicativos_brutos() -> List[dict]:
    entradas: Dict[str, dict] = {}
    for pasta in diretorios_de_aplicativos():
        arquivos = glob.glob(os.path.join(pasta, "*.desktop")) + glob.glob(os.path.join(pasta, "*", "*.desktop"))
        for caminho in sorted(arquivos):
            desktop_id = os.path.relpath(caminho, pasta).replace(os.sep, "-")
            if desktop_id in entradas:
                continue  # o diretório mais prioritário vence (especificação XDG)
            entrada = _ler_entrada_desktop(caminho, desktop_id)
            if entrada is not None:
                entradas[desktop_id] = entrada
    return [e for e in entradas.values() if not e["removido"]]


def aplicativos_instalados(forcar: bool = False) -> List[dict]:
    return _em_cache("aplicativos", TTL_MONTAGENS_S, _aplicativos_brutos, forcar)


def argv_do_exec(linha_exec: str) -> List[str]:
    """Converte a linha Exec= de um .desktop em argumentos (sem %u, %f... nem @@ do Flatpak)."""
    try:
        partes = shlex.split(linha_exec)
    except ValueError:
        return []
    argv = []
    for parte in partes:
        if parte in ("@@", "@@u") or re.fullmatch(r"%[a-zA-Z]", parte):
            continue
        parte = re.sub(r"%[fFuUickdDnNvm]", "", parte).replace("%%", "%")
        if parte:
            argv.append(parte)
    return argv


def encontrar_aplicativo(consulta: str) -> Optional[dict]:
    """Atalho .desktop visível que melhor corresponde ao nome pedido."""
    q = " ".join(consulta.strip().lower().split())
    if not q:
        return None
    melhor, melhor_nota = None, 0
    for app in aplicativos_instalados():
        if app["oculto"]:
            continue
        sem_ext = app["id"].lower().removesuffix(".desktop")
        chaves = [app["nome"].lower(), app["nome_original"].lower(), sem_ext, sem_ext.rsplit(".", 1)[-1]]
        if q in chaves:
            nota = 4
        elif len(q) < 3:
            continue  # nomes curtos só abrem com correspondência exata, nunca um app aleatório
        elif any(c.startswith(q) for c in chaves if c):
            nota = 3
        elif any(q in c for c in chaves if c):
            nota = 2
        else:
            continue
        if nota > melhor_nota:
            melhor, melhor_nota = app, nota
    return melhor


# Nomes que o usuário fala → categoria de aplicativo
ALIASES_DE_CATEGORIA = {
    "navegador": "navegador", "browser": "navegador", "navegador de internet": "navegador",
    "chrome": "chrome", "google chrome": "chrome",
    "firefox": "firefox",
    "vscode": "vscode", "vs code": "vscode", "visual studio code": "vscode", "codigo": "vscode", "código": "vscode",
    "terminal": "terminal", "console": "terminal", "prompt de comando": "terminal",
    "calculadora": "calculadora", "calc": "calculadora", "calculator": "calculadora",
    "arquivos": "arquivos", "gerenciador de arquivos": "arquivos", "explorador de arquivos": "arquivos",
    "files": "arquivos", "pastas": "arquivos",
    "editor": "editor", "editor de texto": "editor", "bloco de notas": "editor", "notepad": "editor",
}

# Executáveis conhecidos de cada categoria, na ordem de preferência
CANDIDATOS_POR_CATEGORIA = {
    "navegador": ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "firefox",
                  "brave-browser", "brave", "microsoft-edge", "microsoft-edge-stable", "vivaldi",
                  "vivaldi-stable", "opera", "zen-browser", "librewolf", "epiphany"],
    "chrome": ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"],
    "firefox": ["firefox", "firefox-esr", "librewolf"],
    "vscode": ["code", "codium", "code-oss", "vscodium"],
    "terminal": ["x-terminal-emulator", "gnome-terminal", "kgx", "ptyxis", "konsole", "xfce4-terminal",
                 "mate-terminal", "lxterminal", "tilix", "terminator", "alacritty", "kitty", "wezterm",
                 "foot", "xterm"],
    "calculadora": ["gnome-calculator", "kcalc", "galculator", "qalculate-gtk", "qalculate-qt",
                    "mate-calc", "xcalc"],
    "arquivos": ["nautilus", "dolphin", "thunar", "nemo", "caja", "pcmanfm", "pcmanfm-qt"],
    "editor": ["gnome-text-editor", "gedit", "kate", "kwrite", "mousepad", "xed", "pluma", "featherpad"],
}

# Categoria freedesktop equivalente (acha apps Flatpak/Snap fora do PATH)
CATEGORIA_FREEDESKTOP = {
    "navegador": "WebBrowser", "terminal": "TerminalEmulator", "calculadora": "Calculator",
    "arquivos": "FileManager", "editor": "TextEditor",
}

# Consulta do aplicativo padrão escolhido pelo usuário no sistema
CONSULTA_DO_PADRAO = {
    "navegador": ["xdg-settings", "get", "default-web-browser"],
    "arquivos": ["xdg-mime", "query", "default", "inode/directory"],
    "editor": ["xdg-mime", "query", "default", "text/plain"],
}


def _entrada_por_id(desktop_id: str) -> Optional[dict]:
    for app in aplicativos_instalados():
        if app["id"] == desktop_id:
            return app
    return None


def aplicativo_padrao(categoria: str) -> Optional[dict]:
    consulta = CONSULTA_DO_PADRAO.get(categoria)
    if not consulta:
        return None
    saida = _executar(consulta)
    desktop_id = saida.splitlines()[0].strip() if saida else ""
    return _entrada_por_id(desktop_id) if desktop_id else None


def resolver_aplicativo(nome: str) -> Optional[dict]:
    """Como abrir o aplicativo pedido nesta máquina.

    Retorna {"tipo": "binario", "argv": [...], "rotulo": ...} ou
    {"tipo": "desktop", "entrada": {...}, "rotulo": ...}; None se não houver.
    """
    q = " ".join(nome.strip().lower().split())
    categoria = ALIASES_DE_CATEGORIA.get(q)
    if categoria:
        padrao = aplicativo_padrao(categoria)
        if padrao:
            return {"tipo": "desktop", "entrada": padrao, "rotulo": padrao["nome"] or padrao["id"]}
        for binario in CANDIDATOS_POR_CATEGORIA.get(categoria, []):
            caminho = shutil.which(binario)
            if caminho:
                return {"tipo": "binario", "argv": [caminho], "rotulo": binario}
        categoria_fd = CATEGORIA_FREEDESKTOP.get(categoria)
        if categoria_fd:
            for app in aplicativos_instalados():
                if not app["oculto"] and categoria_fd in app["categorias"]:
                    return {"tipo": "desktop", "entrada": app, "rotulo": app["nome"] or app["id"]}
        return None
    entrada = encontrar_aplicativo(q)
    if entrada:
        return {"tipo": "desktop", "entrada": entrada, "rotulo": entrada["nome"] or entrada["id"]}
    return None


# ---------------------------------------------------------------- perfil consolidado
def _perfil_bruto() -> dict:
    padroes = {}
    for categoria in ("navegador", "arquivos", "terminal"):
        resolvido = resolver_aplicativo(categoria)
        padroes[categoria] = resolvido["rotulo"] if resolvido else None
    return {
        "sistema": sistema_operacional(),
        "processador": processador(),
        "memoria_ram_gb": memoria_total_gb(),
        "placas_de_video": placas_de_video(),
        "discos": discos(),
        "tela": resolucao_da_tela(),
        "audio": servidor_de_audio(),
        "aplicativos_padrao": padroes,
    }


def perfil_da_maquina(forcar: bool = False) -> dict:
    """Perfil completo da máquina (cacheado por alguns minutos)."""
    if forcar:
        limpar_cache()
    return _em_cache("perfil", TTL_PERFIL_S, _perfil_bruto, forcar)


def resumo_da_maquina(forcar: bool = False) -> str:
    """Uma linha com o essencial da máquina, sem caminhos nem nomes de usuário."""
    p = perfil_da_maquina(forcar)
    so, cpu = p["sistema"], p["processador"]
    partes = [so["distribuicao"]]
    if so["sessao_grafica"] != "sem sessão gráfica":
        partes.append(f"{so['ambiente_grafico']} em {so['sessao_grafica'].upper() if so['sessao_grafica'] == 'x11' else 'Wayland'}")
    threads = f" ({cpu['nucleos_logicos']} threads)" if cpu.get("nucleos_logicos") else ""
    partes.append(f"CPU {cpu['modelo']}{threads}")
    partes.append(f"{p['memoria_ram_gb']:g} GB de RAM")
    gpus = p["placas_de_video"]
    partes.append("GPU " + ", ".join(f"{g['modelo']} ({g['tipo']})" for g in gpus) if gpus else "sem GPU detectada")
    if p["tela"] != "desconhecida":
        partes.append(f"tela {p['tela']}")
    if p["discos"]:
        partes.append("armazenamento: " + ", ".join(d["resumo"] for d in p["discos"]))
    return " · ".join(partes)
