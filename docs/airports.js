/* Aeroportos: busca por código, cidade (em português ou inglês), nome do aeroporto ou país.
 *
 * Dados: docs/airports.json (≈ 8 mil aeroportos com código IATA + códigos de cidade como SAO/RIO/NYC),
 * gerado por scripts/build_airports.py. Os nomes em português abaixo (PT) têm prioridade e também servem
 * de apelido para os demais aeroportos da mesma cidade (ex.: "Rome" → "Roma").
 */
(function (g) {
  "use strict";
  const norm = (s) => String(s || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  const title = (s) => String(s || "").toLowerCase().replace(/(^|[\s\-(/])(\p{L})/gu, (_, a, b) => a + b.toUpperCase()).replace(/\b(De|Do|Da|Dos|Das|Di|Del|La|Le|Los|Las|Y|E|Of|The|Et)\b/g, (w) => w.toLowerCase()).replace(/^./, (c) => c.toUpperCase());

  // Nomes em português dos aeroportos/cidades mais comuns (têm prioridade nas buscas).
  const PT = {
    GRU: "São Paulo", CGH: "São Paulo (Congonhas)", VCP: "Campinas", GIG: "Rio de Janeiro", SDU: "Rio (Santos Dumont)", BSB: "Brasília",
    CNF: "Belo Horizonte", PLU: "Belo Horizonte (Pampulha)", SSA: "Salvador", REC: "Recife", FOR: "Fortaleza", POA: "Porto Alegre",
    CWB: "Curitiba", FLN: "Florianópolis", BEL: "Belém", MAO: "Manaus", NAT: "Natal", MCZ: "Maceió", AJU: "Aracaju", SLZ: "São Luís",
    THE: "Teresina", VIX: "Vitória", GYN: "Goiânia", CGB: "Cuiabá", CGR: "Campo Grande", JPA: "João Pessoa", IGU: "Foz do Iguaçu",
    BPS: "Porto Seguro", LDB: "Londrina", NVT: "Navegantes", JOI: "Joinville", MCP: "Macapá", PMW: "Palmas", UDI: "Uberlândia",
    RAO: "Ribeirão Preto", MGF: "Maringá", BVB: "Boa Vista", PVH: "Porto Velho", RBR: "Rio Branco", STM: "Santarém", IOS: "Ilhéus",
    JDO: "Juazeiro do Norte", PNZ: "Petrolina", VDC: "Vitória da Conquista", CXJ: "Caxias do Sul", XAP: "Chapecó", FEN: "Fernando de Noronha",
    LIS: "Lisboa", OPO: "Porto", FAO: "Faro", MAD: "Madri", BCN: "Barcelona", AGP: "Málaga", VLC: "Valência", SVQ: "Sevilha", CDG: "Paris",
    ORY: "Paris (Orly)", LHR: "Londres", LGW: "Londres (Gatwick)", STN: "Londres (Stansted)", FCO: "Roma", MXP: "Milão", LIN: "Milão (Linate)",
    VCE: "Veneza", NAP: "Nápoles", FLR: "Florença", AMS: "Amsterdã", BRU: "Bruxelas", FRA: "Frankfurt", MUC: "Munique", BER: "Berlim",
    ZRH: "Zurique", GVA: "Genebra", VIE: "Viena", PRG: "Praga", BUD: "Budapeste", WAW: "Varsóvia", CPH: "Copenhague", ARN: "Estocolmo",
    OSL: "Oslo", HEL: "Helsinque", DUB: "Dublin", EDI: "Edimburgo", ATH: "Atenas", IST: "Istambul", DXB: "Dubai", AUH: "Abu Dhabi",
    DOH: "Doha", TLV: "Tel Aviv", CAI: "Cairo", CMN: "Casablanca", RAK: "Marrakech", JNB: "Joanesburgo", CPT: "Cidade do Cabo",
    LAD: "Luanda", MPM: "Maputo", ADD: "Adis Abeba", NBO: "Nairóbi", JFK: "Nova York", EWR: "Nova York (Newark)", LGA: "Nova York (LaGuardia)",
    MIA: "Miami", FLL: "Fort Lauderdale", MCO: "Orlando", TPA: "Tampa", ATL: "Atlanta", BOS: "Boston", IAD: "Washington", DCA: "Washington",
    ORD: "Chicago", DFW: "Dallas", IAH: "Houston", DEN: "Denver", LAS: "Las Vegas", LAX: "Los Angeles", SFO: "São Francisco", SEA: "Seattle",
    YYZ: "Toronto", YUL: "Montreal", YVR: "Vancouver", MEX: "Cidade do México", CUN: "Cancún", PTY: "Cidade do Panamá", SJO: "San José",
    SJU: "San Juan", PUJ: "Punta Cana", HAV: "Havana", BOG: "Bogotá", MDE: "Medellín", CTG: "Cartagena", LIM: "Lima", CUZ: "Cusco",
    UIO: "Quito", GYE: "Guayaquil", SCL: "Santiago", EZE: "Buenos Aires", AEP: "Buenos Aires (Aeroparque)", COR: "Córdoba", MVD: "Montevidéu",
    ASU: "Assunção", VVI: "Santa Cruz de la Sierra", LPB: "La Paz", USH: "Ushuaia", BRC: "Bariloche", MDZ: "Mendoza", NRT: "Tóquio (Narita)",
    HND: "Tóquio (Haneda)", KIX: "Osaka", ICN: "Seul", PEK: "Pequim", PVG: "Xangai", HKG: "Hong Kong", SIN: "Singapura", BKK: "Bangkok",
    DEL: "Nova Délhi", BOM: "Mumbai", SYD: "Sydney", MEL: "Melbourne", AKL: "Auckland",
  };
  // Códigos de cidade: o Google Voos busca em todos os aeroportos da cidade de uma vez.
  const METRO = { SAO: "São Paulo (todos os aeroportos)", RIO: "Rio de Janeiro (todos os aeroportos)", BHZ: "Belo Horizonte (todos os aeroportos)",
    NYC: "Nova York (todos os aeroportos)", WAS: "Washington (todos os aeroportos)", CHI: "Chicago (todos os aeroportos)", LON: "Londres (todos os aeroportos)",
    PAR: "Paris (todos os aeroportos)", ROM: "Roma (todos os aeroportos)", MIL: "Milão (todos os aeroportos)", BUE: "Buenos Aires (todos os aeroportos)",
    TYO: "Tóquio (todos os aeroportos)", OSA: "Osaka (todos os aeroportos)", MOW: "Moscou (todos os aeroportos)", STO: "Estocolmo (todos os aeroportos)",
    BJS: "Pequim (todos os aeroportos)", SEL: "Seul (todos os aeroportos)", YTO: "Toronto (todos os aeroportos)", YMQ: "Montreal (todos os aeroportos)" };
  Object.assign(PT, METRO);

  // Apelidos (inglês/idioma local) das cidades cuja grafia na base não é a que as pessoas costumam digitar.
  const ALT = { FLN: "floripa", SAO: "sampa", GRU: "sampa", RIO: "cidade maravilhosa", VCE: "venice venezia", NAP: "naples napoli", FLR: "florence firenze", MXP: "milan milano", LIN: "milan milano", TRN: "turin torino", BLQ: "bologna",
    CGN: "cologne koln", MUC: "munich munchen", VIE: "vienna wien", PRG: "prague praha", WAW: "warsaw warszawa", CPH: "copenhagen kobenhavn", ATH: "athens athina",
    IST: "istanbul constantinopla", SAW: "istanbul", ZRH: "zurich", GVA: "geneva geneve", BRU: "brussels bruxelles", LIS: "lisbon", OPO: "oporto porto", SVQ: "seville sevilla",
    NRT: "tokyo", HND: "tokyo", KIX: "osaka", ITM: "osaka", PEK: "beijing peking", PKX: "beijing", PVG: "shanghai", ICN: "seoul", DEL: "delhi new delhi", BOM: "bombay mumbai",
    JNB: "johannesburg", CPT: "cape town", MEX: "mexico city ciudad de mexico", PTY: "panama city", SJU: "san juan puerto rico", HAV: "havana la habana",
    LHR: "london", LGW: "london", STN: "london", LTN: "london", LCY: "london", CDG: "paris", ORY: "paris", JFK: "new york", EWR: "new york", LGA: "new york",
    IAD: "washington dc", DCA: "washington dc", SFO: "san francisco", LAX: "los angeles", MIA: "miami", MCO: "orlando", DXB: "dubai", DOH: "doha" };

  let rows = [], byCode = new Map(), ptByCity = new Map(), regionNames = null;
  const country = (cc) => {
    if (regionNames === null) { try { regionNames = new Intl.DisplayNames(["pt-BR"], { type: "region" }); } catch { regionNames = false; } }
    try { return (regionNames && cc && regionNames.of(cc)) || cc || ""; } catch { return cc || ""; }
  };

  function build(list) {
    byCode = new Map(); ptByCity = new Map();
    // cidade em inglês -> nome em português, a partir dos aeroportos que já temos em português
    for (const [iata, city] of list) if (PT[iata] && !METRO[iata]) ptByCity.set(norm(city), PT[iata].replace(/ \(.*\)$/, ""));
    rows = list.map(([iata, city, name, cc]) => {
      const pt = PT[iata] || ptByCity.get(norm(city)) || null;
      const cityLabel = pt || title(city);
      const ctry = country(cc);
      const base = cityLabel.replace(/ \(.*\)$/, "");
      const o = { iata, city: cityLabel, name: METRO[iata] ? "Todos os aeroportos da cidade" : name, country: ctry, pop: PT[iata] ? 1 : 0, metro: !!METRO[iata],
        cityN: norm(base), enN: norm(city), altN: norm(ALT[iata] || ""), nameN: norm(name), ctryN: norm(ctry), intl: /international|intl/i.test(name) };
      byCode.set(iata, o);
      return o;
    });
    // aeroportos "curados" que não estão na base (segurança)
    for (const [iata, label] of Object.entries(PT)) if (!byCode.has(iata)) {
      const o = { iata, city: label, name: "", country: "", pop: 1, metro: !!METRO[iata], cityN: norm(label.replace(/ \(.*\)$/, "")), enN: "", altN: "", nameN: "", ctryN: "", intl: false };
      byCode.set(iata, o); rows.push(o);
    }
  }
  const wordStart = (hay, q) => hay === q || hay.startsWith(q + " ") || hay.includes(" " + q);

  function scoreOf(a, nq, tokens) {
    if (a.iata.toLowerCase() === nq) return 1000;
    let s = 0;
    if (nq.length >= 2 && a.iata.toLowerCase().startsWith(nq)) s = 560;
    if (a.cityN === nq || a.enN === nq) s = Math.max(s, 700);
    else if (a.cityN.startsWith(nq) || a.enN.startsWith(nq)) s = Math.max(s, 520);
    else if (a.altN && (a.altN === nq || a.altN.startsWith(nq) || wordStart(a.altN, nq))) s = Math.max(s, 500);
    else if (wordStart(a.cityN, nq) || wordStart(a.enN, nq)) s = Math.max(s, 380);
    else if (a.nameN.startsWith(nq) || wordStart(a.nameN, nq)) s = Math.max(s, 260);
    if (!s && tokens.length > 1) {  // "sao paulo", "rio de janeiro": todas as palavras, em qualquer campo
      const hay = `${a.cityN} ${a.enN} ${a.nameN} ${a.ctryN}`;
      if (tokens.every((t) => wordStart(hay, t))) s = 300;
    }
    if (!s && nq.length >= 3 && (a.cityN.includes(nq) || a.enN.includes(nq))) s = 140;
    if (!s && nq.length >= 3 && (a.nameN.includes(nq) || a.ctryN.startsWith(nq))) s = 90;
    return s ? s + (a.pop ? 60 : 0) + (a.metro ? 25 : 0) + (a.intl ? 12 : 0) : 0;
  }

  function search(q, limit = 8) {
    const nq = norm(q);
    if (!nq || !rows.length) return [];
    const tokens = nq.split(" ");
    const out = [];
    for (const a of rows) { const s = scoreOf(a, nq, tokens); if (s) out.push([s, a]); }
    out.sort((x, y) => y[0] - x[0] || x[1].city.localeCompare(y[1].city) || x[1].iata.localeCompare(y[1].iata));
    return out.slice(0, limit).map(([, a]) => a);
  }
  const describe = (iata) => byCode.get(String(iata || "").toUpperCase()) || null;
  const cityOf = (iata) => { const d = describe(iata); return (d && d.city) || PT[iata] || iata; };
  /** Texto digitado -> código IATA (um código conhecido, ou o 1º resultado da busca). */
  function resolve(text) {
    const t = String(text || "").trim();
    if (/^[A-Za-z]{3}$/.test(t) && byCode.has(t.toUpperCase())) return t.toUpperCase();
    const r = search(t, 1)[0];
    return r ? r.iata : null;
  }

  let loading = null;
  function load(url = "airports.json") {
    return (loading ||= fetch(url).then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((list) => { build(list); return rows.length; })
      .catch(() => { build([]); return rows.length; }));  // sem o arquivo, ainda funciona com a lista em português
  }
  build([]);
  g.Airports = { load, search, describe, cityOf, resolve, norm, _build: build, get size() { return rows.length; } };
})(globalThis);
