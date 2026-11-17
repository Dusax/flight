from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

CABINS = {"economy": 1, "premium": 2, "business": 3, "first": 4}
IATA_RE = re.compile(r"^[A-Za-z]{3}$")
MAX_LEGS = 5  # limite do Google Voos para multi-destino
MAX_COMBOS = 2000     # combinações de datas que um período pode gerar (quanto cabe numa coleta é o `budget`)
MAX_RANGE_DAYS = 60   # tamanho máximo do período de um trecho
DEFAULT_BUDGET = 40   # consultas ao Google por coleta, por busca monitorada
BUDGET_MIN, BUDGET_MAX = 10, 100


def parse_date(value: str) -> date:
    """Aceita YYYY-MM-DD ou DD/MM/YYYY."""
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"data inválida: {value!r} (use AAAA-MM-DD ou DD/MM/AAAA)")


def parse_iata(value: str) -> str:
    if not IATA_RE.match(value.strip()):
        raise ValueError(f"código de aeroporto inválido: {value!r} (use o código IATA de 3 letras, ex.: GRU)")
    return value.strip().upper()


@dataclass(frozen=True)
class Leg:
    origin: str
    destination: str
    date: date


def legs_to_json(legs) -> Optional[str]:
    if not legs:
        return None
    return json.dumps([{"origin": l.origin, "destination": l.destination, "date": l.date.isoformat()} for l in legs],
                      separators=(",", ":"))


def legs_from_json(text: Optional[str]) -> Tuple[Leg, ...]:
    if not text:
        return ()
    return tuple(Leg(parse_iata(x["origin"]), parse_iata(x["destination"]), parse_date(x["date"]))
                 for x in json.loads(text))


@dataclass(frozen=True)
class SearchSpec:
    """Uma consulta ao Google Voos.

    Três tipos: só ida, ida e volta (`return_date`) ou multi-destino (`legs`, de 2 a 5 trechos).
    `origin`/`destination`/`depart_date` sempre descrevem o 1º trecho, que é o que o Google
    lista (nos dois últimos tipos o preço mostrado é o total da viagem inteira).
    """
    origin: str
    destination: str
    depart_date: date
    return_date: Optional[date] = None
    adults: int = 1
    cabin: str = "economy"
    max_stops: Optional[int] = None  # None = qualquer; 0 = só diretos
    currency: str = "BRL"
    legs: Tuple[Leg, ...] = ()
    leg_index: Optional[int] = None  # preenchido nas buscas "separadas" (um trecho como só ida)

    def __post_init__(self):
        if self.cabin not in CABINS:
            raise ValueError(f"classe inválida: {self.cabin!r} (use {', '.join(CABINS)})")
        if not 1 <= self.adults <= 9:
            raise ValueError("adultos deve estar entre 1 e 9")
        if self.return_date and self.return_date < self.depart_date:
            raise ValueError("a volta não pode ser antes da ida")
        if self.legs:
            if self.return_date:
                raise ValueError("multi-destino não usa data de volta")
            if not 2 <= len(self.legs) <= MAX_LEGS:
                raise ValueError(f"multi-destino precisa de 2 a {MAX_LEGS} trechos")
            first = self.legs[0]
            if (first.origin, first.destination, first.date) != (self.origin, self.destination, self.depart_date):
                raise ValueError("o 1º trecho deve ser igual a origem/destino/ida")
            for a, b in zip(self.legs, self.legs[1:]):
                if b.date < a.date:
                    raise ValueError("as datas dos trechos devem estar em ordem")
            for l in self.legs:
                if l.origin == l.destination:
                    raise ValueError(f"trecho com origem e destino iguais: {l.origin}")

    # -- construção ----------------------------------------------------------
    @classmethod
    def multi(cls, legs, adults: int = 1, cabin: str = "economy", max_stops: Optional[int] = None,
              currency: str = "BRL") -> "SearchSpec":
        legs = tuple(legs)
        if not legs:
            raise ValueError("multi-destino precisa de trechos")
        f = legs[0]
        return cls(f.origin, f.destination, f.date, None, adults, cabin, max_stops, currency, legs)

    # -- propriedades --------------------------------------------------------
    @property
    def trip_type(self) -> str:
        if self.legs:
            return "multi_city"
        return "round_trip" if self.return_date else "one_way"

    @property
    def legs_json(self) -> Optional[str]:
        return legs_to_json(self.legs)

    def all_legs(self) -> Tuple[Leg, ...]:
        """Os trechos da viagem, seja qual for o tipo."""
        if self.legs:
            return self.legs
        out = [Leg(self.origin, self.destination, self.depart_date)]
        if self.return_date:
            out.append(Leg(self.destination, self.origin, self.return_date))
        return tuple(out)

    @property
    def end_date(self) -> date:
        return self.all_legs()[-1].date

    def route(self) -> str:
        """GRU→LIS, GRU⇄LIS ou GRU→LIS→MAD→GRU."""
        if self.legs:
            pts = [self.legs[0].origin] + [l.destination for l in self.legs]
            return "→".join(pts)
        return f"{self.origin}{'⇄' if self.return_date else '→'}{self.destination}"

    def label(self) -> str:
        if self.legs:
            return f"{self.route()} {self.depart_date.isoformat()} … {self.end_date.isoformat()}"
        s = f"{self.origin}→{self.destination} {self.depart_date.isoformat()}"
        if self.return_date:
            s += f" ↩ {self.return_date.isoformat()}"
        if self.leg_index is not None:
            s += f" (trecho {self.leg_index + 1}, só ida)"
        return s

    # -- derivações ----------------------------------------------------------
    def shift(self, days: int) -> "SearchSpec":
        """Move todas as datas (ida, volta e trechos) o mesmo número de dias."""
        d = timedelta(days=days)
        legs = tuple(Leg(l.origin, l.destination, l.date + d) for l in self.legs)
        return replace(self, depart_date=self.depart_date + d,
                       return_date=self.return_date + d if self.return_date else None, legs=legs)

    def with_dates(self, dates) -> "SearchSpec":
        """Mesma viagem com uma data para cada trecho (na ordem de `all_legs()`)."""
        dates = tuple(dates)
        legs = self.all_legs()
        if len(dates) != len(legs):
            raise ValueError("uma data por trecho")
        if self.legs:
            return replace(self, depart_date=dates[0],
                           legs=tuple(Leg(l.origin, l.destination, d) for l, d in zip(self.legs, dates)))
        return replace(self, depart_date=dates[0], return_date=dates[1] if self.return_date else None)

    def leg_specs(self) -> List["SearchSpec"]:
        """Cada trecho como uma busca só de ida (para comparar com o preço do pacote)."""
        return [SearchSpec(l.origin, l.destination, l.date, None, self.adults, self.cabin, self.max_stops,
                           self.currency, (), i) for i, l in enumerate(self.all_legs())]

    def key(self) -> Tuple:
        """Identifica a busca para comparar com o que está no banco."""
        return (self.depart_date.isoformat(), self.return_date.isoformat() if self.return_date else None,
                self.legs_json, self.adults, self.cabin, self.max_stops, self.currency, self.leg_index)


def normalize_ends(spec: SearchSpec, ends) -> Tuple[Optional[date], ...]:
    """Fim do período de cada trecho (None = só a data do trecho). Valida e devolve uma tupla
    com um item por trecho de `spec.all_legs()`."""
    legs = spec.all_legs()
    ends = tuple(ends) if ends else (None,) * len(legs)
    if len(ends) != len(legs):
        raise ValueError("informe o fim do período de cada trecho (ou deixe em branco)")
    out = []
    for i, (l, e) in enumerate(zip(legs, ends)):
        if e in (None, "", l.date):
            out.append(None)
            continue
        if e < l.date:
            raise ValueError(f"trecho {i + 1}: o fim do período ({e.isoformat()}) é antes do início")
        if (e - l.date).days + 1 > MAX_RANGE_DAYS:
            raise ValueError(f"trecho {i + 1}: período de no máximo {MAX_RANGE_DAYS} dias")
        out.append(e)
    return tuple(out)


def combo_dates(spec: SearchSpec, ends=None, today: Optional[date] = None, include_past: bool = False,
                limit: Optional[int] = None) -> List[Tuple[date, ...]]:
    """Todas as combinações de datas (uma por trecho) dentro dos períodos, com os dias inicial e final
    incluídos. Cada trecho respeita o anterior: a data nunca volta no tempo. Sem períodos, é só a
    data da própria busca. Datas de ida no passado ficam de fora."""
    today = today or date.today()
    ends = normalize_ends(spec, ends)
    legs = spec.all_legs()
    spans = [[l.date + timedelta(days=k) for k in range(((e or l.date) - l.date).days + 1)] for l, e in zip(legs, ends)]
    out: List[Tuple[date, ...]] = []

    def walk(i: int, acc: Tuple[date, ...]) -> bool:
        if i == len(spans):
            out.append(acc)
            return limit is None or len(out) <= limit
        for d in spans[i]:
            if acc and d < acc[-1]:
                continue
            if not include_past and i == 0 and d < today:
                continue
            if not walk(i + 1, acc + (d,)):
                return False
        return True

    walk(0, ())
    return out


def plan_specs(spec: SearchSpec, flex: int = 0, compare_separate: bool = False,
               today: Optional[date] = None, include_past: bool = False, ends=None) -> List[SearchSpec]:
    """Tudo que precisa ser consultado para uma busca monitorada.

    Duas formas de variar as datas (não se misturam):
    - `flex`: todas as datas da viagem andam juntas ±`flex` dias;
    - `ends`: cada trecho tem um período (da sua data até o fim dado, inclusive) e entram todas as
      combinações possíveis.
    Com `compare_separate` e mais de um trecho, vêm também os trechos como só ida (sem repetir).
    Datas passadas ficam de fora.
    """
    today = today or date.today()
    ends = normalize_ends(spec, ends)
    if any(ends) and flex:
        raise ValueError("use período de datas ou ± dias flexíveis, não os dois")
    if any(ends):
        variants = [spec.with_dates(c) for c in combo_dates(spec, ends, today, include_past)]
    else:
        variants = [s for s in (spec.shift(d) for d in range(-flex, flex + 1)) if include_past or s.depart_date >= today]
    out: List[SearchSpec] = []
    seen = set()
    for s in variants:
        group = [s]
        if compare_separate and len(s.all_legs()) > 1:
            group += s.leg_specs()
        for g in group:
            if g.key() + (g.origin, g.destination, g.depart_date) in seen:
                continue
            seen.add(g.key() + (g.origin, g.destination, g.depart_date))
            out.append(g)
    return out


@dataclass
class Offer:
    price: float
    currency: str
    airline: str
    operated_by: Optional[str]
    origin: str
    destination: str
    dep_time: Optional[str]  # ISO local, ex.: 2026-12-20T22:10
    arr_time: Optional[str]
    duration_min: Optional[int]
    stops: int
    flight_numbers: Optional[str]
    layover_airports: Optional[str]
    layovers: List[dict] = field(default_factory=list)
    section: Optional[str] = None  # "Top flights" / "Other flights"
    position: int = 0
    # --- detalhes (vêm do card expandido; None quando o voo não foi detalhado) ---
    cabin: Optional[str] = None
    aircraft: Optional[str] = None
    co2_kg: Optional[int] = None
    co2_diff_pct: Optional[int] = None  # vs. a média da rota; negativo = emite menos
    overnight: Optional[bool] = None
    often_delayed: Optional[bool] = None
    segments: List[dict] = field(default_factory=list)
    # --- tela de compra (só nos voos mais baratos de buscas só de ida) ---
    booking: Optional[Dict] = None

    def key(self) -> tuple:
        return (self.flight_numbers, self.dep_time, self.airline, self.price)


@dataclass
class ScrapeResult:
    offers: List[Offer]
    url: str
    price_level: Optional[str] = None  # low / typical / high (insight do Google)
    typical_low: Optional[float] = None   # faixa de preço típica da rota (insight do Google; só ida e ida e volta)
    typical_high: Optional[float] = None
    cards_no_price: int = 0  # listados sem preço (o Google mostra 'price unavailable')
    cards_unreadable: int = 0  # cards que o parser não entendeu (layout pode ter mudado)
    warnings: List[str] = field(default_factory=list)
