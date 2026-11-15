"""Gera docs/airports.json a partir do pacote `airportsdata` (MIT; dados do OurAirports e outras fontes abertas).

    pip install airportsdata && python scripts/build_airports.py

Formato: [[IATA, cidade, nome do aeroporto, país (ISO-2)], ...] ordenado por IATA. O painel usa isso
para o autocompletar de aeroportos e para mostrar o nome da cidade em qualquer viagem.
"""
import json
from pathlib import Path

import airportsdata

rows = []
for code, a in airportsdata.load("IATA").items():
    if len(code) != 3 or not code.isalpha():
        continue
    rows.append([code, (a["city"] or "").strip(), (a["name"] or "").strip(), (a["country"] or "").strip()])
# Códigos de cidade (todos os aeroportos da cidade): o Google Voos aceita, e dá menos trabalho que escolher um a um.
METRO = {"SAO": ("Sao Paulo", "BR"), "RIO": ("Rio De Janeiro", "BR"), "BHZ": ("Belo Horizonte", "BR"), "NYC": ("New York", "US"), "WAS": ("Washington", "US"),
         "CHI": ("Chicago", "US"), "LON": ("London", "GB"), "PAR": ("Paris", "FR"), "ROM": ("Rome", "IT"), "MIL": ("Milan", "IT"), "BUE": ("Buenos Aires", "AR"),
         "TYO": ("Tokyo", "JP"), "OSA": ("Osaka", "JP"), "MOW": ("Moscow", "RU"), "STO": ("Stockholm", "SE"), "BJS": ("Beijing", "CN"), "SEL": ("Seoul", "KR"),
         "YTO": ("Toronto", "CA"), "YMQ": ("Montreal", "CA")}
have = {r[0] for r in rows}
rows += [[code, city, "All airports", cc] for code, (city, cc) in METRO.items() if code not in have]
rows.sort(key=lambda r: r[0])
out = Path(__file__).resolve().parent.parent / "docs" / "airports.json"
out.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print(f"{len(rows)} aeroportos -> {out} ({out.stat().st_size // 1024} KB)")
