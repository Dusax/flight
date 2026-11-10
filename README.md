# flights — preços do Google Voos, num painel seu

Coleta preços de passagens no Google Voos (navegador headless), guarda num banco
SQLite e mostra tudo num painel web (Vercel): mais barata, mais barata direta,
viagem mais rápida, datas, histórico de preço, a lista de voos e os detalhes de
cada um (aeronave, assentos, bagagem, vendedor...). Você cadastra as buscas e a
frequência de cada uma pelo próprio painel e pode disparar uma coleta na hora.

## Tipos de busca

| Tipo | No painel | Na linha de comando |
|---|---|---|
| **Só ida** | "Só ida" | `flights search GRU GIG 2026-12-05` |
| **Ida e volta** | "Ida e volta" | `flights search GRU LIS 2026-12-20 --return 2026-12-30` (ou `--nights 10`) |
| **Multi-destino** (2 a 5 trechos) | "Multi-destino" | `flights multi GRU@2026-12-20 LIS@2026-12-27 MAD@2027-01-03 GRU` |
| **Comparar com cada trecho separado** | caixa "Comparar com cada trecho…" | `--compare-separate` |

- **Multi-destino:** cada ponto leva a data em que você *sai* dele; o último é só o
  aeroporto final e pode ser outro (ex.: ida por Lisboa, volta por Roma: "open jaw").
- **Comparar separado:** além do pacote, consulta cada trecho como só ida e soma os
  mais baratos. O painel diz se o pacote ou o separado compensa, e quanto. Vale para
  ida e volta e multi-destino. Passagens separadas não têm proteção de conexão.
- **Datas flexíveis (±N dias):** todas as datas da viagem andam juntas.
- Em ida e volta e multi-destino, o Google lista os voos do **1º trecho** com o preço
  **da viagem inteira**; os demais trechos só aparecem depois de escolher o voo no
  site. Para ver os outros trechos, use a comparação por trecho.

```
 REPOSITÓRIO PÚBLICO  (este: código, workflow, página)        REPOSITÓRIO PRIVADO  (só os seus dados)
 ┌───────────────────────────────────────────────┐             ┌──────────────────────────┐
 │ docs/index.html ──► Vercel (só a página)      │             │ watches.json  (suas buscas)
 │ .github/workflows/scrape.yml (Actions, grátis)│◄── token ──►│ dashboard.json (resultados)
 └───────────────────────────────────────────────┘             │ cloud.db      (histórico)
        ▲                                                       └──────────────────────────┘
        │ "Buscar agora" dispara o workflow                              ▲
        └──────────────── a página lê/grava os dados pela API do GitHub, com o seu token
```

**Por que dois repositórios:** repositório privado consome a cota de minutos do Actions (e a conta
pode travar por cobrança); repositório público roda de graça. Então o código fica público e os dados
(o que você pesquisa, preços, histórico) ficam num repositório privado à parte. O Actions lê e grava
nele com um token dedicado.

**Privacidade, em concreto:**
- Os dados nunca estão no repositório público nem no site da Vercel (que serve só `docs/`).
- Os logs do Actions de repositório público são públicos, então o scraper roda em modo
  silencioso (`FLIGHTS_QUIET=1`): só imprime contagens e status, nunca rota, data, preço nem mensagem de
  erro (as do Playwright trazem a URL da busca). Não há upload de artefatos de debug.
- Sem o token, quem abrir o painel não vê nada. O `vercel.json` manda `noindex` e uma CSP que só
  deixa a página falar com `api.github.com`.
- O que continua visível publicamente: o código, e que o workflow rodou (horários e se deu certo).

## Configurar (uma vez)

1. **Crie o repositório de dados, PRIVADO e vazio:** *New repository* → nome `flight-data` (padrão:
   `<este-repo>-data`) → **Private** → marque *Add a README*.
2. **Token do Actions:** em <https://github.com/settings/personal-access-tokens/new> crie um token
   *fine-grained* só com o repositório `flight-data` e permissão **Contents: Read and write**. No
   repositório do código: *Settings → Secrets and variables → Actions → New repository secret* com
   nome **`DATA_REPO_TOKEN`**. (Se usar outro nome de repo de dados, crie a *variable* `DATA_REPO`
   com `dono/nome`.)
3. **Torne o repositório do código público:** *Settings → General → Danger Zone → Change visibility*.
4. **Vercel:** *Add New → Project → Import* o repositório do código. Não precisa configurar nada: o
   `vercel.json` já aponta a pasta `docs` e não há build. (Os dados ficam em outro repositório, então não geram deploys.)
5. **Token do painel:** outro token *fine-grained*, com os **dois** repositórios, permissões
   **Contents: Read and write** e **Actions: Read and write**.
6. Abra o painel (`*.vercel.app`) → **⚙ Conexão**: repositório do código (`dono/flight`), repositório
   dos dados (deixe vazio para usar `<código>-data`) e cole o token. Ele fica só no seu navegador
   (localStorage) e só é enviado para `api.github.com`. O painel recusa um repositório de dados público.

Pronto: **+ Nova busca** (origem/destino em código IATA, datas, adultos, classe,
escalas, datas flexíveis ± N dias e **frequência**: manual, 1 h, 3 h, 6 h, 12 h, todo dia…).

> O cron do GitHub roda de hora em hora e pode atrasar alguns minutos (menor frequência útil: 1 h).
> Em repositório público o GitHub desativa o agendamento após 60 dias sem atividade; o workflow tenta
> evitar isso reabilitando a si mesmo a cada rodada. Se mesmo assim parar, reative em *Actions* (o botão
> "Buscar agora" continua funcionando).

## Usar localmente (sem GitHub)

```bash
pip install -e .
playwright install chromium

flights search GRU LIS 2026-12-20 --return 2026-12-30 --adults 2   # busca agora e grava
flights search GRU LIS 20/12/2026 --nights 10 --flex 2             # ±2 dias em torno da ida
flights multi GRU@2026-12-20 LIS@2026-12-27 MAD@2027-01-03 GRU --compare-separate
flights search GRU GIG 2026-12-05 --max-stops 0 --booking 3        # + tela de compra dos 3 mais baratos
flights watch add GRU GIG 2026-12-05 --max-stops 0 --every 6       # cadastra uma rota
flights watch add-multi GRU@2026-12-20 LIS@2026-12-27 GRU --every 24 --compare-separate
flights run                 # coleta todas as rotas ativas   (run --due: só as vencidas)
flights history GRU LIS     # evolução do menor preço (por rota)  |  history --watch 3 (por busca)
flights offers --details    # voos da última coleta, com aeronave, assentos, conexões e compra
flights export --out data/export.csv
flights publish             # gera data/dashboard.json para o painel
```

O banco local fica em `data/flights.db` (ignorado pelo git); o do GitHub Actions é
`data/cloud.db`. Use `--headed` para ver o navegador e `--debug` para salvar
HTML + screenshot quando algo falhar. Se o Chromium estiver em outro lugar:
`--chromium-path` ou `FLIGHTS_CHROMIUM_PATH`.

## O que é gravado

- `searches`: uma linha por coleta (quando, rota, datas, trechos, status, menor preço,
  menor direto, viagem mais rápida, avaliação do Google *low/typical/high*).
- `offers`: um voo por linha.
  - **Sempre:** preço, companhia, nº dos voos, saída/chegada, duração, escalas, conexões.
  - **Detalhes** (dos 30 mais baratos; 15 no Actions): classe, aeronave de cada trecho,
    espaço entre assentos, Wi-Fi/tomadas/vídeo, "costuma atrasar", pernoite, emissões de CO₂
    (vs. a média da rota), duração de cada conexão.
  - **Compra** (só em buscas só de ida, dos N mais baratos; N = "Detalhar compra"):
    bagagem de mão e despachada (grátis ou paga, e quanto), vendedor com preço, faixa de
    preço típica da rota ("R$ 2.100–4.600, este voo está normal/baixo/alto") e o link do voo
    no Google Voos.
- `watches`: as buscas cadastradas, frequência, tipo, trechos e opções.

**Sobre "tarifa":** o Google Voos só mostra a marca da tarifa (Light, Standard, Flex…) em
alguns casos. Nos testes (LATAM, Gol, Tap, Ryanair, easyJet) ele mostrou apenas classe,
preço do vendedor e condições de bagagem, e é isso que é guardado. Se o Google passar a exibir
mais texto por vendedor, ele vai para o campo `notes` do vendedor.

**Como ler os preços:** o valor é o **total para todos os passageiros** (o painel
mostra também o valor por adulto). Em ida e volta e multi-destino é o menor total da viagem
inteira com aquele voo no 1º trecho.

## Limites e cuidados

- **Não foi testado rodando dentro do GitHub Actions.** O scraper foi validado ao
  vivo, mas o Google pode tratar IPs de datacenter (como os do Actions) com mais
  desconfiança e pedir captcha. Se acontecer, o painel mostra a coleta como
  bloqueada e o log traz o motivo. Plano B: rodar `flights run` + `flights publish`
  no seu computador e dar `git push`.
- O scraper lê o `aria-label` dos cards (em inglês, `hl=en` fixo) e não classes CSS,
  que o Google ofusca e muda. Se o layout mudar, a coleta falha de forma explícita
  ("cards ilegíveis") em vez de gravar lixo.
- Automatizar o Google Voos vai contra os termos de uso do Google. Mantenha o
  volume baixo (use frequências razoáveis; o scraper já espaça as consultas) e por
  sua conta e risco.
- Só adultos; moeda BRL no painel.
- As opções de compra dependem do país da consulta: o scraper usa `gl=BR` (mude com
  `--country` ou `FLIGHTS_COUNTRY`). Sem país o Google respondeu "Booking isn't supported
  yet in your location".
- Abrir a tela de compra é uma navegação por voo (~5 s cada) e aumenta a chance de
  bloqueio; por isso o padrão é detalhar só o voo mais barato.
- Se você editar as datas/trechos de uma busca, as coletas antigas dela deixam de aparecer
  no painel (ficam no banco).

## Testes

```bash
pip install pytest && pytest
```
