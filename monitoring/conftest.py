"""Configuração de coleta do pytest para o diretório monitoring.

`test_suite.py` e `test_adk.py` são suítes executáveis por script: elas expõem
um bloco `if __name__ == "__main__":` e orquestram os próprios cenários via
`asyncio.run`. Várias das funções `test_*` nesses arquivos são corrotinas ou
recebem argumentos posicionais (por exemplo `test_gemini_live_handshake(key)`),
de modo que o pytest as coleta e as reporta como falha mesmo quando a suíte
passa integralmente pelo runner nativo.

Além do ruído no relatório, a coleta pelo pytest executa parcialmente o caminho
do Gemini Live e grava ERROR/WARNING falsos em monitoring/logs/errors.log.

Execute essas duas suítes pelo runner próprio:

    python monitoring/test_suite.py          # diagnóstico
    python monitoring/test_suite.py --p0     # suíte P0 completa
    python monitoring/test_adk.py

Os arquivos de pytest legítimos (test_trust_gates.py, test_reproduction_p0.py)
continuam sendo coletados normalmente.
"""

collect_ignore = [
    "test_suite.py",
    "test_adk.py",
]
