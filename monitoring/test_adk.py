"""
Suíte de Testes Automatizados da Arquitetura Unificada do Google ADK & Policy Gate.
Verifica:
1. Roteamento inteligente de texto (escolher_caminho: rápido vs coordenador).
2. Ausência total de ferramentas de auto-autorização no schema do LLM.
3. Policy Engine como autoridade única de segurança.
4. Concessão de autorização estritamente one-shot com TTL e hash de argumentos.
5. Revogação e expurgo imediato do token após consumo (não reutilizável).
6. Aprovação verbal / textual de ações pendentes.
"""

import os
import sys
import time

# Garante acesso à raiz do projeto
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

from policy_engine import policy_engine, RiskLevel
from agentes.roteador import escolher_caminho, CAMINHO_RAPIDO, CAMINHO_COMPLEXO
from agentes.assistente import (
    criar_agente_rapido,
    criar_agente_coordenador,
    criar_agente_de_voz,
    guarda_de_ferramentas,
)


def test_roteador_inteligente():
    """Valida o despachante heurístico de baixa latência."""
    # Ações diretas e curtas
    caminho1, _ = escolher_caminho("Que horas são agora?")
    assert caminho1 == CAMINHO_RAPIDO, f"Esperado {CAMINHO_RAPIDO}, obtido {caminho1}"

    caminho2, _ = escolher_caminho("status do sistema")
    assert caminho2 == CAMINHO_RAPIDO, f"Esperado {CAMINHO_RAPIDO}, obtido {caminho2}"

    # Tarefas compostas e multi-etapas
    caminho3, _ = escolher_caminho("Primeiro analise o sistema e depois abra o navegador com o resumo")
    assert caminho3 == CAMINHO_COMPLEXO, f"Esperado {CAMINHO_COMPLEXO}, obtido {caminho3}"

    # Pedidos longos (> 14 palavras)
    caminho4, _ = escolher_caminho("Gostaria de saber detalhadamente todas as métricas da minha máquina e como estão os processos rodando no Linux")
    assert caminho4 == CAMINHO_COMPLEXO, f"Esperado {CAMINHO_COMPLEXO}, obtido {caminho4}"
    print(" [✔ PASS] Roteador Inteligente ADK (escolher_caminho: rápido vs coordenador)")


def test_ausencia_de_auto_autorizacao_no_llm():
    """Garante que autorizar_acao NUNCA seja exposta como ferramenta ao modelo."""
    for nome, fabrica in [
        ("agente_rapido", criar_agente_rapido),
        ("coordenador", criar_agente_coordenador),
        ("agente_voz", criar_agente_de_voz),
    ]:
        agente = fabrica()
        nomes_tools = []
        for t in agente.tools:
            if hasattr(t, "name"):
                nomes_tools.append(t.name)
            elif hasattr(t, "__name__"):
                nomes_tools.append(t.__name__)
            elif hasattr(t, "agent"):
                nomes_tools.append(t.agent.name)
        assert "autorizar_acao" not in nomes_tools, (
            f"FALHA DE SEGURANÇA: 'autorizar_acao' encontrada nas ferramentas de {nome}: {nomes_tools}"
        )
    print(" [✔ PASS] Ausência de Auto-Autorização: LLM não possui ferramenta de auto-liberação")


def test_policy_engine_bloqueio_e_one_shot():
    """Valida que ferramentas sensíveis bloqueiam e só executam sob liberação única one-shot."""
    args = {"url": "https://antigravity.google.com"}
    session_id = "teste-sessao-segura-adk"

    # Avaliação do Policy Engine
    decision = policy_engine.evaluate("abrir_site", args, session_id=session_id)
    assert decision.allowed is True
    assert decision.requires_confirmation is True
    assert decision.risk_level == RiskLevel.EXTERNAL_WRITE

    # Simula chamada da ferramenta sem autorização prévia
    class MockTool:
        name = "abrir_site"

    class MockContext:
        session_id = "teste-sessao-segura-adk"

    resultado_bloqueio = guarda_de_ferramentas(MockTool(), args, MockContext())
    assert isinstance(resultado_bloqueio, dict), "Esperado bloqueio com dict"
    assert resultado_bloqueio.get("status") == "bloqueado_aguardando_confirmacao"
    action_id = resultado_bloqueio.get("id_confirmacao")
    assert action_id is not None, "ID de confirmação ausente"

    # Tentativa de consumo antes da aprovação do usuário deve falhar
    assert policy_engine.consume_authorization("abrir_site", args, session_id=session_id) is False

    # Usuário aprova a ação através da autoridade legítima
    aprovado = policy_engine.approve_action(action_id, session_id=session_id)
    assert aprovado is True

    # Agora a execução deve ser autorizada e o token consumido (one-shot)
    resultado_liberado = guarda_de_ferramentas(MockTool(), args, MockContext())
    assert resultado_liberado is None, "Deveria retornar None (permitir execução original)"

    # Segunda execução imediata com os mesmos argumentos DEVE FALHAR (token já foi consumido)
    resultado_reexecucao = guarda_de_ferramentas(MockTool(), args, MockContext())
    assert isinstance(resultado_reexecucao, dict), "Re-execução deveria ser bloqueada (one-shot violado)"
    assert resultado_reexecucao.get("status") == "bloqueado_aguardando_confirmacao"
    print(" [✔ PASS] Policy Gate One-Shot: Bloqueio, aprovação única e revogação imediata pós-consumo")


def test_aprovacao_verbal_e_argumentos_especificos():
    """Valida aprovação verbal da última pendência e vinculação estrita de argumentos."""
    session_id = "sessao-verbal-test"
    args_original = {"url": "https://github.com"}
    args_adulterado = {"url": "https://malicious-site.test"}

    # Cria pendência para o site original
    pending = policy_engine.create_pending_action("abrir_site", args_original, session_id=session_id)

    # Usuário fala 'sim' / 'autorizar', resolvendo a última pendência
    aprovado_verbal = policy_engine.approve_latest_pending(session_id=session_id)
    assert aprovado_verbal is not None
    assert aprovado_verbal.action_id == pending.action_id

    # Tentativa de consumir com argumentos diferentes DEVE FALHAR (args_hash mismatch)
    consumo_adulterado = policy_engine.consume_authorization("abrir_site", args_adulterado, session_id=session_id)
    assert consumo_adulterado is False, "Não pode consumir autorização com argumentos alterados"

    # Consumo com os argumentos originais aprovados deve ter sucesso
    consumo_legitimo = policy_engine.consume_authorization("abrir_site", args_original, session_id=session_id)
    assert consumo_legitimo is True
    print(" [✔ PASS] Aprovação Verbal e Integridade Criptográfica dos Argumentos (args_hash)")


def executar_todos_testes_adk():
    print("\n=== EXECUTANDO TESTES DO GOOGLE ADK & POLICY GATE (FASE P0.15) ===")
    test_roteador_inteligente()
    test_ausencia_de_auto_autorizacao_no_llm()
    test_policy_engine_bloqueio_e_one_shot()
    test_aprovacao_verbal_e_argumentos_especificos()
    print("\n✔ Todos os testes do módulo ADK passaram com 100% de conformidade!\n")


if __name__ == "__main__":
    executar_todos_testes_adk()
