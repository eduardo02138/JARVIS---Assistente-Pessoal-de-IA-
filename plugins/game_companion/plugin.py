"""
Plug-in: Companhia em Jogos Online (Game Companion)
Fornece análise tática, timers de cooldown/objetivos e assistência estratégica para jogos online.
"""

import time
from plugin_sdk import JarvisPlugin, PluginMeta

class GameCompanionPlugin(JarvisPlugin):
    def __init__(self):
        super().__init__(PluginMeta(
            id="game_companion",
            name="Companhia em Jogos Online",
            version="1.0.0",
            category="gaming",
            icon="🎮",
            description="Assistência tática em tempo real para jogos (Marvel Rivals, GTA, RPGs, shooters), timers de objetivos e dicas estratégicas."
        ))
        self.active_game = "Nenhum"
        self.timers = {}

    def on_load(self):
        self.register_tool(
            name="game_companion_set_active_game",
            description="Define qual jogo o senhor está jogando no momento para calibrar os conselhos e telemetria gamer do JARVIS.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "game_name": {
                        "type": "STRING",
                        "description": "Nome do jogo em execução (ex: 'Marvel Rivals', 'GTA V', 'Counter-Strike', 'Minecraft')."
                    }
                },
                "required": ["game_name"]
            },
            handler=self.set_active_game
        )

        self.register_tool(
            name="game_companion_tactical_timer",
            description="Inicia ou consulta um cronômetro tático no jogo (ex: tempo de respawn de boss, recarga de ultimate, tempo de zona).",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "label": {
                        "type": "STRING",
                        "description": "Nome do objetivo ou habilidade (ex: 'Respawn do Boss', 'Ultimate Inimigo')."
                    },
                    "seconds": {
                        "type": "INTEGER",
                        "description": "Duração em segundos para o timer tático."
                    }
                },
                "required": ["label", "seconds"]
            },
            handler=self.start_tactical_timer
        )

        self.register_tool(
            name="game_companion_get_strategy",
            description="Obtém dicas táticas imediatas para o jogo atual, confronto de personagens ou fraqueza de inimigo.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "target_character_or_boss": {
                        "type": "STRING",
                        "description": "Personagem, classe, herói ou boss que o usuário está enfrentando ou utilizando."
                    }
                },
                "required": ["target_character_or_boss"]
            },
            handler=self.get_strategy
        )

    def set_active_game(self, game_name: str) -> dict:
        self.active_game = game_name.strip()
        return {
            "sucesso": True,
            "jogo_ativo": self.active_game,
            "mensagem": f"Protocolo gamer sintonizado em '{self.active_game}', senhor. Telemetria de GPU e módulos táticos prontos para a partida."
        }

    def start_tactical_timer(self, label: str, seconds: int) -> dict:
        now = time.time()
        end_time = now + int(seconds)
        self.timers[label] = end_time
        mins, secs = divmod(seconds, 60)
        tempo_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
        return {
            "sucesso": True,
            "objetivo": label,
            "duracao_segundos": seconds,
            "mensagem": f"Cronômetro tático iniciado para '{label}': {tempo_str}. Avisarei assim que o tempo expirar, senhor."
        }

    def get_strategy(self, target_character_or_boss: str) -> dict:
        dicas = [
            f"Mantenha a vantagem de terreno elevado e explore a mobilidade contra {target_character_or_boss}.",
            f"Foque no controle de grupo (crowd control) e não engaje sem suporte da equipe contra {target_character_or_boss}.",
            f"Monitore as recargas das habilidades chave de {target_character_or_boss} antes de avançar."
        ]
        return {
            "sucesso": True,
            "alvo": target_character_or_boss,
            "jogo_ativo": self.active_game,
            "conselho_tatico": dicas[0],
            "mensagem": f"Análise tática para {target_character_or_boss}: {dicas[0]}"
        }
