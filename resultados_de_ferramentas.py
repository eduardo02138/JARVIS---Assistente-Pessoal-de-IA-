"""Resultados de ferramentas prontos para o modelo: serializáveis e com tamanho limitado.

Resultados grandes (a resposta completa do agente Antigravity, a biblioteca de jogos
inteira, a saída de um servidor MCP) iam sem corte para a sessão Live, o HUD e o log
de auditoria, gastando a janela de contexto e derrubando a sessão. Tipos que o JSON
não representa (set, bytes, datetime, Path) quebravam o envio da resposta.

O corte preserva a estrutura: textos longos e listas extensas são encurtados em
etapas, com marcadores do que foi omitido, e as chaves curtas (sucesso, mensagem,
erro) chegam intactas. O limite, em caracteres de JSON, vem de
JARVIS_LIMITE_RESULTADO_FERRAMENTA (padrão 16000, mínimo 2000).
"""

import dataclasses
import datetime
import enum
import json
import math
import os
import pathlib
from typing import Any, Optional

LIMITE_PADRAO = 16000
LIMITE_MINIMO = 2000
_PROFUNDIDADE_MAXIMA = 12
# (maior texto, maior lista) de cada etapa de redução
_ETAPAS_DE_REDUCAO = ((4000, 100), (1500, 40), (500, 15), (160, 5))
_CHAVES_ESSENCIAIS = ("sucesso", "status", "mensagem", "erro")


def limite_configurado() -> int:
    try:
        valor = int(os.environ.get("JARVIS_LIMITE_RESULTADO_FERRAMENTA", LIMITE_PADRAO))
    except ValueError:
        return LIMITE_PADRAO
    return max(LIMITE_MINIMO, valor)


def normalizar_resultado(valor: Any, _profundidade: int = 0) -> Any:
    """Converte o resultado em algo que o JSON representa, sem perder a estrutura."""
    if valor is None or isinstance(valor, (bool, int, str)):
        return valor
    if isinstance(valor, float):
        return valor if math.isfinite(valor) else str(valor)
    if _profundidade >= _PROFUNDIDADE_MAXIMA:
        return "[estrutura aninhada demais: omitida]"
    proximo = _profundidade + 1
    if isinstance(valor, dict):
        return {str(chave): normalizar_resultado(item, proximo) for chave, item in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [normalizar_resultado(item, proximo) for item in valor]
    if isinstance(valor, (set, frozenset)):
        itens = [normalizar_resultado(item, proximo) for item in valor]
        try:
            return sorted(itens)
        except TypeError:
            return itens
    if isinstance(valor, (bytes, bytearray, memoryview)):
        return f"[{len(valor)} bytes binários omitidos]"
    if isinstance(valor, enum.Enum):
        return normalizar_resultado(valor.value, proximo)
    if isinstance(valor, (datetime.datetime, datetime.date, datetime.time)):
        return valor.isoformat()
    if isinstance(valor, pathlib.PurePath):
        return str(valor)
    if dataclasses.is_dataclass(valor) and not isinstance(valor, type):
        return normalizar_resultado(dataclasses.asdict(valor), proximo)
    model_dump = getattr(valor, "model_dump", None)
    if callable(model_dump):
        try:
            return normalizar_resultado(model_dump(mode="json"), proximo)
        except Exception:
            pass
    return str(valor)


def tamanho_do_resultado(valor: Any) -> int:
    """Tamanho em caracteres do resultado serializado (o que o modelo recebe)."""
    return len(json.dumps(valor, ensure_ascii=False, default=str))


def tamanho_json_estrito(valor: Any) -> int:
    """Como tamanho_do_resultado, mas com TypeError se houver objeto que o JSON não representa."""
    return len(json.dumps(valor, ensure_ascii=False))


def _reduzir(valor: Any, maior_texto: int, maior_lista: int) -> Any:
    if isinstance(valor, str):
        if len(valor) > maior_texto:
            return valor[:maior_texto] + f" … [+{len(valor) - maior_texto} caracteres omitidos]"
        return valor
    if isinstance(valor, dict):
        return {chave: _reduzir(item, maior_texto, maior_lista) for chave, item in valor.items()}
    if isinstance(valor, list):
        itens = [_reduzir(item, maior_texto, maior_lista) for item in valor[:maior_lista]]
        if len(valor) > maior_lista:
            itens.append(f"… [+{len(valor) - maior_lista} itens omitidos]")
        return itens
    return valor


def _resumo_compacto(normalizado: Any, limite: int) -> dict:
    """Último recurso: as chaves essenciais mais um trecho do JSON original."""
    resumo: dict = {}
    if isinstance(normalizado, dict):
        for chave in _CHAVES_ESSENCIAIS:
            item = normalizado.get(chave)
            if isinstance(item, str):
                resumo[chave] = item[:300]
            elif item is None or isinstance(item, (bool, int, float)):
                if chave in normalizado:
                    resumo[chave] = item
    texto = json.dumps(normalizado, ensure_ascii=False)
    corte = max(0, limite - 400)
    while True:
        resumo["conteudo_parcial"] = texto[:corte] + " …"
        if corte == 0 or tamanho_do_resultado(resumo) + 80 <= limite:
            return resumo
        corte = int(corte * 0.7)


def limitar_resultado(resultado: Any, limite: Optional[int] = None) -> Any:
    """Resultado serializável que cabe no limite; os pequenos voltam só normalizados."""
    limite = limite or limite_configurado()
    normalizado = normalizar_resultado(resultado)
    tamanho_original = tamanho_do_resultado(normalizado)
    if tamanho_original <= limite:
        return normalizado
    reduzido = None
    for maior_texto, maior_lista in _ETAPAS_DE_REDUCAO:
        candidato = _reduzir(normalizado, maior_texto, maior_lista)
        if tamanho_do_resultado(candidato) + 80 <= limite:  # folga para os marcadores abaixo
            reduzido = candidato
            break
    if reduzido is None:
        reduzido = _resumo_compacto(normalizado, limite)
    if not isinstance(reduzido, dict):
        reduzido = {"resultado": reduzido}
    reduzido["resultado_truncado"] = True
    reduzido["tamanho_original"] = tamanho_original
    return reduzido
