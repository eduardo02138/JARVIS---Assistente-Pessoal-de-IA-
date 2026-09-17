"""
Plug-in: Transmissões ao Vivo & Streaming (Live Stream Companion)
Monitora chat ao vivo, metas de espectadores e anuncia eventos de transmissão.
"""

from plugin_sdk import JarvisPlugin, PluginMeta

class LiveStreamPlugin(JarvisPlugin):
    def __init__(self):
        super().__init__(PluginMeta(
            id="live_stream",
            name="Transmissão ao Vivo & Streaming",
            version="1.0.0",
            category="streaming",
            icon="📡",
            description="Integração para transmissões ao vivo: leitura e síntese de chat em tempo real, contagem de espectadores e alertas de doações."
        ))
        self.streaming = False
        self.platform = "Twitch / YouTube"
        self.viewers = 142
        self.recent_chat = [
            {"user": "CyberDev", "text": "Essa refatoração no Antigravity ficou incrível!"},
            {"user": "TechKnight", "text": "Jarvis, qual a latência dessa chamada?"},
            {"user": "PixelArt", "text": "Boa noite pessoal, cheguei agora!"}
        ]

    def on_load(self):
        self.register_tool(
            name="live_stream_toggle_status",
            description="Ativa ou encerra o monitoramento de transmissão ao vivo do streamer.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "is_live": {
                        "type": "BOOLEAN",
                        "description": "True se a live estiver no ar, False se encerrada."
                    },
                    "title": {
                        "type": "STRING",
                        "description": "Título opcional da live."
                    }
                },
                "required": ["is_live"]
            },
            handler=self.toggle_live
        )

        self.register_tool(
            name="live_stream_read_chat_summary",
            description="Obtém um resumo conciso das últimas mensagens enviadas pelo público no chat da live.",
            parameters={"type": "OBJECT", "properties": {}},
            handler=self.read_chat_summary
        )

        self.register_tool(
            name="live_stream_send_alert",
            description="Aciona um alerta audiovisual no HUD sobre um evento de transmissão (novo seguidor, sub ou doação).",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "event_type": {
                        "type": "STRING",
                        "description": "Tipo de evento: 'sub', 'donation', 'follower' ou 'raid'."
                    },
                    "username": {
                        "type": "STRING",
                        "description": "Nome do espectador."
                    },
                    "amount_or_details": {
                        "type": "STRING",
                        "description": "Detalhes como valor da doação ou meses de inscrição."
                    }
                },
                "required": ["event_type", "username"]
            },
            handler=self.send_alert
        )

    def toggle_live(self, is_live: bool, title: str = None) -> dict:
        self.streaming = is_live
        if is_live:
            t = f" com o tema '{title}'" if title else ""
            msg = f"Transmissão ao vivo iniciada{t}. Telemetria de chat e contagem de espectadores online no HUD, senhor."
        else:
            msg = "Transmissão ao vivo encerrada com sucesso, senhor. Estatísticas finais arquivadas."
        return {"sucesso": True, "no_ar": self.streaming, "mensagem": msg}

    def read_chat_summary(self) -> dict:
        qtd = len(self.recent_chat)
        destaque = ", ".join([f"{m['user']}: \"{m['text']}\"" for m in self.recent_chat[-2:]])
        return {
            "sucesso": True,
            "espectadores_ativos": self.viewers,
            "mensagens_recentes": self.recent_chat,
            "mensagem": f"Senhor, a live conta com {self.viewers} espectadores simultâneos. As mensagens em destaque no chat são: {destaque}."
        }

    def send_alert(self, event_type: str, username: str, amount_or_details: str = "") -> dict:
        det = f" ({amount_or_details})" if amount_or_details else ""
        return {
            "sucesso": True,
            "alerta": f"{event_type.upper()}: {username}{det}",
            "mensagem": f"Novo alerta de transmissão: {username} realizou uma ação de {event_type}{det}! Transmitindo agradecimento."
        }
