"""
Plug-in: Mídias Sociais & Comunicação (Social Feed & Notifications)
Monitora menções, mensagens diretas e ajuda na publicação de atualizações em redes sociais.
"""

from plugin_sdk import JarvisPlugin, PluginMeta

class SocialFeedPlugin(JarvisPlugin):
    def __init__(self):
        super().__init__(PluginMeta(
            id="social_feed",
            name="Mídias Sociais & Notificações",
            version="1.0.0",
            category="social",
            icon="💬",
            description="Monitoramento inteligente de feeds, menções, mensagens diretas (Discord, Telegram, X/Twitter) e publicação de atualizações."
        ))
        self.notifications = [
            {"canal": "Discord", "autor": "Equipe Antigravity", "conteudo": "Nova versão do SDK agy disponível para testes.", "urgente": True},
            {"canal": "GitHub", "autor": "DeepMind Devs", "conteudo": "Pull request mesclada no repositório de ferramentas.", "urgente": False},
            {"canal": "Telegram", "autor": "Carlos", "conteudo": "Confirmada nossa reunião técnica de alinhamento.", "urgente": True}
        ]

    def on_load(self):
        self.register_tool(
            name="social_feed_check_notifications",
            description="Verifica notificações e mensagens recentes não lidas nas redes e canais de comunicação do usuário.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "only_urgent": {
                        "type": "BOOLEAN",
                        "description": "Se True, traz apenas avisos com prioridade urgente."
                    }
                }
            },
            handler=self.check_notifications,
            risk_level="READ"
        )

        self.register_tool(
            name="social_feed_post_update",
            description="Rascunha ou programa uma publicação de atualização rápida para redes sociais (X/Twitter, Discord, etc.).",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "platform": {
                        "type": "STRING",
                        "description": "Plataforma alvo (ex: 'Discord', 'X/Twitter', 'Telegram')."
                    },
                    "text_content": {
                        "type": "STRING",
                        "description": "Conteúdo textual da mensagem ou postagem."
                    }
                },
                "required": ["platform", "text_content"]
            },
            handler=self.post_update,
            risk_level="EXTERNAL_WRITE"
        )

    def check_notifications(self, only_urgent: bool = False) -> dict:
        itens = [n for n in self.notifications if n["urgente"]] if only_urgent else self.notifications
        total = len(itens)
        resumo = "; ".join([f"[{n['canal']}] {n['autor']}: {n['conteudo']}" for n in itens[:2]])
        return {
            "sucesso": True, "mock": True, "executado_externamente": False,
            "total_notificacoes": total,
            "notificacoes": itens,
            "mensagem": f"Senhor, você possui {total} notificações pendentes. Em destaque: {resumo}."
        }

    def post_update(self, platform: str, text_content: str) -> dict:
        return {
            "sucesso": True, "mock": True, "executado_externamente": False,
            "plataforma": platform,
            "conteudo": text_content,
            "mensagem": f"Atualização enviada para o canal {platform}: \"{text_content}\", senhor."
        }
