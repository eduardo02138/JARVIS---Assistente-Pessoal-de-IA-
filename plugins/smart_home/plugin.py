"""
Plug-in: Casa Inteligente & Automação Residencial (Smart Home / LifeKit)
Permite controlar luzes, climatização e acionar cenas no laboratório e residência.
"""

import json
import logging
import os
from plugin_sdk import JarvisPlugin, PluginMeta

logger = logging.getLogger("jarvis.plugins.smart_home")

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ASSETS_PADRAO = os.path.join(RAIZ, "skills", "smart-home", "assets", "estado_padrao.json")

# Fallback embutido caso o arquivo de camada L3 (assets/) seja removido.
ESTADO_FALLBACK = {
    "lights": {
        "escritorio": {"ligado": True, "cor": "Ciano Holográfico", "brilho": 80},
        "quarto": {"ligado": False, "cor": "Branco Quente", "brilho": 40},
        "sala": {"ligado": True, "cor": "Branco Neutro", "brilho": 60},
    },
    "clima": {"temperatura": "22°C", "modo": "Refrigeração Nominal", "umidade": "55%"},
}


def _carregar_estado_padrao() -> dict:
    """Lê estado inicial da camada L3 (assets/) com fallback embutido."""
    try:
        with open(ASSETS_PADRAO, encoding="utf-8") as f:
            dados = json.load(f)
        if isinstance(dados, dict) and "lights" in dados:
            return dados
    except Exception as e:
        logger.warning("Estado padrão (assets) indisponível; usando fallback embutido: %s", e)
    return ESTADO_FALLBACK

class SmartHomePlugin(JarvisPlugin):
    def __init__(self):
        super().__init__(PluginMeta(
            id="smart_home",
            name="Casa Inteligente & IoT",
            version="1.0.0",
            category="smart_home",
            icon="🏠",
            description="Controle de iluminação inteligente, climatização, cenas de ambiente ('Foco/Trabalho', 'Cinema', 'Descanso') e automação IoT."
        ))
        estado = _carregar_estado_padrao()
        self.lights = estado.get("lights", ESTADO_FALLBACK["lights"])
        self.clima = estado.get("clima", ESTADO_FALLBACK["clima"])
        self.cenas = estado.get("cenas", {})

    def on_load(self):
        self.register_tool(
            name="smart_home_set_light",
            description="Ajusta o estado, cor ou brilho das lâmpadas inteligentes em um cômodo específico.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "room": {
                        "type": "STRING",
                        "description": "Cômodo ou setor: 'escritorio', 'sala' ou 'quarto'."
                    },
                    "state": {
                        "type": "BOOLEAN",
                        "description": "True para ligar, False para desligar."
                    },
                    "color": {
                        "type": "STRING",
                        "description": "Cor opcional desejada (ex: 'azul', 'ciano', 'laranja', 'vermelho')."
                    },
                    "brightness": {
                        "type": "INTEGER",
                        "description": "Nível de brilho de 0 a 100 porcento."
                    }
                },
                "required": ["room", "state"]
            },
            handler=self.set_light,
            risk_level="LOW_WRITE"
        )

        self.register_tool(
            name="smart_home_activate_scene",
            description="Ativa um perfil/cena pré-configurado de automação residencial (ex: 'Foco/Trabalho', 'Cinema', 'Descanso', 'Alerta').",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "scene_name": {
                        "type": "STRING",
                        "description": "Nome da cena: 'Foco', 'Cinema', 'Descanso' ou 'Alerta'."
                    }
                },
                "required": ["scene_name"]
            },
            handler=self.activate_scene,
            risk_level="LOW_WRITE"
        )

        self.register_tool(
            name="smart_home_get_climate",
            description="Consulta a temperatura, refrigeração e telemetria climática dos ambientes da residência.",
            parameters={"type": "OBJECT", "properties": {}},
            handler=self.get_climate,
            risk_level="READ"
        )

    def set_light(self, room: str, state: bool, color: str = None, brightness: int = None) -> dict:
        key = room.lower().strip()
        if key not in self.lights:
            self.lights[key] = {"ligado": state, "cor": color or "Branco", "brilho": brightness or 100}
        else:
            self.lights[key]["ligado"] = state
            if color:
                self.lights[key]["cor"] = color
            if brightness is not None:
                self.lights[key]["brilho"] = brightness

        status_str = "ligada(s)" if state else "desligada(s)"
        detalhe = f", tom {self.lights[key]['cor']} e brilho em {self.lights[key]['brilho']}%" if state else ""
        return {
            "sucesso": True, "mock": True, "executado_externamente": False,
            "comodo": room,
            "estado": self.lights[key],
            "mensagem": f"Iluminação do {room} agora {status_str}{detalhe}, senhor."
        }

    def activate_scene(self, scene_name: str) -> dict:
        scene = scene_name.lower()
        if "foco" in scene or "trabalho" in scene or "dev" in scene:
            self.lights["escritorio"] = {"ligado": True, "cor": "Ciano Stark", "brilho": 90}
            msg = "Cena de Foco ativada. Iluminação de trabalho sintonizada em ciano de alta concentração e silenciador de ruído ativo."
        elif "cinema" in scene or "filme" in scene:
            self.lights["sala"] = {"ligado": True, "cor": "Âmbar Suave", "brilho": 20}
            self.lights["escritorio"] = {"ligado": False, "cor": "Apagado", "brilho": 0}
            msg = "Cena de Cinema ativada. Luzes atenuadas para atmosfera de exibição, senhor."
        elif "descanso" in scene or "dormir" in scene:
            for r in self.lights:
                self.lights[r]["ligado"] = False
            msg = "Cena de Descanso estabelecida. Todas as luzes foram desativadas e os sistemas de segurança estão em sentinela."
        else:
            msg = f"Cena '{scene_name}' aplicada com sucesso aos atuadores residenciais, senhor."

        return {
            "sucesso": True, "mock": True, "executado_externamente": False,
            "cena": scene_name,
            "mensagem": msg
        }

    def get_climate(self) -> dict:
        return {
            "sucesso": True, "mock": True, "executado_externamente": False,
            "clima": self.clima,
            "mensagem": f"Ambiente em {self.clima['temperatura']} com {self.clima['umidade']} de umidade relativa. Climatização em {self.clima['modo']}."
        }
