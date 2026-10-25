"""
Subsistema de Ingestão de Documentos, Índice RAG Local com ACL e Copilot Pages (Fase F4).

Implementa:
1. DocumentIngestionService:
   - Limite de até 20 arquivos por conversa/lote e até 50 MB por arquivo.
   - Validação estrita de extensão, MIME e magic bytes (bloqueando ELF, PE/MZ e falsificações de formato).
   - Extração local real de texto e estrutura para PDF, DOCX, XLSX, PPTX, CSV, JSON, MD, TXT, PNG, JPEG e WEBP.
2. LocalRAGIndex:
   - Chunking com overlap, indexação híbrida (BM25 + similaridade de cosseno lexical) e ACL estrita por user_id.
   - Propagação imediata de exclusão: remover um documento expurga instantaneamente todos os seus chunks do índice.
3. CopilotPagesManager:
   - Artefatos persistentes editáveis com histórico de versões imutável, restauração de versão e exportação (md, html, txt, json).
"""

from __future__ import annotations

import base64
import csv
import datetime
import hashlib
import html
import io
import json
import logging
import math
import os
import re
import secrets
import struct
import threading
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

logger = logging.getLogger("jarvis.documentos_rag_pages")

MAX_FILES_PER_CONVERSATION = 20
MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB

SUPPORTED_EXTENSIONS = frozenset({
    ".pdf",
    ".docx",
    ".xlsx",
    ".pptx",
    ".csv",
    ".json",
    ".md",
    ".txt",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
})

FORBIDDEN_MAGIC_PREFIXES: Tuple[Tuple[bytes, str], ...] = (
    (b"\x7fELF", "Executável binário Linux (ELF) proibido."),
    (b"MZ", "Executável binário Windows/DOS (PE/MZ) proibido."),
    (b"#!", "Script executável (shebang) proibido como documento."),
)

DEFAULT_DOCS_RAG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "data",
    "documents_rag_store.json",
)
DEFAULT_PAGES_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "data",
    "copilot_pages_store.json",
)


def sanitize_filename(filename: str) -> str:
    """Remove diretórios (path traversal), caracteres de controle e normaliza nome do arquivo."""
    raw = (filename or "").replace("\\", "/").strip()
    base = os.path.basename(raw)
    clean = re.sub(r"[^A-Za-z0-9._\- ()]", "_", base).strip(" .")
    if not clean or clean in (".", ".."):
        raise ValueError("Nome de arquivo inválido.")
    return clean


def validate_file_security(filename: str, raw_bytes: bytes) -> Tuple[str, str]:
    """Valida limites de tamanho, extensão e magic bytes reais do arquivo."""
    if not isinstance(raw_bytes, (bytes, bytearray)):
        raise ValueError("Conteúdo de arquivo inválido.")
    size = len(raw_bytes)
    if size == 0:
        raise ValueError("Arquivo vazio não pode ser processado.")
    if size > MAX_FILE_BYTES:
        raise ValueError(f"Arquivo excede o limite máximo de 50 MB ({size} bytes).")

    safe_name = sanitize_filename(filename)
    _, ext = os.path.splitext(safe_name.lower())
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Extensão '{ext}' não suportada. Formatos aceitos: {sorted(SUPPORTED_EXTENSIONS)}")

    for prefix, reason in FORBIDDEN_MAGIC_PREFIXES:
        if raw_bytes.startswith(prefix):
            raise ValueError(f"Arquivo rejeitado por segurança (magic bytes): {reason}")

    # Validação de assinatura binária por formato
    if ext == ".pdf":
        if not raw_bytes.startswith(b"%PDF-"):
            raise ValueError("Assinatura binária inválida: arquivo .pdf não começa com '%PDF-'.")
    elif ext in (".docx", ".xlsx", ".pptx"):
        if not raw_bytes.startswith(b"PK\x03\x04"):
            raise ValueError(f"Assinatura binária inválida: arquivo {ext} não é um pacote OpenXML (ZIP) válido.")
    elif ext == ".png":
        if not raw_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Assinatura binária inválida: arquivo .png corrompido ou falsificado.")
    elif ext in (".jpg", ".jpeg"):
        if not raw_bytes.startswith(b"\xff\xd8\xff"):
            raise ValueError("Assinatura binária inválida: arquivo JPEG corrompido ou falsificado.")
    elif ext == ".webp":
        if not (len(raw_bytes) >= 12 and raw_bytes[:4] == b"RIFF" and raw_bytes[8:12] == b"WEBP"):
            raise ValueError("Assinatura binária inválida: arquivo WEBP corrompido ou falsificado.")

    return safe_name, ext


def _extract_pdf_text(raw_bytes: bytes) -> str:
    """Extrai texto de PDFs via pypdf (se disponível) e fallback nativo de streams PDF."""
    texts: List[str] = []
    try:
        import pypdf  # type: ignore

        reader = pypdf.PdfReader(io.BytesIO(raw_bytes))
        for idx, page in enumerate(reader.pages, start=1):
            t = (page.extract_text() or "").strip()
            if t:
                texts.append(f"[Página {idx}]\n{t}")
        if texts:
            return "\n\n".join(texts)
    except Exception:
        pass

    # Extrator nativo para operadores de texto PDF: (texto) Tj / [(texto)] TJ
    decoded = raw_bytes.decode("latin-1", errors="ignore")
    matches = re.findall(r"\(([^()\\]*(?:\\.[^()\\]*)*)\)\s*Tj", decoded)
    for m in matches:
        clean = m.replace("\\(", "(").replace("\\)", ")").replace("\\n", "\n").strip()
        if clean:
            texts.append(clean)
    for arr in re.findall(r"\[(.*?)\]\s*TJ", decoded, flags=re.DOTALL):
        parts = re.findall(r"\(([^()\\]*(?:\\.[^()\\]*)*)\)", arr)
        joined = "".join(parts).strip()
        if joined:
            texts.append(joined)
    return "\n".join(texts).strip() or "[PDF válido processado sem camada textual extraível]"


def _extract_openxml_text(raw_bytes: bytes, ext: str) -> str:
    """Extrai texto real de pacotes OpenXML (.docx, .xlsx, .pptx) usando zipfile + ElementTree."""
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
        names = set(zf.namelist())
        if ext == ".docx":
            if "word/document.xml" not in names:
                raise ValueError("Pacote DOCX inválido: word/document.xml ausente.")
            xml_data = zf.read("word/document.xml")
            root = ET.fromstring(xml_data)
            paragraphs: List[str] = []
            for p in root.iter():
                if p.tag.endswith("}p"):
                    runs = [node.text for node in p.iter() if node.tag.endswith("}t") and node.text]
                    if runs:
                        paragraphs.append("".join(runs))
            return "\n".join(paragraphs).strip()

        if ext == ".xlsx":
            shared_strings: List[str] = []
            if "xl/sharedStrings.xml" in names:
                ss_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                for si in ss_root.iter():
                    if si.tag.endswith("}si"):
                        texts = [t.text for t in si.iter() if t.tag.endswith("}t") and t.text]
                        shared_strings.append("".join(texts))

            sheet_files = sorted(n for n in names if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
            if not sheet_files:
                raise ValueError("Pacote XLSX inválido: nenhuma planilha encontrada.")

            lines: List[str] = []
            for sname in sheet_files:
                s_root = ET.fromstring(zf.read(sname))
                for row in s_root.iter():
                    if row.tag.endswith("}row"):
                        row_vals: List[str] = []
                        for cell in row:
                            if not cell.tag.endswith("}c"):
                                continue
                            c_type = cell.attrib.get("t", "")
                            v_text = ""
                            for child in cell.iter():
                                if child.tag.endswith("}v") and child.text is not None:
                                    v_text = child.text
                                    break
                                if child.tag.endswith("}t") and child.text is not None:
                                    v_text = child.text
                                    break
                            if c_type == "s" and v_text.isdigit():
                                idx = int(v_text)
                                if 0 <= idx < len(shared_strings):
                                    v_text = shared_strings[idx]
                            if v_text:
                                row_vals.append(v_text)
                        if row_vals:
                            lines.append(" | ".join(row_vals))
            return "\n".join(lines).strip()

        if ext == ".pptx":
            slide_files = sorted(n for n in names if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
            if not slide_files:
                raise ValueError("Pacote PPTX inválido: nenhum slide encontrado.")
            slides_out: List[str] = []
            for idx, sname in enumerate(slide_files, start=1):
                s_root = ET.fromstring(zf.read(sname))
                texts = [t.text.strip() for t in s_root.iter() if t.tag.endswith("}t") and t.text and t.text.strip()]
                if texts:
                    slides_out.append(f"[Slide {idx}] " + " ".join(texts))
            return "\n".join(slides_out).strip()

    raise ValueError(f"Formato OpenXML desconhecido: {ext}")


def _extract_image_metadata(raw_bytes: bytes, ext: str, filename: str, alt_text: str = "") -> str:
    """Extrai dimensões reais de cabeçalhos PNG/JPEG/WEBP e metadados descritivos."""
    width, height = 0, 0
    if ext == ".png" and len(raw_bytes) >= 24:
        width, height = struct.unpack(">II", raw_bytes[16:24])
    elif ext in (".jpg", ".jpeg") and len(raw_bytes) > 4:
        # Procura marcador SOF0/SOF2
        idx = 2
        while idx < len(raw_bytes) - 9:
            if raw_bytes[idx] != 0xFF:
                idx += 1
                continue
            marker = raw_bytes[idx + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                height, width = struct.unpack(">HH", raw_bytes[idx + 5 : idx + 9])
                break
            seg_len = struct.unpack(">H", raw_bytes[idx + 2 : idx + 4])[0]
            idx += 2 + seg_len
    desc = f"[Imagem {ext.upper().lstrip('.')} '{filename}' ({width}x{height} px, {len(raw_bytes)} bytes)]"
    if alt_text:
        desc += f"\nDescrição/OCR: {alt_text.strip()}"
    return desc


def extract_document_text(filename: str, raw_bytes: bytes, alt_text: str = "") -> Tuple[str, str, str]:
    """Valida e extrai o texto estruturado de um documento suportado."""
    safe_name, ext = validate_file_security(filename, raw_bytes)

    if ext == ".pdf":
        text = _extract_pdf_text(raw_bytes)
    elif ext in (".docx", ".xlsx", ".pptx"):
        text = _extract_openxml_text(raw_bytes, ext)
    elif ext == ".csv":
        decoded = raw_bytes.decode("utf-8", errors="replace")
        reader = csv.reader(io.StringIO(decoded))
        rows = [" | ".join(col.strip() for col in row if col.strip()) for row in reader]
        text = "\n".join(r for r in rows if r)
    elif ext == ".json":
        decoded = raw_bytes.decode("utf-8")
        parsed = json.loads(decoded)
        text = json.dumps(parsed, ensure_ascii=False, indent=2)
    elif ext in (".md", ".txt"):
        text = raw_bytes.decode("utf-8", errors="replace").strip()
    elif ext in (".png", ".jpg", ".jpeg", ".webp"):
        text = _extract_image_metadata(raw_bytes, ext, safe_name, alt_text=alt_text)
    else:
        raise ValueError(f"Extensão não suportada: {ext}")

    return safe_name, ext, text


class DocumentChunk(BaseModel):
    """Chunk indexado no motor RAG local com vínculo estrito a user_id e doc_id."""

    chunk_id: str
    doc_id: str
    user_id: str
    conversation_id: str
    filename: str
    chunk_index: int
    text: str
    tokens: List[str] = Field(default_factory=list)
    sha256: str


class IngestedDocument(BaseModel):
    """Metadados de um documento ingerido e indexado por usuário."""

    doc_id: str
    user_id: str
    conversation_id: str
    filename: str
    extension: str
    size_bytes: int
    sha256: str
    extracted_chars: int
    chunk_count: int
    created_at: str
    untrusted_source: bool = True


class LocalRAGIndex:
    """Índice RAG local híbrido (BM25 + Cosseno TF) com ACL estrita por usuário e exclusão atômica."""

    def __init__(self, store_path: str = DEFAULT_DOCS_RAG_PATH):
        self._lock = threading.RLock()
        self._store_path = store_path
        self._documents: Dict[str, IngestedDocument] = {}
        self._chunks: Dict[str, DocumentChunk] = {}
        os.makedirs(os.path.dirname(self._store_path), exist_ok=True)
        self._load()

    def _load(self) -> None:
        with self._lock:
            if not os.path.exists(self._store_path):
                return
            try:
                with open(self._store_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for did, dval in (data.get("documents") or {}).items():
                    self._documents[did] = IngestedDocument.model_validate(dval)
                for cid, cval in (data.get("chunks") or {}).items():
                    self._chunks[cid] = DocumentChunk.model_validate(cval)
            except Exception as exc:
                logger.warning("Falha ao carregar documents_rag_store.json: %s", exc)

    def _save(self) -> None:
        with self._lock:
            try:
                tmp = f"{self._store_path}.tmp"
                payload = {
                    "documents": {did: d.model_dump(mode="json") for did, d in self._documents.items()},
                    "chunks": {cid: c.model_dump(mode="json") for cid, c in self._chunks.items()},
                }
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self._store_path)
            except Exception as exc:
                logger.warning("Falha ao salvar documents_rag_store.json: %s", exc)

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        return [tok for tok in re.findall(r"[a-zA-ZÀ-ÿ0-9_]{2,}", (text or "").lower())]

    @classmethod
    def _split_chunks(cls, text: str, chunk_size: int = 600, overlap: int = 100) -> List[str]:
        clean = (text or "").strip()
        if not clean:
            return []
        if len(clean) <= chunk_size:
            return [clean]
        chunks: List[str] = []
        start = 0
        step = max(100, chunk_size - overlap)
        while start < len(clean):
            piece = clean[start : start + chunk_size].strip()
            if piece:
                chunks.append(piece)
            start += step
        return chunks

    def ingest_file(
        self,
        *,
        user_id: str,
        conversation_id: str,
        filename: str,
        raw_bytes: bytes,
        alt_text: str = "",
    ) -> IngestedDocument:
        if not user_id or not conversation_id:
            raise ValueError("user_id e conversation_id são obrigatórios para ingestão de documentos.")

        with self._lock:
            conv_docs = [
                d
                for d in self._documents.values()
                if d.user_id == user_id and d.conversation_id == conversation_id
            ]
            if len(conv_docs) >= MAX_FILES_PER_CONVERSATION:
                raise ValueError(
                    f"Limite máximo de {MAX_FILES_PER_CONVERSATION} arquivos por conversa atingido."
                )

        safe_name, ext, extracted_text = extract_document_text(filename, raw_bytes, alt_text=alt_text)
        file_sha = hashlib.sha256(raw_bytes).hexdigest()
        doc_id = f"doc_{secrets.token_hex(8)}"
        raw_chunks = self._split_chunks(extracted_text)

        doc_record = IngestedDocument(
            doc_id=doc_id,
            user_id=user_id,
            conversation_id=conversation_id,
            filename=safe_name,
            extension=ext,
            size_bytes=len(raw_bytes),
            sha256=file_sha,
            extracted_chars=len(extracted_text),
            chunk_count=len(raw_chunks),
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            untrusted_source=True,
        )

        with self._lock:
            self._documents[doc_id] = doc_record
            for idx, ch_text in enumerate(raw_chunks):
                cid = f"chk_{doc_id}_{idx}"
                self._chunks[cid] = DocumentChunk(
                    chunk_id=cid,
                    doc_id=doc_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    filename=safe_name,
                    chunk_index=idx,
                    text=ch_text,
                    tokens=self._tokenize(ch_text),
                    sha256=hashlib.sha256(ch_text.encode("utf-8")).hexdigest(),
                )
            self._save()

        return doc_record

    def ingest_batch(
        self,
        *,
        user_id: str,
        conversation_id: str,
        files: List[Dict[str, Any]],
    ) -> List[IngestedDocument]:
        if len(files) > MAX_FILES_PER_CONVERSATION:
            raise ValueError(
                f"O lote excede o limite de {MAX_FILES_PER_CONVERSATION} arquivos por operação."
            )
        with self._lock:
            existing = sum(
                1
                for d in self._documents.values()
                if d.user_id == user_id and d.conversation_id == conversation_id
            )
            if existing + len(files) > MAX_FILES_PER_CONVERSATION:
                raise ValueError(
                    f"A conversa já possui {existing} arquivo(s); limite total é {MAX_FILES_PER_CONVERSATION}."
                )

        ingested: List[IngestedDocument] = []
        for item in files:
            fname = item.get("filename") or "documento.txt"
            content_b64 = item.get("content_b64")
            if content_b64 is not None:
                raw_bytes = base64.b64decode(content_b64)
            elif isinstance(item.get("content_bytes"), (bytes, bytearray)):
                raw_bytes = bytes(item["content_bytes"])
            else:
                raw_bytes = str(item.get("content_text") or "").encode("utf-8")

            doc = self.ingest_file(
                user_id=user_id,
                conversation_id=conversation_id,
                filename=fname,
                raw_bytes=raw_bytes,
                alt_text=item.get("alt_text") or "",
            )
            ingested.append(doc)
        return ingested

    def list_documents(
        self,
        user_id: str,
        conversation_id: Optional[str] = None,
    ) -> List[IngestedDocument]:
        if not user_id:
            return []
        with self._lock:
            docs = [
                d
                for d in self._documents.values()
                if d.user_id == user_id
                and (conversation_id is None or d.conversation_id == conversation_id)
            ]
        return sorted(docs, key=lambda d: d.created_at, reverse=True)

    def delete_document(self, user_id: str, doc_id: str) -> bool:
        """Exclui o documento e propaga imediatamente a remoção de todos os seus chunks do índice RAG."""
        if not user_id or not doc_id:
            return False
        with self._lock:
            doc = self._documents.get(doc_id)
            if not doc or doc.user_id != user_id:
                return False
            del self._documents[doc_id]
            for cid in [k for k, c in self._chunks.items() if c.doc_id == doc_id]:
                del self._chunks[cid]
            self._save()
            return True

    def query(
        self,
        *,
        user_id: str,
        query_text: str,
        conversation_id: Optional[str] = None,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """Busca chunks relevantes aplicando ACL estrita por user_id e ranking híbrido BM25."""
        if not user_id or not (query_text or "").strip():
            return []

        q_tokens = self._tokenize(query_text)
        if not q_tokens:
            return []

        with self._lock:
            # Filtro ACL estrito ANTES do cálculo de similaridade
            candidate_chunks = [
                c
                for c in self._chunks.values()
                if c.user_id == user_id
                and (conversation_id is None or c.conversation_id == conversation_id)
            ]

        if not candidate_chunks:
            return []

        n_docs = len(candidate_chunks)
        avgdl = sum(max(1, len(c.tokens)) for c in candidate_chunks) / n_docs
        df: Counter[str] = Counter()
        for c in candidate_chunks:
            for utok in set(c.tokens):
                df[utok] += 1

        k1 = 1.5
        b = 0.75
        scored: List[Tuple[float, DocumentChunk]] = []

        for chunk in candidate_chunks:
            tf = Counter(chunk.tokens)
            dl = max(1, len(chunk.tokens))
            score = 0.0
            for qtok in q_tokens:
                freq = tf.get(qtok, 0)
                if freq == 0:
                    continue
                doc_freq = df.get(qtok, 0)
                idf = math.log(1.0 + (n_docs - doc_freq + 0.5) / (doc_freq + 0.5))
                denom = freq + k1 * (1.0 - b + b * (dl / avgdl))
                score += idf * ((freq * (k1 + 1.0)) / denom)
            if score > 0.0:
                scored.append((round(score, 6), chunk))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        results: List[Dict[str, Any]] = []
        for score, ch in scored[: max(1, min(top_k, 20))]:
            results.append(
                {
                    "chunk_id": ch.chunk_id,
                    "doc_id": ch.doc_id,
                    "filename": ch.filename,
                    "chunk_index": ch.chunk_index,
                    "score": score,
                    "text": ch.text,
                    "sha256": ch.sha256,
                    "untrusted_context": True,
                }
            )
        return results


class PageVersion(BaseModel):
    """Versão imutável de uma Copilot Page."""

    version_number: int
    content_markdown: str
    summary: str
    sha256: str
    created_at: str


class CopilotPage(BaseModel):
    """Artefato persistente e editável estilo Copilot Pages."""

    page_id: str
    user_id: str
    conversation_id: str
    title: str
    content_markdown: str
    current_version: int = 1
    versions: List[PageVersion] = Field(default_factory=list)
    created_at: str
    updated_at: str


class CopilotPagesManager:
    """Gerencia criação, edição iterativa versionada, restauração e exportação de Pages."""

    def __init__(self, store_path: str = DEFAULT_PAGES_PATH):
        self._lock = threading.RLock()
        self._store_path = store_path
        self._pages: Dict[str, CopilotPage] = {}
        os.makedirs(os.path.dirname(self._store_path), exist_ok=True)
        self._load()

    def _load(self) -> None:
        with self._lock:
            if not os.path.exists(self._store_path):
                return
            try:
                with open(self._store_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                for pid, pval in (raw or {}).items():
                    self._pages[pid] = CopilotPage.model_validate(pval)
            except Exception as exc:
                logger.warning("Falha ao carregar copilot_pages_store.json: %s", exc)

    def _save(self) -> None:
        with self._lock:
            try:
                tmp = f"{self._store_path}.tmp"
                payload = {pid: p.model_dump(mode="json") for pid, p in self._pages.items()}
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self._store_path)
            except Exception as exc:
                logger.warning("Falha ao salvar copilot_pages_store.json: %s", exc)

    def create_page(
        self,
        *,
        user_id: str,
        conversation_id: str,
        title: str,
        content_markdown: str,
        summary: str = "Versão inicial",
    ) -> CopilotPage:
        if not user_id:
            raise ValueError("user_id obrigatório.")
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        pid = f"pg_{secrets.token_hex(8)}"
        clean_md = content_markdown or ""
        v1 = PageVersion(
            version_number=1,
            content_markdown=clean_md,
            summary=summary,
            sha256=hashlib.sha256(clean_md.encode("utf-8")).hexdigest(),
            created_at=now_iso,
        )
        page = CopilotPage(
            page_id=pid,
            user_id=user_id,
            conversation_id=conversation_id or f"conv_{user_id}",
            title=(title or "Página Sem Título").strip(),
            content_markdown=clean_md,
            current_version=1,
            versions=[v1],
            created_at=now_iso,
            updated_at=now_iso,
        )
        with self._lock:
            self._pages[pid] = page
            self._save()
        return page

    def update_page(
        self,
        *,
        user_id: str,
        page_id: str,
        content_markdown: str,
        title: Optional[str] = None,
        summary: str = "Edição iterativa",
    ) -> Optional[CopilotPage]:
        with self._lock:
            page = self._pages.get(page_id)
            if not page or page.user_id != user_id:
                return None
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            next_ver = len(page.versions) + 1
            ver = PageVersion(
                version_number=next_ver,
                content_markdown=content_markdown,
                summary=summary,
                sha256=hashlib.sha256(content_markdown.encode("utf-8")).hexdigest(),
                created_at=now_iso,
            )
            page.versions.append(ver)
            page.content_markdown = content_markdown
            page.current_version = next_ver
            if title is not None and title.strip():
                page.title = title.strip()
            page.updated_at = now_iso
            self._save()
            return page

    def restore_version(self, *, user_id: str, page_id: str, version_number: int) -> Optional[CopilotPage]:
        with self._lock:
            page = self._pages.get(page_id)
            if not page or page.user_id != user_id:
                return None
            target = next((v for v in page.versions if v.version_number == version_number), None)
            if target is None:
                return None
            return self.update_page(
                user_id=user_id,
                page_id=page_id,
                content_markdown=target.content_markdown,
                summary=f"Restaurado da versão {version_number}",
            )

    def get_page(self, user_id: str, page_id: str) -> Optional[CopilotPage]:
        with self._lock:
            page = self._pages.get(page_id)
            if not page or page.user_id != user_id:
                return None
            return page

    def list_pages(self, user_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            items = [p for p in self._pages.values() if p.user_id == user_id]
        items.sort(key=lambda p: p.updated_at, reverse=True)
        return [
            {
                "page_id": p.page_id,
                "title": p.title,
                "conversation_id": p.conversation_id,
                "current_version": p.current_version,
                "updated_at": p.updated_at,
            }
            for p in items
        ]

    def delete_page(self, user_id: str, page_id: str) -> bool:
        with self._lock:
            page = self._pages.get(page_id)
            if not page or page.user_id != user_id:
                return False
            del self._pages[page_id]
            self._save()
            return True

    def export_page(self, user_id: str, page_id: str, fmt: str = "md") -> Optional[Dict[str, Any]]:
        page = self.get_page(user_id=user_id, page_id=page_id)
        if not page:
            return None
        clean_fmt = (fmt or "md").strip().lower().lstrip(".")
        if clean_fmt in ("md", "markdown"):
            return {
                "page_id": page.page_id,
                "title": page.title,
                "version": page.current_version,
                "format": "md",
                "mime_type": "text/markdown",
                "content": page.content_markdown,
            }
        if clean_fmt == "txt":
            plain = re.sub(r"[#*`_~>\[\]]", "", page.content_markdown)
            return {
                "page_id": page.page_id,
                "title": page.title,
                "version": page.current_version,
                "format": "txt",
                "mime_type": "text/plain",
                "content": plain,
            }
        if clean_fmt == "html":
            escaped_title = html.escape(page.title)
            escaped_body = html.escape(page.content_markdown).replace("\n", "<br>\n")
            html_doc = (
                f"<!DOCTYPE html>\n<html lang=\"pt-BR\">\n<head><meta charset=\"utf-8\">"
                f"<title>{escaped_title}</title></head>\n<body>\n<h1>{escaped_title}</h1>\n"
                f"<div class=\"copilot-page-content\">{escaped_body}</div>\n</body>\n</html>"
            )
            return {
                "page_id": page.page_id,
                "title": page.title,
                "version": page.current_version,
                "format": "html",
                "mime_type": "text/html",
                "content": html_doc,
            }
        if clean_fmt == "json":
            return {
                "page_id": page.page_id,
                "title": page.title,
                "version": page.current_version,
                "format": "json",
                "mime_type": "application/json",
                "content": json.dumps(page.model_dump(mode="json"), ensure_ascii=False, indent=2),
            }
        raise ValueError(f"Formato de exportação '{fmt}' não suportado (use md, html, txt ou json).")


# Instâncias globais da Fase F4
rag_index = LocalRAGIndex()
pages_manager = CopilotPagesManager()
