# Portal Integra Sinc — sincronizador Domínio → Portal Integra

> **Procedência.** Este é o plano fundador do projeto — as cinco etapas e as
> armadilhas de cada uma. Morava fora do repositório, no diretório de planos do
> Claude Code; entrou aqui em 23/09/2026 para que revisão de decisão apareça em
> diff, como a da D4 abaixo. A numeração `SINC-000` diz que ele vem antes das
> mudanças numeradas: as Etapas 1 a 5 são deste documento, e a
> [SINC-006](SINC-006-assinatura-e-incremental.md) é a sexta.
>
> **Cinco links apontam para arquivos que o Portal removeu** em 23/09/2026, com
> a Fase 9 do SYNC-001: `app/importacao/{dominio,services,repositories}.py`,
> `app/importacao/importadores/empresas.py` e `app/matriz_filiais/sugestao.py`.
> Ficaram como estão de propósito — dizem de onde cada arquivo daqui veio, e é
> isso que se quer saber ao ler. Estão vivos no histórico do `portal-integra`,
> até o commit `05fbbea`. O link para `admin/index.html` continua válido, mas as
> **linhas 187-232 não são mais a tela de importação**: ela saiu no mesmo commit.

## Context

O Portal Integra hoje é **cliente** do Domínio: dez importadores em
`app/importacao/importadores/` abrem ODBC contra o SQL Anywhere, leem,
transformam e gravam no PostgreSQL. Isso não sobrevive à publicação na VPS —
o driver ODBC do SQL Anywhere é proprietário, não existe na imagem Docker, e a
VPS não tem rota para o banco do cliente.

A solução é um processo separado que roda **na máquina do cliente** (onde o
driver ODBC existe), lê o Domínio e grava no PostgreSQL do Portal. É o mesmo
papel que o `Portal-DP-Importa-Dados` cumpre para o Portal DP.

Este projeto é esse processo. Ele nasce com a **cara do Portal Integra** e uma
tela de operação com sincronização manual.

> O portal-integra já tem um plano próprio para isto —
> [SYNC-001](../../portal-integra/changes/SYNC-001-plano-de-migracao-arquitetural.md),
> 2.240 linhas — que desenha a integração por **API HTTP com HMAC e remessas**.
> Ele **não** é o caminho escolhido agora (ver D1). Vale como referência para as
> armadilhas de extração (§16.9), que este plano reproduz, e como destino
> possível se o Portal um dia sair da rede do cliente.

## Decisões tomadas

| # | Decisão | Escolha |
|---|---|---|
| D1 | Como grava no Portal | **PostgreSQL direto** (psycopg2). A gravação fica isolada para poder virar HTTP depois sem reescrever extração |
| D2 | O que a tela pede | **Nada de credenciais.** Padrão visual do Portal Integra + sincronização manual |
| D3 | Onde ficam os segredos | `.env`, editado à mão. A aplicação **lê e nunca escreve** |
| D4 | Importadores do portal-integra | ~~Ficam como estão~~ — **REVOGADA em 23/09/2026.** Foram removidos; este projeto é a ÚNICA via. Ver abaixo |

> D3 concilia duas respostas suas: você pediu `.env` como fonte da configuração
> e, ao rever a tela, tirou dela os campos de conexão. Sem campos não há o que
> gravar — então o `.env` é editado por quem opera a máquina, e a tela apenas
> reporta se cada ponta respondeu.

### D4 foi revogada — o que mudou, e o que isso custa

A D4 dizia que os importadores do Portal ficariam como estão, e que este
projeto seria uma **via alternativa**. Em 22/09/2026 o `portal-integra`
executou a Fase 9 do SYNC-001 (commit `05fbbea`, mesclado no PR #21 em
23/09/2026) e removeu:

    app/importacao/dominio.py
    app/importacao/importadores/          (os dez)
    app/data/queries/{bi,fiscal}/dominio/ (as 23 consultas)
    scripts/conferir_legado.py
    pyodbc  do requirements.txt

**Este projeto passou a ser o único caminho de dados para o Portal.** Não há
mais via paralela, e isso muda três coisas do plano original:

**1. A conferência cruzada acabou.** A "prova real de que a cópia não perdeu
nada" era rodar os dois importadores contra bancos iguais e comparar. Não há
mais com o que comparar, e o `conferir_legado.py` que servia de molde também
foi removido. A Verificação §3 foi reescrita.

**2. Não há mais plano B.** Antes, um defeito aqui tinha o `/admin` do Portal
como saída manual. Agora um defeito aqui é o Portal sem dado — e o Portal não
tem como saber disso sozinho, porque a origem já não está ao alcance dele.

**3. "Cópia" virou "o original".** As divergências que documentei — a consulta
de `_empresas_com_saldo`, a remoção de `OPCOES`, o aviso uma vez por processo,
o mapa `DEPENDE_DE` — deixaram de ser divergências em relação a um código
vivo. Não há mais para onde portar de volta. Os cabeçalhos que dizem "vale
portar de volta" ficaram desatualizados e foram corrigidos.

O que **não** muda: a regra de não editar os arquivos copiados para "melhorar"
continua valendo, e pelo mesmo motivo de sempre — a lógica tem regra de
negócio medida contra dados reais, e reescrevê-la produz número plausível e
errado. O que mudou é que agora nem existe um segundo lugar onde conferir se a
reescrita foi fiel.

## Arquitetura

```text
máquina do cliente                                   servidor do Portal
┌─────────────────────────────────────────┐
│ portal-integra-sinc  (Flask, 127.0.0.1) │
│                                         │
│  tela /  ──► app/importacao/services    │
│                    │                    │
│      pyodbc ◄──────┤                    │
│   (SQL Anywhere)   │                    │
│                    └──── psycopg2 ──────┼──►  PostgreSQL portalintegra
└─────────────────────────────────────────┘      (as 18 tabelas + histórico)
```

**Este projeto não é dono de nenhuma tabela.** Grava nas tabelas que o Portal
cria via Alembic. Consequências:

- **Zero migrations.** Nada de `alembic/` aqui.
- O histórico reusa `importacao_execucao`, que já existe. A coluna `origem` é
  `VARCHAR(20)` **sem CHECK** (só `status` tem) — então grava `origem='sinc'`
  sem migration. ~~As execuções aparecem no `/admin` do Portal~~ — a
  importação do `/admin` saiu do ar em 23/09/2026 e **ninguém mais lê essa
  tabela do lado de lá**; a tela daqui virou o único lugar. Verificado em
  [20260903_0900_importacao_execucao.py:33](../../portal-integra/migrations/versions/20260903_0900_importacao_execucao.py#L33).
- `id_usuario` é FK anulável → o sincronizador grava `NULL`.
- **Guarda de schema:** no boot, ler `alembic_version` e comparar com a revisão
  esperada (constante no código). Divergente → log de aviso destacado na tela.
  Este processo escreve num schema que não controla; descobrir a divergência ao
  subir é muito mais barato que descobrir por dado errado.

**Exclusão mútua:** `trava_de_sessao(f"importacao:{chave}")`, um
`pg_try_advisory_lock`. Escrito para excluir mutuamente esta via e a do
`/admin` do Portal — que deixou de existir em 23/09/2026 (ver a revisão da D4).
Hoje o que ela protege é a tela contra a rodada agendada, que é o caso real:
alguém sincronizar às 02:00 sem saber que o Agendador acabou de disparar.

**Rede:** `app_host = 127.0.0.1` fixo. A aplicação escreve no banco de produção
sem login; ela não pode escutar em `0.0.0.0`. Se depois for preciso acesso
remoto, o passo certo é login (reusando `usuario` do próprio Portal, já que a
conexão existe), não abrir o bind.

---

## Etapa 1 — Fundação: a stack, sem importar nada

**Objetivo:** subir um Flask com a cara do Portal Integra, conectado às duas
pontas, sem nenhum importador.

### Raiz

`pyproject.toml` (`requires-python = ">=3.13"`, ruff line-length 100, mesmas
regras e ignores do Portal; pytest `-q --strict-markers`) · `requirements.txt` ·
`requirements-dev.txt` · `.env.example` · `.gitignore` (com `.env`) · `README.md` ·
`CLAUDE.md`.

`requirements.txt` — 7 pacotes (5 a menos que o Portal: sai Flask-Login,
Flask-WTF, WTForms, gunicorn, alembic):

```text
Flask==3.1.3
pydantic==2.9.2
pydantic-settings==2.6.1
python-dotenv==1.2.2
psycopg2-binary==2.9.12
pyodbc==5.3.0
APScheduler==3.10.4
```

`.env.example` — só os campos que restam (ver `app/config.py` abaixo).

### Copiar sem alterar

| De | Para |
|---|---|
| [app/shared/errors.py](../../portal-integra/app/shared/errors.py) | `app/shared/errors.py` |
| [app/shared/responses.py](../../portal-integra/app/shared/responses.py) | `app/shared/responses.py` |
| [app/shared/pagination.py](../../portal-integra/app/shared/pagination.py) | `app/shared/pagination.py` |
| `app/static/css/{tokens,style,components,formularios,admin,importacao,mobile}.css` | idem |
| `app/static/js/portal.js` | idem — `window.Portal` inteiro |
| `app/static/img/integra-mark.png` | idem |
| `app/templates/errors/_erro.html` + `{400,403,404,500}.html` | idem |

`tokens.css` é a fonte da verdade visual do Portal — não existe documento de
identidade separado. **Não copiar `app/static/js/app.js`**: é código morto com
mecanismo de tema contraditório, e o próprio CLAUDE.md do Portal avisa isso.

### Adaptar

**`app/config.py`** — podar as 40 opções do Portal para as 14 que importam:
`database_{host,port,user,password,name}`, `db_pool_{min,max}`,
`db_{connect_timeout,statement_timeout_ms}`, `dominio_{dsn,connstr,user,password,schema}`,
mais `app_name`, `app_port`, `log_level`, `tz`. Manter `_credencial_obrigatoria`
e a propriedade `database_url`. **Remover `_secret_key_obrigatoria`,
`ITENS_DE_PRODUCAO` e `_producao_nao_aceita_default_de_desenvolvimento`** — os
dois primeiros levantam no import e derrubariam o boot aqui.

**`app/data/connection.py`** — cópia quase literal de
[connection.py](../../portal-integra/app/data/connection.py). Única mudança:
`settings_ativas()` some e vira `get_settings()` direto (não há injeção de
Settings no factory aqui). `get_connection`, `transacao`, `trava_de_sessao`,
`linhas_dict`, `linha_dict` ficam idênticos.

**`app/data/sql.py`** — copiar só `QUERIES_DIR`, `carregar_sql` e `limpar_cache`.
Descartar `carregar_sql_com_nome` e `padrao_de_busca` (são de tela do Portal).

**`app/__init__.py`** — `create_app()` no molde do Portal, mas enxuto: sem
Flask-Login, sem CSRF, sem ProxyFix. Manter o que dá a cara e a segurança de
base: nonce CSP em `before_request`, `context_processor` com `csp_nonce` e
`app_name`, `_cabecalhos_seguranca` em `after_request`, `?v=<mtime>` nos
estáticos, `_quer_json()` e os handlers de `ErroApp`/`HTTPException`/`Exception`.

> Sem Flask-WTF, `portal.js` continua mandando `X-CSRFToken` lido de
> `meta[name=csrf-token]`. Emitir a meta vazia no `base.html` e deixar o header
> ir — é inofensivo e evita tocar no `portal.js`.

**`app/templates/base.html`** — adaptar o do Portal: manter a ordem dos CSS no
`<head>`, as Google Fonts (IBM Plex Sans + Outfit + Material Symbols), o loader
global com o SVG da marca, e os IIFEs de tema e sidebar. Trocar a sidebar do
Portal por dois itens: **Sincronização** e **Histórico**. Tirar o menu de usuário.

**`app/health/`** — `/health` e `/health/live`, com o `/health` reportando as
duas conexões e a revisão de schema.

### Critério de aceite

`python -m app` sobe em `127.0.0.1:7810`; a home abre com navbar, sidebar e
tokens do Portal Integra; `/health` responde `{"data":{"postgres":"ok",
"dominio":"ok","schema":"0020_empresa_apelido"}}`; `ruff check . && ruff format --check . && pytest` verdes.

---

## Etapa 2 — A tela de sincronização

**Objetivo:** a tela existe, mostra diagnóstico e histórico, e o botão dispara
a rodada — que nesta etapa percorre um `REGISTRO` vazio e reporta zero.

### Copiar

| De | Para | Nota |
|---|---|---|
| [app/importacao/services.py](../../portal-integra/app/importacao/services.py) | `app/importacao/services.py` | **literal** — não tem nada de Flask |
| [app/importacao/repositories.py](../../portal-integra/app/importacao/repositories.py) | `app/importacao/repositories.py` | tirar o `LEFT JOIN usuario` de `ultima_execucao` e `historico` |
| `app/static/js/admin_importacao.js` | `app/static/js/sincronizacao.js` | trocar o prefixo da API e os ids dos containers |

De `services.py` ficam as três decisões que valem mais que o código:
`trava_de_sessao` **de sessão e não de transação** (a importação abre várias
transações; uma trava de transação soltaria no primeiro commit), o
`expirar_orfas(900)` chamado **antes** de montar os cards, e a validação que
exclui `bool` explicitamente da lista de ids — em Python `bool` é `int`, então
`[true]` no JSON virava o id `1`, importando a empresa 1 sem ninguém pedir.

Acrescentar `ORIGEM_SINC = "sinc"` ao lado de `ORIGEM_PAINEL`/`ORIGEM_CLI`.

### Criar

**`app/importacao/importadores/__init__.py`** — `REGISTRO: dict[str, ModuleType] = {}`
por enquanto, com o contrato documentado no cabeçalho.

> **Resolver `OPCOES` agora, não depois.** No Portal ele é contrato pela metade:
> `services.listar` o expõe, o JS desenha as caixas e manda o estado no POST, e
> `routes.executar` descarta tudo — um controle inerte na tela. Aqui: **remover
> `OPCOES` do contrato**. Nenhum dos dez importadores o usa.

**`app/importacao/rotas.py`** — quatro endpoints em `/api/v1/sincronizacao`,
espelhando os do Portal (`GET ""`, `POST "/<chave>"`, `GET "/<chave>/pendentes"`,
`GET "/historico"`), **sem** `@login_required`/`@admin_required`, mais dois novos:

- `GET /api/v1/sincronizacao/diagnostico` → testa Domínio e PostgreSQL e devolve
  a revisão de schema encontrada. É o que dá o que fazer à tela nesta etapa.
- `POST /api/v1/sincronizacao/tudo` → percorre o `REGISTRO` em ordem, para na
  primeira falha, devolve o placar por chave. É o "sincronizar automaticamente".

**`app/main/rotas.py`** + `app/templates/index.html` — a tela. Estrutura do
`view-importacao` do Portal
([admin/index.html:187-232](../../portal-integra/app/templates/admin/index.html#L187-L232),
no commit `05fbbea^` — aquelas linhas já não estão lá):
cabeçalho `.page-title`/`.page-subtitle`, card de aviso de origem não
configurada, `#importLista` (cards montados pelo JS), e a tabela de histórico
com o chip `.ativo-chip--{regular,crit,media}`.

Acrescentar acima da lista o **card de diagnóstico** (Domínio · PostgreSQL ·
schema, cada um com rótulo textual além da cor) e o botão
`.btn-primary.btn-primary--accent` **Sincronizar tudo**.

### Regras visuais a respeitar

- `.btn-primary` é o **discreto**; o de destaque é `.btn-primary--accent` (ouro).
- Resultado que não gravou nada sai em **âmbar `--aviso`, nunca verde**, com o
  motivo e o próximo passo.
- **Cor sempre com label textual** — regra declarada em `tokens.css`.
- Nenhum literal de cor novo: só `var(--token)`. `--surface` **não existe**
  (usar `--card` / `--panel-inset`); um `var(--surface)` não dá erro, só deixa
  o elemento transparente.
- Proibido `onclick=` no HTML — a CSP bloqueia. `data-*` + delegação.
- Todo texto vindo do banco passa por `Portal.texto()`; dentro de atributo,
  `Portal.atributo()`.
- Todo `<script>` executável leva `nonce="{{ csp_nonce }}"`.
- O tema claro **não** redefine tokens: toda regra nova precisa do par
  `body.light` escrito à mão.

### Critério de aceite

A tela abre com a cara do Portal; o card de diagnóstico mostra as duas conexões
verdes; **Sincronizar tudo** responde em milissegundos com placar zerado e grava
uma linha em `importacao_execucao` com `origem='sinc'`, ~~visível também no
`/admin` do Portal~~ (era verdade quando a etapa foi escrita; aquela tela saiu
do ar em 23/09/2026); derrubar o DSN do Domínio no `.env` faz o aviso aparecer.

---

## Etapa 3 — Importação de empresas

**Objetivo:** a tabela `empresas` sincroniza ponta a ponta.

### Copiar

| De | Para | Adaptação |
|---|---|---|
| [app/importacao/dominio.py](../../portal-integra/app/importacao/dominio.py) (296 L) | `app/importacao/dominio.py` | **literal**; só os imports mudam |
| [importadores/empresas.py](../../portal-integra/app/importacao/importadores/empresas.py) (277 L) | idem | ver abaixo |

`dominio.py` copia inteiro, e três coisas nele **não podem ser "limpas"**:

1. O **codec `cp1252_tolerante`** registrado via `codecs.register`, que ignora
   de propósito o `errors` que o chamador pedir. Há texto UTF-8 gravado dentro
   de coluna cp1252 na origem (caso real: empresa 536, `ctlancto`); com
   `strict`, cinco linhas derrubavam a importação inteira daquela empresa.
2. O `_tentar_teto_de_consulta` com `try/except`. O driver da SAP responde
   `HYC00 Driver not capable` a `SQL_ATTR_QUERY_TIMEOUT`; sem a guarda, **todo**
   importador morre com erro genérico de conexão, com a conexão perfeita.
3. O `conn.close()` no `finally` — o context manager do pyodbc encerra a
   transação mas **não** fecha a conexão, e a sessão fica pendurada no
   SQL Anywhere.

### `empresas.py` — o que muda

O SQL é inline (`_SQL`, com `{schema}` e `{filtro}`) e a gravação são dois
blocos: `INSERT ... ON CONFLICT DO NOTHING` (só `id_empresa` e `ativo`) e um
`UPDATE` seletivo de `razao_social`, `nome_fantasia`, `apelido`, `cnpj`.

**A regra de propriedade é o contrato e não pode afrouxar:** do Domínio vêm
apenas aqueles quatro campos; `ativo`, `nome_exibicao` e `usa_folha_pagamento`
são do Portal e o sincronizador **nunca** os toca. E `incluir=True` **exige
`ids`** — é o único importador com essa recusa, e ela existe para que um
sincronizador mal configurado não despeje as 917 empresas do Domínio no Portal.

**`_sugerir_grupos()` (L233 e L243-277) — decisão a tomar nesta etapa.** Ele
deriva grupo matriz/filiais da raiz do CNPJ depois do UPDATE, e é o eixo que o
BI usa para consolidar. Depende de
[matriz_filiais/sugestao.py](../../portal-integra/app/matriz_filiais/sugestao.py)
(167 L, puro) e de parte de `matriz_filiais/repositories.py` (427 L).

*Recomendação: portar.* Portar `sugestao.py` inteiro e só as funções de
repository que ele chama. Sem isso, empresa que entre pelo sincronizador nunca
tem grupo sugerido, e a consolidação do BI a perde **em silêncio** — que é
exatamente a classe de defeito que este plano tenta evitar. A alternativa
barata, se o porte se mostrar grande, é deixar de fora e rodar o importador
`empresas` do Portal uma vez depois; mas isso é um passo manual recorrente.

### Critério de aceite

`POST /api/v1/sincronizacao/empresas` com `ids` traz as empresas escolhidas;
a lista de pendentes some das já cadastradas; reexecutar não muda nada
(`incluidas=0`, `atualizadas=N`); nenhum campo do Portal foi sobrescrito;
`_sugerir_grupos` produz os mesmos grupos que o Portal produziria.

---

## Etapa 4 — BI Contábil

**Objetivo:** plano de contas, saldos, DFC e lançamentos.

### Copiar — 4 importadores + 7 queries

| Ordem | Módulo | L | Idioma de gravação |
|---|---|---|---|
| 2 | `contabil_plano.py` | 357 | UPSERT + reconciliação (`DELETE ... codi_cta <> ALL(%s)`) |
| 3 | `contabil_saldos.py` | 435 | UPSERT por PK de 4 colunas + DELETE do obsoleto calculado em Python |
| 4 | `contabil_dfc.py` | 515 | negativar ordem → UPSERT → DELETE → vínculos |
| 5 | `contabil_lancamentos.py` | 523 | substituição de **janela** por `data_lan` |

`app/data/queries/bi/dominio/` — os 7 `.sql` copiam **literalmente**, com o
caminho relativo preservado: os importadores os referenciam por string
(`carregar_sql("bi/dominio/saldos_mensais.sql")`).

### As armadilhas desta etapa

Cada uma produz **número plausível**, sem erro e sem log:

- **`orig_lan <> 2` separa duas medidas que não são redundância.** `movimento`
  exclui encerramento e é a base do **DRE**; `movimento_total` os inclui e é a
  base do **Balanço**. Trocar: o DRE zera em todo ano encerrado e o Balanço
  deixa de fechar.
- **`movimento_total = credito_total − debito_total`, exato** — é `NUMERIC`, e
  o Portal trata como invariante.
- **`clas_cta` não é única** (2.575 pares repetidos em 359 empresas). A chave da
  conta é `codi_cta`.
- **A negativação da ordem em `contabil_dfc` não é enfeite:**
  `uq_bi_linha_dfc_ordem` é UNIQUE por empresa, e trocar duas linhas de posição
  no Domínio daria `UniqueViolation` no meio do `executemany`.
- **Os dois motivos de descarte de saldo não se confundem:** `sem_conta`
  (`cdeb_lan`/`ccre_lan` = 0, sentinela, benigno) e `fora_do_plano` (conta fora
  de `bi_conta`, **pode quebrar Ativo = Passivo + PL**). Só o segundo loga
  `warning`. A mesma regra vale em `contabil_lancamentos` — há teste no Portal
  comparando as duas.
- **`contabil_lancamentos._gravar` lê o Domínio dentro da transação PostgreSQL**,
  lote a lote, de propósito: acumular antes devolveria o pico de memória (a
  maior empresa tem 370.437 lançamentos no biênio). Manter.
- **`execute_values`, nunca `executemany`**, na inserção de lançamentos —
  medido em minutos a 780 mil linhas.

### Configuração a soltar

`contabil_lancamentos.MAXIMO_EMPRESAS_POR_EXECUCAO = 20` e `ANOS_JANELA = 3`
viram campos de `Settings`. O teto existe porque a rota do Portal morre no
`--timeout 120` do gunicorn; **aqui a carga em lote é justamente o trabalho**, e
não há gunicorn. No Portal a mensagem de erro já sugere o CLI para carga
inicial, mas a constante não entrega — é uma promessa quebrada que não se copia.

### Critério de aceite

Para uma empresa conhecida (272 serve — o Portal tem os números conferidos):
Ativo = Passivo + PL ao centavo em 31/05/2026; `movimento_total =
credito_total − debito_total` em toda linha; reexecutar não muda contagem;
o DRE e o Balancete do Portal lendo o banco sincronizado batem com os de hoje.

---

## Etapa 5 — BI Fiscal

**Objetivo:** cadastros globais, dimensões, movimento, apuração e produto.

### Copiar — 5 importadores + 16 queries + 2 módulos puros

| Ordem | Módulo | L | Idioma |
|---|---|---|---|
| 6 | `fiscal_cadastros.py` | 188 | UPSERT **global**, sem empresa |
| 7 | `fiscal_dimensoes.py` | 443 | substituição de partição por empresa, 5 tabelas |
| 8 | `fiscal_movimento.py` | 429 | substituição de partição por `(empresa, tipo)` |
| 9 | `fiscal_apuracao.py` | 351 | substituição de partição por empresa |
| 10 | `fiscal_produto.py` | 299 | substituição de partição por `(empresa, tipo)` |

Mais [app/fiscal/impostos.py](../../portal-integra/app/fiscal/impostos.py) (126 L) e
[app/fiscal/modelos.py](../../portal-integra/app/fiscal/modelos.py) (116 L) — ambos
puros, sem I/O — e os 16 `.sql` de `app/data/queries/fiscal/dominio/`.

### As armadilhas desta etapa

- **A competência é `dsai_sai`/`dent_ent`, NUNCA `compte_sai`/`compte_ent`.**
  Estas trazem ano 1900 em 6,12 de 6,15 milhões de linhas. Usá-las produz um BI
  **vazio**, que se lê como "não há dado no período".
- **`codi_esp` não é o modelo.** NF-e tem 4 códigos, NFC-e 3, NFS-e 6 — e
  `codi_esp = 57` é NFS-e enquanto o **modelo** `'57'` é CT-e. A
  desnormalização de `modelo` é do extrator, e `rotulo()` recebe string, nunca int.
- **A contagem de notas é BRUTA**, com `qtd_canceladas` ao lado. O Portal
  subtrai num lugar só; entregar líquido produz **subtração dupla**. E
  cancelamento tem duas marcas: 4.899 notas com `cancelada_sai = 'N'` **e**
  `situacao_sai = 2` — por isso `situacao` entra no grão.
- **`LEFT JOIN`, nunca interno, e o filtro do item DENTRO da derivada.**
  23.411 de 284.443 notas (8,23%) não têm item; com join interno somem R$ 101,2
  milhões por trimestre e a tela mostra menos sem nada acusar. O filtro movido
  para o `WHERE` de fora anula o `LEFT JOIN` em silêncio.
- **A derivada agrega ao grão da NOTA antes do join** — join direto
  multiplicaria `qtd_notas` pelo número de itens.
- **`TABELA_DE_EXTEMPORANEO_POR_UF` é o mapa de quem TEM A COLUNA**, não de quem
  tem a tabela. `EFSDOIMP_ESTADUAL_<UF>` existe nos 27 estados, mas só 7
  declaram `ICMS_DOCUMENTOS_EXTEMPORANEOS_RECOLHER`; nos outros a consulta morre
  com erro de coluna inexistente. É condição de execução, não `LEFT JOIN`.
- **`extemporaneo_recolher` não cai para zero:** `None` significa "a origem não
  informa". Distinto de zero.
- **`canonizar()` nunca devolve "Outros"**, e **não funde códigos diferentes** —
  `codi_imp` 4 e 17 são regimes distintos. O truncamento em 40 chars acontece em
  Python, não no banco: deixar o PostgreSQL recusar derrubaria a importação
  inteira da empresa.
- **`fiscal_dimensoes._gravar_produtos` lê o Domínio dentro da transação**, como
  `contabil_lancamentos`. Empresa 481 tem 61.563 produtos (~18 MB só dela).
- **`_recarregar` e o INSERT da apuração montam identificador por f-string.** Os
  valores vêm de constantes do módulo, nunca de requisição. **Manter essa
  invariante ao adaptar.**

### Configuração a soltar

`TETO_DE_EMPRESAS = 20` nos quatro importadores fiscais → `Settings`, mesma
justificativa da Etapa 4.

### Verificar antes de copiar

`CANONICOS` em `impostos.py` tem **43 entradas**, mas o comentário do arquivo
diz 44. Conferir contra `efsdoimp` antes de confiar no número.

### Critério de aceite

`fiscal_movimento` (2,36 M linhas) e `fiscal_produto` (5,69 M) completam sem
estourar memória; competência máxima por empresa bate com a origem; as telas
`/fiscal`, `/fiscal/entradas` e `/fiscal/saidas` do Portal mostram os mesmos
números de hoje; reexecutar é idempotente.

---

## Verificação end-to-end

1. `ruff check . && ruff format --check . && pytest` — a sequência da casa. O CI
   do Portal roda só por disparo manual, então esta é a única rede.
2. **Contra um dump restaurado, nunca contra produção**, na primeira vez:
   `pg_dump` do `portalintegra`, restaura local, aponta o `.env` para a cópia,
   roda a sincronização inteira.
3. **A conferência que vale, agora que não há segunda implementação.** A D4 foi
   revogada e a comparação cruzada morreu com ela. Restam três provas, e
   nenhuma depende de um segundo importador:

   - **Invariantes internas.** `movimento_total = credito_total − debito_total`
     ao centavo, partidas dobradas somando zero por empresa, nenhuma perna sem
     conta no plano. Medido em 22/09: zero violações em 554.006 linhas.
   - **Idempotência.** Reexecutar não pode mudar contagem de linha. Medido:
     `fiscal_produto` reinseriu 3.637.182 linhas e a tabela terminou igual.
   - **Origem contra destino, por agregado.** Recalcular `COUNT` e `SUM` no
     Domínio e comparar com o que está no Portal. É independente da
     implementação, e a maquinaria da [SINC-006](SINC-006-assinatura-e-incremental.md)
     já faz 80% disso — o que falta é comparar VALOR, não só existência.
4. Subir as telas do Portal contra o banco sincronizado e comparar os números
   com os de hoje. Número plausível e errado é o defeito que este domínio
   produz; só a comparação o pega.

## Fora de escopo (nomeado, para não virar surpresa)

- **Migrations.** Este projeto não é dono de tabela nenhuma.
- **Autenticação.** Mitigada por `127.0.0.1`. Se surgir necessidade de acesso
  remoto, o passo é login reusando `usuario` do Portal — não abrir o bind.
- **Agendamento.** `APScheduler` está no `requirements.txt` para a rodada
  automática (daemon `--serve` + `.bat` no Agendador de Tarefas do Windows, como
  o `Portal-DP-Importa-Dados` faz), mas a tela da Etapa 2 entrega o disparo
  manual. Agendar é o passo seguinte natural, depois da Etapa 5.
- **A API HTTP do SYNC-001.** D1 escolheu o banco direto. A gravação fica
  isolada nos importadores para que trocá-la depois não toque na extração.
- ~~**Remover os importadores do Portal.** D4: ficam.~~ **Aconteceu** em
  23/09/2026, fora deste projeto. Ver a revisão da D4 acima.

## Nota sobre o schema hardcoded

`bethadba` está **hardcoded em 22 dos 23 `.sql`**; só `empresas.py` usa
`{schema}` de `settings.dominio_schema`. Copiar assim, e saber que
`DOMINIO_SCHEMA` no `.env` só tem efeito sobre `empresas`. Tornar configurável
de verdade é uma varredura nos 22 arquivos — vale se algum cliente usar schema
diferente, e não vale antes disso.
