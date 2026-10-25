"""
F9 — Testes de Geração e Edição Iterativa de Imagens (`monitoring/test_imagens.py`)

Testes obrigatórios:
- test_geracao_imagem_ou_indisponibilidade_honesta
- test_edicao_iterativa_preserva_historico_do_ativo
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from geracao_imagens import ImageGenerationService
from server import app
from servidor.seguranca import JARVIS_SECRET_TOKEN, emitir_sessao

FAKE_PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"


def _headers() -> dict:
    if JARVIS_SECRET_TOKEN:
        return {"X-Jarvis-Token": JARVIS_SECRET_TOKEN}
    return {}


def test_geracao_imagem_ou_indisponibilidade_honesta(tmp_path, monkeypatch):
    """
    Garante que sem provedor configurado o serviço retorna honestamente
    `UNAVAILABLE_PROVIDER` sem forjar imagem; e com provedor configurado
    valida magic-bytes de referência, provenance e quota diária.
    """
    monkeypatch.delenv("JARVIS_IMAGE_PROVIDER_KEY", raising=False)
    uid = emitir_sessao("user_img_f9")

    # 1. Sem provedor configurado -> UNAVAILABLE_PROVIDER honesto
    svc_no_prov = ImageGenerationService(storage_path=tmp_path / "gallery_noprov.json", provider_adapter=None)
    res_unavail = svc_no_prov.generate_image(user_id=uid, prompt="Um farol cibernético à noite")
    assert res_unavail["status"] == "UNAVAILABLE_PROVIDER"
    assert res_unavail["provider_available"] is False

    # 2. Com provedor configurado -> valida referência visual (rejeita binário ELF) e gera com proveniência
    svc_real = ImageGenerationService(
        storage_path=tmp_path / "gallery_real.json",
        provider_adapter=lambda prompt, opts: (FAKE_PNG_BYTES, "image/png", "gemini_imagen", "imagen-3.0"),
        daily_quota_per_user=2,
    )

    with pytest.raises(ValueError, match="Formato de referência visual inválido"):
        svc_real.generate_image(
            user_id=uid,
            prompt="Retrato estilizado",
            reference_image_bytes=b"\x7fELF\x02\x01\x01\x00\x00\x00",
        )

    res_ok = svc_real.generate_image(
        user_id=uid,
        prompt="Laboratório futurista no Linux",
        aspect_ratio="16:9",
        style="cinematic",
        reference_image_bytes=FAKE_PNG_BYTES,
    )
    assert res_ok["status"] == "SUCCEEDED"
    asset = res_ok["asset"]
    assert asset["provenance"]["ai_generated"] is True
    assert asset["provenance"]["provider"] == "gemini_imagen"
    assert asset["provenance"]["reference_sha256"] is not None

    # 3. Testa limite de quota diária (quota = 2)
    res_ok2 = svc_real.generate_image(user_id=uid, prompt="Segunda imagem do dia")
    assert res_ok2["status"] == "SUCCEEDED"

    res_quota = svc_real.generate_image(user_id=uid, prompt="Terceira imagem excedendo quota")
    assert res_quota["status"] == "QUOTA_EXCEEDED"


def test_edicao_iterativa_preserva_historico_do_ativo(tmp_path):
    """
    Garante que edições conversacionais iterativas mantêm `root_asset_id`,
    `parent_asset_id`, `revision_number` incremental e `edit_history` completo.
    """
    svc = ImageGenerationService(
        storage_path=tmp_path / "gallery_iter.json",
        provider_adapter=lambda prompt, opts: (FAKE_PNG_BYTES, "image/png", "gemini_imagen", "imagen-3.0"),
    )
    uid = emitir_sessao("user_edit_f9")

    gen = svc.generate_image(user_id=uid, prompt="Logo minimalista de raposa azul")
    root_id = gen["asset"]["asset_id"]
    assert gen["asset"]["revision_number"] == 1

    edit1 = svc.edit_image_iteratively(
        user_id=uid,
        parent_asset_id=root_id,
        edit_instruction="Adicione óculos geométricos dourados",
    )
    asset_v2 = edit1["asset"]
    assert asset_v2["revision_number"] == 2
    assert asset_v2["parent_asset_id"] == root_id
    assert asset_v2["root_asset_id"] == root_id
    assert len(asset_v2["edit_history"]) == 2

    edit2 = svc.edit_image_iteratively(
        user_id=uid,
        parent_asset_id=asset_v2["asset_id"],
        edit_instruction="Mude o fundo para gradiente escuro",
    )
    asset_v3 = edit2["asset"]
    assert asset_v3["revision_number"] == 3
    assert asset_v3["parent_asset_id"] == asset_v2["asset_id"]
    assert asset_v3["root_asset_id"] == root_id
    assert [h["revision_number"] for h in asset_v3["edit_history"]] == [1, 2, 3]

    # Verifica galeria e exclusão
    gallery = svc.list_gallery(user_id=uid)
    assert len(gallery) == 3
    assert svc.delete_asset(user_id=uid, asset_id=asset_v3["asset_id"]) is True
    assert len(svc.list_gallery(user_id=uid)) == 2

    # Valida também endpoint HTTP /api/images/gallery
    client = TestClient(app)
    r_gal = client.get(f"/api/images/gallery?sessao={uid}", headers=_headers())
    assert r_gal.status_code == 200
    assert r_gal.json()["status"] == "ok"
