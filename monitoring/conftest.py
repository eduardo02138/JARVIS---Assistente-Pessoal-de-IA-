"""Configuração de coleta do pytest para o diretório monitoring.

`test_suite.py` e `test_adk.py` são suítes executáveis por script: elas expõem
um bloco `if __name__ == "__main__":` e orquestram os próprios cenários via
`asyncio.run` (runner P0 e 14 cenários ADK P0.17.2). Várias das funções
`test_*` nesses arquivos são corrotinas ou recebem argumentos posicionais
(por exemplo `test_gemini_live_handshake(key)`), de modo que o pytest as
coletaria e as reportaria como falha mesmo quando a suíte passa integralmente
pelo runner nativo. Por isso elas ficam de fora da coleta (collect_ignore) e
são executadas pelo runner próprio:

    python monitoring/test_suite.py --p0     # suíte P0 completa
    python monitoring/test_adk.py

Os arquivos de pytest legítimos (test_trust_gates.py, test_reproduction_p0.py)
continuam sendo coletados normalmente.

Isolamento de ambiente durante o pytest:
  - A coleção nunca abre a suíte orquestradora, então não há as antigas
    falhas de "async collection" nem ruído de corrotina coletada.
  - SESSION_DB_URL="memoria" é definida antes de qualquer import de server.py:
    o TestClient/app não tocam no sessoes.db real do repositório.
  - Os arquivos de log/evento do monitoring/logger.py (assistant.log,
    events.jsonl, errors.log) são re-apontados para um diretório temporário por
    uma fixture de sessão autouse; nada é gravado em monitoring/logs/ durante
    o pytest e o estado original é restaurado ao final.
"""

import os

os.environ.setdefault("SESSION_DB_URL", "memoria")

import logging

import pytest

import monitoring.logger as mlogger

collect_ignore = [
    "test_suite.py",
    "test_adk.py",
]

_LOG_PATHS = {
    "TEXT_LOG_FILE": "assistant.log",
    "JSONL_LOG_FILE": "events.jsonl",
    "ERROR_LOG_FILE": "errors.log",
}


@pytest.fixture(scope="session", autouse=True)
def _isolar_logs_em_pytest(tmp_path_factory):
    """Redireciona os arquivos de log/evento do logger.py para diretório temporário.

    A importação de server.py/agentes instancia os FileHandlers de
    monitoring.logger apontando para monitoring/logs/. Para o pytest não gravar
    nada nesses arquivos, (1) as constantes TEXT_LOG_FILE/JSONL_LOG_FILE/
    ERROR_LOG_FILE de monitoring.logger são re-apontadas para arquivos
    temporários e (2) os handlers do logger "jarvis_monitor" são substituídos
    por handlers nesses mesmos arquivos temporários. O estado original é
    restaurado ao final da sessão.
    """
    dir_logs = tmp_path_factory.mktemp("logs_pytest")

    constantes_originais = {}
    for nome_const, nome_arquivo in _LOG_PATHS.items():
        constantes_originais[nome_const] = getattr(mlogger, nome_const)
        setattr(mlogger, nome_const, str(dir_logs / nome_arquivo))

    log_jarvis = logging.getLogger("jarvis_monitor")
    handlers_originais = list(log_jarvis.handlers)
    for handler in handlers_originais:
        log_jarvis.removeHandler(handler)

    def _novo_handler(caminho: str, nivel: int):
        handler = logging.FileHandler(caminho, encoding="utf-8")
        handler.setLevel(nivel)
        handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
        log_jarvis.addHandler(handler)
        return handler

    _novo_handler(str(dir_logs / "assistant.log"), logging.INFO)
    _novo_handler(str(dir_logs / "errors.log"), logging.WARNING)

    yield

    log_jarvis.handlers.clear()
    log_jarvis.handlers.extend(handlers_originais)
    for nome_const, valor in constantes_originais.items():
        setattr(mlogger, nome_const, valor)