"""
preferences_manager.py - Gerenciador de Memória Persistente & Preferências do Usuário para o J.A.R.V.I.S.
Semelhante aos Aplicativos Padrão do Windows e retenção de perfil contextual.
"""

import os
import json
import threading
import logging

logger = logging.getLogger("JARVIS_PREFERENCES")

PREFERENCES_FILE = os.path.expanduser("/home/edu/Documentos/assistente/user_preferences.json")
_lock = threading.Lock()

DEFAULT_SCHEMA = {
    "default_apps": {
        "music_platform": None,  # None indica que deve perguntar ao usuário na 1ª vez (YouTube, Spotify, etc.)
        "browser": "default",     # "default", "google-chrome", "firefox", "brave", etc.
        "email_client": "default",
        "text_editor": "antigravity", # IDE padrão
        "image_viewer": "default",
        "video_player": "default"
    },
    "game_preferences": {},       # ex: {"gta": {"distribuidora": "heroic", "custom_args": ""}}
    "file_associations": {},      # ex: {".pdf": "evince", ".py": "antigravity"}
    "custom_memories": {}         # Fatos e preferências gerais aprendidas sobre o senhor
}

def load_preferences() -> dict:
    """Carrega as preferências salvas no disco. Inicializa com o schema padrão se não existir."""
    with _lock:
        if not os.path.exists(PREFERENCES_FILE):
            save_preferences_unlocked(DEFAULT_SCHEMA)
            return dict(DEFAULT_SCHEMA)
        try:
            with open(PREFERENCES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Garante que todas as chaves básicas existem
            for key, val in DEFAULT_SCHEMA.items():
                if key not in data:
                    data[key] = val
                elif isinstance(val, dict) and isinstance(data[key], dict):
                    for subkey, subval in val.items():
                        if subkey not in data[key]:
                            data[key][subkey] = subval
            return data
        except Exception as e:
            logger.error(f"Erro ao carregar preferências: {e}")
            return dict(DEFAULT_SCHEMA)

def save_preferences_unlocked(data: dict) -> bool:
    """Salva de forma atômica no arquivo JSON."""
    try:
        tmp_file = f"{PREFERENCES_FILE}.tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_file, PREFERENCES_FILE)
        return True
    except Exception as e:
        logger.error(f"Erro ao salvar preferências: {e}")
        return False

def get_preference(category: str, key: str, default=None):
    """Obtém uma preferência específica em uma categoria."""
    data = load_preferences()
    cat = data.get(category, {})
    if isinstance(cat, dict):
        return cat.get(key, default)
    return default

def set_preference(category: str, key: str, value) -> bool:
    """Salva ou atualiza uma preferência em uma categoria de forma thread-safe."""
    with _lock:
        try:
            if os.path.exists(PREFERENCES_FILE):
                with open(PREFERENCES_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = dict(DEFAULT_SCHEMA)
        except Exception:
            data = dict(DEFAULT_SCHEMA)

        if category not in data or not isinstance(data[category], dict):
            data[category] = {}

        data[category][key] = value
        return save_preferences_unlocked(data)

def delete_preference(category: str, key: str) -> bool:
    """Remove uma preferência específica."""
    with _lock:
        try:
            if not os.path.exists(PREFERENCES_FILE):
                return True
            with open(PREFERENCES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if category in data and isinstance(data[category], dict) and key in data[category]:
                del data[category][key]
                return save_preferences_unlocked(data)
            return True
        except Exception as e:
            logger.error(f"Erro ao deletar preferência: {e}")
            return False

def get_all_preferences() -> dict:
    """Retorna todas as preferências gravadas."""
    return load_preferences()

def reset_all_preferences() -> bool:
    """Restaura as configurações de fábrica."""
    with _lock:
        return save_preferences_unlocked(DEFAULT_SCHEMA)
