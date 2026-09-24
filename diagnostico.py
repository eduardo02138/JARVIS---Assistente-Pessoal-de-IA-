"""Diagnóstico do J.A.R.V.I.S. nesta máquina (o "doctor" do assistente).

Confere configuração, segurança, o que cada função precisa desta máquina e as
integrações (plug-ins, skills, MCP, OmniRoute), e diz como corrigir o que falta.
Só lê: não altera arquivos, configurações nem o estado do servidor.

    python diagnostico.py            # relatório legível; código de saída 1 se houver erro
    python diagnostico.py --json     # o mesmo relatório em JSON
    python diagnostico.py --sem-mcp  # não conecta aos servidores MCP configurados

Com o servidor rodando, GET /api/diagnostico (com o token) devolve o mesmo relatório,
já com o estado real das conexões MCP.
"""

import contextlib
import glob
import json
import os
import re
import shutil
import sqlite3
import sys
from dataclasses import asdict, dataclass
from typing import Callable, List, Optional

from servidor import ENV_PATH, host_local_do_servidor  # carrega o .env antes dos demais módulos

OK, AVISO, ERRO = "ok", "aviso", "erro"


@dataclass
class Achado:
    id: str
    nivel: str
    titulo: str
    detalhe: str = ""
    correcao: str = ""


# ----------------- Dependências -----------------

# Faixas testadas. Uma major nova muda a API (veja as notas de migração 1.x -> 2.0 do ADK):
# (versão mínima, major que já não é suportada, como corrigir)
DEPENDENCIAS_SUPORTADAS = {
    "google-adk": ((2, 9), 3, "pip install 'google-adk[db]>=2.9.1,<3'"),
    "google-genai": ((2, 19), 3, "pip install -r requirements.txt"),
    "mcp": ((2, 2), 3, "pip install 'mcp>=2.2,<3' (google-adk[mcp] e [all] instalam mcp<2, sem a API que o servidor MCP usa)"),
}


def _versao(texto: str) -> tuple:
    numeros = re.match(r"(\d+)(?:\.(\d+))?", texto or "")
    return (int(numeros.group(1)), int(numeros.group(2) or 0)) if numeros else (0, 0)


def _dependencias() -> List[Achado]:
    import importlib.metadata as metadados

    achados, instaladas = [], []
    for pacote, (minima, major_limite, correcao) in DEPENDENCIAS_SUPORTADAS.items():
        try:
            versao = metadados.version(pacote)
        except metadados.PackageNotFoundError:
            achados.append(Achado(f"dependencias.{pacote}", ERRO, f"{pacote} não está instalado", "",
                                  "pip install -r requirements.txt"))
            continue
        numeros = _versao(versao)
        if numeros < minima or numeros[0] >= major_limite:
            achados.append(Achado(f"dependencias.{pacote}", ERRO, f"{pacote} {versao} fora da faixa suportada",
                                  f"O JARVIS usa {pacote} >= {minima[0]}.{minima[1]} e < {major_limite}.", correcao))
        else:
            instaladas.append(f"{pacote} {versao}")
    if instaladas and not achados:
        achados.append(Achado("dependencias.versoes", OK, "Dependências nas versões suportadas", ", ".join(instaladas)))
    return achados


# ----------------- Configuração e segurança -----------------

def _configuracao() -> List[Achado]:
    from provider_router import GoogleStudioProvider

    achados = []
    if os.path.isfile(ENV_PATH):
        achados.append(Achado("configuracao.env", OK, "Arquivo .env encontrado", ENV_PATH))
    else:
        achados.append(Achado("configuracao.env", AVISO, "Sem arquivo .env",
                              "Só as variáveis do ambiente do processo estão valendo.",
                              "cp .env.example .env e preencha as chaves."))
    chaves = GoogleStudioProvider.get_keys()
    if not chaves:
        achados.append(Achado("configuracao.chave_gemini", ERRO, "Nenhuma chave do Gemini configurada",
                              "Voz, chat e agentes dependem do Gemini.",
                              "Defina GEMINI_API_KEY (ou GEMINI_API_KEYS=chave1,chave2) no .env."))
    else:
        detalhe = "Com uma chave só, não há rotação quando a cota acabar." if len(chaves) == 1 else ""
        achados.append(Achado("configuracao.chave_gemini", OK, f"{len(chaves)} chave(s) do Gemini no pool", detalhe))
    return achados


def _seguranca() -> List[Achado]:
    achados = []
    token = os.environ.get("JARVIS_TOKEN", "")
    if not token:
        achados.append(Achado("seguranca.token", AVISO, "JARVIS_TOKEN não definido",
                              "Um token novo é gerado a cada inicialização: o servidor MCP, a ponte gemini "
                              "e o monitor não conseguem se autenticar.",
                              "Defina JARVIS_TOKEN no .env (python -c \"import secrets; print(secrets.token_urlsafe(32))\")."))
    elif len(token) < 16:
        achados.append(Achado("seguranca.token", AVISO, "JARVIS_TOKEN curto demais",
                              f"{len(token)} caracteres; use pelo menos 16.",
                              "Gere outro com python -c \"import secrets; print(secrets.token_urlsafe(32))\"."))
    else:
        achados.append(Achado("seguranca.token", OK, "JARVIS_TOKEN definido"))

    host = os.environ.get("JARVIS_HOST", "127.0.0.1").strip() or "127.0.0.1"
    if host_local_do_servidor() in ("127.0.0.1", "::1", "localhost") and host not in ("0.0.0.0", "::", "[::]"):
        achados.append(Achado("seguranca.rede", OK, "Servidor restrito a esta máquina", f"JARVIS_HOST={host}"))
    else:
        achados.append(Achado("seguranca.rede", AVISO, "Servidor aceita conexões de outras máquinas",
                              f"JARVIS_HOST={host}: qualquer um na rede com o token controla o assistente.",
                              "Use JARVIS_HOST=127.0.0.1, salvo se precisar de acesso pela rede."))

    import rede_segura
    if rede_segura.rede_local_permitida():
        achados.append(Achado("seguranca.leitura_web", AVISO, "read_web_page pode ler a rede local",
                              "JARVIS_WEB_PERMITIR_REDE_LOCAL está ativo (loopback e metadados seguem bloqueados).",
                              "Desative se o modelo não precisar ler páginas da sua rede."))
    else:
        achados.append(Achado("seguranca.leitura_web", OK, "read_web_page limitado a páginas públicas"))

    import processos
    repassadas = sorted(processos.variaveis_repassadas())
    if repassadas:
        achados.append(Achado("seguranca.processos", AVISO, "Segredos repassados aos programas abertos",
                              "JARVIS_ENV_REPASSAR libera: " + ", ".join(repassadas),
                              "Mantenha só o que a CLI agy (ou outro programa) realmente precisa."))
    else:
        achados.append(Achado("seguranca.processos", OK, "Programas abertos pelo JARVIS não recebem os segredos dele"))
    return achados


# ----------------- O que cada função precisa desta máquina -----------------

def _funcoes_da_maquina() -> List[Achado]:
    import perfil_maquina
    import system_tools

    achados = []
    sessao = perfil_maquina.sessao_grafica()
    if sessao in ("wayland", "x11"):
        achados.append(Achado("maquina.sessao_grafica", OK, f"Sessão gráfica {sessao}"))
    else:
        achados.append(Achado("maquina.sessao_grafica", AVISO, "Sem sessão gráfica",
                              "Abrir aplicativos, capturas de tela e a janela do app não funcionam fora do desktop.",
                              "Inicie o JARVIS de dentro da sessão do desktop (./run_jarvis.sh)."))

    volume = system_tools._comandos_de_volume("aumentar", 5)
    if volume:
        achados.append(Achado("funcao.volume", OK, f"Controle de volume por {volume[0][0]}"))
    else:
        achados.append(Achado("funcao.volume", AVISO, "Controle de volume indisponível",
                              "Nenhum de pactl, wpctl ou amixer foi encontrado.",
                              "Instale pulseaudio-utils (pactl), wireplumber (wpctl) ou alsa-utils (amixer)."))

    captura = system_tools._comandos_de_captura(os.path.join(os.sep, "tmp", "jarvis-diagnostico.png"))
    if captura:
        achados.append(Achado("funcao.captura_de_tela", OK, f"Captura de tela por {captura[0][0]}"))
    else:
        dica = ("instale grim (Sway/Hyprland), spectacle (KDE) ou gnome-screenshot (GNOME)" if sessao == "wayland"
                else "instale maim, scrot ou imagemagick" if sessao == "x11"
                else "rode o JARVIS dentro de uma sessão gráfica")
        achados.append(Achado("funcao.captura_de_tela", AVISO, "Captura de tela indisponível", "", dica[0].upper() + dica[1:] + "."))

    if shutil.which("xdg-open"):
        achados.append(Achado("funcao.abrir_links", OK, "Sites, pesquisas e arquivos abrem pelo xdg-open"))
    else:
        achados.append(Achado("funcao.abrir_links", AVISO, "xdg-open não encontrado",
                              "open_website, search_web e play_music não conseguem abrir o navegador.",
                              "Instale o pacote xdg-utils."))

    import controller_engine
    if not controller_engine.EVDEV_DISPONIVEL:
        achados.append(Achado("funcao.modo_controle", AVISO, "Modo Controle sem mouse e teclado virtuais",
                              "A biblioteca evdev não está instalada.", "pip install evdev"))
    elif not os.access("/dev/uinput", os.W_OK):
        achados.append(Achado("funcao.modo_controle", AVISO, "Modo Controle sem acesso ao /dev/uinput",
                              "O usuário atual não pode criar dispositivos virtuais.",
                              "sudo modprobe uinput e adicione seu usuário ao grupo input (depois, novo login)."))
    else:
        achados.append(Achado("funcao.modo_controle", OK, "Mouse e teclado virtuais disponíveis (uinput)"))

    gpu = system_tools.get_gpu_status()
    if gpu.get("disponivel"):
        tempo_real = gpu.get("telemetria_em_tempo_real", True)
        achados.append(Achado("funcao.gpu", OK, f"Placa de vídeo: {gpu.get('modelo', 'detectada')}",
                              "" if tempo_real else "O driver não expõe uso e temperatura em tempo real."))
    else:
        achados.append(Achado("funcao.gpu", AVISO, "Nenhuma placa de vídeo detectada",
                              "get_gpu_status e a telemetria do HUD ficam sem dados de GPU.",
                              "Em máquina com GPU, confira o driver (nvidia-smi para NVIDIA, amdgpu para AMD)."))

    achados.append(_computer_use())
    achados.extend(_modo_ide())
    return achados


def _computer_use() -> Achado:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return Achado("funcao.computer_use", AVISO, "Modo Computador indisponível",
                      "O pacote playwright não está instalado.",
                      "pip install playwright && playwright install chromium")
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or os.path.expanduser("~/.cache/ms-playwright")
    if base == "0":
        import playwright as pacote
        base = os.path.join(os.path.dirname(pacote.__file__), "driver", "package", ".local-browsers")
    if glob.glob(os.path.join(base, "chromium*")):
        return Achado("funcao.computer_use", OK, "Chromium do Playwright instalado", base)
    return Achado("funcao.computer_use", AVISO, "Modo Computador sem navegador",
                  f"Nenhum Chromium do Playwright em {base}.", "playwright install chromium")


def _modo_ide() -> List[Achado]:
    from gemini_bridge import AGY_BIN, ANTIGRAVITY_BIN, WORKSPACE_DIR

    achados = []
    faltando = [nome for nome, caminho in (("agy", AGY_BIN), ("antigravity", ANTIGRAVITY_BIN))
                if not (os.path.isfile(caminho) and os.access(caminho, os.X_OK))]
    if not faltando:
        achados.append(Achado("funcao.modo_ide", OK, "CLI agy e IDE Antigravity encontradas", AGY_BIN))
    else:
        achados.append(Achado("funcao.modo_ide", AVISO, "Modo IDE incompleto",
                              "Não encontrado: " + ", ".join(faltando) + ".",
                              "Instale o Antigravity ou aponte AGY_BIN e ANTIGRAVITY_BIN no .env."))
    if not os.path.isdir(WORKSPACE_DIR):
        achados.append(Achado("funcao.workspace_ide", ERRO, "Pasta de trabalho do Modo IDE não existe",
                              WORKSPACE_DIR, "Corrija JARVIS_WORKSPACE_DIR no .env."))
    return achados


# ----------------- Integrações -----------------

def _plugins() -> List[Achado]:
    from plugin_manager import plugin_manager
    from policy_engine import policy_engine

    achados = []
    for plugin_id, erro in sorted(plugin_manager.erros_de_carga().items()):
        achados.append(Achado(f"plugins.{plugin_id}", ERRO, f"Plug-in '{plugin_id}' não carregou", erro,
                              "Corrija o plugin.json ou o módulo de entrada do plug-in."))
    sem_politica = [t.name for t in plugin_manager.get_active_tools() if policy_engine.get_risk_level(t.name) is None]
    if sem_politica:
        achados.append(Achado("plugins.politicas", AVISO, "Ferramentas de plug-in sem nível de risco",
                              "Bloqueadas pelo Policy Engine: " + ", ".join(sorted(sem_politica)),
                              "Declare risk_level em register_tool (READ, LOW_WRITE, EXTERNAL_WRITE ou PRIVILEGED)."))
    ativos = sum(1 for p in plugin_manager._plugins.values() if p.meta.enabled)
    achados.append(Achado("plugins.carregados", OK, f"{len(plugin_manager._plugins)} plug-in(s) carregado(s), {ativos} ativo(s)",
                          "Os simulados ficam desligados; JARVIS_ATIVAR_MOCKS=1 liga todos."))
    return achados


def _skills() -> List[Achado]:
    from adk_skill_loader import adk_skill_loader

    relatorio = adk_skill_loader.relatorio()
    problemas = [item for item in relatorio if not item.get("spec_compliant")]
    achados = [Achado(f"skills.{item['id']}", AVISO, f"Skill '{item['id']}' fora da especificação",
                      str(item.get("spec_compliance_error") or item.get("erro") or ""),
                      "Ajuste o SKILL.md (name igual à pasta, em kebab-case, e description preenchida).")
               for item in problemas]
    if not problemas:
        achados.append(Achado("skills.conformidade", OK, f"{len(relatorio)} skills conformes"))
    return achados


def _mcp() -> List[Achado]:
    from mcp_client_manager import mcp_client_manager

    servidores = mcp_client_manager.status()
    if not servidores:
        return [Achado("mcp.servidores", OK, "Nenhum servidor MCP externo configurado",
                       "", "Veja mcp_servers.example.json para conectar servidores.")]
    achados = []
    for srv in servidores:
        chave = f"mcp.{srv['nome']}"
        if srv.get("conectado") is False:
            achados.append(Achado(chave, AVISO, f"Servidor MCP '{srv['nome']}' não conectou", srv.get("erro") or "",
                                  "Confira command/args/url no mcp_servers.json e rode o servidor à mão para ver o erro."))
        elif srv.get("conectado") is None:
            achados.append(Achado(chave, AVISO, f"Servidor MCP '{srv['nome']}' ainda não verificado", "",
                                  "Rode python diagnostico.py sem --sem-mcp para testar a conexão."))
        else:
            achados.append(Achado(chave, OK, f"Servidor MCP '{srv['nome']}' conectado",
                                  f"{len(srv.get('ferramentas', []))} ferramenta(s)"))
        if srv.get("sem_politica"):
            achados.append(Achado(f"{chave}.politicas", AVISO, f"Ferramentas de '{srv['nome']}' sem nível de risco",
                                  "Bloqueadas: " + ", ".join(srv["sem_politica"]),
                                  "Declare policies ou default_risk_level no mcp_servers.json."))
    return achados


def _omniroute() -> List[Achado]:
    from provider_router import OmniRouteProvider, provider_router

    estado = OmniRouteProvider.check_status()
    if estado["online"]:
        return [Achado("provedores.omniroute", OK, "OmniRoute disponível como segundo provedor", estado["url"])]
    if provider_router.active_provider == "omniroute":
        return [Achado("provedores.omniroute", ERRO, "OmniRoute selecionado, mas offline", estado["url"],
                       "Inicie o OmniRoute ou volte para AI_PROVIDER=google_studio.")]
    return [Achado("provedores.omniroute", OK, "OmniRoute offline (opcional)",
                   f"{estado['url']}: sem ele, falhas do Gemini não têm segundo provedor.")]


def _armazenamento() -> List[Achado]:
    import gemini_bridge

    achados = []
    url = os.environ.get("SESSION_DB_URL", "sqlite+aiosqlite:///sessoes.db").strip()
    if url.lower() in ("", "memoria", "memory", "none"):
        achados.append(Achado("armazenamento.sessoes", AVISO, "Sessões só em memória",
                              "As conversas se perdem quando o servidor reinicia.",
                              "Remova SESSION_DB_URL=memoria para usar o sessoes.db."))
    elif url.startswith("sqlite"):
        caminho = url.split(":///", 1)[-1]
        pasta = os.path.dirname(os.path.abspath(caminho)) or "."
        if os.access(pasta, os.W_OK):
            achados.append(Achado("armazenamento.sessoes", OK, "Banco de sessões gravável", os.path.abspath(caminho)))
        else:
            achados.append(Achado("armazenamento.sessoes", ERRO, "Sem permissão para gravar o banco de sessões",
                                  pasta, "Ajuste SESSION_DB_URL ou as permissões da pasta."))
        if os.path.isfile(caminho) and versao_do_schema_de_sessoes(caminho) == "0":
            novo = os.path.splitext(caminho)[0] + "_v1.db"
            achados.append(Achado(
                "armazenamento.schema_sessoes", AVISO, "Banco de sessões no formato antigo do ADK (v0)",
                "O ADK 2.x ainda lê o schema v0, que serializa eventos com pickle, mas vai deixar de suportá-lo.",
                f"Com o servidor parado: adk migrate session --source_db_url sqlite:///{caminho} "
                f"--dest_db_url sqlite:///{novo} e depois troque o arquivo antigo pelo novo "
                "(use --allow-unsafe-unpickling só se o banco for seu e a migração pedir).",
            ))
    else:
        achados.append(Achado("armazenamento.sessoes", OK, "Sessões em banco externo", url.split("://", 1)[0]))
    if os.access(gemini_bridge.GEMINI_DIR, os.W_OK):
        achados.append(Achado("armazenamento.auditoria", OK, "Pasta gemini/ (auditoria e ponte da IDE) gravável"))
    else:
        achados.append(Achado("armazenamento.auditoria", AVISO, "Pasta gemini/ sem permissão de escrita",
                              gemini_bridge.GEMINI_DIR, "Ajuste as permissões da pasta gemini/ do projeto."))
    return achados


def versao_do_schema_de_sessoes(caminho: str) -> Optional[str]:
    """Schema de um banco de sessões SQLite do ADK: "1" (JSON), "0" (pickle legado) ou None (vazio)."""
    with contextlib.closing(sqlite3.connect(f"file:{os.path.abspath(caminho)}?mode=ro", uri=True)) as conexao:
        tabelas = {linha[0] for linha in conexao.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "adk_internal_metadata" in tabelas:
            linha = conexao.execute(
                "SELECT value FROM adk_internal_metadata WHERE \"key\" = 'schema_version'").fetchone()
            return str(linha[0]) if linha else None
        if "events" in tabelas:
            colunas = {coluna[1] for coluna in conexao.execute("PRAGMA table_info(events)")}
            if "actions" in colunas and "event_data" not in colunas:
                return "0"
    return None


VERIFICACOES: List[Callable[[], List[Achado]]] = [
    _dependencias, _configuracao, _seguranca, _funcoes_da_maquina, _plugins, _skills, _mcp, _omniroute,
    _armazenamento,
]


def executar_diagnostico() -> dict:
    """Roda todas as verificações; uma que quebre vira aviso, sem interromper as demais."""
    achados: List[Achado] = []
    for verificacao in VERIFICACOES:
        try:
            achados.extend(verificacao())
        except Exception as erro:
            nome = verificacao.__name__.strip("_")
            achados.append(Achado(f"diagnostico.{nome}", AVISO, f"Verificação '{nome}' falhou", str(erro)))
    resumo = {nivel: sum(1 for a in achados if a.nivel == nivel) for nivel in (OK, AVISO, ERRO)}
    return {"resumo": resumo, "achados": [asdict(a) for a in achados]}


def _verificar_servidores_mcp() -> None:
    """No terminal, conecta a cada servidor MCP configurado para testar de verdade."""
    import asyncio
    from mcp_client_manager import mcp_client_manager

    if mcp_client_manager.carregar_toolsets():
        asyncio.run(mcp_client_manager.descobrir_ferramentas(timeout_s=20.0))


def _imprimir(relatorio: dict) -> None:
    simbolos = {OK: "✔", AVISO: "⚠", ERRO: "✖"}
    print("\nDiagnóstico do J.A.R.V.I.S.\n")
    for achado in relatorio["achados"]:
        print(f" {simbolos[achado['nivel']]} {achado['titulo']}")
        if achado["detalhe"] and achado["nivel"] != OK:
            print(f"     {achado['detalhe']}")
        if achado["correcao"] and achado["nivel"] != OK:
            print(f"     → {achado['correcao']}")
    r = relatorio["resumo"]
    print(f"\n{r[OK]} ok, {r[AVISO]} aviso(s), {r[ERRO]} erro(s)\n")


def main(argv: List[str]) -> int:
    import logging
    import warnings
    # O relatório já explica cada problema; avisos de log e de bibliotecas só poluem a saída
    logging.disable(logging.WARNING)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if "--sem-mcp" not in argv:
                try:
                    _verificar_servidores_mcp()
                except Exception as erro:
                    print(f"Aviso: não foi possível testar os servidores MCP ({erro})", file=sys.stderr)
            relatorio = executar_diagnostico()
    finally:
        logging.disable(logging.NOTSET)
    if "--json" in argv:
        print(json.dumps(relatorio, ensure_ascii=False, indent=2))
    else:
        _imprimir(relatorio)
    return 1 if relatorio["resumo"][ERRO] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
