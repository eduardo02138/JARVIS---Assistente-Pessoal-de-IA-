"""
F10 — Testes de Compras e Espaço Informativo de Saúde (`monitoring/test_compras_saude.py`)

Testes obrigatórios:
- test_comparacao_compras_com_fonte_e_sem_compra_autonoma
- test_saude_isolada_da_memoria_geral
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from compras_e_saude import HealthInformationalSpace, ShoppingAssistant
from pesquisa_e_memoria import user_memory_manager
from policy_engine import policy_engine
from server import app
from servidor.seguranca import JARVIS_SECRET_TOKEN, emitir_sessao


def _headers() -> dict:
    if JARVIS_SECRET_TOKEN:
        return {"X-Jarvis-Token": JARVIS_SECRET_TOKEN}
    return {}


def test_comparacao_compras_com_fonte_e_sem_compra_autonoma():
    """
    Garante que o assistente de compras ordena ofertas reais com moeda, timestamp
    e fonte verificada, rejeita URLs internas (SSRF) e nunca realiza compra autônoma
    sem confirmação humana explícita.
    """
    shop = ShoppingAssistant()
    uid = emitir_sessao("user_shop_f10")

    report = shop.compare_offers(
        query="SSD NVMe 2TB PCIe 4.0",
        currency="BRL",
        raw_offers=[
            {
                "product_name": "SSD NVMe 2TB Pro",
                "merchant": "Loja Alpha",
                "price": 989.90,
                "currency": "BRL",
                "source_url": "https://loja-alpha.example.com/ssd-2tb",
            },
            {
                "product_name": "SSD NVMe 2TB Pro",
                "merchant": "Loja Beta",
                "price": 849.50,
                "currency": "BRL",
                "source_url": "https://loja-beta.example.com/ssd-2tb",
            },
            {
                "product_name": "Oferta SSRF Maliciosa",
                "merchant": "Localhost",
                "price": 10.00,
                "currency": "BRL",
                "source_url": "http://127.0.0.1:8000/internal",
            },
        ],
    )
    assert len(report.offers) == 2
    assert report.offers[0].merchant == "Loja Beta"
    assert report.offers[0].price == 849.50
    assert report.offers[0].currency == "BRL"
    assert report.offers[0].collected_at > 0
    assert report.autonomous_purchase_executed is False

    # Tentativa de checkout/compra pausa obrigatoriamente aguardando confirmação humana
    chk = shop.request_checkout_or_purchase(
        user_id=uid,
        offer_id=report.offers[0].offer_id,
        merchant="Loja Beta",
        price=849.50,
        currency="BRL",
        source_url="https://loja-beta.example.com/ssd-2tb",
    )
    assert chk["status"] == "aguardando_confirmacao"
    assert chk["requires_confirmation"] is True
    assert chk["autonomous_purchase_executed"] is False

    # Após confirmação humana one-shot, autoriza checkout assistido exatamente 1 vez
    assert policy_engine.approve_action(chk["id_confirmacao"], session_id=uid, user_id=uid) is True
    chk_approved = shop.request_checkout_or_purchase(
        user_id=uid,
        offer_id=report.offers[0].offer_id,
        merchant="Loja Beta",
        price=849.50,
        currency="BRL",
        source_url="https://loja-beta.example.com/ssd-2tb",
    )
    assert chk_approved["status"] == "autorizado_para_checkout_assistido"


def test_saude_isolada_da_memoria_geral():
    """
    Garante que o espaço de saúde exige opt-in explícito, inclui aviso médico
    obrigatório, proíbe ações clínicas autônomas e mantém isolamento total da
    memória geral do usuário (`user_memory_manager`).
    """
    hspace = HealthInformationalSpace()
    uid = emitir_sessao("user_health_f10")

    # 1. Sem opt-in -> bloqueado
    with pytest.raises(PermissionError, match="opt-in"):
        hspace.query_health_information(
            user_id=uid,
            question="Quais são os benefícios da hidratação adequada?",
        )

    # 2. Usuário realiza opt-in explícito
    hspace.set_opt_in(user_id=uid, enabled=True)

    # 3. Ação clínica autônoma (prescrição/diagnóstico) -> bloqueada
    with pytest.raises(PermissionError, match="proíbe ações clínicas autônomas"):
        hspace.query_health_information(
            user_id=uid,
            question="Prescreva antibiótico",
            requested_clinical_action="prescrever_medicamento_controlado",
        )

    # 4. Consulta informativa com fonte verificada
    res = hspace.query_health_information(
        user_id=uid,
        question="Importância do sono REM e higiene do sono",
        sources=[
            {
                "title": "Ministério da Saúde / Guia do Sono",
                "url": "https://www.gov.br/saude/pt-br/assuntos/saude-de-a-a-z",
                "snippet": "Adultos necessitam em média de 7 a 9 horas de sono regular por noite.",
            }
        ],
    )
    assert res["status"] == "ok"
    assert res["isolated_from_general_memory"] is True
    assert res["autonomous_clinical_action_performed"] is False
    assert "NÃO substituem consulta" in res["clinical_disclaimer"]

    # 5. Verifica que NADA do espaço de saúde vazou para a memória geral do usuário
    general_memories = user_memory_manager.search_facts(user_id=uid, query="sono")
    assert general_memories == []

    # 6. Valida também via API REST e exclusão do histórico de saúde
    client = TestClient(app)
    r_opt = client.post("/api/health_assistant/opt_in", json={"sessao": uid, "enabled": True}, headers=_headers())
    assert r_opt.status_code == 200
    r_del = client.delete(f"/api/health_assistant/history?sessao={uid}", headers=_headers())
    assert r_del.status_code == 200
