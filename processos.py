"""Processos externos abertos pelo J.A.R.V.I.S. sem herdar os segredos do servidor.

Aplicativos, jogos, o navegador padrão, a IDE Antigravity e a CLI agy herdavam o
ambiente inteiro do servidor, com o JARVIS_TOKEN e as chaves do Gemini carregadas do
.env. Com o token, qualquer processo aberto pelo assistente (ou um agente de IDE sob
prompt injection) consegue aprovar ações pendentes pela API local. Aqui o ambiente
dos filhos é montado sem esses segredos: cada programa usa a própria autenticação.

JARVIS_ENV_REPASSAR (lista separada por vírgulas) libera variáveis específicas, por
exemplo GEMINI_API_KEY para uma CLI agy que se autentica por chave. JARVIS_TOKEN e
JARVIS_SECRET_TOKEN nunca são repassados.
"""

import os
import re
import subprocess
from typing import Mapping, Optional, Sequence

RAIZ = os.path.dirname(os.path.abspath(__file__))
ARQUIVO_ENV = os.path.join(RAIZ, ".env")

# Credenciais da API local: nunca saem do servidor, nem com JARVIS_ENV_REPASSAR
SEGREDOS_DA_API_LOCAL = frozenset({"JARVIS_TOKEN", "JARVIS_SECRET_TOKEN"})

# Segredos que o JARVIS usa mesmo quando vêm do shell, e não do .env
SEGREDOS_CONHECIDOS = SEGREDOS_DA_API_LOCAL | frozenset({
    "GEMINI_API_KEY",
    "GEMINI_API_KEYS",
    "GOOGLE_API_KEY",
    "OMNIROUTE_API_KEY",
})

# Nome com cara de credencial: DISCORD_BOT_TOKEN, SPOTIFY_CLIENT_SECRET, SMTP_PASSWORD,
# ou URL de banco que pode carregar usuário e senha (SESSION_DB_URL, DATABASE_URL)
_NOME_DE_SEGREDO = re.compile(
    r"(API_?KEYS?|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|DB_URL|DATABASE_URL|DSN)$",
    re.IGNORECASE,
)


def _variaveis_do_env_do_projeto() -> set:
    """Nomes definidos no .env do projeto (configuração do JARVIS, não da sessão do usuário)."""
    try:
        from dotenv import dotenv_values
        return set(dotenv_values(ARQUIVO_ENV)) if os.path.isfile(ARQUIVO_ENV) else set()
    except Exception:
        return set()


def variaveis_repassadas() -> set:
    """Variáveis liberadas pelo operador em JARVIS_ENV_REPASSAR (menos o token da API local)."""
    bruto = os.environ.get("JARVIS_ENV_REPASSAR", "")
    return {nome.strip() for nome in bruto.split(",") if nome.strip()} - SEGREDOS_DA_API_LOCAL


def eh_segredo_do_jarvis(nome: str, do_projeto: Optional[set] = None) -> bool:
    """Segredo conhecido ou credencial declarada no .env do projeto."""
    if nome in SEGREDOS_CONHECIDOS:
        return True
    if do_projeto is None:
        do_projeto = _variaveis_do_env_do_projeto()
    return nome in do_projeto and bool(_NOME_DE_SEGREDO.search(nome))


def ambiente_sem_segredos(base: Optional[Mapping[str, str]] = None) -> dict:
    """Cópia do ambiente (os.environ por padrão) sem as credenciais do JARVIS.

    As variáveis da sessão do usuário (DISPLAY, PATH, XDG_*, tokens exportados no
    próprio shell que não pertencem ao JARVIS) continuam disponíveis.
    """
    env = dict(os.environ if base is None else base)
    do_projeto = _variaveis_do_env_do_projeto()
    liberadas = variaveis_repassadas()
    for nome in list(env):
        if nome in liberadas:
            continue
        if eh_segredo_do_jarvis(nome, do_projeto):
            env.pop(nome, None)
    return env


def abrir_desanexado(argv: Sequence[str], **kwargs) -> subprocess.Popen:
    """Abre um programa gráfico desacoplado do servidor: sessão própria, sem terminal e sem segredos."""
    kwargs.setdefault("env", ambiente_sem_segredos())
    kwargs.setdefault("stdin", subprocess.DEVNULL)
    kwargs.setdefault("stdout", subprocess.DEVNULL)
    kwargs.setdefault("stderr", subprocess.DEVNULL)
    kwargs.setdefault("start_new_session", True)
    return subprocess.Popen(list(argv), **kwargs)


def executar(argv: Sequence[str], **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run com o ambiente sem segredos (para CLIs de terceiros, como a agy)."""
    kwargs.setdefault("env", ambiente_sem_segredos())
    return subprocess.run(list(argv), **kwargs)
