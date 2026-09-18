"""
Pool Unificado de Chaves Gemini (jarvis.core.key_pool)
Autoridade única de gerenciamento, rotação e failover de credenciais de IA.
"""

import logging
import os
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger("jarvis.key_pool")


class GeminiKeyPool:
    """
    Gerenciador thread-safe do pool de chaves da API do Google Gemini.
    Suporta rotação round-robin, failover em rate limit (429) e observabilidade.
    """
    _instance: Optional["GeminiKeyPool"] = None

    def __init__(self):
        self._keys: List[str] = []
        self._current_index: int = 0
        self._lock = threading.Lock()
        self._failed_keys: Dict[str, str] = {}
        self.reload()

    @classmethod
    def get_instance(cls) -> "GeminiKeyPool":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def reload(self) -> List[str]:
        """Recarrega as chaves a partir das variáveis de ambiente."""
        raw_keys = os.environ.get("GEMINI_API_KEYS", "")
        keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
        single_key = os.environ.get("GEMINI_API_KEY")
        if single_key and single_key.strip() and single_key.strip() not in keys:
            keys.insert(0, single_key.strip())

        with self._lock:
            self._keys = keys
            if self._current_index >= len(self._keys):
                self._current_index = 0
            self._failed_keys.clear()

        logger.info(f"KeyPool carregado com {len(keys)} chave(s).")
        return list(keys)

    def get_keys(self) -> List[str]:
        """Retorna uma cópia da lista de todas as chaves do pool."""
        with self._lock:
            return list(self._keys)

    def get_active_key(self) -> Optional[str]:
        """Retorna a chave atualmente ativa."""
        with self._lock:
            if not self._keys:
                return None
            return self._keys[self._current_index % len(self._keys)]

    def get_active_index(self) -> int:
        """Retorna o índice da chave ativa."""
        with self._lock:
            return self._current_index

    def rotate_key(self, reason: str = "rotação") -> Optional[str]:
        """Avança para a próxima chave disponível no pool."""
        with self._lock:
            if not self._keys:
                return None
            old_idx = self._current_index
            self._current_index = (self._current_index + 1) % len(self._keys)
            new_key = self._keys[self._current_index]
            logger.info(
                f"KeyPool rotacionado ({reason}): conta [{old_idx + 1}/{len(self._keys)}] -> "
                f"[{self._current_index + 1}/{len(self._keys)}]"
            )
            return new_key

    def mark_key_failed(self, key: str, reason: str = "quota"):
        """Registra falha em uma chave específica."""
        with self._lock:
            self._failed_keys[key] = reason
        logger.warning(f"Chave terminada em '...{key[-6:] if len(key) >= 6 else key}' marcada com falha: {reason}")

    def get_status(self) -> Dict[str, Any]:
        """Retorna o estado do pool para observabilidade e telemetria."""
        with self._lock:
            total = len(self._keys)
            active_idx = self._current_index
            active_key = self._keys[active_idx] if self._keys else None
            masked_key = f"{active_key[:8]}...{active_key[-4:]}" if active_key and len(active_key) >= 12 else "nenhuma"
            return {
                "total_chaves": total,
                "indice_ativo": active_idx + 1 if total > 0 else 0,
                "chave_ativa_mascarada": masked_key,
                "falhas_registradas": len(self._failed_keys),
            }


# Singleton e funções utilitárias canônicas
key_pool = GeminiKeyPool.get_instance()


def get_gemini_keys() -> List[str]:
    return key_pool.get_keys()


def get_active_gemini_key() -> Optional[str]:
    return key_pool.get_active_key()


def rotate_gemini_key(reason: str = "quota") -> Optional[str]:
    return key_pool.rotate_key(reason=reason)
