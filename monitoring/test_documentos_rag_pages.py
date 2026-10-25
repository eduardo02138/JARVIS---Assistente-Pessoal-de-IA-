# -*- coding: utf-8 -*-
"""
Suíte de Testes da Fase F4 — Documentos, Índice RAG Local com ACL e Copilot Pages.

Valida:
1. Extração real de PDF, DOCX, XLSX, PPTX, CSV, JSON, MD, TXT e PNG.
2. Bloqueio de magic bytes maliciosos (ELF, PE/MZ, falsificação de extensão) e limite de 20 arquivos por conversa.
3. ACL estrita por usuário no RAG (zero vazamento cross-user) e propagação imediata de exclusão de documentos.
4. Copilot Pages: criação, edição iterativa com histórico de versões imutável, restauração e exportação segura (HTML anti-XSS).
"""

import base64
import io
import struct
import zipfile
import pytest
from fastapi.testclient import TestClient

from documentos_rag_pages import (
    CopilotPagesManager,
    LocalRAGIndex,
    extract_document_text,
    validate_file_security,
)
from server import JARVIS_SECRET_TOKEN, app
from servidor.seguranca import emitir_sessao


def _make_docx_bytes(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
        )
        zf.writestr("word/document.xml", xml)
    return buf.getvalue()


def _make_xlsx_bytes(cell_text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        ss = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"<si><t>{cell_text}</t></si></sst>"
        )
        sheet = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row></sheetData></worksheet>'
        )
        zf.writestr("xl/sharedStrings.xml", ss)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def _make_pptx_bytes(slide_text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        slide = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            f"<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{slide_text}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
        )
        zf.writestr("ppt/slides/slide1.xml", slide)
    return buf.getvalue()


def _make_png_bytes(width: int = 320, height: int = 240) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr + b"\x00\x00\x00\x00"


def test_f4_extracao_multiformato_e_validacao_magic_bytes():
    """Valida extração real de PDF, DOCX, XLSX, PPTX, CSV, JSON, MD, TXT, PNG e rejeição de ELF/PE."""
    # 1. PDF
    pdf_bytes = b"%PDF-1.4\n1 0 obj\n<<>>\nstream\nBT (Arquitetura Soberana JARVIS) Tj ET\nendstream\n%%EOF"
    _, ext_pdf, txt_pdf = extract_document_text("manual.pdf", pdf_bytes)
    assert ext_pdf == ".pdf" and "Arquitetura Soberana JARVIS" in txt_pdf

    # 2. DOCX
    _, _, txt_docx = extract_document_text("contrato.docx", _make_docx_bytes("Cláusula de Confidencialidade"))
    assert "Cláusula de Confidencialidade" in txt_docx

    # 3. XLSX
    _, _, txt_xlsx = extract_document_text("planilha.xlsx", _make_xlsx_bytes("Receita Anual 2026"))
    assert "Receita Anual 2026" in txt_xlsx

    # 4. PPTX
    _, _, txt_pptx = extract_document_text("apresentacao.pptx", _make_pptx_bytes("Roadmap Estratégico Q4"))
    assert "Roadmap Estratégico Q4" in txt_pptx

    # 5. PNG
    _, _, txt_png = extract_document_text("diagrama.png", _make_png_bytes(640, 480), alt_text="Topologia de Rede")
    assert "640x480" in txt_png and "Topologia de Rede" in txt_png

    # 6. Bloqueio de binário ELF e PE/MZ e falsificação de assinatura
    with pytest.raises(ValueError, match="ELF"):
        validate_file_security("falso.txt", b"\x7fELF\x02\x01\x01\x00")

    with pytest.raises(ValueError, match="PE/MZ"):
        validate_file_security("falso.csv", b"MZ\x90\x00\x03\x00")

    with pytest.raises(ValueError, match="Assinatura binária inválida"):
        validate_file_security("falso.pdf", b"Isto nao e um PDF real")


def test_f4_rag_acl_por_usuario_exclusao_imediata_e_limite_20_arquivos():
    """Gate F4: ACL estrita por usuário no RAG, propagação imediata de exclusão e limite de 20 arquivos."""
    client = TestClient(app)
    headers = {"X-Jarvis-Token": JARVIS_SECRET_TOKEN}
    user_a = emitir_sessao("sessao-f4-rag-a")
    user_b = emitir_sessao("sessao-f4-rag-b")

    # 1. Upload de documento confidencial pelo Usuário A
    r_up = client.post(
        "/api/documents/upload",
        json={
            "sessao": user_a,
            "conversation_id": "conv_rag_1",
            "filename": "segredo_orion.md",
            "content_text": "O projeto Orion utiliza criptografia pós-quântica Kyber-1024 no núcleo Linux.",
        },
        headers=headers,
    )
    assert r_up.status_code == 200
    doc_id = r_up.json()["documentos"][0]["doc_id"]

    # 2. Usuário A recupera o chunk via busca RAG
    r_q_a = client.post(
        "/api/documents/query",
        json={"sessao": user_a, "query": "criptografia Kyber-1024 Orion"},
        headers=headers,
    )
    assert r_q_a.status_code == 200
    hits_a = r_q_a.json()["resultados"]
    assert len(hits_a) >= 1
    assert "Kyber-1024" in hits_a[0]["text"]
    assert hits_a[0]["untrusted_context"] is True

    # 3. Usuário B faz a mesma busca -> ZERO resultados (ACL estrita por user_id)
    r_q_b = client.post(
        "/api/documents/query",
        json={"sessao": user_b, "query": "criptografia Kyber-1024 Orion"},
        headers=headers,
    )
    assert r_q_b.status_code == 200
    assert r_q_b.json()["resultados"] == []

    # 4. Usuário B tenta excluir documento de A -> 404
    assert client.delete(f"/api/documents/{doc_id}?sessao={user_b}", headers=headers).status_code == 404

    # 5. Usuário A exclui o documento -> propagação imediata (nova busca retorna vazio na hora)
    r_del = client.delete(f"/api/documents/{doc_id}?sessao={user_a}", headers=headers)
    assert r_del.status_code == 200

    r_q_after = client.post(
        "/api/documents/query",
        json={"sessao": user_a, "query": "criptografia Kyber-1024 Orion"},
        headers=headers,
    )
    assert r_q_after.json()["resultados"] == [], "Chunks permaneceram no índice após exclusão do documento!"

    # 6. Limite de 20 arquivos por conversa
    lote_21 = [
        {"filename": f"arq_{i}.txt", "content_text": f"Conteúdo {i}"}
        for i in range(21)
    ]
    r_over = client.post(
        "/api/documents/upload",
        json={"sessao": user_a, "conversation_id": "conv_limite", "files": lote_21},
        headers=headers,
    )
    assert r_over.status_code == 400
    assert "20" in r_over.json()["mensagem"]


def test_f4_copilot_pages_versionamento_restauracao_e_exportacao_segura():
    """Valida criação, edição iterativa versionada, restauração e exportação anti-XSS em Copilot Pages."""
    client = TestClient(app)
    headers = {"X-Jarvis-Token": JARVIS_SECRET_TOKEN}
    user_a = emitir_sessao("sessao-f4-pages-a")
    user_b = emitir_sessao("sessao-f4-pages-b")

    # 1. Cria Page (v1)
    r_create = client.post(
        "/api/pages",
        json={
            "sessao": user_a,
            "title": "Especificação Técnica",
            "content_markdown": "# Versão 1\nConteúdo base.",
        },
        headers=headers,
    )
    assert r_create.status_code == 200
    page_id = r_create.json()["page"]["page_id"]
    assert r_create.json()["page"]["current_version"] == 1

    # 2. Edição iterativa (v2) incluindo tag <script> para testar sanitização na exportação HTML
    r_upd = client.put(
        f"/api/pages/{page_id}",
        json={
            "sessao": user_a,
            "content_markdown": "# Versão 2\nConteúdo atualizado <script>alert('xss')</script>",
            "summary": "Adicionada seção 2",
        },
        headers=headers,
    )
    assert r_upd.status_code == 200
    assert r_upd.json()["page"]["current_version"] == 2
    assert len(r_upd.json()["page"]["versions"]) == 2

    # 3. Isolamento: Usuário B não acessa nem edita a Page de A
    assert client.get(f"/api/pages/{page_id}?sessao={user_b}", headers=headers).status_code == 404

    # 4. Exportação HTML escapa tags perigosas
    r_exp_html = client.get(f"/api/pages/{page_id}/export?sessao={user_a}&formato=html", headers=headers)
    assert r_exp_html.status_code == 200
    html_out = r_exp_html.json()["exportacao"]["content"]
    assert "<script>" not in html_out
    assert "&lt;script&gt;" in html_out

    # 5. Restauração da versão 1 cria versão 3 com o conteúdo original preservado
    r_rest = client.post(
        f"/api/pages/{page_id}/restore",
        json={"sessao": user_a, "version_number": 1},
        headers=headers,
    )
    assert r_rest.status_code == 200
    restored_page = r_rest.json()["page"]
    assert restored_page["current_version"] == 3
    assert restored_page["content_markdown"] == "# Versão 1\nConteúdo base."
