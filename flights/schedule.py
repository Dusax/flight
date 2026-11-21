"""Horários de coleta em formato cron, no fuso de Brasília.

`schedule` é um cron de 5 campos (`min hora dia-do-mês mês dia-da-semana`). O coletor do GitHub roda de hora em hora,
então a unidade é a **hora cheia**: o campo de minutos é aceito mas ignorado. Suporta `*`, `a`, `a-b`, `a,b`, `*/n`,
`a-b/n`, nomes de dia (SUN..SAT) e de mês (JAN..DEC), e `0`/`7` = domingo. Extensão: faixa de horas que vira a
meia-noite (`22-5`). Com dia-do-mês e dia-da-semana ambos restritos vale um OU o outro (regra padrão do cron).

Esta lógica é espelhada em docs/schedule.js (o painel e o servidor); tests/schedule_cases.json garante que as duas
implementações concordam.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import FrozenSet, Optional
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Sao_Paulo")
CATCHUP_H = 3  # o GitHub atrasa/pula rodadas: um horário perdido ainda é coletado até 3 horas cheias depois

DOW_NAMES = {"SUN": 0, "MON": 1, "TUE": 2, "WED": 3, "THU": 4, "FRI": 5, "SAT": 6}
MONTH_NAMES = {n: i + 1 for i, n in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}


@dataclass(frozen=True)
class Schedule:
    expr: str
    hours: FrozenSet[int]
    dom: FrozenSet[int]
    months: FrozenSet[int]
    dow: FrozenSet[int]       # 0 = domingo
    dom_any: bool
    dow_any: bool


def _field(text: str, lo: int, hi: int, what: str, names=None, wrap: bool = False):
    """Interpreta um campo do cron. Devolve (conjunto de valores, começa com '*')."""
    out = set()
    for part in text.split(","):
        if not part:
            raise ValueError(f"{what}: campo vazio")
        rng, _, step_s = part.partition("/")
        if step_s:
            if not step_s.isdigit() or int(step_s) < 1:
                raise ValueError(f"{what}: passo inválido em {part!r}")
            step = int(step_s)
        else:
            step = 1

        def val(tok: str) -> int:
            t = tok.upper()
            if names and t in names:
                return names[t]
            if not tok.isdigit():
                raise ValueError(f"{what}: valor inválido {tok!r}")
            v = int(tok)
            if not lo <= v <= hi:
                raise ValueError(f"{what}: {v} fora de {lo}-{hi}")
            return v

        if rng == "*":
            a, b = lo, hi
        elif "-" in rng:
            x, _, y = rng.partition("-")
            a, b = val(x), val(y)
        else:
            a = val(rng)
            b = hi if step_s else a
        if a <= b:
            vals = range(a, b + 1, step)
        elif wrap:
            vals = (list(range(a, hi + 1)) + list(range(lo, b + 1)))[::step]   # 22-5 = 22,23,0..5
        else:
            raise ValueError(f"{what}: faixa {a}-{b} ao contrário")
        out.update(vals)
    return out, text.startswith("*")


def parse(expr: str) -> Schedule:
    if not isinstance(expr, str):
        raise ValueError("horários: expressão inválida")
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("horários: use 5 campos (minuto hora dia-do-mês mês dia-da-semana), ex.: 0 22-23,0-5 * * *")
    _field(parts[0], 0, 59, "minuto")  # só valida: a coleta é de hora em hora
    hours, _ = _field(parts[1], 0, 23, "hora", wrap=True)
    dom, dom_any = _field(parts[2], 1, 31, "dia do mês")
    months, _ = _field(parts[3], 1, 12, "mês", MONTH_NAMES)
    dow, dow_any = _field(parts[4], 0, 7, "dia da semana", DOW_NAMES)
    dow = {0 if d == 7 else d for d in dow}
    return Schedule(" ".join(parts), frozenset(hours), frozenset(dom), frozenset(months), frozenset(dow), dom_any, dow_any)


def _local(t: datetime) -> datetime:
    return t.astimezone(TZ)


def matches(s: Schedule, t: datetime) -> bool:
    """A hora de `t` (no fuso de Brasília) é um horário de coleta?"""
    lt = _local(t)
    if lt.hour not in s.hours or lt.month not in s.months:
        return False
    dow_ok = ((lt.weekday() + 1) % 7) in s.dow
    dom_ok = lt.day in s.dom
    if s.dom_any and s.dow_any:
        return True
    if s.dom_any:
        return dow_ok
    if s.dow_any:
        return dom_ok
    return dom_ok or dow_ok


def _floor_hour(t: datetime) -> datetime:
    return t.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def latest_slot(s: Schedule, now: datetime, lookback_h: int = CATCHUP_H) -> Optional[datetime]:
    """Horário de coleta mais recente ≤ agora, dentro da folga. Em UTC, na hora cheia."""
    base = _floor_hour(now)
    for k in range(lookback_h + 1):
        t = base - timedelta(hours=k)
        if matches(s, t):
            return t
    return None


def next_slot(s: Schedule, after: datetime, horizon_days: int = 400) -> Optional[datetime]:
    """Próximo horário de coleta depois de `after` (em UTC, hora cheia)."""
    t = _floor_hour(after) + timedelta(hours=1)
    for _ in range(horizon_days * 24):
        if matches(s, t):
            return t
        t += timedelta(hours=1)
    return None


def due(s: Schedule, now: datetime, last_run: Optional[datetime]) -> bool:
    """Há um horário de coleta (≤ agora, dentro da folga) ainda não atendido por nenhuma coleta?"""
    slot = latest_slot(s, now)
    return slot is not None and (last_run is None or last_run < slot)
