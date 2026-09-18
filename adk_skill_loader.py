"""
ADK Skill Loader do ecossistema J.A.R.V.I.S.

Carrega os SKILL.md declarados pelos plug-ins em plugins/<id>/ como Skills ADK
(camadas L1 frontmatter, L2 instruções e L3 recursos do modelo google.adk.skills).

Conformidade: cada plug-in declara um SKILL.md com cabecalho YAML (name kebab-case,
description) e corpo Markdown. Recursos opcionais vivem em references/ e assets/,
lidos sob demanda e injetados no modelo Skill.

Limitação conhecida: `load_skill_from_dir` exige que o nome do diretório coincida
exatamente com o `name:` kebab-case do frontmatter. Os plug-ins usam IDs com
underscore (game_companion), então o loader tenta a API oficial primeiro e recai
para um parser local equivalente que preserva o modelo Skill do ADK.
"""

import os
import glob
import logging

from typing import Optional

logger = logging.getLogger("jarvis.adk_skill_loader")

try:
    from google.adk.skills import load_skill_from_dir
    from google.adk.skills.models import Frontmatter, Resources, Skill, Script
    ADK_DISPONIVEL = True
except Exception:  # google-adk ausente no ambiente
    load_skill_from_dir = None
    Frontmatter = Resources = Skill = Script = None
    ADK_DISPONIVEL = False

try:
    import yaml
except Exception:  # PyYAML ausente
    yaml = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGINS_DIR = os.path.join(BASE_DIR, "plugins")
SKILLS_DIR = os.path.join(BASE_DIR, "skills")

# subdiretórios da camada L3
L3_DIRS = ("references", "assets", "scripts")

import re
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def validar_conformidade_spec(dir_skill: str, frontmatter: Optional[Frontmatter]) -> tuple[bool, Optional[str]]:
    """Valida estritamente a conformidade com a especificação oficial de Agent Skills."""
    if frontmatter is None:
        return False, "Frontmatter ausente ou inválido."
    dir_name = os.path.basename(os.path.abspath(dir_skill))
    name = frontmatter.name or ""
    desc = frontmatter.description or ""

    if dir_name != name:
        return False, f"Nome do diretório '{dir_name}' difere do name '{name}' no frontmatter."
    if not (1 <= len(name) <= 64):
        return False, f"Nome da skill tem tamanho inválido ({len(name)} chars; permitido 1 a 64)."
    if not SKILL_NAME_PATTERN.match(name):
        return False, f"Nome da skill '{name}' não é kebab-case minúsculo válido (sem --, sem underscore)."
    if not (1 <= len(desc) <= 1024):
        return False, f"Descrição tem tamanho inválido ({len(desc)} chars; permitido 1 a 1024)."
    return True, None


def _ler_recursos(dir_skill: str) -> Resources:
    """Monta a camada L3 (references/assets/scripts) a partir do diretório da skill."""
    if Script is None:
        return Resources()
    refs, assets, scripts = {}, {}, {}
    for sub in ("references", "assets"):
        pasta = os.path.join(dir_skill, sub)
        if os.path.isdir(pasta):
            for arquivo in sorted(os.listdir(pasta)):
                caminho = os.path.join(pasta, arquivo)
                if os.path.isfile(caminho):
                    try:
                        with open(caminho, encoding="utf-8") as f:
                            conteudo = f.read()
                    except OSError as e:
                        logger.warning("L3 %s ilegível em %s: %s", sub, caminho, e)
                        continue
                    chave = f"{sub}/{arquivo}"
                    if sub == "references":
                        refs[chave] = conteudo
                    else:
                        assets[chave] = conteudo
    pasta_scripts = os.path.join(dir_skill, "scripts")
    if os.path.isdir(pasta_scripts):
        for arquivo in sorted(os.listdir(pasta_scripts)):
            caminho = os.path.join(pasta_scripts, arquivo)
            if os.path.isfile(caminho):
                scripts[arquivo] = Script(src=caminho)
    return Resources(references=refs, assets=assets, scripts=scripts)


def _parsear_skill_local(dir_skill: str) -> Skill:
    """Parser fallback: SKILL.md de contingência."""
    caminho = os.path.join(dir_skill, "SKILL.md")
    with open(caminho, encoding="utf-8") as f:
        texto = f.read()

    if not texto.startswith("---"):
        raise ValueError("SKILL.md sem frontmatter YAML (---).")
    _, bloco, corpo = texto.split("---", 2)

    if yaml is None:
        raise ValueError("PyYAML não instalado para ler frontmatter do SKILL.md.")
    dados = yaml.safe_load(bloco) or {}

    nome = dados.get("name", "")
    descricao = dados.get("description", "")
    if not nome or not descricao:
        raise ValueError("Frontmatter incompleto: campos name e description são obrigatórios.")
    if not any(c.isalpha() for c in nome):
        raise ValueError(f"name inválido no frontmatter: '{nome}'.")

    frontmatter = Frontmatter(
        name=nome,
        description=descricao,
        license=dados.get("license"),
        compatibility=dados.get("compatibility"),
        allowed_tools=dados.get("allowed_tools"),
        metadata=(dados.get("metadata") or {}),
    )
    return Skill(
        frontmatter=frontmatter,
        instructions=corpo.strip(),
        resources=_ler_recursos(dir_skill),
    )


class ADKSkillLoader:
    """Carrega e expõe as Skills declaradas no ecossistema J.A.R.V.I.S.

    Conformidade oficial:
    - O diretório canônico é skills/<skill-name>/ onde o nome coincide com o name: kebab-case.
    - O carregador usa google.adk.skills.load_skill_from_dir oficial com verificação estrita.
    """

    def __init__(self, dir_plugin: Optional[str] = None):
        self.dir_plugin = dir_plugin or (SKILLS_DIR if os.path.isdir(SKILLS_DIR) else PLUGINS_DIR)
        self._skills: dict[str, Skill] = {}
        self._dirs: dict[str, str] = {}
        self._erros: dict[str, str] = {}
        self._origem: dict[str, str] = {}
        self.carregar()

    def carregar(self) -> None:
        self._skills.clear()
        self._dirs.clear()
        self._erros.clear()
        self._origem.clear()
        if not ADK_DISPONIVEL:
            logger.warning("google-adk ausente: loader de skills inativo.")
            return

        padrao_busca = os.path.join(self.dir_plugin, "*", "SKILL.md")
        arquivos_skill = sorted(glob.glob(padrao_busca))

        # Se não encontrou no diretório alvo e skills/ existe, busca em skills/
        if not arquivos_skill and os.path.isdir(SKILLS_DIR) and self.dir_plugin != SKILLS_DIR:
            arquivos_skill = sorted(glob.glob(os.path.join(SKILLS_DIR, "*", "SKILL.md")))

        # Fallback de busca em plugins/
        if not arquivos_skill and os.path.isdir(PLUGINS_DIR) and self.dir_plugin != PLUGINS_DIR:
            arquivos_skill = sorted(glob.glob(os.path.join(PLUGINS_DIR, "*", "SKILL.md")))

        for skill_md in arquivos_skill:
            dir_skill = os.path.dirname(skill_md)
            id_dir = os.path.basename(dir_skill)
            try:
                if load_skill_from_dir is not None:
                    try:
                        skill = load_skill_from_dir(dir_skill)
                        origem = "adk_oficial"
                    except ValueError:
                        skill = _parsear_skill_local(dir_skill)
                        origem = "parser_local"
                else:
                    skill = _parsear_skill_local(dir_skill)
                    origem = "parser_local"

                # Chave canônica primária: id do diretório
                self._skills[id_dir] = skill
                self._dirs[id_dir] = dir_skill
                self._origem[id_dir] = origem

                # Registra alias secundário para compatibilidade entre underscore e hyphen
                alias = id_dir.replace("-", "_") if "-" in id_dir else id_dir.replace("_", "-")
                if alias not in self._skills:
                    self._skills[alias] = skill
                    self._dirs[alias] = dir_skill
                    self._origem[alias] = origem

            except Exception as e:
                self._erros[id_dir] = str(e)
                logger.warning("Skill '%s' não carregada: %s", id_dir, e)

    def get_skill(self, id_plugin: str) -> Optional[Skill]:
        return (
            self._skills.get(id_plugin)
            or self._skills.get(id_plugin.replace("_", "-"))
            or self._skills.get(id_plugin.replace("-", "_"))
        )

    def skills_carregadas(self) -> dict[str, Skill]:
        return dict(self._skills)

    def ids_carregados(self) -> list[str]:
        return sorted(self._skills)

    def recursos(self, id_plugin: str) -> dict:
        skill = self.get_skill(id_plugin)
        if not skill:
            return {}
        return {
            "references": list(skill.resources.references.keys()),
            "assets": list(skill.resources.assets.keys()),
            "scripts": list(skill.resources.scripts.keys()),
        }

    def ids_ativos(self) -> list[str]:
        try:
            from plugin_manager import plugin_manager
            ativos = [p for p in plugin_manager._plugins.values() if p.meta.enabled]
            res = set()
            for p in ativos:
                res.add(p.meta.id)
                res.add(p.meta.id.replace("_", "-"))
                res.add(p.meta.id.replace("-", "_"))
            return sorted(res)
        except Exception:
            return sorted(self._skills)

    def bloco_de_instrucoes(self, apenas_ativas: bool = True) -> str:
        """Instrução agregada das skills (L2), usada para injetar contexto no prompt."""
        alvo = set(self._skills)
        if apenas_ativas:
            alvo &= set(self.ids_ativos())
        # Evita duplicações causadas por aliases
        skills_unicas = {}
        for k in sorted(alvo):
            sk = self._skills[k]
            skills_unicas[sk.frontmatter.name] = sk

        blocos = []
        for name in sorted(skills_unicas):
            skill = skills_unicas[name]
            blocos.append(
                f"--- SKILL {name} ---\n"
                f"{skill.instructions.strip()}"
            )
        if not blocos:
            return ""
        return "\n\n" + "\n\n".join(blocos) + "\n"

    def relatorio(self) -> list[dict]:
        saida = []
        # Exibe skills canônicas sem duplicar aliases no relatório
        skills_unicas = {}
        for k, skill in sorted(self._skills.items()):
            name = skill.frontmatter.name
            if name not in skills_unicas:
                skills_unicas[name] = (k, skill)

        for name, (id_plugin, skill) in sorted(skills_unicas.items()):
            dir_skill = self._dirs.get(id_plugin, "")
            is_compliant, motivo = validar_conformidade_spec(dir_skill, skill.frontmatter)
            saida.append({
                "id": skill.frontmatter.name,
                "skill_name": skill.frontmatter.name,
                "descricao": skill.frontmatter.description,
                "ativo": id_plugin in self.ids_ativos(),
                "origem_carga": self._origem.get(id_plugin),
                "l1_frontmatter_ok": True,
                "spec_compliant": is_compliant,
                "spec_compliance_error": motivo,
                "l2_instrucoes_chars": len(skill.instructions),
                "l3_recursos": self.recursos(id_plugin),
            })
        for id_plugin, erro in sorted(self._erros.items()):
            saida.append({
                "id": id_plugin,
                "skill_name": None,
                "ativo": False,
                "origem_carga": None,
                "l1_frontmatter_ok": False,
                "spec_compliant": False,
                "spec_compliance_error": erro,
                "erro": erro,
            })
        return saida


adk_skill_loader = ADKSkillLoader()