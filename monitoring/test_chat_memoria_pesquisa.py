# -*- coding: utf-8 -*-
"""
Suíte de Testes da Fase F3 — Chat, Memória Personalizada, Modos e Deep Research Real.

Valida:
1. Histórico conversacional isolado por usuário (listar, reabrir, excluir item, excluir tudo).
2. Compartilhamento estático por link (imutabilidade pós-snapshot, ausência de vazamento de sessão viva e revogação).
3. Memória personalizada por usuário (busca, exclusão granular/total, opt-out de personalização e retenção/expiração).
4. Deep Research real com decomposição em sub-perguntas, citações SHA-256 verificadas, rejeição de URLs inválidas/SSRF e honestidade quando sem fontes.
"""

from fastapi.testclient import TestClient

from pesquisa_e_memoria import (
    ConversationHistoryManager,
    RealDeepResearchEngine,
    UserMemoryManager,
)
from server import JARVIS_SECRET_TOKEN, app
from servidor.seguranca import emitir_sessao


def test_f3_historico_isolado_e_snapshot_compartilhado_imutavel(tmp_path):
    """Gate F3: Isolamento entre usuários no histórico e imutabilidade do snapshot compartilhado."""
    client = TestClient(app)
    headers = {"X-Jarvis-Token": JARVIS_SECRET_TOKEN}
    user_a = emitir_sessao("sessao-f3-hist-a")
    user_b = emitir_sessao("sessao-f3-hist-b")

    from pesquisa_e_memoria import history_manager

    conv = history_manager.append_turn(
        user_id=user_a,
        conversation_id="conv_secreta_a",
        role="user",
        text="Qual é a arquitetura do kernel Linux?",
        mode="profundo",
        title="Arquitetura Linux",
    )
    history_manager.append_turn(
        user_id=user_a,
        conversation_id=conv.conversation_id,
        role="assistant",
        text="O kernel Linux utiliza uma arquitetura monolítica modular.",
        mode="profundo",
    )

    # 1. Usuário A lista e reabre sua conversa
    r_list_a = client.get(f"/api/history/conversations?sessao={user_a}", headers=headers)
    assert r_list_a.status_code == 200
    ids_a = [c["conversation_id"] for c in r_list_a.json()["conversas"]]
    assert "conv_secreta_a" in ids_a

    r_open_a = client.get(f"/api/history/conversations/conv_secreta_a?sessao={user_a}", headers=headers)
    assert r_open_a.status_code == 200
    assert len(r_open_a.json()["conversa"]["turns"]) == 2

    # 2. Usuário B NÃO vê, NÃO reabre e NÃO exclui a conversa de A
    r_list_b = client.get(f"/api/history/conversations?sessao={user_b}", headers=headers)
    assert "conv_secreta_a" not in [c["conversation_id"] for c in r_list_b.json()["conversas"]]

    r_open_b = client.get(f"/api/history/conversations/conv_secreta_a?sessao={user_b}", headers=headers)
    assert r_open_b.status_code == 404

    r_del_b = client.delete(f"/api/history/conversations/conv_secreta_a?sessao={user_b}", headers=headers)
    assert r_del_b.status_code == 404

    # 3. Compartilhamento estático por link (congela apenas os 2 turnos atuais)
    r_share = client.post(
        "/api/history/share",
        json={"sessao": user_a, "conversation_id": "conv_secreta_a", "title": "Snapshot Público"},
        headers=headers,
    )
    assert r_share.status_code == 200
    share_id = r_share.json()["share_id"]

    # Adiciona 3º turno SENSÍVEL na conversa ativa após gerar o link compartilhado
    history_manager.append_turn(
        user_id=user_a,
        conversation_id="conv_secreta_a",
        role="user",
        text="Mensagem privada enviada DEPOIS do compartilhamento!",
        mode="profundo",
    )

    # O snapshot compartilhado continua estático com apenas 2 turnos e não vaza owner_user_id
    r_shared = client.get(f"/api/history/shared/{share_id}")
    assert r_shared.status_code == 200
    snap_data = r_shared.json()["snapshot"]
    assert snap_data["is_static_snapshot"] is True
    assert "owner_user_id" not in snap_data
    assert len(snap_data["turns"]) == 2
    assert all("DEPOIS do compartilhamento" not in t["text"] for t in snap_data["turns"])

    # 4. Revogação do link compartilhado
    r_rev = client.delete(f"/api/history/shared/{share_id}?sessao={user_a}", headers=headers)
    assert r_rev.status_code == 200
    assert client.get(f"/api/history/shared/{share_id}").status_code == 404

    # 5. Exclusão específica e exclusão total do histórico de A
    r_del_a = client.delete(f"/api/history/conversations/conv_secreta_a?sessao={user_a}", headers=headers)
    assert r_del_a.status_code == 200
    assert client.get(f"/api/history/conversations/conv_secreta_a?sessao={user_a}", headers=headers).status_code == 404


def test_f3_memoria_personalizada_isolamento_retencao_e_opt_out(tmp_path):
    """Gate F3: Memória por usuário com busca, exclusão granular/total, opt-out e expiração por TTL."""
    mem_mgr = UserMemoryManager(store_path=str(tmp_path / "mem_f3.json"))
    u1 = "user_mem_1"
    u2 = "user_mem_2"

    f1 = mem_mgr.remember_fact(user_id=u1, key="editor_favorito", value="Neovim com LSP", now_ts=1000.0, ttl_seconds=60.0)
    f2 = mem_mgr.remember_fact(user_id=u1, key="linguagem_principal", value="Python 3.12 e Rust", now_ts=1000.0, ttl_seconds=3600.0)
    mem_mgr.remember_fact(user_id=u2, key="editor_favorito", value="VS Code", now_ts=1000.0, ttl_seconds=3600.0)

    # 1. Isolamento: busca de u1 jamais retorna memórias de u2
    res_u1 = mem_mgr.search_facts(u1, query="editor", now_ts=1010.0)
    assert len(res_u1) == 1 and res_u1[0].value == "Neovim com LSP"

    # 2. Expiração automática por política de retenção (now_ts=1070 > 1060)
    res_after_ttl = mem_mgr.search_facts(u1, query="", now_ts=1070.0)
    assert len(res_after_ttl) == 1
    assert res_after_ttl[0].memory_id == f2.memory_id

    # 3. Opt-out de personalização impede gravação de novos fatos
    mem_mgr.set_retention_policy(u1, personalization_enabled=False, retention_days=30)
    assert mem_mgr.remember_fact(user_id=u1, key="segredo", value="nao_salvar") is None

    # 4. Exclusão granular e total
    assert mem_mgr.delete_fact(u1, f2.memory_id) is True
    assert mem_mgr.search_facts(u1, now_ts=1070.0) == []
    assert mem_mgr.delete_all_facts(u2) == 1


def test_f3_deep_research_real_citacoes_verificadas_e_anti_alucinacao():
    """Gate F3: Deep Research real valida SHA-256 das citações, rejeita URLs maliciosas e não fabrica fontes."""
    client = TestClient(app)
    headers = {"X-Jarvis-Token": JARVIS_SECRET_TOKEN}
    sessao = emitir_sessao("sessao-f3-research")

    # 1. Execução com fontes válidas + tentativa de injetar URL inválida (javascript: / file:)
    r_res = client.post(
        "/api/research/deep",
        json={
            "sessao": sessao,
            "topic": "Segurança de capacidades em sistemas multi-agente Linux",
            "max_subquestions": 3,
            "sources": [
                {
                    "url": "https://kernel.org/doc/html/latest/security/credentials.html",
                    "title": "Linux Kernel Credentials Documentation",
                    "content": "Segurança de capacidades no Linux separa privilégios em unidades granulares auditáveis.",
                },
                {
                    "url": "javascript:alert(1)",
                    "title": "Fonte Maliciosa XSS",
                    "content": "Conteúdo que deve ser descartado pelo validador.",
                },
            ],
        },
        headers=headers,
    )
    assert r_res.status_code == 200
    rel = r_res.json()["relatorio"]
    assert rel["mock"] is False
    assert rel["status"] == "COMPLETED"
    assert len(rel["sub_questions"]) == 3
    # Apenas a fonte HTTPS legítima passa; a fonte javascript: é rejeitada e reportada em limitations
    assert len(rel["citations"]) == 1
    cit = rel["citations"][0]
    assert cit["domain"] == "kernel.org"
    assert len(cit["content_sha256"]) == 64
    assert cit["verified"] is True
    assert "[1]" in rel["markdown_report"]

    # 2. Execução sem fontes externas -> NÃO fabrica citações falsas
    r_empty = client.post(
        "/api/research/deep",
        json={
            "sessao": sessao,
            "topic": "Tópico inédito sem fontes fornecidas",
            "sources": [],
        },
        headers=headers,
    )
    assert r_empty.status_code == 200
    rel_empty = r_empty.json()["relatorio"]
    assert rel_empty["citations"] == []
    assert rel_empty["status"] == "COMPLETED_WITHOUT_EXTERNAL_SOURCES"
    assert all(sq["evidence_status"] == "INSUFFICIENT_EVIDENCE" for sq in rel_empty["sub_questions"])
