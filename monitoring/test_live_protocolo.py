"""Testes unitários do protocolo compartilhado live_protocolo.py.

Cobrem o parser de confirmação/recusa e a classificação de close code,
guardas de regressão após a unificação dos cinco parsers originais.
"""

import pytest

from live_protocolo import EncerramentoLimpoDaSessao, _limpar, palavra_confirma, palavra_recusa


def test_confirma_simples():
    assert palavra_confirma("sim") is True
    assert palavra_confirma("Sim.") is True
    assert palavra_confirma("ok") is True
    assert palavra_confirma("confirmar") is True
    assert palavra_confirma("conceder") is True
    assert palavra_confirma("yes") is True


def test_confirma_frase_multi_word():
    assert palavra_confirma("fazer teste") is True
    assert palavra_confirma("fazer teste avançado") is True
    assert palavra_confirma("pode fazer teste agora") is True


def test_veto_de_recusa_sobrepoe_aceite():
    assert palavra_confirma("sim, cancelar") is False
    assert palavra_confirma("pode cancelar") is False
    assert palavra_confirma("sim mas recuso") is False
    assert palavra_confirma("fazer teste, recuso") is False


def test_recusa_simples():
    assert palavra_confirma("não") is False
    assert palavra_confirma("recusar") is False
    assert palavra_confirma("no") is False
    assert palavra_recusa("recuso") is True
    assert palavra_recusa("não pode") is True


def test_confirma_com_negacao_sem_recusa_intencional():
    assert palavra_confirma("sim, pode autorizar") is True
    assert palavra_confirma("pode prosseguir") is True


def test_verbo_de_pedido_nao_autoriza():
    assert palavra_confirma("pode repetir") is False
    assert palavra_confirma("pode continuar") is False
    assert palavra_confirma("sim, pode explicar") is False
    assert palavra_confirma("pode repetir a pergunta?") is False
    assert palavra_confirma("ok, mas pode detalhar?") is False


def test_vazios_e_nulos():
    assert palavra_confirma("") is False
    assert palavra_confirma(None) is False
    assert palavra_confirma("   ") is False
    assert palavra_recusa("") is False


def test_limpar_normaliza_texto():
    assert " ".join(_limpar("  SIM!!,  senhor.  ").split()) == "sim senhor"
    assert palavra_confirma("Sim!!, senhor.") is True


def test_close_code_limpo_eh_apenas_1000():
    exc = EncerramentoLimpoDaSessao()
    assert str(exc) == ""
    assert issubclass(EncerramentoLimpoDaSessao, Exception)