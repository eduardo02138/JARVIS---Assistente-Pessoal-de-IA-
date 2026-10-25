"""
F9 — Geração e Edição Iterativa de Imagens com Proveniência (`geracao_imagens.py`)

Implementa:
1. Adaptador configurável de geração e edição de imagens (Gemini Imagen / OmniRoute / Custom);
2. Transparência honesta: se não houver provedor de imagem real configurado, retorna
   `status="UNAVAILABLE_PROVIDER"` sem simular arquivo falso;
3. Edição conversacional iterativa preservando árvore genealógica do ativo
   (`parent_asset_id`, `root_asset_id`, `revision_number`, `edit_history`);
4. Upload de imagem de referência com validação de magic-bytes (PNG, JPEG, WEBP);
5. Galeria isolada por usuário (`user_id`), metadados de proveniência (`ai_generated=True`,
   `prompt_sha256`, `provider`, `model`), quota diária de custo/uso e exclusão.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

DEFAULT_GALLERY_PATH = Path(
    os.environ.get(
        "JARVIS_IMAGE_GALLERY_PATH",
        str(Path(__file__).resolve().parent / "data" / "image_gallery.json"),
    )
)

VALID_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"RIFF", "image/webp"),
)


def validate_reference_image_bytes(raw_bytes: bytes) -> Tuple[bool, str]:
    if not raw_bytes or len(raw_bytes) < 8:
        return False, "Arquivo de referência visual vazio ou truncado."
    for magic, mime in VALID_IMAGE_MAGIC:
        if raw_bytes.startswith(magic):
            if magic == b"RIFF" and b"WEBP" not in raw_bytes[:16]:
                continue
            return True, mime
    return False, "Formato de referência visual inválido (permitidos apenas PNG, JPEG ou WEBP)."


def _build_default_imagen_adapter(api_key: str) -> Callable[[str, Dict[str, Any]], Tuple[bytes, str, str, str]]:
    """Adaptador real para a API Google GenAI Imagen (imagen-3.0-generate-002)."""
    def _adapter(prompt: str, opts: Dict[str, Any]) -> Tuple[bytes, str, str, str]:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)
        aspect = opts.get("aspect_ratio", "1:1")
        resp = client.models.generate_images(
            model="imagen-3.0-generate-002",
            prompt=prompt,
            config=types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio=aspect,
                output_mime_type="image/png",
            ),
        )
        if not resp.generated_images:
            raise RuntimeError("Nenhuma imagem gerada retornada pela API Imagen.")
        img_bytes = resp.generated_images[0].image.image_bytes
        return img_bytes, "image/png", "google_imagen", "imagen-3.0-generate-002"
    return _adapter


class ImageProvenance(BaseModel):
    ai_generated: bool = True
    provider: str
    model: str
    prompt_sha256: str
    created_at: float = Field(default_factory=time.time)
    reference_sha256: Optional[str] = None
    c2pa_like_claim: str = "Generated/Edited by JARVIS Multimodal Image Pipeline"


class ImageEditRevision(BaseModel):
    revision_number: int
    prompt: str
    asset_id: str
    parent_asset_id: Optional[str] = None
    timestamp: float = Field(default_factory=time.time)


class ImageAssetRecord(BaseModel):
    asset_id: str = Field(default_factory=lambda: f"img_{secrets.token_urlsafe(8)}")
    user_id: str
    root_asset_id: str = ""
    parent_asset_id: Optional[str] = None
    revision_number: int = 1
    prompt: str
    aspect_ratio: str = "1:1"
    style: str = "natural"
    mime_type: str = "image/png"
    image_base64: str
    provenance: ImageProvenance
    edit_history: List[ImageEditRevision] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)


class ImageGenerationService:
    """
    Serviço de Geração e Edição Iterativa de Imagens com galeria por usuário,
    controle de quota e honestidade quando não há provedor configurado.
    """

    def __init__(
        self,
        storage_path: Optional[Path] = None,
        provider_adapter: Optional[Callable[[str, Dict[str, Any]], Tuple[bytes, str, str, str]]] = None,
        daily_quota_per_user: int = 50,
    ) -> None:
        self._lock = threading.RLock()
        self.storage_path = storage_path or DEFAULT_GALLERY_PATH
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.provider_adapter = provider_adapter
        self.daily_quota_per_user = max(1, int(daily_quota_per_user))
        self._assets: Dict[str, ImageAssetRecord] = {}
        self._usage_counts: Dict[str, int] = {}
        self._load()

    def _load(self) -> None:
        with self._lock:
            if not self.storage_path.exists():
                return
            try:
                raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
                for aid, adata in raw.get("assets", {}).items():
                    self._assets[aid] = ImageAssetRecord.model_validate(adata)
            except Exception:
                pass

    def _save(self) -> None:
        with self._lock:
            payload = {
                "assets": {k: v.model_dump(mode="json") for k, v in self._assets.items()},
            }
            tmp = self.storage_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.storage_path)

    def _resolve_adapter(self) -> Optional[Callable[[str, Dict[str, Any]], Tuple[bytes, str, str, str]]]:
        if self.provider_adapter is not None:
            return self.provider_adapter
        key = os.environ.get("JARVIS_IMAGE_PROVIDER_KEY", "").strip()
        if not key:
            return None
        return _build_default_imagen_adapter(key)

    def is_provider_configured(self) -> bool:
        return self._resolve_adapter() is not None

    def generate_image(
        self,
        user_id: str,
        prompt: str,
        aspect_ratio: str = "1:1",
        style: str = "natural",
        reference_image_bytes: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        cleaned_prompt = (prompt or "").strip()
        if not cleaned_prompt:
            raise ValueError("O prompt de geração de imagem não pode ser vazio.")

        ref_hash: Optional[str] = None
        if reference_image_bytes is not None:
            ok_ref, mime_or_err = validate_reference_image_bytes(reference_image_bytes)
            if not ok_ref:
                raise ValueError(mime_or_err)
            ref_hash = hashlib.sha256(reference_image_bytes).hexdigest()

        adapter = self._resolve_adapter()
        if adapter is None:
            return {
                "status": "UNAVAILABLE_PROVIDER",
                "provider_available": False,
                "mensagem": (
                    "Nenhum provedor real de geração de imagens está configurado neste ambiente "
                    "(configure JARVIS_IMAGE_PROVIDER_KEY ou injete um provider_adapter)."
                ),
            }

        with self._lock:
            used = self._usage_counts.get(user_id, 0)
            if used >= self.daily_quota_per_user:
                return {
                    "status": "QUOTA_EXCEEDED",
                    "provider_available": True,
                    "mensagem": f"Limite diário de geração de imagens atingido ({self.daily_quota_per_user}).",
                }

        try:
            img_bytes, mime_type, prov_name, model_name = adapter(
                cleaned_prompt,
                {
                    "mode": "generate",
                    "aspect_ratio": aspect_ratio,
                    "style": style,
                    "reference_image_bytes": reference_image_bytes,
                },
            )
        except Exception as exc:
            logger.error("Falha ao gerar imagem pelo provedor: %s", exc)
            return {
                "status": "PROVIDER_ERROR",
                "provider_available": True,
                "mensagem": f"Falha na execução do provedor de geração de imagem: {exc}",
            }
        prompt_hash = hashlib.sha256(cleaned_prompt.encode("utf-8")).hexdigest()
        asset_id = f"img_{secrets.token_urlsafe(8)}"
        rev = ImageEditRevision(
            revision_number=1,
            prompt=cleaned_prompt,
            asset_id=asset_id,
            parent_asset_id=None,
        )
        record = ImageAssetRecord(
            asset_id=asset_id,
            user_id=user_id,
            root_asset_id=asset_id,
            parent_asset_id=None,
            revision_number=1,
            prompt=cleaned_prompt,
            aspect_ratio=aspect_ratio,
            style=style,
            mime_type=mime_type,
            image_base64=base64.b64encode(img_bytes).decode("ascii"),
            provenance=ImageProvenance(
                ai_generated=True,
                provider=prov_name,
                model=model_name,
                prompt_sha256=prompt_hash,
                reference_sha256=ref_hash,
            ),
            edit_history=[rev],
        )
        with self._lock:
            self._assets[asset_id] = record
            self._usage_counts[user_id] = self._usage_counts.get(user_id, 0) + 1
            self._save()
        return {
            "status": "SUCCEEDED",
            "provider_available": True,
            "asset": record.model_dump(mode="json"),
        }

    def edit_image_iteratively(
        self,
        user_id: str,
        parent_asset_id: str,
        edit_instruction: str,
        style: Optional[str] = None,
    ) -> Dict[str, Any]:
        instruction = (edit_instruction or "").strip()
        if not instruction:
            raise ValueError("A instrução de edição de imagem não pode ser vazia.")

        with self._lock:
            parent = self._assets.get(parent_asset_id)
            if parent is None or parent.user_id != user_id:
                raise KeyError(f"Ativo de imagem '{parent_asset_id}' não encontrado para este usuário.")

        adapter = self._resolve_adapter()
        if adapter is None:
            return {
                "status": "UNAVAILABLE_PROVIDER",
                "provider_available": False,
                "mensagem": "Provedor de edição de imagem indisponível.",
            }

        combined_prompt = f"{parent.prompt} | Edição: {instruction}"
        try:
            img_bytes, mime_type, prov_name, model_name = adapter(
                combined_prompt,
                {
                    "mode": "edit",
                    "parent_asset_id": parent.asset_id,
                    "edit_instruction": instruction,
                    "style": style or parent.style,
                },
            )
        except Exception as exc:
            logger.error("Falha ao editar imagem pelo provedor: %s", exc)
            return {
                "status": "PROVIDER_ERROR",
                "provider_available": True,
                "mensagem": f"Falha na execução do provedor de edição de imagem: {exc}",
            }
        new_asset_id = f"img_{secrets.token_urlsafe(8)}"
        new_rev_num = parent.revision_number + 1
        new_rev = ImageEditRevision(
            revision_number=new_rev_num,
            prompt=instruction,
            asset_id=new_asset_id,
            parent_asset_id=parent.asset_id,
        )
        history = [r.model_copy(deep=True) for r in parent.edit_history] + [new_rev]
        prompt_hash = hashlib.sha256(combined_prompt.encode("utf-8")).hexdigest()

        record = ImageAssetRecord(
            asset_id=new_asset_id,
            user_id=user_id,
            root_asset_id=parent.root_asset_id or parent.asset_id,
            parent_asset_id=parent.asset_id,
            revision_number=new_rev_num,
            prompt=combined_prompt,
            aspect_ratio=parent.aspect_ratio,
            style=style or parent.style,
            mime_type=mime_type,
            image_base64=base64.b64encode(img_bytes).decode("ascii"),
            provenance=ImageProvenance(
                ai_generated=True,
                provider=prov_name,
                model=model_name,
                prompt_sha256=prompt_hash,
                reference_sha256=parent.provenance.prompt_sha256,
            ),
            edit_history=history,
        )
        with self._lock:
            self._assets[new_asset_id] = record
            self._usage_counts[user_id] = self._usage_counts.get(user_id, 0) + 1
            self._save()
        return {
            "status": "SUCCEEDED",
            "provider_available": True,
            "asset": record.model_dump(mode="json"),
        }

    def list_gallery(self, user_id: str) -> List[ImageAssetRecord]:
        with self._lock:
            items = [a.model_copy(deep=True) for a in self._assets.values() if a.user_id == user_id]
        items.sort(key=lambda x: x.created_at, reverse=True)
        return items

    def delete_asset(self, user_id: str, asset_id: str) -> bool:
        with self._lock:
            a = self._assets.get(asset_id)
            if not a or a.user_id != user_id:
                return False
            self._assets.pop(asset_id, None)
            self._save()
            return True


image_generation_service = ImageGenerationService()
