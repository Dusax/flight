"""Monta a URL de busca do Google Voos.

O Google aceita a busca inteira no parâmetro `tfs`: um protobuf em base64
com rotas, datas, passageiros, classe e limite de escalas. Em vez de
depender de uma lib, codificamos o protobuf à mão (são só 3 tipos de campo).
O parâmetro `tfu` é exigido pelo backend; sem ele a página responde
"Oops, something went wrong".
"""
from __future__ import annotations

import base64
from typing import Optional
from urllib.parse import urlencode

from .models import CABINS, SearchSpec

BASE_URL = "https://www.google.com/travel/flights"
TFU = "EgQIABABIgA"


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _field_varint(num: int, value: int) -> bytes:
    return _varint(num << 3) + _varint(value)


def _field_bytes(num: int, data) -> bytes:
    if isinstance(data, str):
        data = data.encode()
    return _varint(num << 3 | 2) + _varint(len(data)) + data


def _leg(date_iso: str, origin: str, dest: str, max_stops) -> bytes:
    msg = _field_bytes(2, date_iso)
    if max_stops is not None:
        msg += _field_varint(5, max_stops)
    msg += _field_bytes(13, _field_bytes(2, origin))
    msg += _field_bytes(14, _field_bytes(2, dest))
    return msg


def build_tfs(spec: SearchSpec) -> str:
    # Um bloco por trecho. Ida e volta = 2 trechos (volta com a rota invertida); multi-destino = N trechos.
    if spec.legs:
        legs = [(l.date.isoformat(), l.origin, l.destination) for l in spec.legs]
        trip = 3  # multi-destino
    else:
        legs = [(spec.depart_date.isoformat(), spec.origin, spec.destination)]
        if spec.return_date:
            legs.append((spec.return_date.isoformat(), spec.destination, spec.origin))
        trip = 1 if spec.return_date else 2  # 1 = ida e volta, 2 = só ida
    msg = b"".join(_field_bytes(3, _leg(d, o, t, spec.max_stops)) for d, o, t in legs)
    for _ in range(spec.adults):
        msg += _field_varint(8, 1)  # 1 = adulto
    msg += _field_varint(9, CABINS[spec.cabin])
    msg += _field_varint(19, trip)
    return base64.urlsafe_b64encode(msg).decode().rstrip("=")


def build_url(spec: SearchSpec, country: Optional[str] = "BR") -> str:
    """`country` (gl) importa: sem ele o Google pode dizer "Booking isn't supported in your location"
    e esconder as opções de compra. Passe None para não enviar."""
    # hl=en é fixo de propósito: o parser lê os aria-labels em inglês.
    params = {"hl": "en", "curr": spec.currency, "tfu": TFU, "tfs": build_tfs(spec)}
    if country:
        params["gl"] = country.upper()
    return f"{BASE_URL}?{urlencode(params)}"
