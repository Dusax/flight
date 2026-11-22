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
- **Versão do painel:** o rodapé mostra a versão (commit) em que a aba está rodando; a cada 5 minutos e quando você volta
  para a aba, o painel confere se a Vercel publicou algo novo e mostra “Há uma nova versão do painel” com o botão
  **Recarregar**. Se uma funcionalidade nova não aparece: confira a versão no rodapé com o último commit da branch,
  recarregue com Ctrl+F5 e, se a versão continuar antiga, veja em Vercel → Deployments se o último commit está
  “Ready” e se o *Production Branch* é a branch que você usa.
- **Horários de coleta (cron):** em “Coletar automaticamente” escolha **Horários personalizados…**: janela (ex.: das 22h às
  6h, aceita virar a meia-noite), repetir a cada N horas dentro dela e dias da semana, com atalhos (noite e madrugada,
  só madrugada, horário comercial) e um campo **cron avançado** sincronizado (`0 22,0,2,4,6 * * 1-5`; use-o para duas
  janelas, meses ou dia do mês). A prévia mostra as próximas coletas. Fuso de **Brasília**. A coleta roda de hora em
  hora (~7 min depois do horário cheio), então a unidade é a hora cheia e o campo de minutos é ignorado. Se o GitHub
  atrasar ou pular uma rodada, o horário perdido ainda é coletado até 3 horas cheias depois. O cron manda sobre “a cada
  N horas”; buscas sem cron continuam como antes; consulta pontual nunca é agendada. Quando nada vence, a rodada
  pula a instalação do Chromium e a coleta (disparo manual sempre coleta). Linha de comando:
  `flights watch add ... --schedule "0 22,0,2,4,6 * * *"` e `flights due`.
- **Melhor data:** a de menor preço; em empate de preço, a de voo mais curto (duração do voo mais barato dela); se ainda
  empatar em preço **e** tempo, todas as datas empatadas são listadas como melhores (cartão “Melhores datas: …”, aba
  Datas “Melhor · 7h40”, Resumo “Empata com …”). Datas de mesmo preço e voo mais longo aparecem como “Mesmo preço”.
- **Faixa de preço típica:** em toda busca só ida e ida e volta a coleta abre o “View price history” do Google e guarda
  a faixa em que costumam ficar os voos mais baratos da rota (ex.: R$ 3.700–4.600) e o nível (baixo/normal/alto). Aparece
  no cartão da viagem, no Resumo e no topo da aba Voos, com a posição do preço atual na faixa. A tela de compra (só ida,
  voos mais baratos) continua trazendo a sua própria faixa, que tem prioridade. Multi-destino não tem: o Google não mostra.
- **Consulta pontual:** o botão **Consulta pontual** (raio, no topo) busca uma vez, na hora, sem cadastrar para repetir.
  Usa o mesmo formulário (ida, ida e volta, multi-destino, períodos de datas), mas sem nome nem frequência. Dispara a
  coleta e o resultado (1 a 5 min) fica na seção **Consultas pontuais** da lista, fora dos indicadores. Nunca entra no
  agendamento nem no “Buscar agora” geral; no detalhe, **Monitorar daqui pra frente** a transforma numa viagem
  monitorada, e **Limpar todas** apaga as pontuais. Tecnicamente é uma busca com `once: true` no `watches.json`.
- **Voos favoritos:** na aba Voos de qualquer viagem, o botão **Favoritar** (estrela) guarda o voo. A aba **Favoritos**
  (botão no topo) junta todos, com o preço de quando você salvou, o preço atual, a variação a cada coleta, se o voo
  apareceu na última coleta e um atalho “Ver na viagem”. Ordena por mais recentes, menor preço ou data. Desfazer ao remover.
  Ficam em `favorites.json` (repositório privado, até 200); o histórico de cada voo vem do banco, reconhecendo-o pelo
  número do voo e horário de saída. O banco guarda os voos mais baratos de cada coleta: se o voo sair dessa lista,
  o painel avisa “não apareceu na última coleta”.
- **Datas flexíveis (±N dias):** todas as datas da viagem andam juntas.
- **Período de datas (de X até Y, inclusive):** marque “Pesquisar um período de datas” e escolha o fim do período de cada
  data. Só ida: uma consulta por dia, de X a Y. Ida e volta: período da ida × período da volta (a volta nunca antes da ida).
  Multi-destino: cada trecho tem o seu período e entram todas as combinações em que os trechos seguem em ordem.
  O formulário mostra quantas combinações o período tem. Não se mistura com ± dias. Limites: 60 dias por período e
  2000 combinações por busca.
- **Períodos grandes (orçamento por coleta):** cada combinação é uma consulta ao Google (~20 s, e muitas seguidas de
  um mesmo IP viram captcha). Em vez de recusar, cada coleta consulta no máximo N combinações (“Consultas por coleta”:
  20, 40, 60 ou 100; padrão 40), escolhidas assim: as mais baratas já vistas (para manter o preço fresco), as
  vizinhas das mais baratas (preços de datas próximas são parecidos), uma amostra espalhada do resto (da malha mais
  grossa para a mais fina) e, quando tudo já foi visto, as que estão há mais tempo sem coleta. A 1ª coleta é uma
  amostra do período inteiro, as seguintes se concentram no que é barato e o resto vai sendo coberto; o painel mostra
  “N de M combinações consultadas”. O trecho separado (comparar) conta dentro do orçamento. O JSON do painel guarda a
  lista de voos só das 20 combinações mais baratas. Com várias viagens, as consultas são intercaladas e a execução
  para de começar consultas depois de 38 min (`FLIGHTS_TIME_BUDGET`): o que faltar fica para a próxima coleta.
  Na linha de comando: `flights watch add ... --depart-to ... --budget 60` e `flights run --time-budget 38`.
- Em ida e volta e multi-destino, o Google lista os voos do **1º trecho** com o preço
  **da viagem inteira**; os demais trechos só aparecem depois de escolher o voo no
  site. Para ver os outros trechos, use a comparação por trecho.

```
 navegador ──(senha → cookie de sessão HttpOnly)──►  Vercel
                                                      ├─ docs/  a página
                                                      └─ api/   funções (login, dados, disparar coleta)
                                                           │  GH_TOKEN: só aqui, nunca no navegador
                                                           ▼
 REPOSITÓRIO PÚBLICO  (código, workflow)  ◄──────►  REPOSITÓRIO PRIVADO  (só os seus dados)
   .github/workflows/scrape.yml (Actions, grátis)       watches.json   suas buscas
   scraper → grava no repo privado ───────────────────► dashboard.json resultados
                                                        favorites.json voos favoritos
                                                        cloud.db       histórico
```

**Por que dois repositórios:** repositório privado consome a cota de minutos do Actions; público
roda de graça. O código fica público; o que você pesquisa, os preços e o histórico ficam no privado.

## Segurança

- **Login por senha.** Sem sessão válida, as funções `/api/*` respondem 401 e nunca chegam ao GitHub.
  A senha é comparada em tempo constante; depois de 5 erros seguidos o IP é bloqueado por 15 min
  (freio por instância: a defesa principal é uma senha longa).
- **O token do GitHub nunca vai para o navegador.** Ele vive na variável `GH_TOKEN` da Vercel. A página
  só tem um cookie de sessão `HttpOnly` + `SameSite=Strict` (+ `Secure` e prefixo `__Host-` em https),
  que o JavaScript não consegue ler. A sessão vale 7 dias e é assinada (HMAC); **trocar a senha
  derruba todas as sessões** (para derrubar sem trocar a senha, mude o `SESSION_SECRET`).
- **O servidor só faz o que o painel precisa:** ler `dashboard.json`/`watches.json`/`favorites.json` do repositório de dados,
  gravar `watches.json` e `favorites.json` (campos e limites validados) e disparar o workflow do repositório do código. O
  navegador não escolhe URL nem repositório. Erros do GitHub não são repassados ao navegador.
- **CSP restrita:** a página só conecta em si mesma (`connect-src 'self'`) e não roda nenhum script
  embutido (`script-src 'self'`), além de `noindex`, sem frames e sem referrer.
- **Logs do Actions** (públicos neste modelo) saem em modo silencioso: só contagens e status, nunca rota,
  data, preço nem mensagem de erro (as do Playwright trazem a URL da busca). Sem artefatos de debug.
- O que continua visível publicamente: o código, e que o workflow rodou (horários e se deu certo).

## Configurar (uma vez)

1. **Repositório de dados, PRIVADO e vazio:** *New repository* → `flight-data` (padrão: `<este-repo>-data`)
   → **Private** → *Add a README*.
2. **Token do Actions (token 1):** em <https://github.com/settings/personal-access-tokens/new>, token
   *fine-grained* só com o repositório `flight-data` e **Contents: Read and write**. No repositório do código:
   *Settings → Secrets and variables → Actions → New repository secret* → **`DATA_REPO_TOKEN`**. (Se o repo de
   dados tiver outro nome, crie a *variable* `DATA_REPO` com `dono/nome`.)
3. **Torne o repositório do código público:** *Settings → General → Danger Zone → Change visibility*.
4. **Vercel:** *Add New → Project → Import* o repositório do código. Não precisa configurar build: o
   `vercel.json` já aponta `docs/` e as funções ficam em `api/`.
5. **Token do painel (token 2):** outro token *fine-grained* com os **dois** repositórios e permissões
   **Contents: Read and write** e **Actions: Read and write**. Ele vai só para a Vercel (próximo passo).
6. **Variáveis de ambiente** (Vercel → projeto → *Settings → Environment Variables*; marque *Sensitive* nas
   três primeiras; ambiente *Production*):

   | Nome | Valor |
   |---|---|
   | `GH_TOKEN` | o token 2 (`github_pat_...`) |
   | `APP_PASSWORD` | a senha de acesso (**mín. 12 caracteres**; uma frase longa é melhor) |
   | `SESSION_SECRET` | texto aleatório de **mín. 32 caracteres**, gerado com `openssl rand -base64 48` (ou `node -e "console.log(require('crypto').randomBytes(48).toString('base64'))"`) |
   | `CODE_REPO` | `dono/flight` |
   | `DATA_REPO` | `dono/flight-data` (opcional: o padrão é `<CODE_REPO>-data`) |

7. **Novo deploy** (variáveis só valem em deploys novos): *Deployments → ⋯ → Redeploy*.
8. Abra o site, entre com a senha e use **+ Nova busca**. Se faltar alguma variável, a página mostra
   exatamente qual.

> Como o painel tem login próprio, você pode desligar o *Vercel Authentication* (Settings → Deployment
> Protection) para abrir de qualquer aparelho. Opcional: em *Firewall* da Vercel, crie uma regra de limite de
> requisições para `/api/login`.
>
> O cron do GitHub roda de hora em hora e pode atrasar alguns minutos (menor frequência útil: 1 h). Em repositório
> público o GitHub desativa o agendamento após 60 dias sem atividade; o workflow tenta evitar isso reabilitando a si
> mesmo a cada rodada. Se mesmo assim parar, reative em *Actions* (o botão "Buscar agora" continua funcionando).

### Testar o painel sem GitHub
`node webtests/run-server.mjs` sobe o painel com um GitHub **falso** (senha `senha-de-teste-123`) e `npm test` roda
os testes das funções (login, sessão, CSRF, validação, vazamento de token). Os testes do coletor: `pytest`.

### Como é o painel
- **Visão geral:** indicadores (viagens, maior queda de preço, última e próxima coleta) e um cartão por viagem, com
  preço, variação, mini-gráfico, tempo da viagem mais rápida e preço do voo direto.
- **Detalhe de cada viagem** (clique no cartão; o endereço vira `#/viagem/ID/aba`, então dá para favoritar):
  **Resumo** (melhor voo, faixa de preço típica, melhores datas, gráfico), **Voos** (cartões com saída → chegada,
  duração, escalas, preço, bagagem e detalhes; ordenar e filtrar), **Datas** (calendário de preços),
  **Histórico**, **Comparação** (pacote × trechos separados) e **Configuração**.
- **Aeroportos com sugestões:** ao criar ou editar uma viagem, digite a cidade (em português ou inglês, com ou sem acento),
  o nome do aeroporto ou o código, e escolha na lista (setas + Enter, mouse ou toque). Aparecem o código, a cidade, o nome
  do aeroporto e o país. "São Paulo" oferece também **SAO** (todos os aeroportos da cidade), além de GRU, CGH...
  Se você digitar só o nome e salvar, o painel entende (ex.: "Lisboa" vira LIS). A lista tem ~8 mil aeroportos
  (`docs/airports.json`, gerada por `scripts/build_airports.py` a partir do pacote [`airportsdata`](https://pypi.org/project/airportsdata/),
  licença MIT, dados do OurAirports e outras fontes abertas) e também mostra o nome da cidade em qualquer viagem.
- Modo claro/escuro automático e layout de celular.

### Testar o painel sem GitHub
`npm run preview` sobe o painel com um GitHub **falso** e dados de exemplo (senha `senha-de-teste-123`). `npm test` roda os testes
das funções (login, sessão, CSRF, validação, vazamento de token) e da busca de aeroportos, e `python webtests/e2e_panel.py` roda o
teste de navegador (Playwright) que percorre a interface inteira, inclusive no celular. Os testes do coletor: `pytest`.

## Usar localmente (sem GitHub)

```bash
pip install -e .
playwright install chromium

flights search GRU LIS 2026-12-20 --return 2026-12-30 --adults 2   # busca agora e grava
flights search GRU LIS 20/12/2026 --nights 10 --flex 2             # ±2 dias em torno da ida
flights search GRU LIS 20/12/2026 --depart-to 27/12/2026            # só ida: todos os dias de 20 a 27/12
flights search GRU LIS 2026-12-20 --return 2027-01-05 --depart-to 2026-12-22 --return-to 2027-01-08   # ida 20-22 × volta 5-8
flights multi GRU@2026-12-20..2026-12-22 LIS@2026-12-27..2026-12-30 MAD@2027-01-03 GRU           # período em cada trecho
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
