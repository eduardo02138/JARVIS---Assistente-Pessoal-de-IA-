"""
F5 — Testes de Voz, Visão e Integração Linux (`monitoring/test_voz_visao.py`)

Testes obrigatórios:
- test_fala_curta_gera_unico_stream_end
- test_modo_ditado_nao_executa_ferramenta
- test_visao_sem_consentimento_bloqueada
"""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from server import app
from servidor.seguranca import JARVIS_SECRET_TOKEN, emitir_sessao
from voz_visao_linux import (
    ConsentedVisionService,
    ReadAloudService,
    VoiceDictationService,
    VoiceTurnDebouncer,
)


def _headers() -> dict:
    if JARVIS_SECRET_TOKEN:
        return {"X-Jarvis-Token": JARVIS_SECRET_TOKEN}
    return {}


def test_fala_curta_gera_unico_stream_end():
    """
    Garante que uma fala curta gera exatamente um único `audio_stream_end`
    e que sinais fantasmas (sem chunks de áudio) ou duplicados em rajada são suprimidos.
    """
    debouncer = VoiceTurnDebouncer(min_chunks_for_turn=1, min_bytes_for_turn=320, debounce_window_s=0.65)

    # 1. Stream end fantasma sem áudio -> suprimido
    ok0, reason0 = debouncer.should_emit_stream_end(now=100.0)
    assert ok0 is False
    assert reason0 == "sem_audio_no_turno"

    # 2. Fala curta real (2 chunks de 640 bytes) -> emite exatamente 1 stream_end
    debouncer.record_audio_chunk(b"\x01\x00" * 320, now=101.0)
    debouncer.record_audio_chunk(b"\x02\x00" * 320, now=101.1)
    ok1, reason1 = debouncer.should_emit_stream_end(now=101.3)
    assert ok1 is True
    assert reason1 == "ok"

    # 3. Duplicata imediata sem novo áudio -> suprimida
    ok2, reason2 = debouncer.should_emit_stream_end(now=101.4)
    assert ok2 is False
    assert reason2 == "sem_audio_no_turno"

    # 4. Rajada dentro da janela de debounce (< 0.65s após o último stream_end) -> suprimida
    debouncer.record_audio_chunk(b"\x01\x00" * 320, now=101.5)
    ok3, reason3 = debouncer.should_emit_stream_end(now=101.6)
    assert ok3 is False
    assert reason3 == "debounce_rajada"

    stats = debouncer.stats()
    assert stats["emitted_count"] == 1
    assert stats["suppressed_count"] == 3


def test_modo_ditado_nao_executa_ferramenta():
    """
    Garante que o modo ditado acumula texto para o editor sem disparar turno
    conversacional nem executar ferramentas do agente, além de validar Read Aloud.
    """
    svc = VoiceDictationService()
    sid = emitir_sessao("sessao_ditado_f5")

    st = svc.start_dictation(session_id=sid, language_code="pt-BR", clear=True)
    assert st.active is True
    assert st.triggers_conversation_turn is False
    assert st.executes_agent_tools is False

    st = svc.append_segment(session_id=sid, text_segment="Relatório técnico de arquitetura.")
    st = svc.append_segment(session_id=sid, text_segment="Ponto dois concluído.")
    assert st.buffer_text == "Relatório técnico de arquitetura. Ponto dois concluído."
    assert st.triggers_conversation_turn is False
    assert st.executes_agent_tools is False

    st_stopped = svc.stop_dictation(session_id=sid)
    assert st_stopped.active is False

    # Também valida Read Aloud via API REST
    client = TestClient(app)
    r_ra = client.post(
        "/api/voice/read_aloud/start",
        json={"sessao": sid, "text": "Primeira frase. Segunda frase!", "voice_name": "Aoede", "rate": 1.25},
        headers=_headers(),
    )
    assert r_ra.status_code == 200
    ra_data = r_ra.json()["read_aloud"]
    assert ra_data["status"] == "PLAYING"
    assert len(ra_data["segments"]) == 2
    assert ra_data["rate"] == 1.25


def test_visao_sem_consentimento_bloqueada():
    """
    Garante que o Copilot Vision exige consentimento explícito por sessão,
    permite revogação imediata e impede qualquer ferramenta de clique/mutação
    durante perguntas sobre a tela.
    """
    vision = ConsentedVisionService()
    sid = emitir_sessao("sessao_visao_f5")
    client = TestClient(app)

    # 1. Tentativa de analisar a tela sem consentimento prévio -> 403 Bloqueado
    r_denied = client.post(
        "/api/vision/analyze",
        json={
            "sessao": sid,
            "source": "screen",
            "question": "O que aparece na janela atual?",
            "image_base64": base64.b64encode(b"fake_jpeg_bytes").decode("ascii"),
        },
        headers=_headers(),
    )
    assert r_denied.status_code == 403
    assert r_denied.json()["status"] == "bloqueado"

    # 2. Concede consentimento explícito para 'screen' e 'window'
    r_grant = client.post(
        "/api/vision/consent/grant",
        json={"sessao": sid, "sources": ["screen", "window"], "ttl_seconds": 300},
        headers=_headers(),
    )
    assert r_grant.status_code == 200
    assert r_grant.json()["consent"]["mode"] == "READ_ONLY_OBSERVATION"

    # 3. Consulta visual somente-leitura funciona
    r_ok = client.post(
        "/api/vision/analyze",
        json={
            "sessao": sid,
            "source": "screen",
            "question": "Qual o título do documento aberto?",
            "ocr_hint_text": "Editor de Código - server.py",
        },
        headers=_headers(),
    )
    assert r_ok.status_code == 200
    body_ok = r_ok.json()
    assert body_ok["mode"] == "READ_ONLY_OBSERVATION"
    assert body_ok["ui_mutation_performed"] is False
    assert "server.py" in body_ok["analysis"]

    # 4. Tentativa de embutir ferramenta de clique ou digitação durante visão -> 403 Bloqueado
    r_mut = client.post(
        "/api/vision/analyze",
        json={
            "sessao": sid,
            "source": "screen",
            "question": "Clique no botão salvar",
            "requested_tools": ["mouse_click"],
        },
        headers=_headers(),
    )
    assert r_mut.status_code == 403
    assert "READ_ONLY_OBSERVATION" in r_mut.json()["mensagem"]

    # 5. Revogação imediata bloqueia novas leituras visuais
    r_rev = client.post("/api/vision/consent/revoke", json={"sessao": sid}, headers=_headers())
    assert r_rev.status_code == 200
    assert r_rev.json()["revoked"] is True

    r_after_rev = client.post(
        "/api/vision/analyze",
        json={"sessao": sid, "source": "screen", "question": "Ainda consegue ver minha tela?"},
        headers=_headers(),
    )
    assert r_after_rev.status_code == 403
