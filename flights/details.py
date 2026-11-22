"""Reaproveitar detalhes já coletados (aeronave, bagagem, CO₂, faixa típica...) em vez de abrir páginas do Google a cada
consulta. O preço e os horários vêm de toda coleta; o detalhe de cada voo só é refeito depois de `ttl_h` horas.

Funções puras (sem navegador nem banco), para testar a decisão separadamente do scraping.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

DEFAULT_TTL_H = 24.0

# Campos de Offer que vêm da expansão do card (detalhe) e mudam pouco de uma coleta para outra.
DETAIL_FIELDS = ("cabin", "aircraft", "co2_kg", "co2_diff_pct", "overnight", "often_delayed", "segments")


def parse_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def fmt_ts(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def age_h(ts: Optional[str], now: datetime) -> Optional[float]:
    t = parse_ts(ts)
    return None if t is None else (now - t).total_seconds() / 3600.0


def is_fresh(ts: Optional[str], now: datetime, ttl_h: float) -> bool:
    a = age_h(ts, now)
    return a is not None and 0 <= a < ttl_h


_PRICE_FROM = re.compile(r"From\s+([\d.,]+)")


def card_price(aria: str) -> float:
    m = _PRICE_FROM.match(aria or "")
    try:
        return float(m.group(1).replace(",", "")) if m else float("inf")
    except ValueError:
        return float("inf")


def plan_detail_needs(cards: Iterable[dict], known: Dict[str, dict], n: int, top: int, ttl_h: float = DEFAULT_TTL_H,
                      now: Optional[datetime] = None, booking_ok: bool = True) -> Tuple[List[str], List[str]]:
    """Que cards expandir e que voos abrir na tela de compra nesta consulta?

    `cards`: [{"aria", "key"}] sem repetidos (o `key` é número do voo + horário de saída; None se não deu para ler).
    `known`: key -> {"detail_at", "booking_at"} do que já está no banco.
    Escolhe, entre os `n` mais baratos (detalhe) e os `top` mais baratos (compra), só os que ainda não têm
    informação fresca (< `ttl_h` horas). Devolve (arias para expandir, arias para abrir a compra).
    """
    now = now or datetime.now(timezone.utc)
    ordered = sorted(cards, key=lambda c: card_price(c["aria"]))
    need_detail, need_booking = [], []
    for c in ordered[: max(0, n)]:
        k = known.get(c.get("key") or "", {})
        if not is_fresh(k.get("detail_at"), now, ttl_h):
            need_detail.append(c["aria"])
    if booking_ok:
        for c in ordered[: max(0, top)]:
            k = known.get(c.get("key") or "", {})
            if not is_fresh(k.get("booking_at"), now, ttl_h):
                need_booking.append(c["aria"])
    return need_detail, need_booking


def strip_seller_prices(booking: Optional[dict]) -> Optional[dict]:
    """Bloco de compra para copiar adiante: bagagem, política e faixa típica ficam; o PREÇO de cada vendedor não
    (muda o tempo todo e apareceria ao lado de um preço de voo mais novo, contradizendo-o)."""
    if not booking:
        return None
    b = copy.deepcopy(booking)
    for s in b.get("sellers") or []:
        for k in ("price", "price_text", "other_price_text"):
            if k in s:
                s[k] = None
    b["carried"] = True
    return b


def carry_forward(offer, prior: dict, now: datetime, got_detail: bool, got_booking: bool) -> None:
    """Preenche no `offer` (objeto Offer) o que não foi refeito agora, a partir do registro anterior `prior`
    ({"fields": {...}, "booking": {...}, "detail_at", "booking_at"}), e carimba as datas."""
    stamp = fmt_ts(now)
    if got_detail:
        offer.detail_at = stamp
    elif prior.get("detail_at"):   # mantém o que havia, com a data original (a idade fica visível no painel)
        for f in DETAIL_FIELDS:
            if getattr(offer, f, None) in (None, [], "") and prior.get("fields", {}).get(f) not in (None, [], ""):
                setattr(offer, f, prior["fields"][f])
        offer.detail_at = prior["detail_at"]
    if got_booking:
        offer.booking_at = stamp
    elif prior.get("booking") and prior.get("booking_at"):
        offer.booking = strip_seller_prices(prior["booking"])
        offer.booking_at = prior["booking_at"]
