"""Que combinações de datas consultar numa coleta quando o período gera mais do que cabe no orçamento.

Cada combinação é uma consulta ao Google (~20 s, e muitas seguidas de um mesmo IP viram captcha). Em vez de
recusar períodos grandes, cada coleta consulta no máximo `budget` combinações, escolhidas assim:

1. **Mais baratas já vistas** (até `hot`): mantém o preço dos melhores candidatos sempre fresco.
2. **Exploração**, alternando duas listas:
   - *vizinhas das mais baratas*: preços de datas vizinhas são parecidos, então quem está ao lado de uma data
     barata tem mais chance de ser barata (subida de encosta);
   - *amostra espalhada*: da malha mais grossa para a mais fina (de 8 em 8 dias, depois de 4 em 4, 2 em 2…),
     para nenhuma região do período ficar sem olhar.
3. **Mais antigas**: com tudo já consultado, renova as que estão há mais tempo sem coleta.

Assim, na 1ª coleta sai uma amostra espalhada; nas seguintes o orçamento se concentra ao redor do que é barato e
o resto do período vai sendo coberto, sem estourar o limite.
"""
from __future__ import annotations

from datetime import date
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

Combo = Tuple[date, ...]
Known = Dict[Combo, Tuple[Optional[float], str]]  # combinação -> (menor preço da última coleta, quando)

NEAR_TOP = 10  # "vizinhas" das N combinações mais baratas


def hot_count(budget: int) -> int:
    return max(3, budget // 4)


def _idx(c: Combo, starts: Sequence[date]) -> Tuple[int, ...]:
    return tuple((d - s).days for d, s in zip(c, starts))


def _coarse_level(ix: Tuple[int, ...]) -> int:
    """Quanto maior, mais grossa a malha em que o ponto aparece (múltiplos de 8 vêm antes dos de 4, 2, 1)."""
    lv = 99
    for i in ix:
        lv = min(lv, 99 if i == 0 else (i & -i).bit_length() - 1)
    return lv


def order(combos: Iterable[Combo], starts: Sequence[date], known: Known, hot: int = 3) -> List[Combo]:
    """Todas as combinações, da mais para a menos prioritária. O chamador corta no orçamento."""
    combos = list(combos)
    seen = [c for c in combos if c in known]
    unseen = [c for c in combos if c not in known]

    priced = sorted((c for c in seen if known[c][0] is not None), key=lambda c: (known[c][0], c))
    hot_list = priced[:hot]
    hot_set = set(hot_list)

    coarse = sorted(unseen, key=lambda c: (-_coarse_level(_idx(c, starts)), c))
    best_ix = [_idx(c, starts) for c in priced[:NEAR_TOP]]
    near: List[Combo] = []
    if best_ix:
        def dist(c: Combo) -> int:
            ix = _idx(c, starts)
            return min(sum(abs(a - b) for a, b in zip(ix, bx)) for bx in best_ix)
        near = sorted(unseen, key=lambda c: (dist(c), -_coarse_level(_idx(c, starts)), c))

    out: List[Combo] = list(hot_list)
    taken = set(out)
    ni = ci = 0
    take_near = True
    while ni < len(near) or ci < len(coarse):
        src, pos = (near, ni) if (take_near and ni < len(near)) or ci >= len(coarse) else (coarse, ci)
        c = src[pos]
        if src is near:
            ni += 1
        else:
            ci += 1
        if c not in taken:
            out.append(c)
            taken.add(c)
            take_near = not take_near if near else False  # sem dados ainda: só a malha espalhada
    stale = sorted((c for c in seen if c not in hot_set), key=lambda c: (known[c][1], c))
    out.extend(stale)
    return out


def coverage_runs(unseen: int, budget: int) -> int:
    """Estimativa de coletas até ver tudo (boa parte do orçamento vai para as mais baratas)."""
    per_run = max(1, budget - hot_count(budget))
    return max(1, -(-unseen // per_run))
