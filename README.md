# Portal Integra Sinc

Sincronizador que lê o **Domínio Contábil** (SQL Anywhere, via ODBC) e grava no
**PostgreSQL do Portal Integra**.

Roda na máquina do cliente, que é onde o driver ODBC do SQL Anywhere existe. O
Portal, publicado numa VPS, não tem rota para o banco do cliente nem o driver
na imagem Docker — é por isso que este processo é separado.

```
┌─ máquina do cliente ────────────────┐
│  portal-integra-sinc                │
│    tela em http://127.0.0.1:7820    │
│        │                            │
│   pyodbc │  Domínio (SQL Anywhere)  │
│        │                            │
│   psycopg2 ─────────────────────────┼──► PostgreSQL do Portal
└─────────────────────────────────────┘
```

## O que ele não é

**Não é dono de nenhuma tabela.** Grava nas tabelas que o Portal cria com
Alembic. Não há migrations aqui, e não deve haver: acrescentar schema por este
lado criaria uma segunda verdade ao lado da do Portal.

O histórico de execuções usa a tabela `importacao_execucao`, que já existe,
gravando `origem='sinc'`. As execuções aparecem no `/admin` do Portal ao lado
das disparadas por lá.

## Instalação

Requer **Python 3.13** e o **driver ODBC do SQL Anywhere de 64 bits**.

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
copy .env.example .env
```

Depois, edite o `.env`. **A aplicação nunca escreve nesse arquivo** — a tela não
tem campos de conexão. Quem configura é quem opera a máquina, e a configuração
é lida uma vez, no start.

## Executar

Duplo clique em **`abrir-sinc.bat`**. Ele sobe o servidor e abre a tela no
navegador quando a porta atender — não depois de uma espera fixa, que abriria
uma página de erro se a subida demorasse. A janela do console fica com o log;
fechá-la ou `Ctrl+C` derruba o servidor.

O `.bat` também liga o UTF-8 no console (`chcp 65001` + `PYTHONIOENCODING`).
Sem isso o log sai com acento quebrado — "revisÃ£o do schema" —, e mensagem
normal passa a parecer defeito.

Sem ele, o equivalente é:

```bat
.venv\Scripts\python.exe run.py
```

A tela abre em `http://127.0.0.1:7820`. O endereço **não é configurável**: esta
aplicação grava no banco de produção do Portal sem autenticação, e escutar só
no loopback é a proteção. Ver `SERVIDOR_HOST` em `app/__init__.py`.

## Sincronizar sem tela

```bat
.venv\Scripts\python.exe sincronizar.py                 a rodada completa
.venv\Scripts\python.exe sincronizar.py --listar        os conjuntos disponíveis
.venv\Scripts\python.exe sincronizar.py --importador contabil_saldos --empresa 272
.venv\Scripts\python.exe sincronizar.py --dry-run       simula, não grava
```

A rodada completa percorre os dez conjuntos na ordem de dependência e **para na
primeira falha**. Nunca inclui registro novo: trazer empresa para o Portal é
decisão de quem opera, com a lista na frente — não de um processo que roda às
duas da manhã.

O log vai para o console **e** para `logs/sincronizacao.log` (rotativo, 5 × 2 MB).
Rodando pelo agendador não há console, e sem o arquivo a única pista de uma
madrugada que deu errado seria a linha de erro no histórico.

### Códigos de saída

| | |
|---|---|
| `0` | tudo certo |
| `1` | a sincronização falhou |
| `2` | já havia execução em andamento — **não é falha** |
| `3` | configuração inválida (`.env`, conexões, conjunto inexistente) |

O `2` existe para o agendador não acordar ninguém à toa: o operador pode estar
sincronizando pela tela na hora da janela, a trava do PostgreSQL faz o seu
trabalho, e a próxima madrugada pega.

## Agendar

### Agendador de Tarefas do Windows — o recomendado

```bat
schtasks /create /tn "Portal Integra Sinc" /sc daily /st 02:00 ^
         /tr "\"C:\caminho\para\sincronizar-agendado.bat\"" ^
         /ru "DOMINIO\conta_de_servico" /rp * /rl HIGHEST
```

O Windows acorda o processo na hora marcada, ele roda, grava e sai. Sobrevive a
reinício da máquina, não depende de ninguém deixar janela aberta, e o próprio
Agendador mostra o código de saída na coluna *Resultado*.

Na aba **Geral** da tarefa, marque *"Executar estando o usuário conectado ou
não"*. O `.bat` já faz `cd /d "%~dp0"`, então o campo *Iniciar em* pode ficar
vazio — verificado rodando a partir de `C:\Windows`.

#### Antes de agendar, confira estas quatro

| | Por quê |
|---|---|
| **`DOMINIO_CONNSTR` com host FIXO** | sem host, a busca do servidor é por *broadcast UDP* na sub-rede — falha em servidor de outra VLAN. E o DSN, se usado, só existe para a conta que o criou |
| **A conta enxerga o PostgreSQL e o Domínio** | rode uma vez à mão sob a conta de serviço: `sincronizar.py --dry-run --importador empresas` |
| **A pasta da instalação é gravável** | o processo escreve `logs\` e `estado-sinc.db` ali — nada fica no perfil do usuário |
| **A janela cabe** | a rodada completa levou **24 min** real e **7 min** em simulação, com o parque já carregado |

#### Na manhã seguinte

O resultado está em três lugares, e o primeiro basta:

1. **Agendador de Tarefas**, coluna *Resultado*: `0` tudo certo, `1` falhou,
   `2` já havia execução em andamento (**não é falha**), `3` configuração.
2. `logs\sincronizacao.log` — o andamento fase a fase e o placar.
3. A tela, ou o `/admin` do Portal: o histórico traz as dez fases com
   `origem = sinc`.

> **Empresa nova no Domínio NÃO entra sozinha.** A rodada agendada roda com
> `incluir=False` por decisão: trazer empresa para o Portal é escolha de quem
> opera, com a lista na frente. O card *Empresas* mostra as pendentes.

### `--serve` — a alternativa

```bat
.venv\Scripts\python.exe sincronizar.py --serve
```

Um processo que fica vivo e dispara no horário de `SINC_CRON`. Serve quando a
máquina já tem o sincronizador aberto o dia todo. O preço é que, se o processo
morrer, ninguém percebe até a sincronização não ter acontecido.

## Diagnóstico

A tela abre com as três checagens, e o mesmo conteúdo sai em JSON:

```bat
curl http://127.0.0.1:7820/health
```

| Linha | O que responde | Onde olhar quando falha |
|---|---|---|
| **Origem** | O Domínio respondeu? | driver ODBC, `DOMINIO_DSN` / `DOMINIO_CONNSTR` |
| **Destino** | O PostgreSQL do Portal respondeu? | `DATABASE_*`, rota de rede |
| **Schema** | É o banco que este código espera? | migrations novas no `portal-integra` |

`/health/live` é a sonda de vida e não abre conexão nenhuma.

### O erro mais comum: `IM014`

```
O DSN especificado contém uma incompatibilidade de arquiteturas
entre o Driver e o Aplicativo
```

O DSN foi criado no **Gerenciador de Fontes de Dados ODBC de 32 bits**
(`C:\Windows\SysWOW64\odbcad32.exe`) e o Python é de 64. Os dois gerenciadores
gravam em ramos separados do registro e **não enxergam um ao outro**. Duas saídas:

**1. Criar o DSN no lado de 64 bits.** Sem privilégio de administrador, dá para
criar um DSN **de usuário**, que não exige elevação:

```powershell
Add-OdbcDsn -Name "Contabil" -DriverName "SQL Anywhere 17" `
    -DsnType "User" -Platform "64-bit" -SetPropertyValue @(
        "Description=Contabil",
        "CommLinks=TCPIP{serverport=2638}",
        "ServerName=srvContabil",
        "DatabaseName=Contabil",
        "AutoStop=yes"
    )
```

Sem senha no registro: quem passa `UID`/`PWD` é a aplicação, pelo `.env`.

> **A ressalva que isso cria:** DSN de usuário existe **só para a conta que o
> criou**. Enquanto a sincronização for disparada pela tela, por quem está na
> máquina, funciona. Quando ela passar a rodar pelo Agendador de Tarefas com uma
> **conta de serviço dedicada**, essa conta não enxerga o DSN e a leitura volta a
> falhar — com `IM002`, não `IM014`. Aí ou se cria o DSN como **System** (exige
> administrador) ou se usa a saída 2, que independe de conta.

**2. Dispensar o DSN e usar a string completa** — é para isso que
`DOMINIO_CONNSTR` existe, e é o caminho que sempre funciona:

```ini
DOMINIO_CONNSTR=DRIVER={SQL Anywhere 17};CommLinks=TCPIP{serverport=2638};ServerName=srvContabil;DatabaseName=Contabil;UID=<usuario>;PWD=<senha>
```

`DOMINIO_CONNSTR` tem precedência sobre `DOMINIO_DSN`.

## Qualidade

```bat
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m pytest
```

Os testes **não leem o `.env`** e não abrem conexão: as Settings são montadas
na fixture. Um teste que dependesse do arquivo apontaria para o banco de
produção do Portal.

## Estrutura

```text
app/
├── __init__.py      create_app(): CSP, cabeçalhos, blueprints, erros
├── config.py        Settings — única fonte de configuração
├── api/             o destino do report-uri da CSP
├── data/
│   ├── connection.py  pool, get_connection(), transacao(), trava_de_sessao()
│   ├── schema.py      a revisão do Portal, conferida e reportada
│   ├── sql.py         carregar_sql()
│   └── queries/
│       ├── bi/dominio/       as 7 consultas do BI Contábil
│       └── fiscal/dominio/   as 16 do BI Fiscal
├── fiscal/          PUROS: o nome canônico do imposto e o rótulo do modelo
├── health/          /health e /health/live, e o diagnóstico das duas pontas
├── importacao/
│   ├── dominio.py      conexão ODBC read-only — cópia literal do Portal
│   ├── services.py     executa, mede, registra; e a rodada completa
│   ├── repositories.py histórico em `importacao_execucao` (tabela do Portal)
│   ├── routes.py       /api/v1/sincronizacao
│   └── importadores/   os 10, em ordem de dependência
├── main/            a tela
├── matriz_filiais/  a sugestão de grupo pela raiz do CNPJ (só o que a
│                      sincronização de empresas usa — o cadastro é do Portal)
├── shared/          erros, envelope de resposta, paginação, nomes de empresa
├── static/          CSS e JS — cópia do Portal, exceto css/sinc.css
└── templates/       base.html, index.html, errors/
abrir-sinc.bat            sobe o servidor e abre a tela (duplo clique)
sincronizar-agendado.bat  a rodada completa, sem tela (Agendador de Tarefas)
run.py                    entrypoint da tela
sincronizar.py            entrypoint sem tela (CLI e daemon)
```

## Sobre o código copiado

Boa parte deste projeto é cópia do `portal-integra`, e é deliberado: a lógica de
leitura do Domínio tem regra de negócio medida contra dados reais, e reescrevê-la
produziria número plausível e errado — o defeito mais caro deste domínio.

**Não edite os arquivos copiados para "melhorar".** Cada um traz no cabeçalho o
motivo das decisões que parecem estranhas. Os casos que mais custam quando se
mexe:

- o **codec `cp1252_tolerante`** em `dominio.py`, que ignora de propósito o
  `errors` que o chamador pedir;
- o `try/except` em torno do teto de consulta — o driver da SAP responde
  `HYC00 Driver not capable`, e sem a guarda **todo** importador morre com erro
  genérico de conexão;
- os seis arquivos de `static/css/`, que são do Portal. O que este projeto
  precisa de diferente mora em `css/sinc.css`.

A única divergência de convenção em relação ao Portal: aqui **não existe
`OPCOES`** no contrato dos importadores. Lá ele é contrato pela metade — a tela
desenha as caixas e a rota descarta o valor —, e nenhum dos dez importadores o
usa.

## Etapas

| | Entrega | Estado |
|---|---|---|
| 1 | Fundação: stack, tela com a cara do Portal, diagnóstico | **pronta** |
| 2 | Tela de sincronização: cards, histórico, disparo manual | **pronta** |
| 3 | Importação de empresas | **pronta** |
| 4 | BI Contábil: plano, saldos, DFC, lançamentos | **pronta** |
| 5 | BI Fiscal: cadastros, dimensões, movimento, apuração, produto | **pronta** |
| 6 | [SINC-006](changes/SINC-006-assinatura-e-incremental.md) — assinatura de origem | **Fase 0** no ar |
| — | Agendamento: CLI, `.bat` para o Agendador e daemon `--serve` | **pronto** |

## Desempenho

A tela abre em ~3 s e a listagem dos cards custa ~2,7 s. Se passar disso,
o suspeito tem nome: o gancho `resumo()` de cada importador consulta a origem
para dizer o próprio estado, e um deles pode estar varrendo tabela grande.

### A correção que vale portar de volta

Medido em 21/09/2026, **antes** de a Etapa 5 fechar:

| | antes | depois |
|---|---|---|
| listagem dos cards (abre a tela e roda após cada sincronização) | 96,4 s | **2,7 s** |
| `contabil_lancamentos` · pendentes | 101,5 s | **0,2 s** |
| `contabil_lancamentos` · sincronizar 1 empresa | 116,3 s | **3,2 s** |

Tudo isso era **uma consulta**, em `contabil_lancamentos._empresas_com_saldo`,
copiada do Portal. Ela marcava "esta empresa já tem lançamento?" com um
`EXISTS` correlacionado na lista de seleção. O plano:

```
SubPlan 1
  -> Seq Scan on bi_lancamento  (loops=306)
       Rows Removed by Filter: 1695457
```

Só 3 das 306 empresas têm lançamento, então o planejador estima que
`id_empresa = ?` casa com um terço da tabela e escolhe varredura sequencial.
Para as 303 sem nenhuma linha, provar a ausência custa a tabela inteira — 306
varreduras de 1,7 milhão de linhas. A reescrita calcula o conjunto uma vez e
junta por ele; o resultado é idêntico linha a linha.

**A tela de importação do `portal-integra` tem exatamente a mesma espera**, e a
correção é a mesma. Há guarda de regressão em `tests/test_importadores_contabeis.py`.

### Sincronizar só o que mudou — SINC-006, Fase 0

Não existe incremental: toda sincronização lê tudo da origem. A
[SINC-006](changes/SINC-006-assinatura-e-incremental.md) avalia o que uma
impressão digital por empresa economizaria e mediu o essencial: fotografar o
parque inteiro custa **12,3 s**, contra dezenas de minutos de uma rodada
completa.

A **Fase 0 está no ar e não pula nada**. Ela calcula a assinatura das cinco
entidades pesadas, compara com a guardada e registra no log:

```
[ASSINATURA] fiscal_movimento: 214 de 363 empresa(s) inalteradas desde a
             última sincronização (59%). Fase 0 — nada foi pulado.
```

**Esse número é o entregável.** Depois de uma semana de operação normal ele diz
se ligar o pulo (Fase 1) vale a pena — e abaixo de ~15% a resposta é não.

O checkpoint fica em `estado-sinc.db`, um SQLite ao lado do executável, fora do
git. Perdê-lo custa uma rodada completa: degradação segura, não corrupção. Para
forçar releitura, `app.importacao.estado.esquecer()`.

Duas propriedades que valem saber:

- **A medição nunca derruba a sincronização.** Origem fora, disco cheio,
  arquivo corrompido: loga e segue. Há teste para cada caso.
- **A taxa medida é otimista** nas rodadas sem `ids`, porque o escopo da
  assinatura é a origem e o importador aplica pré-requisitos próprios. Corrigir
  exige o importador dizer quais empresas processou — trabalho da Fase 1.

### O que continua caro, e é inerente

Ler os lançamentos de uma empresa grande do Domínio: 268.768 lançamentos em
**2,6 s** de ODBC. Testei lotes de 5.000, 20.000 e 50.000 linhas — dá o mesmo
tempo, então `LOTE_LEITURA` não é onde mexer.

## Progresso da rodada completa

**Sincronizar tudo** mostra em que pé está: o cabeçalho diz a fase corrente
("Em andamento: Plano de contas contábil — 2 de 10…") e cada fase concluída
vira uma linha com o próprio placar, conforme termina.

Isso funciona porque `POST /tudo` responde em **NDJSON transmitido**, e não numa
resposta única ao fim. Medido: o cabeçalho chega em 0s e o evento da segunda
fase aos 67,3s — espaçado pelo trabalho real.

A CLI usa o mesmo gancho e escreve `[2/10] Plano de contas contábil…` no log,
para que a rodada agendada também não fique muda.

## Duas armadilhas desta instalação

### Duas janelas do sincronizador dividem a porta

No Windows dois processos **podem** ligar na mesma porta, e qual deles atende
cada conexão é indefinido. O sintoma é cruel: a segunda janela sobe dizendo
`Running on http://127.0.0.1:7820`, o navegador responde, e quem atende é a
**primeira** — com o código antigo.

O `run.py` recusa subir quando a porta está ocupada, e diz o porquê. Se vir
essa mensagem, use a janela que já está aberta.

### O pool do PostgreSQL não pode ser pequeno

Ao terminar uma sincronização a tela recarrega tudo de uma vez: a listagem dos
dez cards, os nove `pendentes` em paralelo, o diagnóstico e o histórico.
Medido: **pico de 14 conexões simultâneas**.

Este projeto nasceu com `DB_POOL_MAX=5`, pelo raciocínio de que "é uma tela
para um operador". Um operador não é uma requisição — seis das doze chamadas
falhavam com `connection pool exhausted`, o diagnóstico caía junto e o botão
ficava desabilitado sem nada para reativá-lo. Hoje são **20**, o mesmo do
Portal. Não reduza.

## Nem toda empresa tem os dois BIs

Medido em 22/09/2026: **187 das 608 empresas ativas não têm nenhuma conta em
`ctcontas`** no Domínio. Para elas o BI Contábil simplesmente não se aplica —
não é falta de importação, é ausência de escrituração na origem. O caso típico
é o grupo com contabilidade centralizada na matriz:

```
510  DOURAGLASS MTZ           contas 1.161   lançamentos 208.865
512  DOURAGLASS FILIAL CG     contas     0   lançamentos   2.292
513  DOURAGLASS FILIAL SP     contas     0   lançamentos      57
514  DOURAGLASS FIL DDOS      contas     0   lançamentos       0
515  DOURAGLASS FILIAL MG     contas     0   lançamentos       2
```

Uma rodada nessas empresas mostra `Lançamentos contábeis` em erro, e **isso é o
esperado**. O que importa é que o ramo FISCAL roda normalmente: a 513 traz 405
notas, 285 apurações e 478 linhas de produto.

## Pendências conhecidas

Coisas que este projeto mede e não resolve, registradas para não se perderem.

### Empresa encerrada na origem continua ativa no Portal

Medido em 21/09/2026: **4 empresas** (`640`, `669`, `687`, `815`) estão
`ativo = TRUE` no Portal e `stat_emp = 'I'` no Domínio. As quatro têm dados no
BI Contábil — 3.117 linhas de saldo entre elas — e continuam sendo atualizadas
a cada sincronização e somando em qualquer consolidado.

Não é defeito da cópia: `ativo` é do Portal, e o sincronizador **não pode**
desligá-la (uma empresa pode estar encerrada na origem e ainda precisar
aparecer enquanto se fecha o exercício). O problema é que o `stat_emp` é lido,
usado para decidir quem entra, e depois **descartado** — nada em `empresas`
registra a situação na origem, então ninguém vê o encerramento.

Duas saídas, nenhuma feita:

1. **Barata, e só aqui:** o `resumo()` de `empresas` já devolve um campo
   `alerta` que a tela pinta em âmbar. Bastaria contar essas empresas e dizer.
2. **Correta, e no outro projeto:** a coluna `empresas.situacao_origem CHAR(1)`
   que o SYNC-001 §11.7 já propõe, com a nota de que *"o portal decide sozinho
   o que fazer com isso e nunca desativa a empresa por conta da sincronização"*.
   Exige migration no `portal-integra`.

### Conta com movimento que não entra no plano

Medido na primeira rodada completa (22/09/2026): **10 das 320 empresas com
saldo não somam zero** em `bi_saldo_mensal` — a identidade de partidas dobradas
não fecha. Maior desvio: R$ 75.901,98 (empresa 1).

**Não é defeito da sincronização**, e o código copiado já previa o caso — é o
aviso *"se a conta foi inativada no Domínio com saldo, a identidade
Ativo = Passivo + PL não vai fechar"*. São três condições do DOMÍNIO:

| Condição | Onde | Efeito |
|---|---|---|
| Conta `SITUACAO_CTA = 'I'` com movimento | 57 contas em 4 empresas (157, 194, 321, 705) | `plano_contas.sql` filtra `= 'A'`, a conta não chega a `bi_conta`, a perna é descartada |
| `codi_cta` citado em `ctlancto` **sem linha em `ctcontas`** | empresa 1 (749 pernas), empresa 704 (2) | referência solta na origem |
| Lado sem conta (sentinela `0`) | 73.060 pernas só na empresa 1 | benigno: o lançamento é de um lado só na própria origem |

As duas primeiras são erro de cadastro na origem e se corrigem **lá**. A
terceira é normal e o importador a trata como tal.

**O `portal-integra` tem o mesmo filtro**, então a condição é compartilhada — o
BI de lá mostra os mesmos números. Reimportar o plano não resolve: a conta
continua inativa na origem.

O que a sincronização garante, e foi conferido: `movimento_total =
credito_total − debito_total` com **zero violações em 554.006 linhas**, e
**nenhuma perna gravada sem conta no plano** (a FK composta barra).

