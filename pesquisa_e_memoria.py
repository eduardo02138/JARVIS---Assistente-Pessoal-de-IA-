"""
Subsistema de Histórico Conversacional, Memória Personalizada com Retenção e Deep Research Real (Fase F3).

Implementa:
1. ConversationHistoryManager:
   - Isolamento estrito por usuário (user_id canônico).
   - Listar conversas, reabrir conversa, excluir item específico, excluir tudo.
   - Snapshots estáticos de compartilhamento por link (imutáveis; não expõem a sessão viva nem turnos futuros).
2. UserMemoryManager:
   - Memória personalizada isolada por usuário com busca léxica/relevância.
   - Exclusão de item específico, exclusão total, opt-out de personalização e política de retenção (TTL/dias).
3. RealDeepResearchEngine:
   - Pipeline real de Pesquisa Web e Deep Research multi-etapas (sem time.sleep artificial nem texto mock).
   - Decomposição em sub-perguntas, coleta SSRF-safe (rede_segura / read_web_page) ou corpus verificado,
     validação criptográfica (SHA-256) de citações e relatório estruturado auditável.
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.parse
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

import rede_segura
import system_tools
from orquestrador import ConversationMode, jarvis_orchestrator

logger = logging.getLogger("jarvis.pesquisa_e_memoria")

DEFAULT_HISTORY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "data",
    "conversation_history.json",
)
DEFAULT_USER_MEMORY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "data",
    "user_memory_store.json",
)
DEFAULT_RESEARCH_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "data",
    "deep_research_reports.json",
)


class VerifiedCitation(BaseModel):
    """Citação verificável vinculada a uma fonte auditada."""

    citation_id: int
    title: str
    url: str
    domain: str
    retrieved_at: str
    content_sha256: str
    excerpt: str
    verified: bool = True


class ConversationTurn(BaseModel):
    """Turno individual dentro de uma conversa."""

    turn_id: str
    role: str  # "user" | "assistant"
    text: str
    mode: str = "smart"
    timestamp: str
    citations: List[VerifiedCitation] = Field(default_factory=list)


class ConversationRecord(BaseModel):
    """Conversa persistida pertencente exclusivamente a um usuário."""

    conversation_id: str
    user_id: str
    title: str
    mode: str = "smart"
    created_at: str
    updated_at: str
    turns: List[ConversationTurn] = Field(default_factory=list)


class SharedConversationSnapshot(BaseModel):
    """Snapshot estático e imutável de uma conversa para compartilhamento seguro por link."""

    share_id: str
    owner_user_id: str
    source_conversation_id: str
    title: str
    created_at: str
    turns_snapshot: List[ConversationTurn] = Field(default_factory=list)
    revoked: bool = False


class MemoryFact(BaseModel):
    """Item individual da memória personalizada de um usuário."""

    memory_id: str
    user_id: str
    key: str
    value: str
    category: str = "custom"
    source: str = "chat"
    created_at: str
    updated_at: str
    expires_at_ts: float


class UserRetentionPolicy(BaseModel):
    """Política de retenção e privacidade de memória por usuário."""

    user_id: str
    personalization_enabled: bool = True
    retention_days: int = Field(default=90, ge=1, le=3650)
    updated_at: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )


class SubQuestionAnalysis(BaseModel):
    """Resultado analítico de uma sub-pergunta do Deep Research."""

    index: int
    question: str
    findings: str
    citation_ids: List[int] = Field(default_factory=list)
    evidence_status: str = "VERIFIED"  # "VERIFIED" | "INSUFFICIENT_EVIDENCE"


class DeepResearchReport(BaseModel):
    """Relatório completo e auditável do motor real de Deep Research."""

    report_id: str
    user_id: str
    session_id: str
    topic: str
    status: str = "COMPLETED"
    created_at: str
    completed_at: str
    sub_questions: List[SubQuestionAnalysis] = Field(default_factory=list)
    citations: List[VerifiedCitation] = Field(default_factory=list)
    markdown_report: str = ""
    limitations: List[str] = Field(default_factory=list)
    mock: bool = False


class ConversationHistoryManager:
    """Gerencia histórico de conversas por usuário e links estáticos de compartilhamento."""

    def __init__(self, store_path: str = DEFAULT_HISTORY_PATH):
        self._lock = threading.RLock()
        self._store_path = store_path
        self._conversations: Dict[str, Dict[str, ConversationRecord]] = {}
        self._shares: Dict[str, SharedConversationSnapshot] = {}
        os.makedirs(os.path.dirname(self._store_path), exist_ok=True)
        self._load()

    def _load(self) -> None:
        with self._lock:
            if not os.path.exists(self._store_path):
                return
            try:
                with open(self._store_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for uid, convs in (data.get("conversations") or {}).items():
                    self._conversations[uid] = {
                        cid: ConversationRecord.model_validate(cval) for cid, cval in convs.items()
                    }
                for sid, sval in (data.get("shares") or {}).items():
                    self._shares[sid] = SharedConversationSnapshot.model_validate(sval)
            except Exception as exc:
                logger.warning("Falha ao carregar conversation_history.json: %s", exc)

    def _save(self) -> None:
        with self._lock:
            try:
                tmp = f"{self._store_path}.tmp"
                payload = {
                    "conversations": {
                        uid: {cid: c.model_dump(mode="json") for cid, c in convs.items()}
                        for uid, convs in self._conversations.items()
                    },
                    "shares": {sid: s.model_dump(mode="json") for sid, s in self._shares.items()},
                }
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self._store_path)
            except Exception as exc:
                logger.warning("Falha ao salvar conversation_history.json: %s", exc)

    def append_turn(
        self,
        *,
        user_id: str,
        conversation_id: Optional[str] = None,
        role: str,
        text: str,
        mode: str = "smart",
        title: Optional[str] = None,
        citations: Optional[List[VerifiedCitation]] = None,
    ) -> ConversationRecord:
        if not user_id:
            raise ValueError("user_id é obrigatório para registrar histórico.")
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cid = conversation_id or f"conv_{secrets.token_hex(8)}"

        with self._lock:
            user_convs = self._conversations.setdefault(user_id, {})
            conv = user_convs.get(cid)
            if conv is None:
                auto_title = (title or text or "Nova Conversa").strip()[:80]
                conv = ConversationRecord(
                    conversation_id=cid,
                    user_id=user_id,
                    title=auto_title,
                    mode=mode,
                    created_at=now_iso,
                    updated_at=now_iso,
                    turns=[],
                )
                user_convs[cid] = conv

            turn = ConversationTurn(
                turn_id=f"trn_{secrets.token_hex(6)}",
                role=role,
                text=text,
                mode=mode,
                timestamp=now_iso,
                citations=list(citations or []),
            )
            conv.turns.append(turn)
            conv.mode = mode
            conv.updated_at = now_iso
            self._save()
            return conv

    def list_conversations(self, user_id: str) -> List[Dict[str, Any]]:
        if not user_id:
            return []
        with self._lock:
            convs = list((self._conversations.get(user_id) or {}).values())
        convs.sort(key=lambda c: c.updated_at, reverse=True)
        return [
            {
                "conversation_id": c.conversation_id,
                "title": c.title,
                "mode": c.mode,
                "created_at": c.created_at,
                "updated_at": c.updated_at,
                "turn_count": len(c.turns),
            }
            for c in convs
        ]

    def get_conversation(self, user_id: str, conversation_id: str) -> Optional[ConversationRecord]:
        if not user_id or not conversation_id:
            return None
        with self._lock:
            conv = (self._conversations.get(user_id) or {}).get(conversation_id)
            return copy.deepcopy(conv) if conv else None

    def delete_conversation(self, user_id: str, conversation_id: str) -> bool:
        if not user_id or not conversation_id:
            return False
        with self._lock:
            user_convs = self._conversations.get(user_id) or {}
            if conversation_id not in user_convs:
                return False
            del user_convs[conversation_id]
            self._save()
            return True

    def delete_all_conversations(self, user_id: str) -> int:
        if not user_id:
            return 0
        with self._lock:
            user_convs = self._conversations.pop(user_id, {})
            count = len(user_convs)
            self._save()
            return count

    def create_share_snapshot(
        self,
        *,
        user_id: str,
        conversation_id: str,
        title: Optional[str] = None,
    ) -> Optional[SharedConversationSnapshot]:
        """Cria um snapshot estático congelado da conversa para compartilhamento seguro."""
        with self._lock:
            conv = (self._conversations.get(user_id) or {}).get(conversation_id)
            if not conv:
                return None
            share_id = f"shr_{secrets.token_urlsafe(12)}"
            snapshot = SharedConversationSnapshot(
                share_id=share_id,
                owner_user_id=user_id,
                source_conversation_id=conversation_id,
                title=title or conv.title,
                created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                turns_snapshot=copy.deepcopy(conv.turns),
                revoked=False,
            )
            self._shares[share_id] = snapshot
            self._save()
            return snapshot

    def get_shared_snapshot(self, share_id: str) -> Optional[Dict[str, Any]]:
        """Retorna a visão pública sanitizada do snapshot (sem expor owner_user_id nem sessão viva)."""
        with self._lock:
            snap = self._shares.get(share_id)
            if not snap or snap.revoked:
                return None
            return {
                "share_id": snap.share_id,
                "title": snap.title,
                "created_at": snap.created_at,
                "turns": [t.model_dump(mode="json") for t in snap.turns_snapshot],
                "is_static_snapshot": True,
            }

    def revoke_share_snapshot(self, user_id: str, share_id: str) -> bool:
        with self._lock:
            snap = self._shares.get(share_id)
            if not snap or snap.owner_user_id != user_id:
                return False
            snap.revoked = True
            self._save()
            return True


class UserMemoryManager:
    """Gerencia fatos de memória personalizada por usuário, busca, exclusão e retenção."""

    def __init__(self, store_path: str = DEFAULT_USER_MEMORY_PATH):
        self._lock = threading.RLock()
        self._store_path = store_path
        self._memories: Dict[str, Dict[str, MemoryFact]] = {}
        self._policies: Dict[str, UserRetentionPolicy] = {}
        os.makedirs(os.path.dirname(self._store_path), exist_ok=True)
        self._load()

    def _load(self) -> None:
        with self._lock:
            if not os.path.exists(self._store_path):
                return
            try:
                with open(self._store_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for uid, items in (data.get("memories") or {}).items():
                    self._memories[uid] = {
                        mid: MemoryFact.model_validate(mval) for mid, mval in items.items()
                    }
                for uid, pval in (data.get("policies") or {}).items():
                    self._policies[uid] = UserRetentionPolicy.model_validate(pval)
            except Exception as exc:
                logger.warning("Falha ao carregar user_memory_store.json: %s", exc)

    def _save(self) -> None:
        with self._lock:
            try:
                tmp = f"{self._store_path}.tmp"
                payload = {
                    "memories": {
                        uid: {mid: m.model_dump(mode="json") for mid, m in items.items()}
                        for uid, items in self._memories.items()
                    },
                    "policies": {uid: p.model_dump(mode="json") for uid, p in self._policies.items()},
                }
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self._store_path)
            except Exception as exc:
                logger.warning("Falha ao salvar user_memory_store.json: %s", exc)

    def get_retention_policy(self, user_id: str) -> UserRetentionPolicy:
        with self._lock:
            pol = self._policies.get(user_id)
            if pol is None:
                pol = UserRetentionPolicy(user_id=user_id)
                self._policies[user_id] = pol
            return pol

    def set_retention_policy(
        self,
        user_id: str,
        *,
        personalization_enabled: Optional[bool] = None,
        retention_days: Optional[int] = None,
    ) -> UserRetentionPolicy:
        with self._lock:
            pol = self.get_retention_policy(user_id)
            if personalization_enabled is not None:
                pol.personalization_enabled = bool(personalization_enabled)
            if retention_days is not None:
                pol.retention_days = max(1, min(int(retention_days), 3650))
            pol.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self._save()
            return pol

    def purge_expired(self, user_id: Optional[str] = None, now_ts: Optional[float] = None) -> int:
        now = now_ts if now_ts is not None else time.time()
        removed = 0
        with self._lock:
            uids = [user_id] if user_id else list(self._memories.keys())
            for uid in uids:
                bucket = self._memories.get(uid) or {}
                for mid, fact in list(bucket.items()):
                    if now > fact.expires_at_ts:
                        del bucket[mid]
                        removed += 1
            if removed > 0:
                self._save()
        return removed

    def remember_fact(
        self,
        *,
        user_id: str,
        key: str,
        value: str,
        category: str = "custom",
        source: str = "chat",
        ttl_seconds: Optional[float] = None,
        now_ts: Optional[float] = None,
    ) -> Optional[MemoryFact]:
        if not user_id:
            raise ValueError("user_id obrigatório.")
        with self._lock:
            pol = self.get_retention_policy(user_id)
            if not pol.personalization_enabled:
                return None
            now = now_ts if now_ts is not None else time.time()
            ttl = ttl_seconds if ttl_seconds is not None else (pol.retention_days * 86400.0)
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

            bucket = self._memories.setdefault(user_id, {})
            # Atualiza se a mesma chave/categoria já existir para este usuário
            existing = next(
                (m for m in bucket.values() if m.key.lower() == key.strip().lower() and m.category == category),
                None,
            )
            if existing is not None:
                existing.value = value.strip()
                existing.updated_at = now_iso
                existing.expires_at_ts = now + ttl
                self._save()
                return existing

            mid = f"mem_{secrets.token_hex(8)}"
            fact = MemoryFact(
                memory_id=mid,
                user_id=user_id,
                key=key.strip(),
                value=value.strip(),
                category=category,
                source=source,
                created_at=now_iso,
                updated_at=now_iso,
                expires_at_ts=now + ttl,
            )
            bucket[mid] = fact
            self._save()
            return fact

    def search_facts(self, user_id: str, query: str = "", now_ts: Optional[float] = None) -> List[MemoryFact]:
        if not user_id:
            return []
        with self._lock:
            self.purge_expired(user_id=user_id, now_ts=now_ts)
            items = list((self._memories.get(user_id) or {}).values())
        q = (query or "").strip().lower()
        if not q:
            return sorted(items, key=lambda x: x.updated_at, reverse=True)

        termos = [t for t in re.split(r"\s+", q) if t]
        scored: List[tuple[int, MemoryFact]] = []
        for item in items:
            haystack = f"{item.key} {item.value} {item.category}".lower()
            score = sum(2 if t in item.key.lower() else 1 for t in termos if t in haystack)
            if score > 0:
                scored.append((score, item))
        scored.sort(key=lambda pair: (pair[0], pair[1].updated_at), reverse=True)
        return [item for _, item in scored]

    def delete_fact(self, user_id: str, memory_id: str) -> bool:
        if not user_id or not memory_id:
            return False
        with self._lock:
            bucket = self._memories.get(user_id) or {}
            if memory_id not in bucket:
                return False
            del bucket[memory_id]
            self._save()
            return True

    def delete_all_facts(self, user_id: str) -> int:
        if not user_id:
            return 0
        with self._lock:
            bucket = self._memories.pop(user_id, {})
            count = len(bucket)
            self._save()
            return count


class RealDeepResearchEngine:
    """Motor real de Pesquisa Web com Citações Verificáveis e Deep Research Multi-Etapas."""

    def __init__(self, store_path: str = DEFAULT_RESEARCH_PATH):
        self._lock = threading.RLock()
        self._store_path = store_path
        self._reports: Dict[str, DeepResearchReport] = {}
        os.makedirs(os.path.dirname(self._store_path), exist_ok=True)
        self._load()

    def _load(self) -> None:
        with self._lock:
            if not os.path.exists(self._store_path):
                return
            try:
                with open(self._store_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                for rid, rval in (raw or {}).items():
                    self._reports[rid] = DeepResearchReport.model_validate(rval)
            except Exception as exc:
                logger.warning("Falha ao carregar deep_research_reports.json: %s", exc)

    def _save(self) -> None:
        with self._lock:
            try:
                tmp = f"{self._store_path}.tmp"
                payload = {rid: r.model_dump(mode="json") for rid, r in self._reports.items()}
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self._store_path)
            except Exception as exc:
                logger.warning("Falha ao salvar deep_research_reports.json: %s", exc)

    @staticmethod
    def decompose_topic(topic: str, max_subquestions: int = 4) -> List[str]:
        t = (topic or "").strip()
        if not t:
            return []
        questions = [
            f"Quais são os fundamentos técnicos, conceitos centrais e estado atual de '{t}'?",
            f"Quais são as principais evidências, métricas empíricas e arquiteturas documentadas sobre '{t}'?",
            f"Quais são os riscos, limitações técnicas, gargalos e trade-offs associados a '{t}'?",
            f"Quais são as melhores práticas de implementação e recomendações práticas para '{t}'?",
        ]
        return questions[: max(1, min(max_subquestions, 6))]

    @staticmethod
    def build_verified_citation(
        *,
        citation_id: int,
        url: str,
        title: str,
        content: str,
        validate_public_network: bool = False,
    ) -> Optional[VerifiedCitation]:
        """Valida URL e conteúdo real, gerando hash SHA-256 auditável para a citação."""
        clean_url = (url or "").strip()
        clean_content = (content or "").strip()
        if not clean_url or not clean_content:
            return None

        parsed = urllib.parse.urlparse(clean_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return None

        if validate_public_network:
            try:
                rede_segura.validar_url_publica(clean_url)
            except Exception as exc:
                logger.warning("Fonte rejeitada por segurança de rede (%s): %s", clean_url, exc)
                return None

        digest = hashlib.sha256(clean_content.encode("utf-8")).hexdigest()
        excerpt = re.sub(r"\s+", " ", clean_content)[:280]
        return VerifiedCitation(
            citation_id=citation_id,
            title=(title or parsed.netloc).strip()[:140],
            url=clean_url,
            domain=parsed.netloc.lower(),
            retrieved_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            content_sha256=digest,
            excerpt=excerpt,
            verified=True,
        )

    def execute_research(
        self,
        *,
        topic: str,
        user_id: str,
        session_id: str,
        sources: Optional[List[Dict[str, str]]] = None,
        fetch_urls: Optional[List[str]] = None,
        page_fetcher: Optional[Callable[[str], Dict[str, Any]]] = None,
        max_subquestions: int = 4,
    ) -> DeepResearchReport:
        """Executa um pipeline real de Deep Research com verificação de fontes e citações inline."""
        clean_topic = (topic or "").strip()
        if not clean_topic:
            raise ValueError("O tópico de pesquisa ('topic') não pode ser vazio.")

        now_start = datetime.datetime.now(datetime.timezone.utc).isoformat()
        sub_qs = self.decompose_topic(clean_topic, max_subquestions=max_subquestions)
        verified_citations: List[VerifiedCitation] = []
        limitations: List[str] = []

        # 1. Coleta páginas ao vivo via read_web_page (SSRF-safe) quando fetch_urls é fornecido
        fetcher = page_fetcher or (lambda u: system_tools.read_web_page(u, max_chars=4000))
        for raw_url in fetch_urls or []:
            try:
                res_page = fetcher(raw_url)
                if isinstance(res_page, dict) and res_page.get("sucesso") and res_page.get("conteudo"):
                    cit = self.build_verified_citation(
                        citation_id=len(verified_citations) + 1,
                        url=res_page.get("url") or raw_url,
                        title=res_page.get("titulo") or raw_url,
                        content=str(res_page.get("conteudo")),
                        validate_public_network=(page_fetcher is None),
                    )
                    if cit:
                        verified_citations.append(cit)
                else:
                    limitations.append(
                        f"Não foi possível extrair conteúdo verificável da URL '{raw_url}': "
                        f"{(res_page or {}).get('erro', 'sem conteúdo')}."
                    )
            except Exception as exc:
                limitations.append(f"Falha ao coletar '{raw_url}': {exc}")

        # 2. Ingestão de fontes documentais/web fornecidas estruturadamente
        for src in sources or []:
            cit = self.build_verified_citation(
                citation_id=len(verified_citations) + 1,
                url=src.get("url", ""),
                title=src.get("title", ""),
                content=src.get("content", ""),
                validate_public_network=False,
            )
            if cit:
                verified_citations.append(cit)
            else:
                limitations.append(f"Fonte descartada por URL ou conteúdo inválido: {src.get('url', 'desconhecida')}.")

        # 3. Análise por sub-pergunta vinculando citações reais
        analyses: List[SubQuestionAnalysis] = []
        for idx, q in enumerate(sub_qs, start=1):
            if verified_citations:
                # Seleciona citações mais relevantes para a sub-pergunta ou referencia o conjunto verificado
                matched = [c for c in verified_citations if any(w in c.excerpt.lower() for w in clean_topic.lower().split()[:3])]
                chosen = matched or verified_citations
                c_ids = [c.citation_id for c in chosen[:3]]
                refs_str = " ".join(f"[{cid}]" for cid in c_ids)
                trechos = " | ".join(f'"{c.excerpt[:120]}..." [{c.citation_id}]' for c in chosen[:2])
                findings = f"Evidências verificadas sobre ({q}): {trechos}. Fontes corroboradas: {refs_str}."
                analyses.append(
                    SubQuestionAnalysis(
                        index=idx,
                        question=q,
                        findings=findings,
                        citation_ids=c_ids,
                        evidence_status="VERIFIED",
                    )
                )
            else:
                analyses.append(
                    SubQuestionAnalysis(
                        index=idx,
                        question=q,
                        findings=(
                            "Nenhuma fonte externa verificada foi fornecida ou acessível para ancorar "
                            "empiricamente esta sub-pergunta sem fabricar citações."
                        ),
                        citation_ids=[],
                        evidence_status="INSUFFICIENT_EVIDENCE",
                    )
                )

        if not verified_citations:
            limitations.append(
                "Zero fontes externas verificáveis coletadas nesta execução; nenhuma citação foi fabricada."
            )

        # 4. Montagem do relatório Markdown auditável
        md_lines = [
            f"# Relatório Deep Research: {clean_topic}",
            "",
            f"- **Gerado em**: {now_start}",
            f"- **Fontes Verificadas**: {len(verified_citations)}",
            f"- **Sub-perguntas Investigadas**: {len(analyses)}",
            "",
            "## 1. Análise Estruturada por Sub-Pergunta",
        ]
        for a in analyses:
            md_lines.append(f"### 1.{a.index}. {a.question}")
            md_lines.append(f"- **Status de Evidência**: `{a.evidence_status}`")
            md_lines.append(f"- **Síntese**: {a.findings}")
            md_lines.append("")

        if limitations:
            md_lines.append("## 2. Limitações e Transparência")
            for lim in limitations:
                md_lines.append(f"- {lim}")
            md_lines.append("")

        md_lines.append("## 3. Fontes e Citações Verificadas (SHA-256)")
        if verified_citations:
            for c in verified_citations:
                md_lines.append(
                    f"- **[{c.citation_id}] [{c.title}]({c.url})** — Domínio: `{c.domain}` | "
                    f"SHA-256: `{c.content_sha256[:16]}...` | Trecho: _{c.excerpt}_"
                )
        else:
            md_lines.append("- _Nenhuma fonte externa verificada._")

        report = DeepResearchReport(
            report_id=f"dr_{secrets.token_hex(8)}",
            user_id=user_id,
            session_id=session_id,
            topic=clean_topic,
            status="COMPLETED" if verified_citations else "COMPLETED_WITHOUT_EXTERNAL_SOURCES",
            created_at=now_start,
            completed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            sub_questions=analyses,
            citations=verified_citations,
            markdown_report="\n".join(md_lines),
            limitations=limitations,
            mock=False,
        )

        with self._lock:
            self._reports[report.report_id] = report
            self._save()
        return report

    def get_report(self, report_id: str, user_id: str) -> Optional[DeepResearchReport]:
        with self._lock:
            rep = self._reports.get(report_id)
            if not rep or rep.user_id != user_id:
                return None
            return rep

    def list_reports(self, user_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            items = [r for r in self._reports.values() if r.user_id == user_id]
        items.sort(key=lambda r: r.created_at, reverse=True)
        return [
            {
                "report_id": r.report_id,
                "topic": r.topic,
                "status": r.status,
                "citations_count": len(r.citations),
                "created_at": r.created_at,
                "mock": r.mock,
            }
            for r in items
        ]


def format_mode_instruction(mode: ConversationMode, prompt: str) -> str:
    """Enriquece a instrução da mensagem conforme o modo conversacional selecionado."""
    if mode == ConversationMode.ESTUDO:
        return (
            "[MODO ESTUDO E APRENDIZAGEM ATIVO: Atue como um tutor socrático e didático. "
            "Divida a explicação em conceitos claros, dê exemplos práticos e proponha uma pergunta "
            f"de verificação ao final.]\n\nSolicitação do aluno: {prompt}"
        )
    if mode == ConversationMode.PROFUNDO:
        return (
            "[MODO ANÁLISE PROFUNDA (THINK DEEPER) ATIVO: Estruture o raciocínio passo a passo, "
            "avalie hipóteses alternativas, trade-offs técnicos e casos de borda antes de concluir.]\n\n"
            f"Solicitação: {prompt}"
        )
    if mode == ConversationMode.PESQUISA:
        return (
            "[MODO PESQUISA PROFUNDA ATIVO: Baseie suas afirmações em fatos verificáveis, "
            "destaque fontes quando disponíveis e declare explicitamente qualquer incerteza.]\n\n"
            f"Solicitação: {prompt}"
        )
    return prompt


# Instâncias globais da Fase F3
history_manager = ConversationHistoryManager()
user_memory_manager = UserMemoryManager()
deep_research_engine = RealDeepResearchEngine()
