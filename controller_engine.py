"""
controller_engine.py - Módulo de Controle Físico de Mouse, Teclado e Janelas do J.A.R.V.I.S.
Utiliza evdev (uinput) no nível de kernel com suporte universal a Wayland (GNOME / CachyOS).
"""

import os
import time
import subprocess
import re
import psutil
import glob
import logging
logger = logging.getLogger("JARVIS_CONTROLLER")

try:
    from evdev import UInput, ecodes as e
    EVDEV_DISPONIVEL = True
except ImportError:
    # Ambientes sem evdev (CI headless, outro sistema operacional) continuam importando
    # o módulo: apenas as funções de controle físico ficam indisponíveis.
    UInput = None
    EVDEV_DISPONIVEL = False

    class _EcodesIndisponivel:
        """Substituto neutro de evdev.ecodes quando a biblioteca não está instalada."""
        def __getattr__(self, nome):
            return 0

    e = _EcodesIndisponivel()
    logger.warning("evdev não encontrado: o controle físico de mouse e teclado está desativado.")

# Capacidades do dispositivo virtual completo (Mouse + Teclado)
CAPABILITIES = {
    e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_HWHEEL],
    e.EV_KEY: [
        e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE, e.BTN_SIDE, e.BTN_EXTRA,
        e.KEY_A, e.KEY_B, e.KEY_C, e.KEY_D, e.KEY_E, e.KEY_F, e.KEY_G, e.KEY_H,
        e.KEY_I, e.KEY_J, e.KEY_K, e.KEY_L, e.KEY_M, e.KEY_N, e.KEY_O, e.KEY_P,
        e.KEY_Q, e.KEY_R, e.KEY_S, e.KEY_T, e.KEY_U, e.KEY_V, e.KEY_W, e.KEY_X,
        e.KEY_Y, e.KEY_Z,
        e.KEY_0, e.KEY_1, e.KEY_2, e.KEY_3, e.KEY_4, e.KEY_5, e.KEY_6, e.KEY_7, e.KEY_8, e.KEY_9,
        e.KEY_SPACE, e.KEY_ENTER, e.KEY_TAB, e.KEY_ESC, e.KEY_BACKSPACE, e.KEY_DELETE,
        e.KEY_MINUS, e.KEY_EQUAL, e.KEY_LEFTBRACE, e.KEY_RIGHTBRACE, e.KEY_BACKSLASH,
        e.KEY_SEMICOLON, e.KEY_APOSTROPHE, e.KEY_GRAVE, e.KEY_COMMA, e.KEY_DOT, e.KEY_SLASH,
        e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT, e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL,
        e.KEY_LEFTALT, e.KEY_RIGHTALT, e.KEY_LEFTMETA, e.KEY_RIGHTMETA,
        e.KEY_UP, e.KEY_DOWN, e.KEY_LEFT, e.KEY_RIGHT,
        e.KEY_PAGEUP, e.KEY_PAGEDOWN, e.KEY_HOME, e.KEY_END, e.KEY_INSERT,
        e.KEY_F1, e.KEY_F2, e.KEY_F3, e.KEY_F4, e.KEY_F5, e.KEY_F6,
        e.KEY_F7, e.KEY_F8, e.KEY_F9, e.KEY_F10, e.KEY_F11, e.KEY_F12
    ]
}

_uinput_device = None

def get_uinput():
    global _uinput_device
    if not EVDEV_DISPONIVEL:
        return None
    if _uinput_device is None:
        try:
            _uinput_device = UInput(CAPABILITIES, name="JARVIS-Virtual-Control")
            logger.info("Dispositivo virtual uinput inicializado com sucesso!")
        except Exception as err:
            logger.error(f"Falha ao inicializar uinput: {err}")
    return _uinput_device

# Mapeamento de botões de mouse
MOUSE_BUTTONS = {
    "left": e.BTN_LEFT, "esquerdo": e.BTN_LEFT, "esq": e.BTN_LEFT,
    "right": e.BTN_RIGHT, "direito": e.BTN_RIGHT, "dir": e.BTN_RIGHT,
    "middle": e.BTN_MIDDLE, "meio": e.BTN_MIDDLE
}

# Mapeamento de caracteres simples para teclas
CHAR_TO_KEY = {
    'a': e.KEY_A, 'b': e.KEY_B, 'c': e.KEY_C, 'd': e.KEY_D, 'e': e.KEY_E,
    'f': e.KEY_F, 'g': e.KEY_G, 'h': e.KEY_H, 'i': e.KEY_I, 'j': e.KEY_J,
    'k': e.KEY_K, 'l': e.KEY_L, 'm': e.KEY_M, 'n': e.KEY_N, 'o': e.KEY_O,
    'p': e.KEY_P, 'q': e.KEY_Q, 'r': e.KEY_R, 's': e.KEY_S, 't': e.KEY_T,
    'u': e.KEY_U, 'v': e.KEY_V, 'w': e.KEY_W, 'x': e.KEY_X, 'y': e.KEY_Y,
    'z': e.KEY_Z,
    '0': e.KEY_0, '1': e.KEY_1, '2': e.KEY_2, '3': e.KEY_3, '4': e.KEY_4,
    '5': e.KEY_5, '6': e.KEY_6, '7': e.KEY_7, '8': e.KEY_8, '9': e.KEY_9,
    ' ': e.KEY_SPACE, '\n': e.KEY_ENTER, '\t': e.KEY_TAB,
    '-': e.KEY_MINUS, '=': e.KEY_EQUAL, '[': e.KEY_LEFTBRACE, ']': e.KEY_RIGHTBRACE,
    ';': e.KEY_SEMICOLON, "'": e.KEY_APOSTROPHE, ',': e.KEY_COMMA, '.': e.KEY_DOT,
    '/': e.KEY_SLASH, '\\': e.KEY_BACKSLASH, '`': e.KEY_GRAVE
}

# Caracteres que exigem SHIFT
SHIFT_CHARS = {
    '!': e.KEY_1, '@': e.KEY_2, '#': e.KEY_3, '$': e.KEY_4, '%': e.KEY_5,
    '^': e.KEY_6, '&': e.KEY_7, '*': e.KEY_8, '(': e.KEY_9, ')': e.KEY_0,
    '_': e.KEY_MINUS, '+': e.KEY_EQUAL, '{': e.KEY_LEFTBRACE, '}': e.KEY_RIGHTBRACE,
    ':': e.KEY_SEMICOLON, '"': e.KEY_APOSTROPHE, '<': e.KEY_COMMA, '>': e.KEY_DOT,
    '?': e.KEY_SLASH, '~': e.KEY_GRAVE, '|': e.KEY_BACKSLASH
}

# Nomes de teclas especiais e atalhos
HOTKEY_NAMES = {
    'ctrl': e.KEY_LEFTCTRL, 'control': e.KEY_LEFTCTRL,
    'alt': e.KEY_LEFTALT,
    'shift': e.KEY_LEFTSHIFT,
    'super': e.KEY_LEFTMETA, 'win': e.KEY_LEFTMETA, 'windows': e.KEY_LEFTMETA, 'meta': e.KEY_LEFTMETA,
    'enter': e.KEY_ENTER, 'return': e.KEY_ENTER,
    'esc': e.KEY_ESC, 'escape': e.KEY_ESC,
    'tab': e.KEY_TAB,
    'backspace': e.KEY_BACKSPACE,
    'space': e.KEY_SPACE, 'espaco': e.KEY_SPACE,
    'up': e.KEY_UP, 'cima': e.KEY_UP,
    'down': e.KEY_DOWN, 'baixo': e.KEY_DOWN,
    'left': e.KEY_LEFT, 'esquerda': e.KEY_LEFT,
    'right': e.KEY_RIGHT, 'direita': e.KEY_RIGHT,
    'pageup': e.KEY_PAGEUP, 'pagedown': e.KEY_PAGEDOWN,
    'home': e.KEY_HOME, 'end': e.KEY_END,
    'delete': e.KEY_DELETE, 'del': e.KEY_DELETE,
    'f1': e.KEY_F1, 'f2': e.KEY_F2, 'f3': e.KEY_F3, 'f4': e.KEY_F4,
    'f5': e.KEY_F5, 'f6': e.KEY_F6, 'f7': e.KEY_F7, 'f8': e.KEY_F8,
    'f9': e.KEY_F9, 'f10': e.KEY_F10, 'f11': e.KEY_F11, 'f12': e.KEY_F12
}

def get_screen_geometry() -> str:
    """Retorna a resolução atual do monitor."""
    try:
        res = subprocess.run(["xdpyinfo"], capture_output=True, text=True, timeout=1.5)
        for line in res.stdout.splitlines():
            if "dimensions:" in line:
                m = re.search(r'(\d+x\d+)\s+pixels', line)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return "3000x2160"

def get_mouse_position() -> dict:
    """Obtém a posição atual aproximada do cursor."""
    try:
        from Xlib import display
        d = display.Display(":0")
        ptr = d.screen().root.query_pointer()
        return {"x": ptr.root_x, "y": ptr.root_y}
    except Exception:
        return {"x": None, "y": None}

def get_open_windows() -> list:
    """Inspeciona janelas e aplicações gráficas ativas do usuário."""
    windows = []
    seen = set()

    # 1. Janelas X11 / Xwayland via xprop
    try:
        res = subprocess.run(["xprop", "-root", "_NET_CLIENT_LIST"], capture_output=True, text=True, timeout=1.5)
        if res.returncode == 0:
            ids = re.findall(r'0x[0-9a-fA-F]+', res.stdout)
            for wid in ids:
                try:
                    wres = subprocess.run(["xprop", "-id", wid, "WM_NAME", "WM_CLASS"], capture_output=True, text=True, timeout=1)
                    title = ""
                    wm_class = ""
                    for line in wres.stdout.splitlines():
                        if "WM_NAME" in line and "=" in line:
                            title = line.split("=", 1)[1].strip().strip('"')
                        elif "WM_CLASS" in line and "=" in line:
                            wm_class = line.split("=", 1)[1].strip().strip('"')
                    if title and title not in seen:
                        seen.add(title)
                        windows.append({"titulo": title, "tipo": "janela_ativa", "classe": wm_class})
                except Exception:
                    pass
    except Exception:
        pass

    # 2. Processos de aplicações desktop do usuário (Chrome, Antigravity, VS Code, Steam, Terminal, etc.)
    desktop_map = {
        "antigravity": "Antigravity IDE",
        "code": "Visual Studio Code",
        "steam": "Steam / Jogos",
        "google-chrome": "Google Chrome",
        "chrome": "Google Chrome",
        "firefox": "Firefox",
        "zen": "Zen Browser",
        "ptyxis": "Terminal (Ptyxis)",
        "gnome-terminal-server": "Terminal GNOME",
        "nautilus": "Arquivos (Nautilus)",
        "dolphin": "Dolphin",
        "discord": "Discord",
        "telegram-desktop": "Telegram",
        "spotify": "Spotify",
        "chatgpt": "ChatGPT",
        "claude-desktop": "Claude",
        "loupe": "Visualizador de Imagens (Loupe)"
    }

    try:
        for p in psutil.process_iter(["name", "cmdline", "pid"]):
            name = (p.info["name"] or "").lower()
            if name in desktop_map:
                app_label = desktop_map[name]
                if app_label not in seen:
                    seen.add(app_label)
                    windows.append({"titulo": app_label, "tipo": "aplicacao_aberta", "binario": name, "pid": p.info["pid"]})
    except Exception:
        pass

    return windows

def move_mouse(delta_x: int, delta_y: int) -> dict:
    """Move o mouse em coordenadas relativas (delta_x, delta_y)."""
    ui = get_uinput()
    if not ui:
        return {"sucesso": False, "mensagem": "Dispositivo virtual de mouse não disponível."}
    try:
        ui.write(e.EV_REL, e.REL_X, int(delta_x))
        ui.write(e.EV_REL, e.REL_Y, int(delta_y))
        ui.syn()
        pos = get_mouse_position()
        return {"sucesso": True, "delta_x": delta_x, "delta_y": delta_y, "posicao_atual": pos, "mensagem": f"Cursor deslocado em ({delta_x}, {delta_y}) pixels, senhor."}
    except Exception as exc:
        return {"sucesso": False, "mensagem": f"Erro ao mover mouse: {exc}"}

def click_mouse(button: str = "left", double: bool = False) -> dict:
    """Clica com o botão do mouse (left, right, middle) ou executa duplo-clique."""
    ui = get_uinput()
    if not ui:
        return {"sucesso": False, "mensagem": "Dispositivo virtual de mouse não disponível."}
    btn_code = MOUSE_BUTTONS.get(button.lower(), e.BTN_LEFT)
    try:
        clicks = 2 if double else 1
        for _ in range(clicks):
            ui.write(e.EV_KEY, btn_code, 1)
            ui.syn()
            time.sleep(0.05)
            ui.write(e.EV_KEY, btn_code, 0)
            ui.syn()
            if double:
                time.sleep(0.08)
        tipo = "duplo-clique" if double else f"clique {button}"
        return {"sucesso": True, "tipo": tipo, "mensagem": f"Executado {tipo} com sucesso, senhor."}
    except Exception as exc:
        return {"sucesso": False, "mensagem": f"Erro ao clicar: {exc}"}

def scroll_mouse(direction: str = "down", amount: int = 3) -> dict:
    """Rola o scroll do mouse para cima ('up') ou para baixo ('down')."""
    ui = get_uinput()
    if not ui:
        return {"sucesso": False, "mensagem": "Dispositivo virtual de mouse não disponível."}
    try:
        steps = amount if direction.lower() in ["up", "cima"] else -amount
        ui.write(e.EV_REL, e.REL_WHEEL, steps)
        ui.syn()
        return {"sucesso": True, "direcao": direction, "passos": amount, "mensagem": f"Scroll realizado para {direction} em {amount} passos, senhor."}
    except Exception as exc:
        return {"sucesso": False, "mensagem": f"Erro ao rolar scroll: {exc}"}

def type_text(text: str) -> dict:
    """Digita texto na janela ativa através do teclado virtual de hardware."""
    ui = get_uinput()
    if not ui:
        return {"sucesso": False, "mensagem": "Dispositivo virtual de teclado não disponível."}
    try:
        for char in text:
            if char in SHIFT_CHARS:
                kcode = SHIFT_CHARS[char]
                ui.write(e.EV_KEY, e.KEY_LEFTSHIFT, 1)
                ui.write(e.EV_KEY, kcode, 1)
                ui.syn()
                time.sleep(0.01)
                ui.write(e.EV_KEY, kcode, 0)
                ui.write(e.EV_KEY, e.KEY_LEFTSHIFT, 0)
                ui.syn()
            elif char.isupper():
                kcode = CHAR_TO_KEY.get(char.lower())
                if kcode:
                    ui.write(e.EV_KEY, e.KEY_LEFTSHIFT, 1)
                    ui.write(e.EV_KEY, kcode, 1)
                    ui.syn()
                    time.sleep(0.01)
                    ui.write(e.EV_KEY, kcode, 0)
                    ui.write(e.EV_KEY, e.KEY_LEFTSHIFT, 0)
                    ui.syn()
            else:
                kcode = CHAR_TO_KEY.get(char)
                if kcode:
                    ui.write(e.EV_KEY, kcode, 1)
                    ui.syn()
                    time.sleep(0.01)
                    ui.write(e.EV_KEY, kcode, 0)
                    ui.syn()
            time.sleep(0.02)
        return {"sucesso": True, "texto": text, "mensagem": f"Texto '{text}' digitado com precisão na interface, senhor."}
    except Exception as exc:
        return {"sucesso": False, "mensagem": f"Erro ao digitar texto: {exc}"}

def press_hotkey(keys_str: str) -> dict:
    """Executa combinações de teclas como 'ctrl+c', 'alt+tab', 'super', 'ctrl+v', 'enter'."""
    ui = get_uinput()
    if not ui:
        return {"sucesso": False, "mensagem": "Dispositivo virtual de teclado não disponível."}
    parts = [p.strip().lower() for p in keys_str.split("+") if p.strip()]
    key_codes = []
    for p in parts:
        if p in HOTKEY_NAMES:
            key_codes.append(HOTKEY_NAMES[p])
        elif p in CHAR_TO_KEY:
            key_codes.append(CHAR_TO_KEY[p])
        else:
            return {"sucesso": False, "mensagem": f"Tecla '{p}' não reconhecida no atalho."}

    try:
        # Pressiona todas as teclas na sequência
        for kc in key_codes:
            ui.write(e.EV_KEY, kc, 1)
        ui.syn()
        time.sleep(0.08)
        # Solta em ordem reversa
        for kc in reversed(key_codes):
            ui.write(e.EV_KEY, kc, 0)
        ui.syn()
        return {"sucesso": True, "atalho": keys_str, "mensagem": f"Atalho '{keys_str}' executado com sucesso no sistema, senhor."}
    except Exception as exc:
        return {"sucesso": False, "mensagem": f"Erro ao executar atalho: {exc}"}
