"""
F10 — Assistente de Compras e Espaço Informativo de Saúde com Isolamento de Privacidade (`compras_e_saude.py`)

Implementa:
1. ShoppingAssistant:
   - Comparação estruturada de ofertas/produtos com preço numérico, código de moeda (ISO 4217),
     timestamp de coleta, URL de origem verificada (anti-SSRF) e atributos prós/contras;
   - Proibição absoluta de compra/checkout autônomo sem confirmação humana explícita
     no `PolicyEngine`.
2. HealthInformationalSpace:
   - Espaço informativo de saúde estritamente opt-in e isolado da memória geral do usuário
     (`isolated_from_general_memory=True`: nenhuma pergunta ou nota de saúde é salva no
     `UserMemoryManager` ou no `JarvisMemoryService`);
   - Aviso clínico obrigatório (`clinical_disclaimer`), exigência de fontes verificáveis
     e bloqueio de ações clínicas autônomas (prescrição, diagnóstico definitivo, compra de medicamentos).
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from policy_engine import policy_engine
from rede_segura import motivo_de_bloqueio_da_url

MANDATORY_CLINICAL_DISCLAIMER = (
    "AVISO IMPORTANTE: As informações apresentadas têm caráter exclusivamente educativo e informativo "
    "e NÃO substituem consulta, diagnóstico ou tratamento por médico ou profissional de saúde qualificado."
)


class ProductOffer(BaseModel):
    offer_id: str = Field(default_factory=lambda: f"off_{secrets.token_urlsafe(6)}")
    product_name: str
    merchant: str
    price: float
    currency: str = "BRL"
    source_url: str
    collected_at: float = Field(default_factory=time.time)
    specs: Dict[str, Any] = Field(default_factory=dict)
    in_stock: bool = True


class ProductComparisonReport(BaseModel):
    comparison_id: str = Field(default_factory=lambda: f"cmp_{secrets.token_urlsafe(8)}")
    query: str
    currency: str = "BRL"
    offers: List[ProductOffer] = Field(default_factory=list)
    best_price_offer_id: Optional[str] = None
    price_Stats: Dict[str, float] = Field(default_factory=dict)
    autonomous_purchase_executed: bool = False
    generated_at: float = Field(default_factory=time.time)


class PriceAlertSubscription(BaseModel):
    alert_id: str = Field(default_factory=lambda: f"pal_{secrets.token_urlsafe(6)}")
    user_id: str
    product_query: str
    target_price: float
    currency: str = "BRL"
    active: bool = True
    created_at: float = Field(default_factory=time.time)


class ShoppingAssistant:
    """
    Copilot Shopping: pesquisa e comparação estruturada de produtos com fonte,
    moeda e timestamp explícitos, alertas de preço e bloqueio de compra autônoma.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._alerts: Dict[str, PriceAlertSubscription] = {}

    def compare_offers(
        self,
        query: str,
        raw_offers: List[Dict[str, Any]],
        currency: str = "BRL",
    ) -> ProductComparisonReport:
        valid_offers: List[ProductOffer] = []
        now = time.time()
        for item in raw_offers or []:
            url = str(item.get("source_url") or item.get("url") or "").strip()
            if not url or motivo_de_bloqueio_da_url(url) is not None:
                continue
            try:
                price_val = float(item.get("price"))
            except (TypeError, ValueError):
                continue
            if price_val <= 0:
                continue

            valid_offers.append(
                ProductOffer(
                    product_name=str(item.get("product_name") or item.get("title") or query),
                    merchant=str(item.get("merchant") or "Loja Externa"),
                    price=round(price_val, 2),
                    currency=str(item.get("currency") or currency).upper(),
                    source_url=url,
                    collected_at=float(item.get("collected_at") or now),
                    specs=dict(item.get("specs") or {}),
                    in_stock=bool(item.get("in_stock", True)),
                )
            )

        valid_offers.sort(key=lambda o: o.price)
        best_id = valid_offers[0].offer_id if valid_offers else None
        stats: Dict[str, float] = {}
        if valid_offers:
            prices = [o.price for o in valid_offers]
            stats = {
                "min_price": min(prices),
                "max_price": max(prices),
                "avg_price": round(sum(prices) / len(prices), 2),
            }

        return ProductComparisonReport(
            query=query.strip(),
            currency=currency.upper(),
            offers=valid_offers,
            best_price_offer_id=best_id,
            price_Stats=stats,
            autonomous_purchase_executed=False,
            generated_at=now,
        )

    def request_checkout_or_purchase(
        self,
        user_id: str,
        offer_id: str,
        merchant: str,
        price: float,
        currency: str = "BRL",
        source_url: str = "",
    ) -> Dict[str, Any]:
        """
        Nenhuma compra pode ser executada autonomamente sem confirmação humana
        explícita one-shot no PolicyEngine.
        """
        params = {
            "offer_id": offer_id,
            "merchant": merchant,
            "price": round(float(price), 2),
            "currency": currency.upper(),
            "source_url": source_url,
        }
        tool_key = "shopping_purchase_checkout"
        if not policy_engine.consume_authorization(
            tool_key,
            params,
            session_id=user_id,
            user_id=user_id,
        ):
            pending = policy_engine.create_pending_action(
                tool_key,
                params,
                session_id=user_id,
                user_id=user_id,
            )
            return {
                "status": "aguardando_confirmacao",
                "requires_confirmation": True,
                "autonomous_purchase_executed": False,
                "id_confirmacao": pending.action_id,
                "mensagem": (
                    f"Compra em '{merchant}' ({currency} {price:.2f}) bloqueada para execução autônoma. "
                    "Exige confirmação humana explícita."
                ),
            }

        return {
            "status": "autorizado_para_checkout_assistido",
            "requires_confirmation": False,
            "autonomous_purchase_executed": False,
            "order_intent": params,
            "mensagem": "Checkout assistido autorizado pelo usuário para esta oferta específica (one-shot).",
        }

    def create_price_alert(
        self,
        user_id: str,
        product_query: str,
        target_price: float,
        currency: str = "BRL",
    ) -> PriceAlertSubscription:
        with self._lock:
            alert = PriceAlertSubscription(
                user_id=user_id,
                product_query=product_query.strip(),
                target_price=float(target_price),
                currency=currency.upper(),
            )
            self._alerts[alert.alert_id] = alert
            return alert.model_copy(deep=True)


class HealthEntry(BaseModel):
    entry_id: str = Field(default_factory=lambda: f"hlth_{secrets.token_urlsafe(6)}")
    user_id: str
    question: str
    summary_answer: str
    verified_sources: List[Dict[str, str]] = Field(default_factory=list)
    clinical_disclaimer: str = MANDATORY_CLINICAL_DISCLAIMER
    isolated_from_general_memory: bool = True
    autonomous_clinical_action_performed: bool = False
    created_at: float = Field(default_factory=time.time)


class HealthInformationalSpace:
    """
    Copilot Health Space: espaço informativo de saúde e bem-estar, estritamente
    opt-in e isolado da memória geral do usuário (`UserMemoryManager`).
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._opt_in_users: Dict[str, bool] = {}
        self._entries: Dict[str, List[HealthEntry]] = {}

    def set_opt_in(self, user_id: str, enabled: bool) -> Dict[str, Any]:
        with self._lock:
            self._opt_in_users[user_id] = bool(enabled)
            return {
                "user_id": user_id,
                "health_opt_in": self._opt_in_users[user_id],
                "isolated_from_general_memory": True,
            }

    def is_opted_in(self, user_id: str) -> bool:
        with self._lock:
            return bool(self._opt_in_users.get(user_id, False))

    def query_health_information(
        self,
        user_id: str,
        question: str,
        sources: Optional[List[Dict[str, str]]] = None,
        requested_clinical_action: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.is_opted_in(user_id):
            raise PermissionError(
                "O espaço informativo de saúde exige opt-in explícito do usuário antes do uso."
            )

        if requested_clinical_action:
            raise PermissionError(
                f"Bloqueado: o assistente de saúde é estritamente informativo e proíbe ações clínicas "
                f"autônomas ('{requested_clinical_action}')."
            )

        verified_sources: List[Dict[str, str]] = []
        for s in sources or []:
            url = str(s.get("url") or "").strip()
            title = str(s.get("title") or "Fonte de Saúde").strip()
            snippet = str(s.get("snippet") or "").strip()
            if url and motivo_de_bloqueio_da_url(url) is None:
                verified_sources.append({"title": title, "url": url, "snippet": snippet})

        if not verified_sources:
            return {
                "status": "INSUFFICIENT_VERIFIED_HEALTH_SOURCES",
                "clinical_disclaimer": MANDATORY_CLINICAL_DISCLAIMER,
                "isolated_from_general_memory": True,
                "autonomous_clinical_action_performed": False,
                "mensagem": (
                    "Não foram fornecidas fontes de saúde externas verificáveis para responder com segurança. "
                    f"{MANDATORY_CLINICAL_DISCLAIMER}"
                ),
            }

        cite_str = "; ".join(f"{v['title']} ({v['url']})" for v in verified_sources)
        answer = (
            f"Síntese informativa baseada em fontes verificadas ({cite_str}): "
            f"{verified_sources[0]['snippet']} — {MANDATORY_CLINICAL_DISCLAIMER}"
        )
        entry = HealthEntry(
            user_id=user_id,
            question=question.strip(),
            summary_answer=answer,
            verified_sources=verified_sources,
            clinical_disclaimer=MANDATORY_CLINICAL_DISCLAIMER,
            isolated_from_general_memory=True,
            autonomous_clinical_action_performed=False,
        )
        with self._lock:
            self._entries.setdefault(user_id, []).append(entry)

        return {
            "status": "ok",
            "entry": entry.model_dump(mode="json"),
            "clinical_disclaimer": MANDATORY_CLINICAL_DISCLAIMER,
            "isolated_from_general_memory": True,
            "autonomous_clinical_action_performed": False,
        }

    def list_health_history(self, user_id: str) -> List[HealthEntry]:
        with self._lock:
            return [e.model_copy(deep=True) for e in self._entries.get(user_id, [])]

    def purge_health_data(self, user_id: str) -> int:
        with self._lock:
            items = self._entries.pop(user_id, [])
            return len(items)


shopping_assistant = ShoppingAssistant()
health_space = HealthInformationalSpace()
