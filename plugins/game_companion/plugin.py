"""
Plug-in: Companhia em Jogos Online (Game Companion)
Fornece análise tática, timers de cooldown/objetivos, assistência estratégica,
localização e inicialização de jogos instalados (Steam, Lutris, Heroic, etc.).
Inclui worker assíncrono para monitoramento e notificação de timers expirados.
"""

import json
import time
import asyncio
import logging
import os
from typing import Optional, Dict, List
from plugin_sdk import JarvisPlugin, PluginMeta

logger = logging.getLogger("jarvis.plugins.game_companion")

RAIZ =os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ASSETS_ESTRATEGIAS = os.path.join(RAIZ, "skills", "game-companion", "assets", "estrategias.json")

# Fallback embutido caso o arquivo de camada L3 (assets/) seja removido.
DICAS_FALLBACK = [
    "Mantenha a vantagem de terreno elevado e explore a mobilidade contra {alvo}.",
    "Foque no controle de grupo (crowd control) e não engaje sem suporte da equipe contra {alvo}.",
    "Monitore as recargas das habilidades chave de {alvo} antes de avançar.",
]


def _carregar_dicas() -> List[str]:
    """Lê as dicas táticas da camada L3 (assets/) com fallback embutido."""
    try:
        with open(ASSETS_ESTRATEGIAS, encoding="utf-8") as f:
            dados = json.load(f)
        dicas = dados.get("dicas", []) if isinstance(dados, dict) else dados
        if isinstance(dicas, list) and dicas:
            return dicas
    except Exception as e:
        logger.warning("Estratégias (assets) indisponíveis; usando fallback embutido: %s", e)
    return DICAS_FALLBACK

class GameCompanionPlugin(JarvisPlugin):
    def __init__(self):
        super().__init__(PluginMeta(
            id="game_companion",
            name="Companhia em Jogos Online",
            version="1.2.0",
            category="gaming",
            icon="🎮",
            description="Assistência tática em tempo real para jogos, timers de objetivos, localização e inicialização direta de jogos instalados em qualquer distribuidora."
        ))
        self.active_game = "Nenhum"
        self.timers: Dict[str, float] = {}
        self.expired_history: List[str] = []
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False

    def on_load(self):
        self._running = True
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
            handler=self.set_active_game,
            risk_level="LOW_WRITE"
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
            handler=self.start_tactical_timer,
            risk_level="LOW_WRITE"
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
            handler=self.get_strategy,
            risk_level="READ"
        )

        self.register_tool(
            name="game_companion_list_installed_games",
            description="Lista e localiza todos os jogos instalados no computador e drivers (Steam, Lutris, Heroic Games, nativos) com informações da distribuidora e comando de inicialização.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "filter_name": {
                        "type": "STRING",
                        "description": "Filtro opcional por nome do jogo ou distribuidora."
                    }
                }
            },
            handler=self.list_installed_games,
            risk_level="READ"
        )

        self.register_tool(
            name="game_companion_launch_game",
            description="Inicia imediatamente um jogo instalado pelo nome ou AppID em sua respectiva distribuidora (Steam, Lutris, etc.).",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "game_name": {
                        "type": "STRING",
                        "description": "Nome do jogo a ser iniciado (ex: 'Marvel Rivals', 'GTA V', 'Red Dead Redemption 2')."
                    }
                },
                "required": ["game_name"]
            },
            handler=self.launch_game,
            risk_level="LOW_WRITE"
        )

        # Inicia o worker em background se houver loop assíncrono ativo
        try:
            loop = asyncio.get_running_loop()
            if not self._worker_task or self._worker_task.done():
                self._worker_task = loop.create_task(self._timer_worker())
        except RuntimeError:
            pass

    def on_unload(self):
        self._running = False
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()

    async def _timer_worker(self):
        """Monitora continuamente os timers ativos e registra alertas de expiração."""
        while self._running:
            try:
                await asyncio.sleep(1.0)
                expired = self.check_expired_timers()
                for label in expired:
                    logger.info(f"⏰ [GAME COMPANION]: Timer tático '{label}' expirou!")
                    try:
                        from monitoring.logger import record_event
                        record_event("game_timer_expired", {"label": label, "game": self.active_game})
                    except Exception:
                        pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Erro no worker de timer tático: {e}")

    def check_expired_timers(self) -> List[str]:
        """Retorna e consome os timers que atingiram o tempo limite."""
        now = time.time()
        expired = []
        for label, end_time in list(self.timers.items()):
            if now >= end_time:
                expired.append(label)
                del self.timers[label]
                self.expired_history.append(label)
        return expired

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

        if self._running and (not self._worker_task or self._worker_task.done()):
            try:
                loop = asyncio.get_running_loop()
                self._worker_task = loop.create_task(self._timer_worker())
            except RuntimeError:
                pass

        return {
            "sucesso": True,
            "objetivo": label,
            "duracao_segundos": seconds,
            "mensagem": f"Cronômetro tático iniciado para '{label}': {tempo_str}. O sistema emitirá alerta no HUD e auditoria assim que o tempo expirar, senhor."
        }

    def get_strategy(self, target_character_or_boss: str) -> dict:
        dicas = [_dica.format(alvo=target_character_or_boss) for _dica in _carregar_dicas()]
        return {
            "sucesso": True,
            "alvo": target_character_or_boss,
            "jogo_ativo": self.active_game,
            "conselho_tatico": dicas[0],
            "mensagem": f"Análise tática para {target_character_or_boss}: {dicas[0]}"
        }

    def list_installed_games(self, filter_name: str = "") -> dict:
        try:
            import system_tools
            return system_tools.list_installed_games(filter_name=filter_name)
        except Exception as e:
            return {"sucesso": False, "erro": str(e), "mensagem": f"Falha ao consultar catálogo de jogos: {e}"}

    def launch_game(self, game_name: str) -> dict:
        try:
            import system_tools
            lista = system_tools.list_installed_games(filter_name=game_name)
            jogos = lista.get("jogos", [])
            if not jogos:
                return {"sucesso": False, "mensagem": f"Jogo '{game_name}' não foi localizado na biblioteca, senhor."}

            jogo = jogos[0]
            cmd = jogo.get("comando")
            if not cmd:
                return {"sucesso": False, "mensagem": f"Comando de inicialização não disponível para {jogo.get('nome')}."}

            import shlex
            import subprocess
            # shell=False: o comando vem de arquivos .desktop e da biblioteca do Steam,
            # então metacaracteres de shell (;, &&, |) não podem virar execução arbitrária.
            argumentos = shlex.split(cmd)
            if not argumentos:
                return {"sucesso": False, "mensagem": f"Comando de inicialização inválido para {jogo.get('nome')}."}
            subprocess.Popen(argumentos)
            self.active_game = jogo.get("nome")
            return {
                "sucesso": True,
                "jogo": jogo.get("nome"),
                "distribuidora": jogo.get("distribuidora"),
                "comando": cmd,
                "mensagem": f"Inicializando '{jogo.get('nome')}' via {jogo.get('distribuidora')}, senhor. Telemetria e timers prontos."
            }
        except Exception as e:
            return {"sucesso": False, "erro": str(e), "mensagem": f"Erro ao iniciar o jogo: {e}"}
