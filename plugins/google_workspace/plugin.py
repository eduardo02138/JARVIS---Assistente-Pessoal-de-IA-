"""
Plug-in: Google Workspace (Gmail, Google Docs, Google Keep)
Permite redigir documentos, buscar mensagens na caixa de entrada e capturar ideias por voz.
"""

import os
import json
import logging
from typing import Optional, List, Dict
from plugin_sdk import JarvisPlugin, PluginMeta

logger = logging.getLogger("jarvis.plugins.google_workspace")

class GoogleWorkspacePlugin(JarvisPlugin):
    def __init__(self):
        super().__init__(PluginMeta(
            id="google_workspace",
            name="Google Workspace (Gmail, Docs & Keep)",
            version="1.0.0",
            category="general",
            icon="📑",
            description="Comandos de voz para redigir documentos no Docs, consultar a caixa de entrada do Gmail e capturar ideias rápidas no Keep."
        ))
        self.keep_notes: List[Dict[str, str]] = [
            {"titulo": "Arquitetura JARVIS", "conteudo": "Implementar pool de conexões e cache de telemetria.", "tags": "tecnologia, dev"}
        ]
        self.drafts: List[Dict[str, str]] = []
        self.docs: Dict[str, str] = {
            "Briefing Projeto Stark": "Especificações de telemetria, motor de políticas e interface holográfica."
        }

    def on_load(self):
        self.register_tool(
            name="workspace_search_emails",
            description="Busca e-mails e mensagens recentes na caixa de entrada do Gmail por remetente, assunto ou palavras-chave.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "query": {
                        "type": "STRING",
                        "description": "Termo de busca, remetente ou assunto a pesquisar no Gmail."
                    },
                    "max_results": {
                        "type": "INTEGER",
                        "description": "Número máximo de e-mails para listar (padrão 5)."
                    }
                },
                "required": ["query"]
            },
            handler=self.search_emails,
            risk_level="READ"
        )

        self.register_tool(
            name="workspace_create_draft",
            description="Redige e salva um rascunho de e-mail no Gmail pronto para envio ou revisão do senhor.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "recipient": {
                        "type": "STRING",
                        "description": "Endereço de e-mail ou nome do destinatário."
                    },
                    "subject": {
                        "type": "STRING",
                        "description": "Assunto do e-mail."
                    },
                    "body": {
                        "type": "STRING",
                        "description": "Corpo da mensagem redigida."
                    }
                },
                "required": ["recipient", "subject", "body"]
            },
            handler=self.create_draft,
            risk_level="EXTERNAL_WRITE"
        )

        self.register_tool(
            name="workspace_append_doc",
            description="Redige, cria ou adiciona novos parágrafos em documentos do Google Docs por comando de voz.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "doc_title": {
                        "type": "STRING",
                        "description": "Título do documento no Google Docs."
                    },
                    "content": {
                        "type": "STRING",
                        "description": "Texto ou notas a serem anexadas ao documento."
                    }
                },
                "required": ["doc_title", "content"]
            },
            handler=self.append_doc,
            risk_level="EXTERNAL_WRITE"
        )

        self.register_tool(
            name="workspace_create_keep_note",
            description="Captura rapidamente uma ideia, anotação ou lista de afazeres no Google Keep.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "title": {
                        "type": "STRING",
                        "description": "Título da nota no Google Keep."
                    },
                    "content": {
                        "type": "STRING",
                        "description": "Conteúdo ou itens da anotação."
                    },
                    "tags": {
                        "type": "STRING",
                        "description": "Tags ou marcadores separados por vírgula (opcional)."
                    }
                },
                "required": ["title", "content"]
            },
            handler=self.create_keep_note,
            risk_level="EXTERNAL_WRITE"
        )

    def search_emails(self, query: str, max_results: int = 5) -> dict:
        q_lower = query.lower()
        mock_emails = [
            {"from": "security@google.com", "subject": "Alerta de Segurança Google Workspace", "snippet": "Novo dispositivo autorizado com sucesso."},
            {"from": "deepmind-research@google.com", "subject": "Novidades no Gemini 3.5 Transcribe", "snippet": "Disponibilizado suporte a múltiplos falantes e filtros de voz."},
            {"from": "relatorios@finance.google.com", "subject": "Resumo Semanal do Portfólio Finance", "snippet": "Suas ações de tecnologia subiram 3.4% nesta semana."}
        ]
        results = [e for e in mock_emails if q_lower in e["subject"].lower() or q_lower in e["from"].lower() or q_lower in e["snippet"].lower()]
        if not results:
            results = mock_emails[:max_results]

        return {
            "sucesso": True,

            "mock": True,

            "executado_externamente": False,
            "query": query,
            "total_encontrados": len(results),
            "emails": results,
            "mensagem": f"Senhor, localizei {len(results)} correspondência(s) no Gmail para '{query}'. O assunto mais recente é '{results[0]['subject']}'."
        }

    def create_draft(self, recipient: str, subject: str, body: str) -> dict:
        draft = {
            "destinatario": recipient,
            "assunto": subject,
            "corpo": body
        }
        self.drafts.append(draft)
        return {
            "sucesso": True,
            "mock": True,
            "executado_externamente": False,
            "rascunho": draft,
            "mensagem": f"Rascunho simulado para '{recipient}' com o assunto '{subject}', senhor. Este plug-in ainda opera em demonstração: nada foi gravado no Gmail."
        }

    def append_doc(self, doc_title: str, content: str) -> dict:
        if doc_title in self.docs:
            self.docs[doc_title] += f"\n{content}"
        else:
            self.docs[doc_title] = content

        return {
            "sucesso": True,

            "mock": True,

            "executado_externamente": False,
            "documento": doc_title,
            "tamanho_total": len(self.docs[doc_title]),
            "mensagem": f"Texto anexado ao documento '{doc_title}' apenas na simulação local, senhor. O Google Docs real não foi alterado."
        }

    def create_keep_note(self, title: str, content: str, tags: str = "") -> dict:
        nota = {
            "titulo": title,
            "conteudo": content,
            "tags": tags
        }
        self.keep_notes.append(nota)
        return {
            "sucesso": True,
            "mock": True,
            "executado_externamente": False,
            "nota": nota,
            "mensagem": f"Ideia capturada no Google Keep: '{title}'. Está salva e acessível em todos os seus dispositivos, senhor."
        }
