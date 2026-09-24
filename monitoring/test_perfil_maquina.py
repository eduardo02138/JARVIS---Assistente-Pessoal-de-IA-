"""Identificação automática da máquina (perfil_maquina.py) com hardware simulado.

Cada teste monta uma árvore falsa de /sys, /proc e /dev para provar que o
assistente reconhece discos, GPUs, telas e aplicativos de qualquer computador
sem nomes fixos, e que nada quebra numa máquina sem esses recursos.
"""

import os
import subprocess

import pytest

import perfil_maquina as pm
import system_tools


@pytest.fixture
def maquina(tmp_path, monkeypatch):
    """Raízes falsas do sistema, sem utilitários externos nem sessão gráfica."""
    raiz = {"sys": tmp_path / "sys", "proc": tmp_path / "proc", "dev": tmp_path / "dev", "dados": tmp_path / "dados"}
    for pasta in raiz.values():
        pasta.mkdir()
    monkeypatch.setattr(pm, "RAIZ_SYS", str(raiz["sys"]))
    monkeypatch.setattr(pm, "RAIZ_PROC", str(raiz["proc"]))
    monkeypatch.setattr(pm, "RAIZ_DEV", str(raiz["dev"]))
    monkeypatch.setattr(pm, "ARQUIVO_OS_RELEASE", str(tmp_path / "os-release"))
    monkeypatch.setattr(pm, "ARQUIVOS_PCI_IDS", ())
    monkeypatch.setattr(pm, "DIRETORIOS_EXTRAS_DE_APLICATIVOS", ())
    monkeypatch.setattr(pm, "_executar", lambda *a, **k: "")
    monkeypatch.setenv("XDG_DATA_HOME", str(raiz["dados"] / "home"))
    monkeypatch.setenv("XDG_DATA_DIRS", str(raiz["dados"] / "sistema"))
    for var in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "DESKTOP_SESSION"):
        monkeypatch.delenv(var, raising=False)
    pm.limpar_cache()
    yield raiz
    pm.limpar_cache()


def _escrever(caminho, conteudo):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(conteudo, encoding="utf-8")


def _criar_disco(sys_raiz, nome, caminho_pai, modelo, setores, rotacional, particoes):
    real = sys_raiz / "devices" / caminho_pai / nome
    _escrever(real / "size", str(setores))
    _escrever(real / "queue" / "rotational", rotacional)
    _escrever(real / "removable", "0")
    _escrever(real / "device" / "model", modelo)
    (sys_raiz / "block").mkdir(exist_ok=True)
    (sys_raiz / "class" / "block").mkdir(parents=True, exist_ok=True)
    os.symlink(real, sys_raiz / "block" / nome)
    os.symlink(real, sys_raiz / "class" / "block" / nome)
    for particao in particoes:
        _escrever(real / particao / "partition", "1")
        os.symlink(real / particao, sys_raiz / "class" / "block" / particao)


def _montar_discos(raiz):
    _criar_disco(raiz["sys"], "nvme0n1", "pci0000:00/0000:00:1d.0/nvme/nvme0", "Samsung SSD 980 1TB",
                 1953525168, "0", ["nvme0n1p1", "nvme0n1p2"])
    _criar_disco(raiz["sys"], "sda", "pci0000:00/0000:00:17.0/ata1/host0/target0:0:0/0:0:0:0/block",
                 "KINGSTON SA400S37240G", 468862128, "0", ["sda1"])
    _criar_disco(raiz["sys"], "sdb", "pci0000:00/0000:00:14.0/usb2/2-1/2-1:1.0/host6/target6:0:0/6:0:0:0/block",
                 "ST2000DM008-2FR102", 3907029168, "1", ["sdb1"])
    _escrever(raiz["proc"] / "mounts", "\n".join([
        "/dev/nvme0n1p2 / btrfs rw 0 0",
        "/dev/nvme0n1p2 /srv/dados btrfs rw 0 0",
        "/dev/nvme0n1p1 /boot/efi vfat rw 0 0",
        "/dev/sda1 /run/media/usuario/Novo\\040volume ext4 rw 0 0",
        "/dev/sdb1 /mnt/jogos ntfs3 rw 0 0",
        "/dev/loop0 /snap/core22/1 squashfs ro 0 0",
        "tmpfs /tmp tmpfs rw 0 0",
    ]) + "\n")
    rotulos = raiz["dev"] / "disk" / "by-label"
    rotulos.mkdir(parents=True)
    os.symlink("../../sda1", rotulos / "Novo\\x20volume")


def test_discos_identificados_por_tipo_modelo_e_rotulo(maquina):
    _montar_discos(maquina)

    assert pm.identificar_disco("/srv/dados/SteamLibrary/steamapps/common/Hades") == "SSD NVMe Samsung SSD 980 1TB (1 TB)"
    assert pm.identificar_disco("/run/media/usuario/Novo volume/Jogos/GTA V") == "SSD SATA KINGSTON SA400S37240G (240 GB) 'Novo volume'"
    assert pm.identificar_disco("/mnt/jogos/Red Dead Redemption 2") == "disco USB ST2000DM008-2FR102 (2 TB)"

    discos = {d["disco"]: d for d in pm.discos()}
    assert set(discos) == {"nvme0n1", "sda", "sdb"}, "EFI, snaps e tmpfs não são discos do usuário"
    assert len(discos["nvme0n1"]["particoes"]) == 1, "subvolumes btrfs do mesmo dispositivo contam uma vez"
    assert discos["sdb"]["tamanho"] == "2 TB"


def test_disco_por_tras_de_luks_ou_lvm(maquina):
    _criar_disco(maquina["sys"], "nvme0n1", "pci0000:00/nvme/nvme0", "WD Blue SN580", 976773168, "0", ["nvme0n1p3"])
    dm = maquina["sys"] / "devices" / "virtual" / "block" / "dm-0"
    (dm / "slaves").mkdir(parents=True)
    (dm / "slaves" / "nvme0n1p3").touch()
    os.symlink(dm, maquina["sys"] / "class" / "block" / "dm-0")
    _escrever(maquina["proc"] / "mounts", "/dev/dm-0 / ext4 rw 0 0\n")

    assert pm.identificar_disco("/home/qualquer") == "SSD NVMe WD Blue SN580 (500 GB)"


def test_placas_de_video_amd_e_intel_com_telemetria(maquina, tmp_path, monkeypatch):
    pci_ids = tmp_path / "pci.ids"
    _escrever(pci_ids, "\n".join([
        "# comentário",
        "1002  Advanced Micro Devices, Inc. [AMD/ATI]",
        "\t73df  Navi 22 [Radeon RX 6700/6700 XT/6750 XT / 6800M/6850M XT]",
        "\t\t1002 0e36  subsistema ignorado",
        "8086  Intel Corporation",
        "\t9a49  TigerLake-LP GT2 [Iris Xe Graphics]",
    ]) + "\n")
    monkeypatch.setattr(pm, "ARQUIVOS_PCI_IDS", (str(pci_ids),))

    def criar_pci(slot, classe, vendor, device, driver, extras=None):
        real = maquina["sys"] / "devices" / "pci0000:00" / slot
        for nome, valor in {"class": classe, "vendor": vendor, "device": device, **(extras or {})}.items():
            _escrever(real / nome, valor)
        (maquina["sys"] / "bus" / "pci" / "drivers" / driver).mkdir(parents=True, exist_ok=True)
        os.symlink(maquina["sys"] / "bus" / "pci" / "drivers" / driver, real / "driver")
        (maquina["sys"] / "bus" / "pci" / "devices").mkdir(parents=True, exist_ok=True)
        os.symlink(real, maquina["sys"] / "bus" / "pci" / "devices" / slot)
        return real

    amd = criar_pci("0000:03:00.0", "0x030000", "0x1002", "0x73df", "amdgpu", {
        "mem_info_vram_total": str(12 * 1024**3), "mem_info_vram_used": str(1024**3),
        "gpu_busy_percent": "37", "hwmon/hwmon3/temp1_input": "52000",
    })
    criar_pci("0000:00:02.0", "0x030000", "0x8086", "0x9a49", "i915")
    criar_pci("0000:00:1f.3", "0x040300", "0x8086", "0xa0c8", "snd_hda_intel")  # áudio: ignorado

    # Telas: modo nativo dos monitores conectados, direto do kernel (sem xrandr)
    drm = maquina["sys"] / "class" / "drm"
    (drm / "card0").mkdir(parents=True)
    os.symlink(amd, drm / "card0" / "device")
    for conector, status, modos in [("card0-DP-1", "disconnected", ""),
                                    ("card0-HDMI-A-1", "connected", "2560x1440\n1920x1080"),
                                    ("card1-eDP-1", "connected", "1920x1080")]:
        _escrever(drm / conector / "status", status)
        _escrever(drm / conector / "modes", modos)

    placas = pm.placas_de_video()
    assert [(p["modelo"], p["tipo"]) for p in placas] == [
        ("AMD Radeon RX 6700/6700 XT/6750 XT / 6800M/6850M XT", "dedicada"),
        ("Intel Iris Xe Graphics", "integrada"),
    ]
    assert placas[0]["driver"] == "amdgpu"
    assert pm.telemetria_gpu_sysfs() == {
        "modelo": "AMD Radeon RX 6700/6700 XT/6750 XT / 6800M/6850M XT",
        "uso_percentual": 37, "vram_usada_mb": 1024, "vram_total_mb": 12288, "temperatura_c": 52,
    }
    assert pm.resolucao_da_tela() == "2560x1440 + 1920x1080"

    # Sem nvidia-smi, a ferramenta da GPU usa a telemetria do kernel
    monkeypatch.setattr(system_tools, "_gpu_nvidia", lambda: None)
    monkeypatch.setattr(system_tools, "_last_gpu_result", None)
    gpu = system_tools.get_gpu_status()
    assert gpu["disponivel"] and gpu["uso_gpu"] == "37%" and gpu["temperatura"] == "52°C"
    assert gpu["modelo"].startswith("AMD Radeon RX 6700")


def test_nomes_de_gpu_sem_banco_pci(maquina):
    assert pm._nome_amigavel("NVIDIA", "AD107 [GeForce RTX 4060]", "0x2882") == "NVIDIA GeForce RTX 4060"
    assert pm._nome_amigavel("AMD", "", "0x73df") == "AMD (dispositivo 0x73df)"
    assert pm._tipo_de_gpu("0x8086", "Intel Arc A770", "") == "dedicada"
    assert pm._tipo_de_gpu("0x8086", "Intel Arc Graphics", "") == "integrada"
    assert pm._tipo_de_gpu("0x1af4", "Virtio 1.0 GPU", "") == "virtual"


def test_sistema_processador_e_ambiente(maquina, monkeypatch, tmp_path):
    _escrever(tmp_path / "os-release", 'NAME="Fedora Linux"\nPRETTY_NAME="Fedora Linux 40 (Workstation Edition)"\n')
    _escrever(maquina["proc"] / "cpuinfo", "processor\t: 0\nmodel\t\t: 33\nmodel name\t: AMD Ryzen(TM) 7 5800X 8-Core Processor\n")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")

    for bruto, esperado in [("ubuntu:GNOME", "GNOME"), ("Budgie:GNOME", "Budgie"), ("X-Cinnamon", "Cinnamon"), ("KDE", "KDE")]:
        monkeypatch.setenv("XDG_CURRENT_DESKTOP", bruto)
        assert pm.sistema_operacional()["ambiente_grafico"] == esperado

    so = pm.sistema_operacional()
    assert so["distribuicao"] == "Fedora Linux 40 (Workstation Edition)"
    assert so["sessao_grafica"] == "wayland"
    assert pm.processador()["modelo"] == "AMD Ryzen 7 5800X 8-Core Processor"


def test_maquina_sem_gpu_tela_ou_discos_nao_quebra(maquina):
    perfil = pm.perfil_da_maquina(forcar=True)
    assert perfil["placas_de_video"] == []
    assert perfil["discos"] == []
    assert perfil["tela"] == "desconhecida"
    assert perfil["sistema"]["sessao_grafica"] == "sem sessão gráfica"
    resumo = pm.resumo_da_maquina()
    assert "sem GPU detectada" in resumo
    assert "/" not in resumo, "o resumo vai para o prompt: sem caminhos nem nomes de usuário"
    assert pm.identificar_disco("/qualquer/lugar") == "armazenamento local"


def _atalho(pasta, nome_arquivo, campos):
    linhas = ["[Desktop Entry]", "Type=Application"] + [f"{chave}={valor}" for chave, valor in campos.items()]
    # Grupos de ação não podem sobrescrever o nome do aplicativo
    _escrever(pasta / nome_arquivo, "\n".join(linhas) + "\n[Desktop Action nova]\nName=Não deve vazar\n")


def test_aplicativos_xdg_flatpak_e_categorias(maquina, monkeypatch):
    usuario = maquina["dados"] / "home" / "applications"
    sistema = maquina["dados"] / "sistema" / "applications"
    _atalho(sistema, "firefox.desktop", {"Name": "Firefox", "Exec": "firefox %u", "Categories": "Network;WebBrowser;"})
    _atalho(usuario, "firefox.desktop", {"Name": "Firefox Pessoal", "Exec": "firefox --profile x %u",
                                         "Categories": "Network;WebBrowser;"})
    _atalho(sistema, "org.gnome.Calculator.desktop", {"Name": "Calculator", "Name[pt_BR]": "Calculadora",
                                                      "Exec": "gnome-calculator", "Categories": "GTK;Utility;Calculator;"})
    _atalho(sistema, "com.spotify.Client.desktop", {
        "Name": "Spotify", "Exec": "/usr/bin/flatpak run --branch=stable --command=spotify com.spotify.Client @@u %U @@"})
    _atalho(sistema, "removido.desktop", {"Name": "Removido", "Exec": "removido", "Hidden": "true"})
    _atalho(sistema, "helper.desktop", {"Name": "Helper Interno", "Exec": "helper", "NoDisplay": "true"})

    apps = {a["id"]: a for a in pm.aplicativos_instalados()}
    assert apps["firefox.desktop"]["nome"] == "Firefox Pessoal", "o diretório do usuário tem prioridade"
    assert "removido.desktop" not in apps
    assert pm.argv_do_exec(apps["com.spotify.Client.desktop"]["exec"]) == [
        "/usr/bin/flatpak", "run", "--branch=stable", "--command=spotify", "com.spotify.Client"]
    assert pm.encontrar_aplicativo("helper interno") is None, "atalhos ocultos não são abertos por nome"
    assert pm.encontrar_aplicativo("spotify")["id"] == "com.spotify.Client.desktop"
    assert pm.encontrar_aplicativo("calculadora")["id"] == "org.gnome.Calculator.desktop"
    assert pm.encontrar_aplicativo("fi") is None, "pedido curto demais não abre app por aproximação"
    assert pm.encontrar_aplicativo("spot")["id"] == "com.spotify.Client.desktop"

    # Sem nenhum executável no PATH, a categoria freedesktop ainda acha o app (ex.: Flatpak)
    monkeypatch.setattr(pm.shutil, "which", lambda nome: None)
    assert pm.resolver_aplicativo("navegador")["entrada"]["id"] == "firefox.desktop"
    assert pm.resolver_aplicativo("calculadora")["rotulo"] == "Calculadora"

    # Com binários instalados, o primeiro candidato disponível vence — sem exigir Google Chrome
    monkeypatch.setattr(pm.shutil, "which", lambda nome: f"/usr/bin/{nome}" if nome in ("firefox", "konsole") else None)
    assert pm.resolver_aplicativo("navegador") == {"tipo": "binario", "argv": ["/usr/bin/firefox"], "rotulo": "firefox"}
    assert pm.resolver_aplicativo("terminal")["rotulo"] == "konsole"

    # O navegador padrão escolhido no sistema tem prioridade sobre a lista
    monkeypatch.setattr(pm, "_executar", lambda argv, timeout=2.0: "firefox.desktop" if argv[0] == "xdg-settings" else "")
    assert pm.resolver_aplicativo("navegador")["tipo"] == "desktop"


def test_captura_de_tela_escolhe_ferramenta_da_sessao(monkeypatch):
    monkeypatch.setattr(system_tools.shutil, "which", lambda nome: f"/usr/bin/{nome}")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    assert system_tools._comandos_de_captura("/tmp/x.png")[0][0] == "spectacle"
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    wayland = system_tools._comandos_de_captura("/tmp/x.png")
    assert wayland[0][0] == "gnome-screenshot"
    assert not any(cmd[0] in ("maim", "scrot", "import", "ffmpeg") for cmd in wayland), "X11 no Wayland gera imagem preta"

    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    x11 = system_tools._comandos_de_captura("/tmp/x.png")
    assert x11[0][0] == "maim"
    ffmpeg = next(cmd for cmd in x11 if cmd[0] == "ffmpeg")
    assert "-video_size" not in ffmpeg, "a resolução não pode ser fixa"

    monkeypatch.delenv("XDG_SESSION_TYPE")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert system_tools._comandos_de_captura("/tmp/x.png") == []


def test_captura_de_tela_nao_sai_da_pasta(tmp_path, monkeypatch):
    monkeypatch.setattr(system_tools, "_pasta_de_capturas", lambda: str(tmp_path / "Capturas"))
    monkeypatch.setattr(system_tools, "_comandos_de_captura", lambda saida: [["ferramenta-falsa", saida]])

    def captura_falsa(cmd, **kwargs):
        with open(cmd[1], "wb") as f:
            f.write(b"\x89PNG")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(system_tools.subprocess, "run", captura_falsa)
    res = system_tools.take_screenshot("../../etc/passwd")
    assert res["sucesso"] is True
    assert res["arquivo"] == str(tmp_path / "Capturas" / "passwd.png")
    assert os.listdir(tmp_path / "Capturas") == ["passwd.png"]


def test_volume_usa_o_controle_disponivel(monkeypatch):
    chamadas = []

    def executar(cmd, **kwargs):
        chamadas.append(cmd[0])
        if cmd[0] == "wpctl":
            raise subprocess.CalledProcessError(1, cmd)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(system_tools.subprocess, "run", executar)
    monkeypatch.setattr(system_tools.shutil, "which", lambda nome: f"/usr/bin/{nome}" if nome in ("wpctl", "amixer") else None)
    res = system_tools.adjust_volume("mutar")
    assert res["sucesso"] is True and res["controle"] == "amixer"
    assert chamadas == ["wpctl", "amixer"]

    chamadas.clear()
    assert system_tools.adjust_volume("explodir")["sucesso"] is False
    assert chamadas == [], "ação desconhecida não executa nada"

    monkeypatch.setattr(system_tools.shutil, "which", lambda nome: None)
    assert system_tools.adjust_volume("aumentar", 10)["sucesso"] is False, "sem controle de áudio não há sucesso falso"


def test_telemetria_em_tela_sem_gpu_nao_inventa_valores(monkeypatch):
    monkeypatch.setattr(system_tools, "get_gpu_status", lambda: {"disponivel": False, "mensagem": "sem GPU"})
    res = system_tools.toggle_telemetry_overlay(True)
    assert res["telemetry"]["gpu_modelo"] is None and res["telemetry"]["gpu_temp"] is None
    assert "nenhuma placa de vídeo" in res["mensagem"]


def test_clientes_locais_usam_a_porta_configurada(monkeypatch):
    from servidor import url_local_do_servidor

    monkeypatch.setenv("PORT", "9123")
    monkeypatch.setenv("JARVIS_HOST", "0.0.0.0")
    assert url_local_do_servidor() == "http://127.0.0.1:9123"
    monkeypatch.setenv("JARVIS_HOST", "::1")
    assert url_local_do_servidor() == "http://[::1]:9123"


def test_nenhum_hardware_fixo_no_prompt_ou_na_interface():
    from servidor.instrucoes import JARVIS_SYSTEM_INSTRUCTION

    assert "RTX 5060" not in JARVIS_SYSTEM_INSTRUCTION and "drive gamer" not in JARVIS_SYSTEM_INSTRUCTION
    assert "identificada automaticamente" in JARVIS_SYSTEM_INSTRUCTION
    assert "get_machine_profile" in system_tools.TOOL_REGISTRY
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for arquivo in ("gemini-live-widget/widget.js", "gemini-live-widget/index.html", "system_tools.py", "controller_engine.py"):
        with open(os.path.join(raiz, arquivo), encoding="utf-8") as f:
            codigo = f.read()
        for fixo in ("GPU NVIDIA RTX", "3000x2160", "1920x1080\"", "/home/edu", "SSD Gamer"):
            assert fixo not in codigo, f"{arquivo} ainda assume a máquina de um usuário: {fixo}"
